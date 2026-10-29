"""Identity rules: users, access overrides, and password hashing."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import date
from enum import Enum
from uuid import UUID

from argon2 import PasswordHasher, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type


ARGON2_TIME_COST = 2
ARGON2_MEMORY_COST = 19_456
ARGON2_PARALLELISM = 1
ARGON2_SALT_BYTES = 16
ARGON2_HASH_BYTES = 32
PASSWORD_HASHER = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST,
    parallelism=ARGON2_PARALLELISM,
    salt_len=ARGON2_SALT_BYTES,
    hash_len=ARGON2_HASH_BYTES,
    type=Type.ID,
)


class IdentityValidationError(ValueError):
    pass


class AccessMode(str, Enum):
    NONE = "none"
    VIEW = "view"
    EDIT = "edit"


# ── Legacy: the role model (until 04.10.2026) ──────────────────────────────────────────────────────────────
# Authorization no longer reads anything in this block (see «Уровни доступа» below). It stays one release for the
# role column and role tables that are still stored, the department guard (role names are not departments) and the
# staff import.
# «Как задумано ролью»: the sections each role was meant to have. Since SUPPLYMAP 01.09.2026 (Portal.gs
# portalApplyAccess_) a section is open only when EXPLICITLY granted — by the role's matrix row or a personal
# exception; an empty cell = closed. ROLE_SEED was never applied at run time: migration 0056 wrote it into
# `identity_role_access` once, so nobody lost access on the switch. It stays as the reference of what a role was set up
# with (the level transfer's tests use it as the role-era matrix).
# View grants on procurement / receiving / warehouse (ID-6) follow SUPPLYMAP's role sets (Portal.gs portalButtons_):
# buyer → склад, receiver → закупка, prodhead/management → закупка+приёмка+склад, master/staff → приёмка+склад.
# NAV-SECTIONS: «Оформить заявку» (`newrequest`) and «Мои заявки» (`myrequests`) are open to every role, as in
# SUPPLYMAP («Оформлять могут все роли»), so «Заявки» (`requests`) can be closed without taking filing away.
# «Выдача со списанием» (`handout`) is apart from picking (`fulfillment`): picking reserves, handout writes off, and
# the two can go to different people. handout: edit where SUPPLYMAP's role sets have it (receiver, admin), view where
# the role reads fulfillment today (engineer, prodhead, master) so their «Выдача» card stays (решено агентом — проверить).
_EVERYONE = {"newrequest": AccessMode.EDIT, "myrequests": AccessMode.EDIT}
ROLE_SEED: dict[str, dict[str, AccessMode]] = {
    "buyer": {
        **_EVERYONE,
        "requests": AccessMode.EDIT,
        "procurement": AccessMode.EDIT,
        "warehouse": AccessMode.VIEW,
        "erpref": AccessMode.VIEW,
    },
    "receiver": {
        **_EVERYONE,
        "receiving": AccessMode.EDIT,
        "warehouse": AccessMode.EDIT,
        "fulfillment": AccessMode.EDIT,
        "handout": AccessMode.EDIT,
        "shipping": AccessMode.EDIT,
        "procurement": AccessMode.VIEW,
        "erpref": AccessMode.VIEW,
    },
    "engineer": {
        **_EVERYONE,
        # «Проекты» (erptechmap): технолог заводит набор из расчёта и собирает из него техкарты (решено агентом — проверить).
        "projects": AccessMode.EDIT,
        "fulfillment": AccessMode.EDIT,
        "handout": AccessMode.VIEW,
        "erpref": AccessMode.VIEW,
    },
    "prodhead": {
        **_EVERYONE,
        "procurement": AccessMode.VIEW,
        "receiving": AccessMode.VIEW,
        "warehouse": AccessMode.VIEW,
        "fulfillment": AccessMode.VIEW,
        "handout": AccessMode.VIEW,
        "production": AccessMode.VIEW,
        "shipping": AccessMode.VIEW,
        "erpref": AccessMode.VIEW,
    },
    "master": {
        **_EVERYONE,
        "receiving": AccessMode.VIEW,
        "warehouse": AccessMode.VIEW,
        "fulfillment": AccessMode.VIEW,
        "handout": AccessMode.VIEW,
        "production": AccessMode.EDIT,
        "shipping": AccessMode.VIEW,
        "erpref": AccessMode.VIEW,
    },
    "management": {
        **_EVERYONE,
        "procurement": AccessMode.VIEW,
        "receiving": AccessMode.VIEW,
        "warehouse": AccessMode.VIEW,
        "production": AccessMode.VIEW,
        "shipping": AccessMode.VIEW,
        "erpref": AccessMode.VIEW,
        "operations": AccessMode.VIEW,
    },
    "pm": {**_EVERYONE, "shipping": AccessMode.EDIT, "erpref": AccessMode.VIEW},
    "staff": {
        **_EVERYONE,
        "requests": AccessMode.EDIT,
        "receiving": AccessMode.VIEW,
        "warehouse": AccessMode.VIEW,
        "production": AccessMode.VIEW,
        "erpref": AccessMode.VIEW,
    },
    "estimator": {**_EVERYONE, "erpref": AccessMode.VIEW},
    "qc": {**_EVERYONE, "production": AccessMode.EDIT, "erpref": AccessMode.VIEW},
}
ROLES = frozenset(
    {
        "admin",
        "buyer",
        "receiver",
        "estimator",
        "prodhead",
        "master",
        "management",
        "pm",
        "qc",
        "engineer",
        "staff",
    }
)
# Sections the «Доступы» matrix manages, in portal block order (Производство · Снабжение · Склад · Менеджмент).
# One ERP key can cover several SUPPLYMAP cards: `production` = планирование + задания + план. «Оформить заявку»,
# «Мои заявки» and «Выдача со списанием» have keys of their own (NAV-SECTIONS, SUPPLYMAP Portal.gs PORTAL_SECTIONS_).
SECTION_LABELS: dict[str, str] = {
    "projects": "Проекты",
    # ID-3 (SUPPLYMAP PORTAL_SECTIONS_): templates, the generator, the reference book and the staff directory are
    # keys of their own (erptctpl, tcgen, erpref, staffdir), so e.g. only the generator can go to one technologist.
    "techcards": "Техкарты проектов",
    "erptctpl": "Шаблоны техкарт",
    "tcgen": "Генератор техкарт",
    "erpref": "Справочник",
    "production": "Планирование, задания, производственный план",
    "newrequest": "Оформить заявку",
    "myrequests": "Мои заявки",
    "requests": "Заявки",
    "procurement": "Снабжение, справочник закупки, первичная документация",
    "receiving": "Приёмка",
    "warehouse": "Склад",
    "fulfillment": "Запросы на комплектацию (набор)",
    "handout": "Выдача со списанием",
    "shipping": "Отгрузка",
    # «Доступы» is not a section: it is always the administrator's (SUPPLYMAP portalButtons_ pushes it for role admin
    # outside the matrix), so nobody can close the settings to themselves.
    "staffdir": "Сотрудники",
    "operations": "Системный журнал",
}
# The role model's «always full» roles (admin, buyer, prodhead, management) and its role_permissions are gone: the
# full levels are FULL_ACCESS_LEVELS (superadmin, admin) below; level_migration keeps the frozen old model.


@dataclass(frozen=True)
class IdentityUser:
    id: UUID
    login: str
    email: str
    name: str
    department: str
    role: str
    site: str | None
    active: bool
    # «Личная карточка» (owner 04.10.2026): кадровые поля. Empty text / None = not filled.
    personnel_number: str = ""  # табельный номер, unique when set (FRX-0017)
    job_title: str = ""  # должность
    qualification: str = ""  # категория / квалификация
    job_since: date | None = None  # в должности с
    manager_id: UUID | None = None  # непосредственный руководитель
    # Уровень доступа (what the gates read) and the two flags (0086). "" — not set (a user built in memory, an old
    # screen that sends only the legacy role): the level then follows the role (level_migration.level_for_role).
    access_level: str = ""
    shop_worker: bool = False  # «Монтажник (работает в цеху)»
    site_master: bool = False  # «Мастер площадки»


def normalize_login(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if not normalized or len(normalized) > 255:
        raise IdentityValidationError("Некорректный логин")
    return normalized


def validate_password(password: str) -> str:
    value = str(password or "")
    if len(value) < 8 or len(value) > 128:
        raise IdentityValidationError("Пароль должен содержать от 8 до 128 символов")
    return value


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    value = validate_password(password)
    actual_salt = salt or secrets.token_bytes(ARGON2_SALT_BYTES)
    if len(actual_salt) != ARGON2_SALT_BYTES:
        raise IdentityValidationError("Некорректная соль пароля")
    return PASSWORD_HASHER.hash(value, salt=actual_salt)


def verify_password(password: str, encoded: str) -> bool:
    try:
        parameters = extract_parameters(encoded)
        if (
            parameters.type != Type.ID
            or parameters.version != 19
            or parameters.time_cost != ARGON2_TIME_COST
            or parameters.memory_cost != ARGON2_MEMORY_COST
            or parameters.parallelism != ARGON2_PARALLELISM
            or parameters.salt_len != ARGON2_SALT_BYTES
            or parameters.hash_len != ARGON2_HASH_BYTES
        ):
            return False
        return PASSWORD_HASHER.verify(encoded, str(password or ""))
    except (InvalidHashError, VerificationError, ValueError, TypeError):
        return False


# ── PIN (SUPPLYMAP Portal.gs portalLogin / portalRevealPin_ / portalSetPin, Staff.gs portalStaffSave) ──────────
# PORTAL_PIN_RE_: one policy for the administrator's PIN and the staff screen — 6–8 digits.
PIN_RE = re.compile(r"^[0-9]{6,8}$")  # as JS \d: ASCII digits only
PIN_POLICY = "PIN — 6–8 цифр."


def validate_pin(pin: str) -> str:
    value = str(pin if pin is not None else "").strip()
    if not PIN_RE.fullmatch(value):
        raise IdentityValidationError(PIN_POLICY)
    return value


def hash_pin(pin: str, *, salt: bytes | None = None) -> str:
    """The PIN is kept as an Argon2id hash with the password parameters (SUPPLYMAP: sha256(email:PIN))."""
    value = validate_pin(pin)
    actual_salt = salt or secrets.token_bytes(ARGON2_SALT_BYTES)
    return PASSWORD_HASHER.hash(value, salt=actual_salt)


def new_pin() -> str:
    """portalNewPin_: six digits, the first is not zero (a PIN typed as a number loses a leading zero)."""
    return str(secrets.randbelow(9) + 1) + "".join(str(secrets.randbelow(10)) for _ in range(5))


# ── «Режим админа» (Portal.gs portalCanAdminMode_ / portalToggleAdmin) ─────────────────────────────────────
# The account that may lift its role to admin for a while: GOD_MODE_EMAIL (Config.gs, Виктор Домрачев).
ADMIN_MODE_EMAIL = "tektra.real.tektra@gmail.com"


def can_admin_mode(email: str) -> bool:
    return str(email or "").strip().casefold() == ADMIN_MODE_EMAIL.casefold()


def _overlay(base: dict[str, AccessMode], overrides: dict[str, AccessMode]) -> dict[str, AccessMode]:
    result = {section: mode for section, mode in base.items() if mode is not AccessMode.NONE}
    for section, mode in overrides.items():
        if mode is AccessMode.NONE:
            result.pop(section, None)
        else:
            result[section] = mode
    return result


def effective_permissions(
    row: dict[str, AccessMode],
    user_access: dict[str, AccessMode],
) -> dict[str, AccessMode]:
    """portalAccessFor_: the personal exception is stronger than the matrix row (`none` takes a section away)."""
    return _overlay(row, user_access)


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ── «Сотрудники» (ID-8, SUPPLYMAP Staff.gs portalStaffGet/portalStaffSave/staffDepts_) ─────────────

# Legacy: PORTAL_ROLE_LABELS_ (Portal.gs), the labels of the old role keys. The screens show LEVEL_LABELS now; these
# stay for the legacy `role` field and the department guard below.
ROLE_LABELS: dict[str, str] = {
    "admin": "Технолог",
    "buyer": "Специалист по снабжению",
    "receiver": "Кладовщик",
    "estimator": "Сметчик",
    "prodhead": "Директор производства",
    "master": "Мастер",
    "management": "Генеральный директор",
    "pm": "Менеджер по проектам",
    "qc": "ОТК",
    "engineer": "Инженер",
    "staff": "Сотрудник",
}
# STAFF_RETIRED_ROLE_LABELS_: labels renamed on 31.08.2026 that still sit in old department values.
RETIRED_ROLE_LABELS = frozenset({"администратор", "приёмка/склад", "приемка/склад", "закупщик"})
# WORK_SITES (Config.gs): empty site = the employee sees every site (office, procurement).
WORK_SITES: dict[str, str] = {"ЦАР": "Москва", "ЖУК": "Раменское", "АДМ": "Админ"}
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
TEXT_LIMIT = 255


def normalize_email(value: str) -> str:
    email = str(value or "").strip().casefold()
    if not email or len(email) > TEXT_LIMIT or not _EMAIL.match(email):
        raise IdentityValidationError(f"Некорректный e-mail «{email}».")
    return email


TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
# Staff without a mailbox get a placeholder address, as the imported shop staff did (import_staff.py).
PLACEHOLDER_EMAIL_DOMAIN = "fraxis.local"


def translit(text: str) -> str:
    out = "".join(TRANSLIT.get(ch, ch) for ch in text.casefold())
    return "".join(ch for ch in out if ch.isascii() and ch.isalnum())


def short_login(name: str, taken: set[str]) -> str:
    """The transliterated surname («Безменов Никита» → bezmenov); a clash adds the first letter of the name, then a digit."""
    parts = name.split()
    surname = translit(parts[0]) if parts else ""
    first = translit(parts[1])[:1] if len(parts) > 1 else ""
    base = surname or "user"
    for candidate in (base, base + first):
        if candidate and candidate not in taken:
            return candidate
    n = 2
    while f"{base}{first}{n}" in taken:
        n += 1
    return f"{base}{first}{n}"


def placeholder_email(name: str, taken: set[str]) -> str:
    """«Зингер Артем» → zinger.artem@fraxis.local; a clash adds a digit."""
    local = ".".join(part for part in (translit(word) for word in name.split()[:2]) if part) or "user"
    candidate, n = f"{local}@{PLACEHOLDER_EMAIL_DOMAIN}", 2
    while candidate in taken:
        candidate, n = f"{local}{n}@{PLACEHOLDER_EMAIL_DOMAIN}", n + 1
    return candidate


def normalize_name(value: str) -> str:
    name = str(value or "").strip()
    if not name:
        raise IdentityValidationError("Пустое ФИО.")
    if len(name) > TEXT_LIMIT:
        raise IdentityValidationError("ФИО длиннее 255 символов.")
    return name


def normalize_department(value: str) -> str:
    department = str(value or "").strip()
    if len(department) > TEXT_LIMIT:
        raise IdentityValidationError("Название отдела длиннее 255 символов.")
    return department


def normalize_role(value: str) -> str:
    role = str(value or "").strip()
    if role not in ROLES:
        raise IdentityValidationError(f"Неизвестная роль «{role}».")
    return role


def normalize_site(value: str | None) -> str | None:
    site = str(value or "").strip().upper()
    if not site:
        return None
    if site not in WORK_SITES:
        raise IdentityValidationError(f"Неизвестное место работы «{site}».")
    return site


def department_is_role_like(value: str) -> bool:
    """deptIsRoleLike_: a department answers «where the person sits», a role «what is open to them»; a
    role key or label (current or retired) is kept out of the department list."""
    lowered = str(value or "").strip().casefold()
    if not lowered:
        return False
    if lowered in RETIRED_ROLE_LABELS:
        return True
    return any(lowered == key.casefold() or lowered == label.casefold() for key, label in ROLE_LABELS.items())


def _ru_key(value: str) -> tuple[str, str]:
    lowered = value.casefold()
    return lowered.replace("ё", "е"), lowered


def department_list(ordered: list[str], *more: list[str]) -> list[str]:
    """staffDepts_: the union of every source, trimmed, without blanks, case-insensitive duplicates and
    role-like values. The first source (the saved list, or what the administrator typed) keeps its order — the
    «Отделы» list is shown as stored (owner 12.10.2026, migration 0104); departments only the other sources bring (in
    use, not listed yet) go to the end, sorted as Russian text. The first spelling met wins."""
    result: dict[str, str] = {}

    def take(values: list[str]) -> list[str]:
        taken: list[str] = []
        for value in values:
            name = str(value or "").strip()
            if not name or department_is_role_like(name) or name.casefold() in result:
                continue
            result[name.casefold()] = name
            taken.append(name)
        return taken

    head = take(ordered)
    tail = [name for source in more for name in take(source)]
    return head + sorted(tail, key=_ru_key)


# ── Кадровые поля «Личной карточки» (native, owner 04.10.2026; no SUPPLYMAP source) ──────────────

PERSONNEL_NUMBER_LIMIT = 32


def normalize_personnel_number(value: str | None) -> str:
    """Табельный номер (FRX-0017): optional; spaces collapsed and letters upper-cased, so «frx-0017» and «FRX-0017» are
    one number and the unique index catches both."""
    number = " ".join(str(value or "").split()).upper()
    if len(number) > PERSONNEL_NUMBER_LIMIT:
        raise IdentityValidationError(f"Табельный номер длиннее {PERSONNEL_NUMBER_LIMIT} символов.")
    return number


def normalize_job_text(value: str | None, label: str) -> str:
    """Должность, категория / квалификация: free text, optional."""
    text = " ".join(str(value or "").split())
    if len(text) > TEXT_LIMIT:
        raise IdentityValidationError(f"{label} длиннее {TEXT_LIMIT} символов.")
    return text


def check_manager_chain(
    user_id: UUID, manager_id: UUID | None, managers: dict[UUID, UUID | None], names: dict[UUID, str],
) -> None:
    """Непосредственный руководитель: not the employee themself, and no closed loop — walking up from the new manager
    (`managers`: employee → their manager, as stored) must never come back to the employee."""
    if manager_id is None:
        return
    if manager_id == user_id:
        raise IdentityValidationError("Сотрудник не может быть руководителем сам себе.")
    seen: set[UUID] = set()
    current: UUID | None = manager_id
    while current is not None and current not in seen:
        if current == user_id:
            raise IdentityValidationError(
                f"Замкнутый круг подчинения: «{names.get(user_id, 'сотрудник')}» уже стоит в цепочке руководителей "
                f"«{names.get(manager_id, 'руководитель')}». Выберите другого руководителя."
            )
        seen.add(current)
        current = managers.get(current)


# ── Уровни доступа вместо ролей-должностей (owner 04.10.2026, docs/migration/access-levels-plan.md) ──────────
# Authorization runs on these (T2): a person has a level (`identity_users.access_level`); the level's row of the
# level matrix (`identity_level_access`) overlaid with their personal exceptions (`identity_user_level_access`, `none`
# closes) is what they may open; superadmin/admin are always full. A route checks only the section modes
# (shared/http_auth.py) — no role lists. The role tables (`identity_role_access`, `identity_user_access`) and the role
# column are legacy, kept one release. Должность is the separate free-text `job_title`.

# Highest first: the order of the «Доступы» columns and of the level select in «Сотрудники».
# «Администратор» is no longer a level a person can hold (owner 14.10.2026: «Администратора можно удалить»; nobody held
# it — the 14.10 levels migration moves any stored holder to superadmin). Its key lives on only as ADMIN_MODE_LEVEL.
ACCESS_LEVELS = ("superadmin", "commercial", "tech_lead", "pm", "storekeeper", "foreman", "operator")
LEVEL_LABELS: dict[str, str] = {
    "superadmin": "Суперадминистратор",  # ГД, учредители
    "commercial": "Коммерческое руководство",  # маркетинг, продажи
    "tech_lead": "Технический руководитель",  # нач. производства, главный технолог, закупщик
    "pm": "Менеджер по проектам",  # owner 14.10.2026
    "storekeeper": "Кладовщик",  # owner 14.10.2026
    "foreman": "Бригадир",  # бригадир
    "operator": "Оператор",  # конечный исполнитель
}
# «Режим админа» (Portal.gs portalToggleAdmin): the session-only level the admin mode lifts a session to — full access,
# but not a superadministrator (it cannot give or take the full level). Never stored, never assignable, no matrix column.
ADMIN_MODE_LEVEL = "admin"
ADMIN_MODE_LABEL = "Администратор"
ADMIN_LEVEL_RETIRED = "Уровня «Администратор» больше нет — выберите «Суперадминистратор» или другой уровень."
# Always edit everywhere, whatever the matrix or a personal exception says; only these see «Доступы», «Сотрудники» and
# the administrator's tools. ADMIN_MODE_LEVEL stays in the set so the admin mode (and a stray stored 'admin') keeps
# full access: nobody loses rights by the level's removal.
FULL_ACCESS_LEVELS = frozenset({"superadmin", ADMIN_MODE_LEVEL})
# The level matrix: the role matrix's sections without «Сотрудники» (a full-access level's own) plus «Задания» (the
# operator's own tasks, split off `production`), «ОТК» (quality edits, split off `production`) and «Отгрузка — склад»
# (the storekeeper's shipping actions, split off `shipping`).
LEVEL_SECTION_LABELS: dict[str, str] = {
    "projects": "Проекты",
    "techcards": "Техкарты проектов",
    "erptctpl": "Шаблоны техкарт",
    "tcgen": "Генератор техкарт",
    "erpref": "Справочник",
    "production": "Планирование и производственный план",
    "myops": "Задания",
    "quality": "ОТК",
    "newrequest": "Оформить заявку",
    # Витя's edits 2–4 (owner 23.10): the keys stay, the screens moved — «Мои заявки» open from the personal card, the
    # «Заявки» register from «Снабжение»; `procurement` also opens «Справочники → Закупка» and «Первичная документация».
    "myrequests": "Мои заявки",
    "requests": "Заявки",
    "procurement": "Снабжение, справочник закупки, первичная документация",
    "receiving": "Приёмка",
    "warehouse": "Склад",
    "fulfillment": "Запросы на комплектацию (набор)",
    "handout": "Выдача со списанием",
    "shipping": "Отгрузка",
    "shipping_goods": "Отгрузка — склад",
    # Owner 29.10.2026: «Отгрузить по пинкоду без УПД: пинкод у Артемия, Зингера, Касаточкина». Edit = the person may
    # permit one shipment to leave without a УПД by typing their own PIN (workflows/ship_no_upd.py). No level seed: only
    # the full levels hold it until the owner grants it in «Доступы».
    "ship_no_upd": "Отгрузка без УПД — разрешение",
    # Owner 28.10.2026: «Сканировать документы в базу может ограниченное количество операторов… печать отдельных
    # документов доступна ограниченному количеству операторов (по должности)». `scan` edit sends a scan job to an agent
    # (on the scanner the person chose) and installs agents on new PCs (warehouse/scan_jobs_api.py scan_edit_principal);
    # `print` edit shows the screens' document print buttons (the frontend reads `permissions.print`).
    "scan": "Сканирование документов",
    "print": "Печать документов",
    "operations": "Системный журнал",
    # «Кадры» (cabinet plan, phase 2): a section key, not a role — edit registers leave requests (identity/leave.py)
    # and substitutes for a missing manager. Closed for every level until granted in «Доступы».
    "hr": "Кадры",
}
# The plan's default matrix: a level nobody holds yet (and a fresh database) starts from it. A level with people gets
# its row from what they could really do (level_migration). «Оформить заявку» and «Мои заявки» stay open to everyone,
# as with the roles («Оформлять могут все роли»; решено агентом — проверить). «Печать документов» (owner 28.10.2026)
# starts open to every level — the owner narrows it in «Доступы»; «Сканирование документов» is the storekeeper's.
_FILE_REQUESTS = {"newrequest": AccessMode.EDIT, "myrequests": AccessMode.EDIT, "print": AccessMode.EDIT}
LEVEL_SEED: dict[str, dict[str, AccessMode]] = {
    "commercial": {
        **_FILE_REQUESTS,
        "projects": AccessMode.VIEW,
        "shipping": AccessMode.VIEW,
        "procurement": AccessMode.VIEW,
        "warehouse": AccessMode.VIEW,
        "production": AccessMode.VIEW,
        "requests": AccessMode.EDIT,
    },
    "tech_lead": {
        **_FILE_REQUESTS,
        "projects": AccessMode.EDIT,
        "techcards": AccessMode.EDIT,
        "erptctpl": AccessMode.EDIT,
        "tcgen": AccessMode.EDIT,
        "erpref": AccessMode.EDIT,
        "production": AccessMode.EDIT,
        "procurement": AccessMode.EDIT,
        "requests": AccessMode.EDIT,
        "fulfillment": AccessMode.EDIT,
        "receiving": AccessMode.VIEW,
        "warehouse": AccessMode.VIEW,
        "handout": AccessMode.VIEW,
        "quality": AccessMode.VIEW,
        "shipping": AccessMode.VIEW,
    },
    # owner 14.10.2026 (Ульяна): «Проекты» and «Отгрузка» are hers, planning and stock readable, «Задания» closed.
    "pm": {
        **_FILE_REQUESTS,
        "projects": AccessMode.EDIT,
        "techcards": AccessMode.VIEW,
        "production": AccessMode.VIEW,
        "erpref": AccessMode.VIEW,
        "requests": AccessMode.EDIT,
        "procurement": AccessMode.VIEW,
        "warehouse": AccessMode.VIEW,
        "shipping": AccessMode.EDIT,
        "shipping_goods": AccessMode.VIEW,
        "scan": AccessMode.EDIT,  # УПД отправки сканом (#747)
    },
    # owner 14.10.2026 (Алексей): receiving, stock, picking, handout and the shipping warehouse actions are his.
    "storekeeper": {
        **_FILE_REQUESTS,
        "projects": AccessMode.VIEW,
        "erpref": AccessMode.VIEW,
        "procurement": AccessMode.VIEW,
        "receiving": AccessMode.EDIT,
        "warehouse": AccessMode.EDIT,
        "fulfillment": AccessMode.EDIT,
        "handout": AccessMode.EDIT,
        "shipping": AccessMode.VIEW,
        "shipping_goods": AccessMode.EDIT,
        "scan": AccessMode.EDIT,
    },
    "foreman": {
        **_FILE_REQUESTS,
        "production": AccessMode.EDIT,
        "myops": AccessMode.EDIT,
        "receiving": AccessMode.EDIT,
        "warehouse": AccessMode.EDIT,
        "fulfillment": AccessMode.EDIT,
        "handout": AccessMode.EDIT,
        "shipping_goods": AccessMode.EDIT,
        "procurement": AccessMode.VIEW,
        "quality": AccessMode.VIEW,
    },
    "operator": {
        **_FILE_REQUESTS,
        "myops": AccessMode.EDIT,
        "receiving": AccessMode.VIEW,
        "warehouse": AccessMode.VIEW,
        "erpref": AccessMode.VIEW,
    },
}
# Sections outside the level matrix: «Сотрудники» belongs to the full levels alone (the matrix cannot give it to
# anyone). A full level's permissions carry it, so the screens keep showing the menu item to administrators.
FULL_LEVEL_SECTIONS: tuple[str, ...] = (*LEVEL_SECTION_LABELS, "staffdir")


def level_label(level: str) -> str:
    """The level's Russian name; the admin-mode session level has one too, an unknown key is shown as is."""
    return ADMIN_MODE_LABEL if level == ADMIN_MODE_LEVEL else LEVEL_LABELS.get(level, level)


def normalize_level(value: str) -> str:
    level = str(value or "").strip()
    if level == ADMIN_MODE_LEVEL:
        raise IdentityValidationError(ADMIN_LEVEL_RETIRED)
    if level not in LEVEL_LABELS:
        raise IdentityValidationError(f"Неизвестный уровень доступа «{level}».")
    return level


def level_permissions(
    level: str, level_row: dict[str, AccessMode], exceptions: dict[str, AccessMode],
) -> dict[str, AccessMode]:
    """What a person may open: superadmin (and the admin-mode session) — every section on edit, whatever is stored (their exceptions are
    ignored); any other level — the level's matrix row overlaid with the personal exceptions (`none` or an empty cell
    closes), limited to the matrix sections."""
    if level in FULL_ACCESS_LEVELS:
        return {section: AccessMode.EDIT for section in FULL_LEVEL_SECTIONS}
    effective = _overlay(level_row, exceptions)
    return {section: mode for section, mode in effective.items() if section in LEVEL_SECTION_LABELS}

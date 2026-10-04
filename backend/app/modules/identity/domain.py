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


# «Как задумано ролью»: the sections each role was meant to have. Since SUPPLYMAP 01.09.2026 (Portal.gs
# portalApplyAccess_) a section is open only when EXPLICITLY granted — by the role's matrix row or a personal
# exception; an empty cell = closed. ROLE_SEED is therefore not applied at run time: migration 0056 wrote it into
# `identity_role_access` once, so nobody lost access on the switch, and `seed_role_matrix` does the same for a fresh
# database. It stays here as the editor's reference of what a role was set up with.
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
    "procurement": "Лист закупки",
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
# Roles that always have full access to every section and cannot be reduced (owner, 2026-09-29: технолог = admin,
# специалист по снабжению = buyer, директор производства = prodhead, генеральный директор = management — «полный
# доступ всегда»). For them an empty cell, a matrix `none` and a personal exception are all ignored: `role_permissions`
# returns edit everywhere and `_principal` skips the personal overlay, so nobody can accidentally take a section away.
# `require_section` also lets them past a route's role list, so an action bound to another role is open to them too.
# «Доступы» (the access-matrix editor, access_admin_principal) stays admin-only, so the guarantee cannot be edited away
# from these roles (решено агентом — проверить).
FULL_ACCESS_ROLES = frozenset({"admin", "buyer", "prodhead", "management"})
# What an EMPTY cell means (`role_permissions`): closed for every role, except the full-access roles above, which have
# every section on edit no matter what the matrix or a personal exception says (ID-5, SUPPLYMAP portalAdminAccess_,
# 14.09.2026, extended 2026-09-29) — a new section needs no entry anywhere.
ROLE_DEFAULTS: dict[str, dict[str, AccessMode]] = {
    role: {section: AccessMode.EDIT for section in SECTION_LABELS} for role in FULL_ACCESS_ROLES
}


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
    # Уровень доступа and the two flags (0086). Read from the database; until the screens edit them, a new or re-roled
    # employee gets them from the role (infrastructure). "" — not read yet (a user built in memory).
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


def role_permissions(role: str, overrides: dict[str, AccessMode]) -> dict[str, AccessMode]:
    """The role's sections: the «Доступы» matrix row (view/edit open, `none` or empty closes); the full-access roles
    start from «edit everywhere» (ROLE_DEFAULTS) and ignore the matrix entirely — their access cannot be reduced."""
    if role in FULL_ACCESS_ROLES:
        return {section: AccessMode.EDIT for section in SECTION_LABELS}
    return _overlay(ROLE_DEFAULTS.get(role, {}), overrides)


def effective_permissions(
    role_access: dict[str, AccessMode],
    user_access: dict[str, AccessMode],
) -> dict[str, AccessMode]:
    """portalAccessFor_: the personal exception is stronger than the role (`none` takes a section away)."""
    return _overlay(role_access, user_access)


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ── «Сотрудники» (ID-8, SUPPLYMAP Staff.gs portalStaffGet/portalStaffSave/staffDepts_) ─────────────

# PORTAL_ROLE_LABELS_ (Portal.gs): the labels the screen shows for role keys.
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


def department_list(*sources: list[str]) -> list[str]:
    """staffDepts_: the union of every source, trimmed, without blanks, case-insensitive duplicates and
    role-like values, sorted as Russian text. The first spelling met wins."""
    result: dict[str, str] = {}
    for source in sources:
        for value in source:
            name = str(value or "").strip()
            if not name or department_is_role_like(name) or name.casefold() in result:
                continue
            result[name.casefold()] = name
    return sorted(result.values(), key=_ru_key)


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
# Migration 0086 stores the level of every person (`identity_users.access_level`), the level matrix
# (`identity_level_access`) and the personal exceptions on top of it (`identity_user_level_access`); the gates still run
# on the old roles until they are switched over. Должность is the separate free-text `job_title`.

# Highest first.
ACCESS_LEVELS = ("superadmin", "admin", "commercial", "tech_lead", "foreman", "operator")
ACCESS_LEVEL_LABELS: dict[str, str] = {
    "superadmin": "Суперадминистратор",  # ГД, учредители
    "admin": "Администратор",  # менеджер высшего звена, отвечающий за систему; дирекция
    "commercial": "Коммерческое руководство",  # маркетинг, продажи
    "tech_lead": "Технический руководитель",  # нач. производства, главный технолог, закупщик
    "foreman": "Бригадир",  # бригадир, кладовщик
    "operator": "Оператор",  # конечный исполнитель
}
# Always edit everywhere, whatever the matrix or a personal exception says; only these see «Доступы», «Сотрудники» and
# the administrator's tools.
FULL_ACCESS_LEVELS = frozenset({"superadmin", "admin"})
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
    "myrequests": "Мои заявки",
    "requests": "Заявки",
    "procurement": "Лист закупки",
    "receiving": "Приёмка",
    "warehouse": "Склад",
    "fulfillment": "Запросы на комплектацию (набор)",
    "handout": "Выдача со списанием",
    "shipping": "Отгрузка",
    "shipping_goods": "Отгрузка — склад",
    "operations": "Системный журнал",
}
# The plan's default matrix: a level nobody holds yet (and a fresh database) starts from it. A level with people gets
# its row from what they could really do (level_migration). «Оформить заявку» and «Мои заявки» stay open to everyone,
# as with the roles («Оформлять могут все роли»; решено агентом — проверить).
_FILE_REQUESTS = {"newrequest": AccessMode.EDIT, "myrequests": AccessMode.EDIT}
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

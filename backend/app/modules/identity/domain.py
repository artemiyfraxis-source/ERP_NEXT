"""Identity rules: users, access overrides, and password hashing."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
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


# Default section access per role; seeded into identity_role_access when a role gets its first user
# (CLI create-user and the «Сотрудники» screen share it).
# View grants on procurement / receiving / warehouse (ID-6) follow SUPPLYMAP's role sets (Portal.gs portalButtons_):
# buyer → склад, receiver → закупка, prodhead/management → закупка+приёмка+склад, master/staff → приёмка+склад.
# Databases that already exist receive them from migration 0038 (ON CONFLICT DO NOTHING).
ROLE_DEFAULTS: dict[str, dict[str, AccessMode]] = {
    "admin": {
        "identity": AccessMode.EDIT,
        "projects": AccessMode.EDIT,
        "calculation": AccessMode.EDIT,
        "techcards": AccessMode.EDIT,
        "requests": AccessMode.EDIT,
        "procurement": AccessMode.EDIT,
        "receiving": AccessMode.EDIT,
        "warehouse": AccessMode.EDIT,
        "fulfillment": AccessMode.EDIT,
        "production": AccessMode.EDIT,
        "shipping": AccessMode.EDIT,
        "references": AccessMode.EDIT,
        "operations": AccessMode.EDIT,
    },
    "buyer": {
        "requests": AccessMode.EDIT,
        "procurement": AccessMode.EDIT,
        "warehouse": AccessMode.VIEW,
        "references": AccessMode.VIEW,
    },
    "receiver": {
        "receiving": AccessMode.EDIT,
        "warehouse": AccessMode.EDIT,
        "fulfillment": AccessMode.EDIT,
        "shipping": AccessMode.EDIT,
        "procurement": AccessMode.VIEW,
        "references": AccessMode.VIEW,
    },
    "engineer": {"fulfillment": AccessMode.EDIT, "references": AccessMode.VIEW},
    "prodhead": {
        "procurement": AccessMode.VIEW,
        "receiving": AccessMode.VIEW,
        "warehouse": AccessMode.VIEW,
        "fulfillment": AccessMode.VIEW,
        "production": AccessMode.VIEW,
        "shipping": AccessMode.VIEW,
        "references": AccessMode.VIEW,
    },
    "master": {
        "receiving": AccessMode.VIEW,
        "warehouse": AccessMode.VIEW,
        "fulfillment": AccessMode.VIEW,
        "production": AccessMode.EDIT,
        "shipping": AccessMode.VIEW,
        "references": AccessMode.VIEW,
    },
    "management": {
        "procurement": AccessMode.VIEW,
        "receiving": AccessMode.VIEW,
        "warehouse": AccessMode.VIEW,
        "production": AccessMode.VIEW,
        "shipping": AccessMode.VIEW,
        "references": AccessMode.VIEW,
        "operations": AccessMode.VIEW,
    },
    "pm": {"shipping": AccessMode.EDIT, "references": AccessMode.VIEW},
    "staff": {
        "requests": AccessMode.EDIT,
        "receiving": AccessMode.VIEW,
        "warehouse": AccessMode.VIEW,
        "production": AccessMode.VIEW,
        "references": AccessMode.VIEW,
    },
    "estimator": {"references": AccessMode.VIEW},
    "qc": {"production": AccessMode.EDIT, "references": AccessMode.VIEW},
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


def effective_permissions(
    role_access: dict[str, AccessMode],
    user_access: dict[str, AccessMode],
) -> dict[str, AccessMode]:
    result = dict(role_access)
    for section, mode in user_access.items():
        if mode is AccessMode.NONE:
            result.pop(section, None)
        else:
            result[section] = mode
    return result


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

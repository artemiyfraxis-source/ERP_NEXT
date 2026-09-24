"""Identity rules: users, access overrides, and password hashing."""

from __future__ import annotations

import hashlib
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

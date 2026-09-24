from uuid import UUID

import pytest

from app.modules.identity.domain import (
    AccessMode,
    IdentityValidationError,
    IdentityUser,
    effective_permissions,
    hash_password,
    normalize_login,
    verify_password,
)


def test_argon2id_password_round_trip_uses_random_salts():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")

    assert first != second
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong password", first)
    assert "correct horse" not in first


def test_password_policy_and_login_normalization():
    assert normalize_login("  User@Example.COM ") == "user@example.com"
    with pytest.raises(IdentityValidationError, match="8"):
        hash_password("short")


def test_hash_parameters_are_bounded_before_expensive_verification():
    hostile = "$argon2id$v=19$m=999999999,t=99,p=99$c2FsdHNhbHRzYWx0c2FsdA$YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE"

    assert verify_password("correct-password", hostile) is False


def test_user_override_can_remove_role_access_or_grant_another_section():
    permissions = effective_permissions(
        {"requests": AccessMode.EDIT, "procurement": AccessMode.VIEW},
        {"procurement": AccessMode.NONE, "warehouse": AccessMode.VIEW},
    )

    assert permissions == {
        "requests": AccessMode.EDIT,
        "warehouse": AccessMode.VIEW,
    }


def test_identity_user_has_stable_uuid_separate_from_display_name():
    user = IdentityUser(
        id=UUID("10000000-0000-0000-0000-000000000001"),
        login="petrov",
        email="petrov@example.test",
        name="Петров",
        department="Закупки",
        role="buyer",
        site=None,
        active=True,
    )

    assert user.id != UUID(int=0)

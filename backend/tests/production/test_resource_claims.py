from datetime import UTC, datetime, timedelta
from dataclasses import replace
from uuid import uuid4

import pytest

from app.modules.production.resource_claims import ResourceClaim, ResourceKey, ResourceKind, ResourceOwner, ResourceWindow


NOW = datetime(2026, 11, 3, 8, tzinfo=UTC)


def claim(**changes):
    result = ResourceClaim(ResourceKey(ResourceKind.MACHINE, "ОБ.027.2"), ResourceOwner("setup", uuid4()), ResourceWindow(NOW, NOW + timedelta(hours=1)))
    return replace(result, **changes)


def test_planned_windows_are_half_open():
    left = claim()
    assert left.conflicts(claim())
    assert not left.conflicts(claim(window=ResourceWindow(NOW + timedelta(hours=1), NOW + timedelta(hours=2))))


def test_active_claim_never_expires_by_planned_finish():
    left = claim(activated_at=NOW)
    assert left.conflicts(claim(window=ResourceWindow(NOW + timedelta(days=2), NOW + timedelta(days=3))))
    assert not replace(left, released_at=NOW + timedelta(hours=3)).conflicts(claim())


def test_other_machine_or_same_owner_not_conflicting():
    left = claim()
    assert not left.conflicts(claim(resource=ResourceKey(ResourceKind.MACHINE, "ОБ.027.3")))
    assert not left.conflicts(claim(owner=left.owner))


def test_released_claim_never_conflicts_with_active_in_either_order():
    released = claim(activated_at=NOW, released_at=NOW + timedelta(minutes=1))
    active = claim(activated_at=NOW + timedelta(minutes=2))
    assert not released.conflicts(active)
    assert not active.conflicts(released)


@pytest.mark.parametrize("key", ["", " ", "not-a-uuid", "A1234567-89AB-4CDE-8FAB-0123456789AB", "00000000-0000-0000-0000-000000000000"])
def test_person_identity_must_be_canonical_uuid(key):
    with pytest.raises(ValueError):
        ResourceKey(ResourceKind.PERSON, key)


@pytest.mark.parametrize("start,finish", [(NOW,NOW), (NOW,NOW-timedelta(seconds=1)), (NOW.replace(tzinfo=None),NOW)])
def test_invalid_windows_rejected(start, finish):
    with pytest.raises(ValueError):
        ResourceWindow(start, finish)


def test_release_without_active_claim_rejected():
    with pytest.raises(ValueError):
        claim(released_at=NOW)


def test_release_before_start_rejected():
    with pytest.raises(ValueError):
        claim(activated_at=NOW, released_at=NOW-timedelta(seconds=1))

from uuid import NAMESPACE_URL, UUID, uuid5

from app.shared.identity import Actor as IdentityActor


def Actor(*, name: str, department: str, id: UUID | None = None) -> IdentityActor:
    """Deterministic identities for tests; production IDs come from Identity."""
    return IdentityActor(
        id=id or uuid5(NAMESPACE_URL, f"erp-test:{department}:{name}"),
        name=name,
        department=department,
    )

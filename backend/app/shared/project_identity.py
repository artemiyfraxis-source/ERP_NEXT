"""Stable project references over the existing immutable rename audit trail.

The original canonical project key is the durable id. Display names follow rename
edges; no registry snapshot or command fingerprint is rewritten.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.shared.persistence import AuditEventRow
from app.shared.project_number import normalize_project_number


class ProjectIdentityConflict(ValueError):
    pass


def project_aliases(session: Session) -> tuple[dict[str, str], dict[str, str]]:
    """(forward: any old name -> current name, backward: any later name -> the original key).

    The history is replayed in time order, so a rename-back (A -> B -> A) is one identity again, not a cycle.
    Only a rename from a name the identity no longer carries, or onto a name another live identity carries,
    is a real conflict."""
    events = session.execute(select(AuditEventRow.occurred_at, AuditEventRow.id, AuditEventRow.details).where(
        AuditEventRow.event_type == "techcards.project_renamed").order_by(AuditEventRow.occurred_at, AuditEventRow.id)).all()
    renames = []
    for occurred_at, _, details in events:
        old = normalize_project_number(details.get("old_project", ""))
        new = normalize_project_number(details.get("project", ""))
        if not old or not new or old == new:
            raise ProjectIdentityConflict("Invalid project rename history")
        renames.append((occurred_at, old, new))
    # Renames stamped with the same instant (one batch) are ordered by dependency: a rename whose OLD is another
    # pending rename's NEW goes after it; a true cycle in one instant (A -> B, B -> A) keeps the stored order.
    ordered = []
    index = 0
    while index < len(renames):
        end = index
        while end < len(renames) and renames[end][0] == renames[index][0]:
            end += 1
        pending = renames[index:end]
        while pending:
            news = {new for _, _, new in pending}
            pick = next((item for item in pending if item[1] not in news), pending[0])
            pending.remove(pick)
            ordered.append(pick)
        index = end
    origin_of: dict[str, str] = {}  # every name ever seen -> the identity's original key
    current: dict[str, str] = {}    # original key -> its current name
    for _, old, new in ordered:
        origin = origin_of.get(old, old)
        if current.get(origin, origin) != old:
            raise ProjectIdentityConflict("Ambiguous project rename history")
        owner = origin_of.get(new)
        if owner is not None and owner != origin and current.get(owner, owner) == new:
            raise ProjectIdentityConflict("Ambiguous project rename history")
        origin_of[old] = origin
        origin_of[new] = origin
        current[origin] = new
    forward = {name: current[origin] for name, origin in origin_of.items() if current[origin] != name}
    backward = {name: origin for name, origin in origin_of.items() if name != origin}
    return forward, backward


def _follow(edges: dict[str, str], value: str) -> str:
    seen = set()
    while value in edges:
        if value in seen:
            raise ProjectIdentityConflict("Cyclic project rename history")
        seen.add(value)
        value = edges[value]
    return value


def stable_project_key(session: Session, value: str | None) -> str | None:
    if not value:
        return value
    _, backward = project_aliases(session)
    return _follow(backward, normalize_project_number(value))


def current_project_name(session: Session, value: str | None) -> str | None:
    if not value:
        return value
    forward, _ = project_aliases(session)
    key = normalize_project_number(value)
    return _follow(forward, key) if key in forward else value


def display_grn_context(session: Session, context: dict | None) -> dict:
    """A read model only; never persist this over registration context."""
    forward, _ = project_aliases(session)
    return {key: _follow(forward, normalize_project_number(value))
            if key in {"project", "customer_order", "production_order"} and normalize_project_number(value) in forward else value
            for key, value in (context or {}).items()}


def project_alias_keys(session: Session, value: str) -> set[str]:
    """Every old and current key of one identity, for ambiguous snapshot checks."""
    _, backward = project_aliases(session)
    key = _follow(backward, normalize_project_number(value))
    result = {key} | {name for name, origin in backward.items() if origin == key}
    return result

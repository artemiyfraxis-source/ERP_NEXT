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
    forward, backward = {}, {}
    rows = session.scalars(select(AuditEventRow.details).where(
        AuditEventRow.event_type == "techcards.project_renamed"))
    for details in rows:
        old = normalize_project_number(details.get("old_project", ""))
        new = normalize_project_number(details.get("project", ""))
        if not old or not new or old == new:
            raise ProjectIdentityConflict("Invalid project rename history")
        if (old in forward and forward[old] != new) or (new in backward and backward[new] != old):
            raise ProjectIdentityConflict("Ambiguous project rename history")
        forward[old], backward[new] = new, old
    for origin in forward:
        _follow(forward, origin)
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
    forward, backward = project_aliases(session)
    key = _follow(backward, normalize_project_number(value))
    result = {key}
    while key in forward:
        key = forward[key]
        result.add(key)
    return result

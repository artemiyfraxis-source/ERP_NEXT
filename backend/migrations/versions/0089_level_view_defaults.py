"""«Режим просмотра» (ID-6) on the level matrix: «Склад», «Лист закупки» and «Приёмка» readable by the levels the
owner's plan opens them to (docs/migration/access-levels-plan.md, owner's decision 28.09).

0086 built each level row with people from what its members could all do (the intersection), so a level whose
members differed lost these view cells even where `LEVEL_SEED` gives them. This migration puts back a «просмотр»
floor — a frozen copy of the LEVEL_SEED cells on the three sections (view, or edit taken as view):
- only into an empty cell (`ON CONFLICT DO NOTHING`): a stored cell, whatever its mode, stays;
- never into a cell an administrator has ever saved in «Доступы» (`identity.level_access_saved` lists the cell in
  its changes): a cell they cleared stays closed;
- never into superadmin/admin (always full) and never over a personal exception (`none` keeps closing it).
One `identity.level_view_defaults_seeded` event lists the cells added (none when nothing was added).

Downgrade removes exactly the cells that event lists while they are still «просмотр», then the event.
"""

import json
from datetime import datetime, timezone
from typing import Sequence
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from alembic import op
import sqlalchemy as sa


revision: str = "0089_level_view_defaults"
down_revision: str | None = "0088_api_tokens_assembly_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Frozen copy (a migration must not import application code); tests/identity/test_level_view_defaults_migration.py
# keeps it equal to the LEVEL_SEED cells on these sections.
VIEW_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("commercial", "procurement"),
    ("commercial", "warehouse"),
    ("tech_lead", "procurement"),
    ("tech_lead", "receiving"),
    ("tech_lead", "warehouse"),
    ("foreman", "procurement"),
    ("foreman", "receiving"),
    ("foreman", "warehouse"),
    ("operator", "receiving"),
    ("operator", "warehouse"),
)
SAVED_EVENT = "identity.level_access_saved"
SEEDED_EVENT = "identity.level_view_defaults_seeded"
ACCESS_AGGREGATE = uuid5(NAMESPACE_URL, "fraxis-erp:identity:access-matrix")  # access.py ACCESS_AGGREGATE

_audit = sa.table(
    "audit_events",
    sa.column("id", sa.Uuid()), sa.column("aggregate_id", sa.Uuid()), sa.column("event_type", sa.String()),
    sa.column("actor_id", sa.Uuid()), sa.column("actor_name", sa.String()), sa.column("actor_department", sa.String()),
    sa.column("occurred_at", sa.DateTime(timezone=True)), sa.column("details", sa.JSON()),
)


def _details(value) -> dict:
    if isinstance(value, str):
        value = json.loads(value or "{}")
    return value if isinstance(value, dict) else {}


def _admin_cells(connection) -> set[tuple[str, str]]:
    """(level, section) cells an administrator has ever changed in «Доступы»."""
    cells: set[tuple[str, str]] = set()
    for raw in connection.execute(sa.select(_audit.c.details).where(_audit.c.event_type == SAVED_EVENT)).scalars():
        details = _details(raw)
        changes = details.get("changes") or {}
        if isinstance(changes, str):
            changes = json.loads(changes or "{}")
        level = str(details.get("level") or "")
        cells.update((level, str(section)) for section in changes)
    return cells


def upgrade() -> None:
    connection = op.get_bind()
    touched = _admin_cells(connection)
    added: list[list[str]] = []
    for level, section in VIEW_DEFAULTS:
        if (level, section) in touched:
            continue
        result = connection.execute(sa.text("""
            INSERT INTO identity_level_access (level, section, mode)
            VALUES (:level, :section, 'view')
            ON CONFLICT (level, section) DO NOTHING
        """).bindparams(level=level, section=section))
        if result.rowcount:
            added.append([level, section])
    if added:
        connection.execute(_audit.insert(), [{
            "id": uuid4(), "aggregate_id": ACCESS_AGGREGATE, "event_type": SEEDED_EVENT, "actor_id": UUID(int=0),
            "actor_name": "Режим просмотра", "actor_department": "Система",
            "occurred_at": datetime.now(timezone.utc),
            "details": {"cells": json.dumps(added, ensure_ascii=False)},
        }])


def downgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(_audit.c.id, _audit.c.details).where(_audit.c.event_type == SEEDED_EVENT)
    ).all()
    for event_id, raw in rows:
        for level, section in json.loads(_details(raw).get("cells") or "[]"):
            connection.execute(sa.text("""
                DELETE FROM identity_level_access
                WHERE level = :level AND section = :section AND mode = 'view'
            """).bindparams(level=level, section=section))
        connection.execute(sa.delete(_audit).where(_audit.c.id == event_id))

"""Notifications address users by id (OP-1): initiator / requester / manager ids + the status mark.

Backfill: a procurement line takes its request's author (`requests.actor_id`); the remaining name
text (Инициатор, ЗАЯВИТЕЛЬ, МЕНЕДЖЕР) is matched to exactly one identity user by name (whitespace
collapsed, case-insensitive). No match or an ambiguous one stays NULL and is simply not notified;
those names are logged. `notified_status` is armed with the current status, so the deploy does not
announce every open line at once (SUPPLYMAP `seedNotified` before `notify_armed`).
"""

from collections import defaultdict
import logging
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0039_notification_recipients"
down_revision: str | None = "0038_identity_view_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_USERS = sa.table("identity_users", sa.column("id", sa.Uuid()), sa.column("name", sa.String()))
_REQUESTS = sa.table("requests", sa.column("id", sa.Uuid()), sa.column("actor_id", sa.Uuid()))
_PROCUREMENT = sa.table(
    "procurement_lines",
    sa.column("id", sa.Uuid()),
    sa.column("source_request_id", sa.Uuid()),
    sa.column("status", sa.String()),
    sa.column("initiator", sa.String()),
    sa.column("initiator_id", sa.Uuid()),
    sa.column("notified_status", sa.String()),
)
_FULFILLMENT = sa.table(
    "fulfillment_requests", sa.column("requester", sa.String()), sa.column("requester_id", sa.Uuid()),
)
_SHIPMENTS = sa.table("shipments", sa.column("manager", sa.String()), sa.column("manager_id", sa.Uuid()))


def _key(name: object) -> str:
    return " ".join(str(name or "").split()).casefold()


def _users_by_name(bind) -> dict[str, object]:
    found: dict[str, list] = defaultdict(list)
    for user_id, name in bind.execute(sa.select(_USERS.c.id, _USERS.c.name)):
        if _key(name):
            found[_key(name)].append(user_id)
    return {key: ids[0] for key, ids in found.items() if len(ids) == 1}


def _match_names(bind, table, name_column: str, id_column: str, users: dict[str, object]) -> list[str]:
    names = table.c[name_column]
    ids = table.c[id_column]
    unmatched: list[str] = []
    for (name,) in bind.execute(sa.select(names).where(ids.is_(None)).distinct()):
        user_id = users.get(_key(name))
        if user_id is None:
            unmatched.append(str(name or ""))
            continue
        bind.execute(sa.update(table).where(names == name, ids.is_(None)).values({id_column: user_id}))
    return sorted(set(unmatched))


def upgrade() -> None:
    with op.batch_alter_table("procurement_lines") as batch:
        batch.add_column(sa.Column("initiator_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("notified_status", sa.String(32), nullable=True))
        batch.create_index("ix_procurement_lines_initiator_id", ["initiator_id"])
    with op.batch_alter_table("fulfillment_requests") as batch:
        batch.add_column(sa.Column("requester_id", sa.Uuid(), nullable=True))
        batch.create_index("ix_fulfillment_requests_requester_id", ["requester_id"])
    with op.batch_alter_table("shipments") as batch:
        batch.add_column(sa.Column("manager_id", sa.Uuid(), nullable=True))
        batch.create_index("ix_shipments_manager_id", ["manager_id"])

    bind = op.get_bind()
    bind.execute(
        sa.update(_PROCUREMENT)
        .where(_PROCUREMENT.c.initiator_id.is_(None))
        .values(initiator_id=(
            sa.select(_REQUESTS.c.actor_id)
            .where(
                _REQUESTS.c.id == _PROCUREMENT.c.source_request_id,
                _REQUESTS.c.actor_id.in_(sa.select(_USERS.c.id)),
            )
            .scalar_subquery()
        ))
    )
    users = _users_by_name(bind)
    for table, name_column, id_column, label in (
        (_PROCUREMENT, "initiator", "initiator_id", "procurement_lines.initiator"),
        (_FULFILLMENT, "requester", "requester_id", "fulfillment_requests.requester"),
        (_SHIPMENTS, "manager", "manager_id", "shipments.manager"),
    ):
        unmatched = _match_names(bind, table, name_column, id_column, users)
        if unmatched:
            logger.warning("OP-1: %s without a user, not notified: %s", label, "; ".join(unmatched))
    bind.execute(sa.update(_PROCUREMENT).values(notified_status=_PROCUREMENT.c.status))


def downgrade() -> None:
    with op.batch_alter_table("shipments") as batch:
        batch.drop_index("ix_shipments_manager_id")
        batch.drop_column("manager_id")
    with op.batch_alter_table("fulfillment_requests") as batch:
        batch.drop_index("ix_fulfillment_requests_requester_id")
        batch.drop_column("requester_id")
    with op.batch_alter_table("procurement_lines") as batch:
        batch.drop_index("ix_procurement_lines_initiator_id")
        batch.drop_column("notified_status")
        batch.drop_column("initiator_id")

"""Старые непереданные заявки отменяются (владелец, 28.10.2026: «сделай так, чтобы там пусто было — там сейчас всё
старое»; «нет, и после 28 тоже»). Верхняя таблица «Не переданы в закупку» ушла, заявки в статусе «Новый» теперь видны
в нижней таблице «Снабжение → Заявки» под своим проектом; на момент выпуска там должно быть пусто (на проде 28.10 —
104 строки 663К.1.001 от 06.10, их заменила 663К.1.002 от 12.10, она в работе).
- Все заявки со статусом «Новый», какой бы ни была дата, получают статус «Отменено», версия +1; на каждую —
  событие аудита `request.cancelled` (кто: «Очистка заявок», было «Новый»).
- Печатается сверка: заявок по статусам было → стало.
DESTRUCTIVE = False. Откат возвращает «Новый» и прежнюю версию тем заявкам, у которых есть событие этой миграции и
статус всё ещё «Отменено», и удаляет события.
"""

from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID, uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0160_cancel_stale_new_requests"
down_revision: str | None = "0157_warehouse_scan_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

NEW = "Новый"
CANCELLED = "Отменено"
EVENT = "request.cancelled"
ACTOR_NAME = "Очистка заявок"

_requests = sa.table(
    "requests",
    sa.column("id", sa.Uuid()), sa.column("legacy_id", sa.String()), sa.column("status", sa.String()),
    sa.column("version", sa.Integer()), sa.column("created_at", sa.DateTime(timezone=True)),
)
_audit = sa.table(
    "audit_events",
    sa.column("id", sa.Uuid()), sa.column("aggregate_id", sa.Uuid()), sa.column("event_type", sa.String()),
    sa.column("actor_id", sa.Uuid()), sa.column("actor_name", sa.String()), sa.column("actor_department", sa.String()),
    sa.column("occurred_at", sa.DateTime(timezone=True)), sa.column("details", sa.JSON()),
)


def _by_status(connection) -> str:
    rows = connection.execute(
        sa.select(_requests.c.status, sa.func.count()).group_by(_requests.c.status).order_by(_requests.c.status)
    ).all()
    return ", ".join(f"{status} {count}" for status, count in rows) or "заявок нет"


def upgrade() -> None:
    connection = op.get_bind()
    before = _by_status(connection)
    stale = connection.execute(
        sa.select(_requests.c.id, _requests.c.legacy_id, _requests.c.version)
        .where(_requests.c.status == NEW)
    ).all()
    now = datetime.now(timezone.utc)
    for request_id, legacy_id, version in stale:
        connection.execute(
            sa.update(_requests).where(_requests.c.id == request_id, _requests.c.status == NEW)
            .values(status=CANCELLED, version=version + 1)
        )
        connection.execute(_audit.insert(), [{
            "id": uuid4(), "aggregate_id": request_id, "event_type": EVENT, "actor_id": UUID(int=0),
            "actor_name": ACTOR_NAME, "actor_department": "Система", "occurred_at": now,
            "details": {"legacy_id": legacy_id, "from": NEW, "to": CANCELLED, "version": version,
                        "reason": "непереданная заявка, очистка при выпуске 28.10.2026"},
        }])
    print(f"0160: отменено «Новых» заявок — {len(stale)}; по статусам было: {before}; стало: {_by_status(connection)}")


def downgrade() -> None:
    connection = op.get_bind()
    events = connection.execute(
        sa.select(_audit.c.id, _audit.c.aggregate_id, _audit.c.details)
        .where(_audit.c.event_type == EVENT, _audit.c.actor_name == ACTOR_NAME)
    ).all()
    for event_id, request_id, details in events:
        version = int((details or {}).get("version", 0))
        connection.execute(
            sa.update(_requests).where(_requests.c.id == request_id, _requests.c.status == CANCELLED)
            .values(status=NEW, version=version)
        )
        connection.execute(sa.delete(_audit).where(_audit.c.id == event_id))

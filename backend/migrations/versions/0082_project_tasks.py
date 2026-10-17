"""Задачи модулям по проекту (ARCH-TRACK-3, C2): арбитр ERP ставит их по событиям модулей.

Одна строка — одна задача разделу (`module` — ключ доступа: production, shipping, …) по проекту и КБ. Задача рождается
из события журнала (`source_event_id` = id строки `audit_events`, `source_type` — его тип); уникальность
(kind, source_event_id) — идемпотентность: повтор того же события вторую задачу не создаёт. `subject_id` — то, о чём
задача (техкарта, комплект, позиция ГП): по нему арбитр видит, что дело сделано, и закрывает задачу сам.
Статус: open → done (закрыта руками или арбитром) | cancelled. Таблица новая и пустая — переносить нечего.

История не превращается в задачи (решено агентом 04.10 — проверить): события-триггеры, записанные до этой миграции,
ставятся в outbox арбитра сразу разобранными (`published_at` = время события), поэтому задачи идут только по новым
событиям, а не сотнями по старым и уже удалённым проектам."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0082_project_tasks"
down_revision: str | None = "0081_production_operation_inputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("item_index", sa.String(64), nullable=False, server_default=""),
        sa.Column("module", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(128), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(255), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "source_event_id", name="ux_project_tasks_kind_source_event"),
        sa.CheckConstraint("status IN ('open', 'done', 'cancelled')", name="ck_project_tasks_status"),
    )
    op.create_index("ix_project_tasks_project", "project_tasks", ["project"])
    op.create_index("ix_project_tasks_module_status", "project_tasks", ["module", "status"])
    op.create_index("ix_project_tasks_status_kind", "project_tasks", ["status", "kind"])
    _mark_history_handled()


_TRIGGERS = (
    "techcards.project_issued", "fulfillment.issued", "shipping.good_accepted",
    "production.box_handed_to_finished_goods",
)


def _mark_history_handled() -> None:
    bind = op.get_bind()
    audit = sa.table("audit_events", sa.column("id", sa.Uuid()), sa.column("event_type", sa.String()),
                     sa.column("occurred_at", sa.DateTime(timezone=True)))
    outbox = sa.table(
        "outbox_messages", sa.column("id", sa.Uuid()), sa.column("topic", sa.String()),
        sa.column("idempotency_key", sa.String()), sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("payload", sa.JSON()), sa.column("published_at", sa.DateTime(timezone=True)),
        sa.column("attempt_count", sa.Integer()),
    )
    queued = {row[0] for row in bind.execute(sa.select(outbox.c.id))}
    rows = [{
        "id": event_id, "topic": "arbiter.trigger", "idempotency_key": f"arbiter.trigger:{event_id}",
        "created_at": occurred_at, "payload": {"history": True}, "published_at": occurred_at, "attempt_count": 0,
    } for event_id, occurred_at in bind.execute(
        sa.select(audit.c.id, audit.c.occurred_at).where(audit.c.event_type.in_(_TRIGGERS))
    ) if event_id not in queued]
    if rows:
        op.bulk_insert(outbox, rows)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM outbox_messages WHERE topic = 'arbiter.trigger'"))
    op.drop_index("ix_project_tasks_status_kind", table_name="project_tasks")
    op.drop_index("ix_project_tasks_module_status", table_name="project_tasks")
    op.drop_index("ix_project_tasks_project", table_name="project_tasks")
    op.drop_table("project_tasks")

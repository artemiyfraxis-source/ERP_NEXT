"""Create Requests, audit, and outbox tables."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_requests"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "request_designations",
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("creation_key", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=128), nullable=False),
        sa.Column("designation", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("actor_id", "creation_key", "purpose"),
        sa.UniqueConstraint("designation"),
    )
    op.create_table(
        "requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legacy_id", sa.String(length=32), nullable=False),
        sa.Column("creation_key", sa.String(length=255), nullable=False),
        sa.Column("creation_index", sa.Integer(), nullable=False),
        sa.Column("creation_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("designation", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("actor_name", sa.String(length=255), nullable=False),
        sa.Column("actor_department", sa.String(length=255), nullable=False),
        sa.Column("procurement_actor_name", sa.String(length=255), nullable=True),
        sa.Column("procurement_actor_department", sa.String(length=255), nullable=True),
        sa.Column("procurement_actor_id", sa.Uuid(), nullable=True),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.String(length=64), nullable=False),
        sa.Column("line_sum", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("purpose", sa.String(length=128), nullable=False),
        sa.Column("project_reference", sa.String(length=128), nullable=True),
        sa.Column("delivery_point", sa.String(length=512), nullable=False),
        sa.Column("name", sa.String(length=1024), nullable=False),
        sa.Column("item_type", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("design_article", sa.String(length=512), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("quality_document", sa.String(length=8), nullable=False),
        sa.Column("procurement_deadline", sa.Date(), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=False),
        sa.Column("price", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("price_with_vat", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_id",
            "creation_key",
            "creation_index",
            name="uq_requests_actor_creation_item",
        ),
        sa.UniqueConstraint("legacy_id"),
    )
    op.create_index("ix_requests_creation_key", "requests", ["creation_key"])
    op.create_index("ix_requests_actor_id", "requests", ["actor_id"])
    op.create_index("ix_requests_designation", "requests", ["designation"])
    op.create_index("ix_requests_project_reference", "requests", ["project_reference"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("actor_name", sa.String(length=255), nullable=False),
        sa.Column("actor_department", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_aggregate_id", "audit_events", ["aggregate_id"])
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])

    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_outbox_messages_topic", "outbox_messages", ["topic"])


def downgrade() -> None:
    op.drop_index("ix_outbox_messages_topic", table_name="outbox_messages")
    op.drop_table("outbox_messages")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_id", table_name="audit_events")
    op.drop_index("ix_audit_events_aggregate_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_requests_project_reference", table_name="requests")
    op.drop_index("ix_requests_designation", table_name="requests")
    op.drop_index("ix_requests_creation_key", table_name="requests")
    op.drop_index("ix_requests_actor_id", table_name="requests")
    op.drop_table("requests")
    op.drop_table("request_designations")

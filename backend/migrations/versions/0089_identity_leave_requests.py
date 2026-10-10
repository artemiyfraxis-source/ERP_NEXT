"""Заявления на отпуск и отгул — «Личная карточка», фаза 2 (docs/migration/cabinet-plan.md, T4/T6).

Schema only, nothing existing changes its rows (the production database is live):
- `identity_leave_requests` — the employee's request: kind (vacation · day_off), the period, the employee's comment,
  the status of the route Сотрудник → Руководитель → Кадры (draft · signed · approved · registered · rejected ·
  withdrawn), the content version and its SHA-256 (an edit raises the version), and `version` — the row version every
  change checks (optimistic lock), so two approvers cannot both act on one state;
- `identity_leave_signatures` — who signed which step of which content version, when and how (PIN or password typed
  again; the secret itself is never stored), the decision and the comment. One signature per step and content version;
- `production_absences.leave_request_id` — a calendar absence written from a registered request (NULL — the master's
  own row). Unique: one absence per request; removed with the request.

Downgrade drops the column and both tables with their rows.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0089_identity_leave_requests"
down_revision: str | None = "0088_api_tokens_assembly_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "identity_leave_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["identity_users.id"], name="fk_identity_leave_requests_employee_id", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("kind IN ('vacation', 'day_off')", name="ck_identity_leave_requests_kind"),
        sa.CheckConstraint(
            "status IN ('draft', 'signed', 'approved', 'registered', 'rejected', 'withdrawn')",
            name="ck_identity_leave_requests_status",
        ),
        sa.CheckConstraint("to_date >= from_date", name="ck_identity_leave_requests_period"),
        sa.CheckConstraint("content_version >= 1", name="ck_identity_leave_requests_content_version"),
        sa.CheckConstraint("version >= 1", name="ck_identity_leave_requests_version"),
    )
    op.create_index("ix_identity_leave_requests_employee_id", "identity_leave_requests", ["employee_id"])
    op.create_index("ix_identity_leave_requests_status", "identity_leave_requests", ["status"])

    op.create_table(
        "identity_leave_signatures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("step", sa.String(16), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("signer_id", sa.Uuid(), nullable=False),
        sa.Column("signer_name", sa.String(255), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(
            ["request_id"], ["identity_leave_requests.id"], name="fk_identity_leave_signatures_request_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signer_id"], ["identity_users.id"], name="fk_identity_leave_signatures_signer_id", ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "step IN ('employee', 'manager', 'hr', 'withdraw')", name="ck_identity_leave_signatures_step",
        ),
        sa.CheckConstraint(
            "decision IN ('signed', 'approved', 'rejected', 'registered', 'withdrawn')",
            name="ck_identity_leave_signatures_decision",
        ),
        sa.CheckConstraint("method IN ('pin', 'password')", name="ck_identity_leave_signatures_method"),
        sa.CheckConstraint("content_version >= 1", name="ck_identity_leave_signatures_content_version"),
        sa.UniqueConstraint(
            "request_id", "step", "content_version", name="uq_identity_leave_signatures_request_step_version",
        ),
    )
    op.create_index("ix_identity_leave_signatures_signer_id", "identity_leave_signatures", ["signer_id"])

    with op.batch_alter_table("production_absences") as batch:
        batch.add_column(sa.Column("leave_request_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_production_absences_leave_request_id", "identity_leave_requests", ["leave_request_id"], ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ux_production_absences_leave_request_id", ["leave_request_id"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("production_absences") as batch:
        batch.drop_index("ux_production_absences_leave_request_id")
        batch.drop_constraint("fk_production_absences_leave_request_id", type_="foreignkey")
        batch.drop_column("leave_request_id")
    op.drop_index("ix_identity_leave_signatures_signer_id", table_name="identity_leave_signatures")
    op.drop_table("identity_leave_signatures")
    op.drop_index("ix_identity_leave_requests_status", table_name="identity_leave_requests")
    op.drop_index("ix_identity_leave_requests_employee_id", table_name="identity_leave_requests")
    op.drop_table("identity_leave_requests")

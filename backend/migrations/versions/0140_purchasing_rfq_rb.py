"""Запросы КП как в Roller_Buyer (стенд Вити, 16.10.2026).

Branch vitya/hybrid only. Adds what «Запросы КП» of Roller_Buyer keeps and the ERP did not:
- КП: its number, the file it came in (the uploaded PDF, stored like every ERP document file), the Kaiten invoice it
  was made from (source «invoice» only with that invoice);
- a request line that left the request: why (`new` — back to the free lines, `requote` — «Перезапрос»), who took it
  out and the note of who answered what (Roller rfq-withdraw.ts). Lines closed with the request keep NULL — read as
  «Перезапрос», as before;
- a decision's reasons as Roller's fixed lists (codes) next to the text the buyer reads;
- a rotation suggestion the buyer unticked when creating the request (Roller declinedSuggestion), for honest rotation.

Nothing existing is rewritten. Downgrade drops the new columns and table (the codes, notes and file links with them)
and deletes the КП made from Kaiten invoices: the old check does not allow them.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0140_purchasing_rfq_rb"
down_revision: str | None = "0139_purchasing_order_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

QUOTES = "purchasing_quotes"
LINES = "purchasing_rfq_lines"
DECISIONS = "purchasing_decisions"
DECLINED = "purchasing_rfq_declined_suggestions"

GATE_CODES = "('urgent', 'single_source', 'repeat_fresh_quote', 'other')"
PRICIER_CODES = "('lead_time', 'availability', 'moq', 'quality', 'other')"

OLD_SOURCE = "source IN ('manual', 'paste', 'file')"
NEW_CHECKS = {
    QUOTES: {
        "ck_purchasing_quotes_source": "source IN ('manual', 'paste', 'file', 'invoice')",
        "ck_purchasing_quotes_invoice": "source <> 'invoice' OR invoice_id IS NOT NULL",
    },
    LINES: {
        "ck_purchasing_rfq_lines_removed_reason": "removed_reason IS NULL OR removed_reason IN ('new', 'requote')",
        "ck_purchasing_rfq_lines_removed": "removed_reason IS NULL OR removed_at IS NOT NULL",
    },
    DECISIONS: {
        "ck_purchasing_decisions_gate_code": f"gate_override_code IS NULL OR gate_override_code IN {GATE_CODES}",
        "ck_purchasing_decisions_pricier_code": f"not_cheapest_code IS NULL OR not_cheapest_code IN {PRICIER_CODES}",
        "ck_purchasing_decisions_gate_text": "gate_override_code IS NULL OR gate_override_reason IS NOT NULL",
        "ck_purchasing_decisions_pricier_text": "not_cheapest_code IS NULL OR not_cheapest_reason IS NOT NULL",
    },
}


# Named: SQLite's table rebuild refuses an unnamed constraint.
FOREIGN_KEYS = (
    ("fk_purchasing_quotes_file", "purchasing_document_files", "file_id"),
    ("fk_purchasing_quotes_invoice", "purchasing_invoices", "invoice_id"),
)


def _columns() -> dict[str, list[sa.Column]]:
    return {
        QUOTES: [
            sa.Column("number", sa.String(64), nullable=True),
            sa.Column("file_id", sa.Uuid(), nullable=True),
            sa.Column("invoice_id", sa.Uuid(), nullable=True),
        ],
        LINES: [
            sa.Column("removed_reason", sa.String(16), nullable=True),
            sa.Column("removed_note", sa.Text(), nullable=True),
            sa.Column("removed_by", sa.Uuid(), nullable=True),
        ],
        DECISIONS: [
            sa.Column("gate_override_code", sa.String(32), nullable=True),
            sa.Column("not_cheapest_code", sa.String(32), nullable=True),
        ],
    }


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table, columns in _columns().items():
            for column in columns:
                op.add_column(table, column)
        for name, target, column in FOREIGN_KEYS:
            op.create_foreign_key(name, QUOTES, target, [column], ["id"], ondelete="SET NULL")
        op.execute(sa.text(f"ALTER TABLE {QUOTES} DROP CONSTRAINT IF EXISTS ck_purchasing_quotes_source"))
        for table, checks in NEW_CHECKS.items():
            for name, sql in checks.items():
                op.execute(sa.text(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({sql})"))
    else:
        # SQLite has no ALTER … ADD CONSTRAINT: the tables are rebuilt with their rows (foreign keys are not enforced
        # on the stand's SQLite, so children of a rebuilt table keep their rows).
        for table, columns in _columns().items():
            with op.batch_alter_table(table, recreate="always") as batch:
                for column in columns:
                    batch.add_column(column)
                if table == QUOTES:
                    batch.drop_constraint("ck_purchasing_quotes_source", type_="check")
                    for name, target, column in FOREIGN_KEYS:
                        batch.create_foreign_key(name, target, [column], ["id"], ondelete="SET NULL")
                for name, sql in NEW_CHECKS[table].items():
                    batch.create_check_constraint(name, sql)
    op.create_index("ix_purchasing_quotes_invoice", QUOTES, ["invoice_id"])
    op.create_table(
        DECLINED,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rfq_id", sa.Uuid(), sa.ForeignKey("purchasing_rfqs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), sa.ForeignKey("purchasing_suppliers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.UniqueConstraint("rfq_id", "supplier_id", name="uq_purchasing_rfq_declined_suggestions"),
    )


def downgrade() -> None:
    op.drop_table(DECLINED)
    op.drop_index("ix_purchasing_quotes_invoice", table_name=QUOTES)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table, checks in NEW_CHECKS.items():
            for name in checks:
                op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))
        for name, _, _ in FOREIGN_KEYS:
            op.execute(sa.text(f"ALTER TABLE {QUOTES} DROP CONSTRAINT IF EXISTS {name}"))
        op.execute(sa.text(f"DELETE FROM {QUOTES} WHERE source = 'invoice'"))
        op.execute(sa.text(f"ALTER TABLE {QUOTES} ADD CONSTRAINT ck_purchasing_quotes_source CHECK ({OLD_SOURCE})"))
        for table, columns in _columns().items():
            for column in columns:
                op.drop_column(table, column.name)
        return
    op.execute(sa.text(f"DELETE FROM {QUOTES} WHERE source = 'invoice'"))
    for table, columns in _columns().items():
        with op.batch_alter_table(table, recreate="always") as batch:
            for name in NEW_CHECKS[table]:
                batch.drop_constraint(name, type_="check")
            if table == QUOTES:
                batch.create_check_constraint("ck_purchasing_quotes_source", OLD_SOURCE)
                for name, _, _ in FOREIGN_KEYS:
                    batch.drop_constraint(name, type_="foreignkey")
            for column in columns:
                batch.drop_column(column.name)

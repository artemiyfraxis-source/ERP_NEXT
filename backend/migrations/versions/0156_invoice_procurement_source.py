"""«Счета»: второй источник счёта — загрузка в закупке ERP (Витя, 26.10.2026: «нужен полноценный разборщик всех данных,
как у нас в roller_buyer»).

Every invoice file a buyer uploads to a procurement order («Файл счёта», «Вставить счёт») lands in the purchasing
registry `purchasing_invoices` as source `procurement` (external_id = procurement's stored file uuid, header and positions
read by the shared parser, the PDF copied into `purchasing_document_files`), so the document chain matches it with its
UPDs like a Kaiten card. The CHECK `ck_purchasing_invoices_source` is widened from ('kaiten') to ('kaiten', 'procurement').

27.10.2026 (owner's session, Витя's open questions): the registry invoice follows the procurement lines that carry its
file — paid with them, «cancelled» when all of them are cancelled, «replaced» when a newer invoice took the file's place.
- `ck_purchasing_invoices_status` gains 'cancelled' and 'replaced'; the new CHECK `ck_purchasing_invoices_void_source`
  lets only a `procurement` invoice take them (a Kaiten card keeps Kaiten's status).
- `purchasing_invoice_uploads` — one row per uploaded procurement file (UNIQUE `file_key` = procurement_files.id, no FK:
  another module's table) → the registry invoice it stands for (FK, ON DELETE CASCADE): the row it created or the
  invoice of the same seller, № and date (± 3 days) it was attached to, a Kaiten card included — one invoice, one row.

Nothing is rewritten or deleted on upgrade. DESTRUCTIVE = False. SQLite rebuilds the table through `batch_alter_table`;
PostgreSQL drops and recreates the CHECK.

Downgrade: an uploaded invoice can't stay under the narrow CHECK, so the `procurement` invoices are removed with their
file rows and the buyer's invoice ↔ UPD decisions on them, and quotes pointing at them lose the link (the stored PDFs stay
in `purchasing_document_files`, procurement keeps its own copy of every file). `purchasing_invoice_uploads` is dropped
(the files' links to Kaiten cards go with it); a Kaiten card the procurement payment turned «paid» stays «paid».
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0156_invoice_procurement_source"
down_revision: str | None = "0155_ter_lug_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

_TABLE = "purchasing_invoices"
_NAME = "ck_purchasing_invoices_source"
_OLD = "source IN ('kaiten')"
_NEW = "source IN ('kaiten', 'procurement')"
_STATUS = "ck_purchasing_invoices_status"
_STATUS_OLD = "status IN ('to_pay', 'paid', 'return', 'other')"
_STATUS_NEW = "status IN ('to_pay', 'paid', 'return', 'other', 'cancelled', 'replaced')"
_VOID = "ck_purchasing_invoices_void_source"
_VOID_RULE = "source = 'procurement' OR status NOT IN ('cancelled', 'replaced')"
_UPLOADS = "purchasing_invoice_uploads"
_UPLOADED = "SELECT id FROM purchasing_invoices WHERE source = 'procurement'"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_NAME, type_="check")
        batch.create_check_constraint(_NAME, _NEW)
        batch.drop_constraint(_STATUS, type_="check")
        batch.create_check_constraint(_STATUS, _STATUS_NEW)
        batch.create_check_constraint(_VOID, _VOID_RULE)
    op.create_table(
        _UPLOADS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "invoice_id", sa.Uuid(),
            sa.ForeignKey(f"{_TABLE}.id", ondelete="CASCADE", name="fk_purchasing_invoice_uploads_invoice_id"),
            nullable=False,
        ),
        sa.Column("file_key", sa.String(64), nullable=False),
        sa.Column("line_id", sa.Uuid(), nullable=True),
        sa.Column("order_number", sa.String(32), nullable=False, server_default=""),
        sa.Column("file_name", sa.String(1024), nullable=False, server_default=""),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_row", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("file_key", name="uq_purchasing_invoice_uploads_file"),
        sa.CheckConstraint("length(file_key) > 0", name="ck_purchasing_invoice_uploads_file"),
    )
    op.create_index("ix_purchasing_invoice_uploads_invoice", _UPLOADS, ["invoice_id"])
    op.create_index("ix_purchasing_invoice_uploads_order", _UPLOADS, ["order_number"])


def downgrade() -> None:
    op.drop_index("ix_purchasing_invoice_uploads_order", table_name=_UPLOADS)
    op.drop_index("ix_purchasing_invoice_uploads_invoice", table_name=_UPLOADS)
    op.drop_table(_UPLOADS)
    op.execute(f"UPDATE purchasing_quotes SET invoice_id = NULL WHERE invoice_id IN ({_UPLOADED})")
    op.execute(f"DELETE FROM purchasing_upd_invoice_links WHERE invoice_id IN ({_UPLOADED})")
    op.execute(f"DELETE FROM purchasing_invoice_files WHERE invoice_id IN ({_UPLOADED})")
    op.execute("DELETE FROM purchasing_invoices WHERE source = 'procurement'")
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_VOID, type_="check")
        batch.drop_constraint(_STATUS, type_="check")
        batch.create_check_constraint(_STATUS, _STATUS_OLD)
        batch.drop_constraint(_NAME, type_="check")
        batch.create_check_constraint(_NAME, _OLD)

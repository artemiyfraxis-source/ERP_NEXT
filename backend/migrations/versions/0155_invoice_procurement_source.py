"""«Счета»: второй источник счёта — загрузка в закупке ERP (Витя, 26.10.2026: «нужен полноценный разборщик всех данных,
как у нас в roller_buyer»).

Every invoice file a buyer uploads to a procurement order («Файл счёта», «Вставить счёт») lands in the purchasing
registry `purchasing_invoices` as source `procurement` (external_id = procurement's stored file uuid, header and positions
read by the shared parser, the PDF copied into `purchasing_document_files`), so the document chain matches it with its
UPDs like a Kaiten card. The CHECK `ck_purchasing_invoices_source` is widened from ('kaiten') to ('kaiten', 'procurement').

Nothing is rewritten or deleted on upgrade. DESTRUCTIVE = False. SQLite rebuilds the table through `batch_alter_table`;
PostgreSQL drops and recreates the CHECK.

Downgrade: an uploaded invoice can't stay under the narrow CHECK, so the `procurement` invoices are removed with their
file rows and the buyer's invoice ↔ UPD decisions on them, and quotes pointing at them lose the link (the stored PDFs stay
in `purchasing_document_files`, procurement keeps its own copy of every file).
"""

from typing import Sequence

from alembic import op


revision: str = "0155_invoice_procurement_source"
down_revision: str | None = "0154_user_ui_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

_TABLE = "purchasing_invoices"
_NAME = "ck_purchasing_invoices_source"
_OLD = "source IN ('kaiten')"
_NEW = "source IN ('kaiten', 'procurement')"
_UPLOADED = "SELECT id FROM purchasing_invoices WHERE source = 'procurement'"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_NAME, type_="check")
        batch.create_check_constraint(_NAME, _NEW)


def downgrade() -> None:
    op.execute(f"UPDATE purchasing_quotes SET invoice_id = NULL WHERE invoice_id IN ({_UPLOADED})")
    op.execute(f"DELETE FROM purchasing_upd_invoice_links WHERE invoice_id IN ({_UPLOADED})")
    op.execute(f"DELETE FROM purchasing_invoice_files WHERE invoice_id IN ({_UPLOADED})")
    op.execute("DELETE FROM purchasing_invoices WHERE source = 'procurement'")
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_NAME, type_="check")
        batch.create_check_constraint(_NAME, _OLD)

"""«Реестр УПД»: третий источник документа — «Скан склада» (owner 26.10.2026, со слов Вити: «сваливать всё в реестр,
что будем принимать»).

Every УПД the warehouse takes in through «Поставка» lands in the purchasing registry: an ЭДО file as an ordinary
`edo` document, a paper scan as a document of the new origin `scan` (id «wh-<file uuid>», the PDF copied into
`purchasing_document_files`, header as the storekeeper saved it, lines from the model's reading). The CHECK
`ck_purchasing_upd_documents_origin` is widened from ('google', 'edo') to the same set plus 'scan'.

Nothing is rewritten or deleted on upgrade. DESTRUCTIVE = False. SQLite rebuilds the table through
`batch_alter_table`; PostgreSQL drops and recreates the CHECK.

Downgrade: a scan document can't stay under the narrow CHECK and has no other origin it could pass for, so the scan
documents are removed (their lines go by ON DELETE CASCADE; the stored PDFs stay in `purchasing_document_files`, and
the warehouse keeps its own copy of every scan) — a re-run of the backfill after an upgrade brings them back.
"""

from typing import Sequence

from alembic import op


revision: str = "0153_upd_scan_origin"
# After 0152 (the parallel «Поставка» migration: lot names by УПД, ПРД of the delivery).
down_revision: str | None = "0152_lot_document_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

_TABLE = "purchasing_upd_documents"
_NAME = "ck_purchasing_upd_documents_origin"
_OLD = "origin IN ('google', 'edo')"
_NEW = "origin IN ('google', 'edo', 'scan')"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_NAME, type_="check")
        batch.create_check_constraint(_NAME, _NEW)


def downgrade() -> None:
    op.execute("DELETE FROM purchasing_upd_lines WHERE document_id IN "
               "(SELECT id FROM purchasing_upd_documents WHERE origin = 'scan')")
    op.execute("DELETE FROM purchasing_upd_documents WHERE origin = 'scan'")
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_NAME, type_="check")
        batch.create_check_constraint(_NAME, _OLD)

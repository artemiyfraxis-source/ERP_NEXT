"""Закупка: платёжное поручение (ПП) как файл строки закупки (owner 23.10.2026, «Закупка» → «Внести ПП»).

`procurement_files.kind` gets one more value, `payment` — the payment order the buyer attaches when he marks the
supplier order paid. ПП is not parsed; it only stays with the order's line next to the invoice. The CHECK
`ck_procurement_files_kind` is widened from ('other', 'permit', 'quality') to the same set plus 'payment'.

Nothing is rewritten or deleted on upgrade: every stored file keeps its kind. DESTRUCTIVE = False. SQLite rebuilds the
table through `batch_alter_table` (no ALTER CONSTRAINT there); PostgreSQL drops and recreates the CHECK.

Downgrade: a ПП file can't stay under the narrow CHECK, so it becomes `other` (the file itself is kept — no data is
removed), then the old CHECK comes back.
"""

from typing import Sequence

from alembic import op


revision: str = "0147_procurement_payment_files"
down_revision: str | None = "0146_split_article_maker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

_NAME = "ck_procurement_files_kind"
_OLD = "kind IN ('other', 'permit', 'quality')"
_NEW = "kind IN ('other', 'permit', 'quality', 'payment')"


def upgrade() -> None:
    with op.batch_alter_table("procurement_files") as batch:
        batch.drop_constraint(_NAME, type_="check")
        batch.create_check_constraint(_NAME, _NEW)


def downgrade() -> None:
    op.execute("UPDATE procurement_files SET kind = 'other' WHERE kind = 'payment'")
    with op.batch_alter_table("procurement_files") as batch:
        batch.drop_constraint(_NAME, type_="check")
        batch.create_check_constraint(_NAME, _OLD)

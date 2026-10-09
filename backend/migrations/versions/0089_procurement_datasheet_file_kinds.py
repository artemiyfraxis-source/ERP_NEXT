"""Лист закупки: DATASHEET строки и вид файла закупки (разрешение, документ качества, прочее).

`procurement_lines.datasheet` — колонка DATASHEET листа: ссылка или текст, правит закупщик строки.
`procurement_files.kind` — что за файл лежит у строки: `permit` (РАЗРЕШЕНИЕ), `quality` (ДОК. КАЧЕСТВА) или `other`
(всё прочее: счета, КП, документы заказа). Уже лежащие файлы — это счета и КП, они получают `other` по умолчанию,
поэтому миграция ничего не стирает и не переписывает; CHECK не даёт записать иной вид."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0089_procurement_datasheet_file_kinds"
down_revision: str | None = "0088_api_tokens_assembly_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("procurement_lines") as batch:
        batch.add_column(sa.Column("datasheet", sa.Text(), nullable=True))
    with op.batch_alter_table("procurement_files") as batch:
        batch.add_column(sa.Column("kind", sa.String(16), nullable=False, server_default="other"))
        batch.create_check_constraint("ck_procurement_files_kind", "kind IN ('other', 'permit', 'quality')")


def downgrade() -> None:
    with op.batch_alter_table("procurement_files") as batch:
        batch.drop_constraint("ck_procurement_files_kind", type_="check")
        batch.drop_column("kind")
    with op.batch_alter_table("procurement_lines") as batch:
        batch.drop_column("datasheet")

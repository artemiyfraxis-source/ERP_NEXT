"""Входы и правки ЭТК для автоматической ревизии (владелец, 19.10.2026).

Карточка ЭТК не должна ждать ручного «Пересобрать» после правки справочника или правила: при влиянии на выданную
карту система сама выпустит новую ревизию (каждая ревизия остаётся замороженным полным документом), перенеся правки
технолога. Для этого карта хранит, из чего собрана и что поправлено руками:

- `techcard_projects.inputs` — JSON: конфиг генератора как пришёл, до дополнения нормами справочников;
- `techcard_projects.overrides` — JSON: правки технолога `{ключ операции: {operation, base_template_version}}` и
  `{route: {blocks: [[ключи операций]]}}`;
- `techcard_projects.review_note` — пометка «нужна проверка» (текст), `derived_hash` — хэш входов последней сборки;
- `techcard_revisions.source` — manual | auto (CHECK), по умолчанию manual; `diff` — JSON, что изменилось;
- `techcard_refresh_requests` — очередь запросов на пересборку (причина, нагрузка, обработано ли).

Только добавление: существующие строки не меняются (inputs/overrides пусты, ревизии получают source = manual).
DESTRUCTIVE = False. Откат снимает добавленное.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0127_etk_inputs_overrides"
down_revision: str | None = "0126_operation_card_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False


def _count(table: str) -> int:
    return int(op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)


def upgrade() -> None:
    cards, revisions = _count("techcard_projects"), _count("techcard_revisions")
    with op.batch_alter_table("techcard_projects") as batch:
        batch.add_column(sa.Column("inputs", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("overrides", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("review_note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("derived_hash", sa.String(64), nullable=True))
    with op.batch_alter_table("techcard_revisions") as batch:
        batch.add_column(sa.Column("source", sa.String(16), nullable=False, server_default="manual"))
        batch.add_column(sa.Column("diff", sa.JSON(), nullable=True))
        batch.create_check_constraint("ck_techcard_revisions_source", "source IN ('manual', 'auto')")
    op.create_table(
        "techcard_refresh_requests",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_techcard_refresh_requests_processed", "techcard_refresh_requests", ["processed_at"])
    print(
        f"0127_etk_inputs_overrides: techcard_projects {cards} → {_count('techcard_projects')}, "
        f"techcard_revisions {revisions} → {_count('techcard_revisions')}, techcard_refresh_requests 0 → "
        f"{_count('techcard_refresh_requests')}"
    )


def downgrade() -> None:
    op.drop_index("ix_techcard_refresh_requests_processed", table_name="techcard_refresh_requests")
    op.drop_table("techcard_refresh_requests")
    with op.batch_alter_table("techcard_revisions") as batch:
        batch.drop_constraint("ck_techcard_revisions_source", type_="check")
        batch.drop_column("diff")
        batch.drop_column("source")
    with op.batch_alter_table("techcard_projects") as batch:
        for column in ("derived_hash", "review_note", "overrides", "inputs"):
            batch.drop_column(column)

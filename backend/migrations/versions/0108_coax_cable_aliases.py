"""Коаксиальные кабели: алиасы и полное название (владелец 12.10.2026).

Кабель находят по короткой марке, по полному названию поставщика или по артикулу поставщика:
- `coax_cables.full_name` — полное название (по первому предложению поставщика с этим самым кабелем), '' — нет;
- `coax_cable_aliases` — другие имена карточки: марка и её написания, полные названия, артикулы (с поставщиком и
  ссылкой). Ключ имени (`alias_key`, ключ генератора: без регистра, пробелов и дефисов) один на все кабели —
  UNIQUE; повтор отклоняет база. FK на карточку с CASCADE: карточка ушла из файла — ушли и её имена.

Обе пусты здесь: `python -m app.modules.references.load_cables` заполняет их из `backend/data/cables/coax-articles.json`
(идемпотентно, его запускает `deploy/update.sh`). Ни одна существующая строка не меняется.

DESTRUCTIVE = False. Downgrade удаляет новую таблицу и столбец (их заполняет загрузчик заново).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0108_coax_cable_aliases"
down_revision: str | None = "0107_stage_ladder"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

KINDS = ("mark", "full_name", "supplier_article")  # literal copy of cable_structure.ALIAS_KINDS


def upgrade() -> None:
    with op.batch_alter_table("coax_cables") as batch:
        batch.add_column(sa.Column("full_name", sa.String(500), nullable=False, server_default=""))
    op.create_table(
        "coax_cable_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cable_id", sa.Uuid(), sa.ForeignKey("coax_cables.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(500), nullable=False),
        sa.Column("alias_key", sa.String(500), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("supplier", sa.String(255), nullable=False, server_default=""),
        sa.Column("url", sa.String(1000), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("alias_key", name="ux_coax_cable_aliases_key"),
        sa.CheckConstraint("alias <> ''", name="ck_coax_cable_aliases_alias_not_blank"),
        sa.CheckConstraint("alias_key <> ''", name="ck_coax_cable_aliases_key_not_blank"),
        sa.CheckConstraint("kind IN (" + ", ".join(f"'{kind}'" for kind in KINDS) + ")", name="ck_coax_cable_aliases_kind"),
        sa.CheckConstraint("kind <> 'supplier_article' OR supplier <> ''", name="ck_coax_cable_aliases_article_supplier"),
    )
    op.create_index("ix_coax_cable_aliases_cable", "coax_cable_aliases", ["cable_id"])


def downgrade() -> None:
    op.drop_index("ix_coax_cable_aliases_cable", table_name="coax_cable_aliases")
    op.drop_table("coax_cable_aliases")
    with op.batch_alter_table("coax_cables") as batch:
        batch.drop_column("full_name")

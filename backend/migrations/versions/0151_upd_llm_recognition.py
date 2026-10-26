"""Распознавание УПД моделью через API (owner 26.10.2026: «давай сразу апи»).

- `warehouse_upd_llm_settings` — one row per installation (CHECK `id = 1`): the address of the OpenAI-compatible API,
  the model, the fallback model, the reasoning effort and the key, saved by the administrator on «Склад» →
  «Распознавание УПД» (the server's .env is root-only). The server env `ERP_UPD_LLM_*` wins field by field. The key is
  write-only for the API (answered masked). CHECKs: effort '' (default) or one of minimal / low / medium / high;
  the address '' or http(s)://.
- `warehouse_receipt_files.recognition` — what the file was read as (JSON text, '' — not read yet): a УПД scanned
  sheet by sheet is merged from the readings of its sheets, never read again (a model call is 8–100 s).

Non-destructive: a new empty table and an empty column. DESTRUCTIVE = False. Downgrade drops both (the saved
settings and the kept readings go; the files stay).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0151_upd_llm_recognition"
down_revision: str | None = "0150_prov_lan_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False
TABLE = "warehouse_upd_llm_settings"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("base_url", sa.String(512), nullable=False, server_default=""),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("fallback_model", sa.String(128), nullable=False, server_default=""),
        sa.Column("effort", sa.String(16), nullable=False, server_default=""),
        sa.Column("api_key", sa.String(512), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default=""),
        sa.CheckConstraint("id = 1", name="ck_warehouse_upd_llm_settings_single_row"),
        sa.CheckConstraint(
            "effort IN ('', 'minimal', 'low', 'medium', 'high')", name="ck_warehouse_upd_llm_settings_effort",
        ),
        sa.CheckConstraint(
            "base_url = '' OR base_url LIKE 'http://%' OR base_url LIKE 'https://%'",
            name="ck_warehouse_upd_llm_settings_base_url",
        ),
    )
    with op.batch_alter_table("warehouse_receipt_files") as batch:
        batch.add_column(sa.Column("recognition", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("warehouse_receipt_files") as batch:
        batch.drop_column("recognition")
    op.drop_table(TABLE)

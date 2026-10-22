"""Замеры КСВН по изделию — таблица `production_vswr_measurements` (ОТК коаксиальных сборок, 22.10.2026).

До сих пор ОТК хранил только число годных и брака (production_qc_inspections); значения замеров нигде не лежали.
Теперь каждый замер на ВАЦ — строка: серийный номер изделия (production_serials) и номер попытки (действует
последняя), СИ (production_instruments), полоса, наибольший КСВН в полосе и его частота, снимок нормы ЭТК, вердикт,
контролёр, время, файл выгрузки прибора (до 2 МБ), параметры развёртки из его шапки (старт, стоп, точки, полоса ПЧ —
для паспорта) и трасса до 500 точек.

Правила в базе:
- попытка ≥ 1 и одна на номер попытки изделия — UNIQUE(serial_id, attempt);
- полоса: 0 < от < до; частота максимума — внутри полосы; КСВН ≥ 1; норма, если есть, ≥ 1;
- вердикт 'pass' | 'fail'; автоматический вердикт (verdict_manual = false) только при норме и согласован с ней
  (годно ⇔ КСВН ≤ нормы); ручной — с непустой причиной;
- файл: имя не пустое, 1 байт … 2 МБ; точек в файле ≥ 1;
- FK: изделие, СИ и контролёр — ON DELETE RESTRICT (запись качества не исчезает вместе с ними).

Ничего существующего не меняется: новая пустая таблица, заполнения нет. DESTRUCTIVE = False.
Откат снимает таблицу (записанные замеры теряются).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0142_production_vswr_measurements"
down_revision: str | None = "0140_purchasing_rfq_rb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "production_vswr_measurements"
FILE_LIMIT = 2 * 1024 * 1024


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("serial_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("band_from_mhz", sa.Numeric(12, 3), nullable=False),
        sa.Column("band_to_mhz", sa.Numeric(12, 3), nullable=False),
        sa.Column("max_vswr", sa.Numeric(9, 4), nullable=False),
        sa.Column("max_vswr_mhz", sa.Numeric(12, 3), nullable=False),
        sa.Column("norm", sa.JSON(), nullable=False),
        sa.Column("norm_vswr_max", sa.Numeric(9, 4), nullable=True),
        sa.Column("verdict", sa.String(8), nullable=False),
        sa.Column("verdict_manual", sa.Boolean(), nullable=False),
        sa.Column("verdict_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("inspector_id", sa.Uuid(), nullable=False),
        sa.Column("inspector_name", sa.String(255), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("file_meta", sa.JSON(), nullable=False),
        sa.Column("trace", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["serial_id"], ["production_serials.id"], ondelete="RESTRICT", name="fk_production_vswr_serial_id",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["production_instruments.id"], ondelete="RESTRICT", name="fk_production_vswr_instrument_id",
        ),
        sa.ForeignKeyConstraint(
            ["inspector_id"], ["identity_users.id"], ondelete="RESTRICT", name="fk_production_vswr_inspector_id",
        ),
        sa.UniqueConstraint("serial_id", "attempt", name="uq_production_vswr_serial_attempt"),
        sa.CheckConstraint("attempt >= 1", name="ck_production_vswr_attempt"),
        sa.CheckConstraint("band_from_mhz > 0 AND band_from_mhz < band_to_mhz", name="ck_production_vswr_band"),
        sa.CheckConstraint("max_vswr >= 1", name="ck_production_vswr_max"),
        sa.CheckConstraint(
            "max_vswr_mhz >= band_from_mhz AND max_vswr_mhz <= band_to_mhz", name="ck_production_vswr_peak_in_band",
        ),
        sa.CheckConstraint("norm_vswr_max IS NULL OR norm_vswr_max >= 1", name="ck_production_vswr_norm"),
        # The value column comes first: the head-1 seeder of CI fills a checked column with the first literal of `IN`.
        sa.CheckConstraint("verdict IN ('pass', 'fail')", name="ck_production_vswr_verdict"),
        sa.CheckConstraint(
            "verdict_manual OR (norm_vswr_max IS NOT NULL AND ((verdict = 'pass' AND max_vswr <= norm_vswr_max)"
            " OR (verdict = 'fail' AND max_vswr > norm_vswr_max)))",
            name="ck_production_vswr_auto_verdict",
        ),
        sa.CheckConstraint(
            "NOT verdict_manual OR length(trim(verdict_reason)) > 0", name="ck_production_vswr_manual_reason",
        ),
        sa.CheckConstraint("length(trim(file_name)) > 0", name="ck_production_vswr_file_name"),
        sa.CheckConstraint(f"length(content) BETWEEN 1 AND {FILE_LIMIT}", name="ck_production_vswr_file_size"),
        sa.CheckConstraint("points >= 1", name="ck_production_vswr_points"),
    )
    op.create_index("ix_production_vswr_measurements_instrument_id", TABLE, ["instrument_id"])
    op.create_index("ix_production_vswr_measurements_inspector_id", TABLE, ["inspector_id"])
    print(f"0142_production_vswr_measurements: создана таблица {TABLE} (пустая)")


def downgrade() -> None:
    op.drop_index("ix_production_vswr_measurements_inspector_id", table_name=TABLE)
    op.drop_index("ix_production_vswr_measurements_instrument_id", table_name=TABLE)
    op.drop_table(TABLE)

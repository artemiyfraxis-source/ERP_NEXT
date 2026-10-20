"""Выпуски комплекта ТД по ЕСТД (ГОСТ РВ): таблица `td_issues` (20.10.2026).

Выпуск — замороженный снимок карты ЭТК проекта (как её отдаёт GET /api/techcards/projects/{id}) с подписантами основной
надписи, литерой, видом ТП (метод по ГОСТ 3.1201), номером изменения и извещением. Обозначения документов комплекта
(ФРАК.60185.00001 …) считаются из `org_code`, `method_code` и `sequence_no`.

Что держит база, а не код:
- статус — только 'draft' / 'signing' / 'active' / 'annulled';
- литера — '' или П, О, О1…О5, А, Б, И (ГОСТ Р 3.102-2024); вид ТП — 85, 88, 80, 81, 02, 03;
- изменение (change_no > 0) без номера извещения и причины не записывается;
- номер изменения у карты один: UNIQUE (карта, change_no);
- первый выпуск двух карт не получает одного порядкового номера: UNIQUE (sequence_no, change_no) — у каждой карты номер
  берёт первый выпуск (change_no = 0), следующие выпуски карты его повторяют;
- действующий выпуск у карты один: частичный UNIQUE (карта) WHERE status = 'active' (PostgreSQL и SQLite).
Подписанты (JSON) сверяются в домене: Н.контр. не может быть Разраб. или Пров.

DESTRUCTIVE = False: создаётся пустая таблица, данные не меняются. Откат удаляет таблицу.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0129_td_issues"
down_revision: str | None = "0128_finished_goods_acts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "td_issues"
ACTIVE = sa.text("status = 'active'")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "project_card_id", sa.Uuid(), sa.ForeignKey("techcard_projects.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("change_no", sa.Integer(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("org_code", sa.String(32), nullable=False, server_default="ФРАК"),
        sa.Column("method_code", sa.String(8), nullable=False),
        sa.Column("litera", sa.String(8), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("etk_revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("signatories", sa.JSON(), nullable=False),
        sa.Column("notice_no", sa.String(64), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("annulled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'signing', 'active', 'annulled')", name="ck_td_issues_status"),
        sa.CheckConstraint(
            "litera IN ('', 'П', 'О', 'О1', 'О2', 'О3', 'О4', 'О5', 'А', 'Б', 'И')", name="ck_td_issues_litera",
        ),
        sa.CheckConstraint("method_code IN ('85', '88', '80', '81', '02', '03')", name="ck_td_issues_method_code"),
        sa.CheckConstraint("change_no >= 0", name="ck_td_issues_change_no"),
        sa.CheckConstraint("sequence_no > 0", name="ck_td_issues_sequence_no"),
        sa.CheckConstraint("change_no = 0 OR (notice_no <> '' AND reason <> '')", name="ck_td_issues_notice"),
        sa.UniqueConstraint("project_card_id", "change_no", name="uq_td_issues_card_change"),
        sa.UniqueConstraint("sequence_no", "change_no", name="uq_td_issues_sequence_change"),
    )
    op.create_index(
        "ux_td_issues_active_card", TABLE, ["project_card_id"], unique=True,
        sqlite_where=ACTIVE, postgresql_where=ACTIVE,
    )


def downgrade() -> None:
    op.drop_index("ux_td_issues_active_card", table_name=TABLE)
    op.drop_table(TABLE)

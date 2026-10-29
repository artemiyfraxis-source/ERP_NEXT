"""Отгрузка: решение «упаковывать», № и дата УПД в карточке проекта, отгрузка без УПД по PIN (владелец 29.10.2026).

«Ульяна на „Готово“ решает „упаковывать“ — только тогда Алексей упаковывает»; «на „Упаковано“ УПД обязателен, ТК
окончательная»; «Отгрузить по пинкоду без УПД: пинкод у Артемия, Зингера, Касаточкина — при попытке они вводят и дают
разрешение на такую отгрузку».

Что делает миграция:
- `shipping_project_addressees` (карточка проекта в Отгрузке): `upd_number` / `upd_date` — № и дата УПД проекта (новая
  отправка берёт их, как файл `upd_url`); `pack_decided` / `pack_decided_by` / `pack_decided_at` — решение
  «упаковывать». CHECK `ck_shipping_project_addressees_pack_decision`: флаг, кто и когда — либо все заданы, либо все пусты;
- `shipments`: `no_upd_approved_by` / `no_upd_approved_at` — кто своим PIN разрешил отгрузку без УПД и когда. CHECK
  `ck_shipments_no_upd_approval`: кто и когда — вместе или никак;
- данные: чтобы сегодняшняя упаковка не встала, решение «упаковывать» (кто — «Миграция», когда — момент миграции)
  ставится проектам с незакрытой отправкой (не «Закрыта», не «Отменена») и проектам с принятой, но не упакованной ГП
  (`accepted > shipped`). Карточка без решения получает его (версия +1); проекта без карточки — новая карточка с одним
  решением. Сверка печатается: проектов с решением, обновлено, добавлено, строк карточек и отправок было → стало.
Раздел доступа «Отгрузка без УПД — разрешение» (`ship_no_upd`) миграция никому не выдаёт: его держат полные уровни, кому
ещё — владелец решает в «Доступах».

DESTRUCTIVE = False: столбцы добавляются, строки только дополняются. Откат снимает CHECK и столбцы (решения, № и даты
УПД карточек и разрешения «без УПД» теряются; карточки, добавленные данными, остаются — пустые адресаты безвредны).
"""

from datetime import datetime, timezone
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0162_pack_decision_no_upd"
down_revision: str | None = "0161_ladder_markers_blockers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

ADDRESSEES = "shipping_project_addressees"
SHIPMENTS = "shipments"
PACK_CHECK = "ck_shipping_project_addressees_pack_decision"
PACK_SQL = (
    "(pack_decided AND pack_decided_by <> '' AND pack_decided_at IS NOT NULL)"
    " OR (NOT pack_decided AND pack_decided_by = '' AND pack_decided_at IS NULL)"
)
NO_UPD_CHECK = "ck_shipments_no_upd_approval"
NO_UPD_SQL = (
    "(no_upd_approved_by = '' AND no_upd_approved_at IS NULL)"
    " OR (no_upd_approved_by <> '' AND no_upd_approved_at IS NOT NULL)"
)
MIGRATION_ACTOR = "Миграция"
# Literal copies of app.modules.shipping.domain.ShipmentStatus (a migration never imports app code that moves on).
FINAL_STATUSES = ("Закрыта", "Отменена")

# Projects that are being packed today: an unfinished shipment, or finished goods accepted and not yet packed.
WANTED_SQL = sa.text(
    "SELECT DISTINCT l.project FROM shipment_lines l JOIN shipments s ON s.id = l.shipment_id"
    " WHERE s.status NOT IN (:closed, :cancelled)"
    " UNION SELECT DISTINCT project FROM finished_goods WHERE accepted > shipped"
).bindparams(closed=FINAL_STATUSES[0], cancelled=FINAL_STATUSES[1])

_cards = sa.table(
    ADDRESSEES,
    sa.column("project", sa.String()), sa.column("version", sa.Integer()), sa.column("receiver", sa.String()),
    sa.column("shipping_point", sa.Text()), sa.column("contact", sa.String()), sa.column("phone", sa.String()),
    sa.column("carrier", sa.String()), sa.column("updated_by", sa.String()),
    sa.column("updated_at", sa.DateTime(timezone=True)), sa.column("pack_decided", sa.Boolean()),
    sa.column("pack_decided_by", sa.String()), sa.column("pack_decided_at", sa.DateTime(timezone=True)),
)


def _count(bind, table: str) -> int:
    return int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)


def _decide(bind) -> tuple[int, int, int]:
    """(projects decided, cards updated, cards added)."""
    now = datetime.now(timezone.utc)
    wanted = sorted({str(row[0]) for row in bind.execute(WANTED_SQL) if str(row[0] or "").strip()})
    existing = {
        str(project): bool(decided)
        for project, decided in bind.execute(sa.select(_cards.c.project, _cards.c.pack_decided))
    }
    updated = added = 0
    for project in wanted:
        if project in existing:
            if existing[project]:
                continue
            bind.execute(_cards.update().where(_cards.c.project == project).values(
                pack_decided=True, pack_decided_by=MIGRATION_ACTOR, pack_decided_at=now,
                version=_cards.c.version + 1,
            ))
            updated += 1
        else:
            bind.execute(_cards.insert().values(
                project=project, version=1, receiver="", shipping_point="", contact="", phone="", carrier="",
                updated_by=MIGRATION_ACTOR, updated_at=now,
                pack_decided=True, pack_decided_by=MIGRATION_ACTOR, pack_decided_at=now,
            ))
            added += 1
    return len(wanted), updated, added


def upgrade() -> None:
    bind = op.get_bind()
    cards_before, shipments_before = _count(bind, ADDRESSEES), _count(bind, SHIPMENTS)
    with op.batch_alter_table(ADDRESSEES) as batch:
        batch.add_column(sa.Column("upd_number", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("upd_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("pack_decided", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("pack_decided_by", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("pack_decided_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(PACK_CHECK, PACK_SQL)
    with op.batch_alter_table(SHIPMENTS) as batch:
        batch.add_column(sa.Column("no_upd_approved_by", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("no_upd_approved_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(NO_UPD_CHECK, NO_UPD_SQL)
    projects, updated, added = _decide(bind)
    print(
        f"0162_pack_decision_no_upd: решение «упаковывать» — проектов {projects} (карточек обновлено {updated}, "
        f"добавлено {added}); {ADDRESSEES} {cards_before} → {_count(bind, ADDRESSEES)}, "
        f"{SHIPMENTS} {shipments_before} → {_count(bind, SHIPMENTS)}"
    )


def downgrade() -> None:
    with op.batch_alter_table(SHIPMENTS) as batch:
        batch.drop_constraint(NO_UPD_CHECK, type_="check")
        batch.drop_column("no_upd_approved_at")
        batch.drop_column("no_upd_approved_by")
    with op.batch_alter_table(ADDRESSEES) as batch:
        batch.drop_constraint(PACK_CHECK, type_="check")
        for column in ("pack_decided_at", "pack_decided_by", "pack_decided", "upd_date", "upd_number"):
            batch.drop_column(column)

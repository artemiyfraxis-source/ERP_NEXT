"""Партии строк отправок: из каких актов приёмки ГП набрана строка (владелец 06.11.2026, аудит прода).

Строка отправки показывала первый ГРН КБ (`finished_goods.grn`), а не ГРН актов, из которых её набрали: ОТГ-20261106-01
набрала 1050 шт из акта ГП.26.0000047, а в строке стоял ГП.26.0000042; все отправки 533К.12 показывали ГП.26.0000003,
хотя актов четыре. Теперь набор берёт акты КБ по FIFO (дата акта, номер) — или явно выбранный ГРН — и записывает,
сколько штук из какого акта.

Что делает миграция:
- создаётся `shipment_line_lots`: строка отправки (FK CASCADE, NOT NULL), акт приёмки ГП (FK RESTRICT, пусто — только
  когда у КБ нет актов), ГРН (NOT NULL), штук (CHECK > 0), порядок; UNIQUE (строка, ГРН). Σ штук партий строки = её
  набранному — держит запись (`SqlAlchemyShipmentRepository`) и сверяет `app.db_integrity`;
- каждой уже набранной строке (набрано > 0, любой статус отправки) — партии: по каждой КБ отправки идут в порядке
  заведения (дата, номер, позиция строки), штуки берутся из актов КБ по FIFO (дата приёмки, время записи, ГРН), у акта —
  его штуки за вычетом откатов «принято → в производство» (как `roll_back_acts`: откат снимает с самого нового акта,
  принятого к его моменту);
- что акты не покрывают, ложится на первый ГРН КБ (её акт с ГРН КБ, иначе первый акт; без актов — ГРН КБ без акта) и
  печатается в списке «не покрыто актами» — молча не угадывается;
- набранное, упакованное, отгруженное и ГРН строк (`shipment_lines.grn`) не меняются: только добавляются партии;
- печатается сверка: строк с набранным и их штук до, партий и их штук после (должны сойтись), строк из нескольких
  актов, и список непокрытого.
DESTRUCTIVE = False: ничего не удаляется. Откат удаляет таблицу партий; повторный подъём строит их заново.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Sequence
from uuid import uuid5

from alembic import op
import sqlalchemy as sa


revision: str = "0173_shipment_line_lots"
down_revision: str | None = "0172_resource_unit_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

LOTS = "shipment_line_lots"
ZERO = Decimal("0")


def _plain(value: Decimal) -> str:
    return format(Decimal(value).normalize(), "f")


def _tables():
    goods = sa.table(
        "finished_goods", sa.column("id", sa.Uuid()), sa.column("project", sa.String()),
        sa.column("item_index", sa.String()), sa.column("grn", sa.String()),
    )
    acts = sa.table(
        "finished_goods_acts", sa.column("id", sa.Uuid()), sa.column("finished_good_id", sa.Uuid()),
        sa.column("grn", sa.String()), sa.column("quantity", sa.Numeric(18, 6)), sa.column("received_at", sa.Date()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    moves = sa.table(
        "finished_goods_moves", sa.column("finished_good_id", sa.Uuid()), sa.column("quantity", sa.Numeric(18, 6)),
        sa.column("from_state", sa.String()), sa.column("to_state", sa.String()),
        sa.column("occurred_at", sa.DateTime(timezone=True)),
    )
    shipments = sa.table(
        "shipments", sa.column("id", sa.Uuid()), sa.column("number", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    lines = sa.table(
        "shipment_lines", sa.column("id", sa.Uuid()), sa.column("shipment_id", sa.Uuid()),
        sa.column("finished_good_id", sa.Uuid()), sa.column("position", sa.Integer()),
        sa.column("project", sa.String()), sa.column("item_index", sa.String()),
        sa.column("picked", sa.Numeric(18, 6)),
    )
    lots = sa.table(
        LOTS, sa.column("id", sa.Uuid()), sa.column("line_id", sa.Uuid()), sa.column("act_id", sa.Uuid()),
        sa.column("position", sa.Integer()), sa.column("grn", sa.String()), sa.column("quantity", sa.Numeric(18, 6)),
    )
    return goods, acts, moves, shipments, lines, lots


def _net_acts(bind, acts, moves) -> dict:
    """КБ → [акт, свободно] по FIFO; свободно — штуки акта за вычетом откатов «принято → в производство»."""
    by_good: dict = defaultdict(list)
    for row in bind.execute(sa.select(
        acts.c.id, acts.c.finished_good_id, acts.c.grn, acts.c.quantity, acts.c.received_at, acts.c.created_at,
    )):
        by_good[row.finished_good_id].append(row)
    rollbacks: dict = defaultdict(list)
    for row in bind.execute(sa.select(moves.c.finished_good_id, moves.c.occurred_at, moves.c.quantity).where(
        moves.c.from_state == "accepted", moves.c.to_state == "production",
    )):
        rollbacks[row.finished_good_id].append((row.occurred_at, Decimal(row.quantity)))
    result: dict = {}
    for good_id, items in by_good.items():
        by_time = sorted(items, key=lambda act: (act.created_at, act.grn))
        taken = {act.id: ZERO for act in items}
        for when, quantity in sorted(rollbacks.get(good_id, ()), key=lambda item: item[0]):
            left = quantity
            for act in reversed([act for act in by_time if act.created_at <= when]):
                take = min(left, Decimal(act.quantity) - taken[act.id])
                if take > 0:
                    taken[act.id] += take
                    left -= take
                if left <= 0:
                    break
        ordered = sorted(items, key=lambda act: (act.received_at, act.created_at, act.grn))
        result[good_id] = [[act, Decimal(act.quantity) - taken[act.id]] for act in ordered]
    return result


def _backfill(bind) -> None:
    goods, acts, moves, shipments, lines, lots = _tables()
    stock = _net_acts(bind, acts, moves)
    grns = {row.id: row.grn for row in bind.execute(sa.select(goods.c.id, goods.c.grn))}
    picked = list(bind.execute(
        sa.select(
            lines.c.id, lines.c.finished_good_id, lines.c.project, lines.c.item_index, lines.c.picked,
            shipments.c.number,
        ).select_from(lines.join(shipments, shipments.c.id == lines.c.shipment_id))
        .where(lines.c.picked > 0)
        .order_by(shipments.c.created_at, shipments.c.number, shipments.c.id, lines.c.position, lines.c.id)
    ))
    rows: list[dict] = []
    unresolved: list[str] = []
    split = 0
    for line in picked:
        entries = stock.get(line.finished_good_id, [])
        own = grns.get(line.finished_good_id)
        first = next((entry for entry in entries if entry[0].grn == own), entries[0] if entries else None)
        left = Decimal(line.picked)
        made: dict[str, list] = {}
        for entry in entries:
            take = min(left, max(entry[1], ZERO))
            if take <= 0:
                continue
            entry[1] -= take
            left -= take
            made[entry[0].grn] = [entry[0].id, take]
            if left <= 0:
                break
        if left > 0:
            act_id, grn = (first[0].id, first[0].grn) if first else (None, own or "")
            if grn in made:
                made[grn][1] += left
            else:
                made[grn] = [act_id, left]
            unresolved.append(
                f"{line.number} {line.project} {line.item_index}: {_plain(left)} из {_plain(Decimal(line.picked))} шт "
                f"не покрыто актами — записано на {grn or 'пустой ГРН'}"
                + ("" if first else " (у КБ нет актов)")
            )
        if len(made) > 1:
            split += 1
        for position, (grn, (act_id, quantity)) in enumerate(made.items()):
            rows.append({
                "id": uuid5(line.id, f"shipment-line-lot:{grn}"), "line_id": line.id, "act_id": act_id,
                "position": position, "grn": grn, "quantity": quantity,
            })
    if rows:
        bind.execute(sa.insert(lots), rows)
    before = sum((Decimal(line.picked) for line in picked), ZERO)
    after = sum((row["quantity"] for row in rows), ZERO)
    print(
        f"{revision}: до — строк отправок с набранным {len(picked)}, штук {_plain(before)}; после — партий {len(rows)}, "
        f"штук {_plain(after)}{'' if before == after else ' — НЕ СХОДИТСЯ'}; строк из нескольких актов {split}; "
        f"не покрыто актами строк {len(unresolved)}."
    )
    for item in unresolved:
        print(f"{revision}:   не покрыто: {item}")


def upgrade() -> None:
    op.create_table(
        LOTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("line_id", sa.Uuid(), sa.ForeignKey("shipment_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("act_id", sa.Uuid(), sa.ForeignKey("finished_goods_acts.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("grn", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.UniqueConstraint("line_id", "grn", name="uq_shipment_line_lots_line_grn"),
        sa.CheckConstraint("quantity > 0", name="ck_shipment_line_lots_quantity"),
    )
    op.create_index("ix_shipment_line_lots_line_id", LOTS, ["line_id"])
    op.create_index("ix_shipment_line_lots_act_id", LOTS, ["act_id"])
    _backfill(op.get_bind())


def downgrade() -> None:
    op.drop_index("ix_shipment_line_lots_act_id", table_name=LOTS)
    op.drop_index("ix_shipment_line_lots_line_id", table_name=LOTS)
    op.drop_table(LOTS)

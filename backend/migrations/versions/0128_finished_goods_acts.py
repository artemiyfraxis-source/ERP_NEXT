"""Акты приёмки ГП: у каждой приёмки КБ свой ГРН (владелец 19.10.2026).

Владелец: «когда идёт приёмка разными частями — ГРН один и тот же». До сих пор ГРН был один на КБ: вторая и следующие
приёмки той же КБ шли под номером первой. Теперь каждая приёмка — акт со своим ГРН «ГП.YY.NNNNNNN».

Что делает миграция:
- создаётся `finished_goods_acts`: КБ (FK RESTRICT), ход «принято» журнала ГП (FK RESTRICT, UNIQUE, NOT NULL — один акт
  на ход), ГРН (NOT NULL, UNIQUE), штук (CHECK > 0), дата приёмки, место хранения, кто, когда записан;
- на каждый уже записанный ход «принято» — акт, по каждой КБ старые первыми:
  * первый акт КБ берёт ГРН самой КБ (`finished_goods.grn` не меняется — этикетки, PL/DN, строки отправок и трек
    проекта видят тот же номер); у КБ без ГРН первый акт получает новый номер, и он же становится ГРН КБ;
  * каждый следующий — новый номер последовательности `shipping_sequences` 'grn-YY' (YY — год хода по Москве), КБ —
    по порядку проект, КБ; последовательность уходит за последний выданный номер;
  * дата приёмки — дата хода по Москве, место — место хранения КБ;
  * отгруженное до ERP (`finished_goods.before_erp`, ходы «перенос 0111: отгружено до ERP» и «отгружено до ERP (лист
    цеха)») акта не получает: эти штуки снимаются с ходов, помеченных так, остаток — со старых ходов КБ;
- сдача коробки на склад ГП (ход «коробка …»): её строка `production_finished_goods_receipts` и серийные номера коробки
  получают ГРН своего акта;
- печатается сверка: ходов «принято», штук и отгруженного до ERP до, актов и штук после (Σ актов = Σ ходов − до ERP,
  её же держит `app.db_integrity`), КБ, принятых несколькими актами, и разбивка по КБ.
DESTRUCTIVE = False: ничего не удаляется. Откат удаляет акты; ГРН коробок и последовательность остаются как есть.
"""

import re
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence
from uuid import uuid5
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa


revision: str = "0128_finished_goods_acts"
down_revision: str | None = "0127_etk_inputs_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

ACTS = "finished_goods_acts"
MOSCOW = ZoneInfo("Europe/Moscow")
BEFORE_ERP_REASONS = ("перенос 0111: отгружено до ERP", "отгружено до ERP (лист цеха)")
GRN = re.compile(r"^ГП\.(\d{2})\.(\d{7})$")
ZERO = Decimal("0")


def _moscow(value) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MOSCOW)


def _plain(value: Decimal) -> str:
    return format(Decimal(value).normalize(), "f")


def _tables():
    goods = sa.table(
        "finished_goods", sa.column("id", sa.Uuid()), sa.column("project", sa.String()),
        sa.column("item_index", sa.String()), sa.column("grn", sa.String()), sa.column("before_erp", sa.Numeric(18, 6)),
        sa.column("location", sa.String()),
    )
    moves = sa.table(
        "finished_goods_moves", sa.column("id", sa.Uuid()), sa.column("finished_good_id", sa.Uuid()),
        sa.column("kind", sa.String()), sa.column("quantity", sa.Numeric(18, 6)), sa.column("actor", sa.String()),
        sa.column("occurred_at", sa.DateTime(timezone=True)), sa.column("reason", sa.Text()),
    )
    acts = sa.table(
        ACTS, sa.column("id", sa.Uuid()), sa.column("finished_good_id", sa.Uuid()), sa.column("move_id", sa.Uuid()),
        sa.column("grn", sa.String()), sa.column("quantity", sa.Numeric(18, 6)), sa.column("received_at", sa.Date()),
        sa.column("location", sa.String()), sa.column("actor", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    sequences = sa.table("shipping_sequences", sa.column("name", sa.String()), sa.column("value", sa.Integer()))
    receipts = sa.table(
        "production_finished_goods_receipts", sa.column("id", sa.Uuid()), sa.column("source_box_id", sa.Uuid()),
        sa.column("finished_good_id", sa.Uuid()), sa.column("grn", sa.String()),
        sa.column("quantity", sa.Numeric(18, 6)), sa.column("received_at", sa.DateTime(timezone=True)),
    )
    serials = sa.table(
        "production_serials", sa.column("current_box_id", sa.Uuid()), sa.column("finished_goods_grn", sa.String()),
    )
    return goods, moves, acts, sequences, receipts, serials


def _counters(bind, goods, sequences) -> dict[str, int]:
    """'grn-YY' → последний выданный номер: значение последовательности или больший номер, уже стоящий на КБ."""
    counters: dict[str, int] = {}
    for name, value in bind.execute(sa.select(sequences.c.name, sequences.c.value).where(sequences.c.name.like("grn-%"))):
        counters[name] = int(value or 0)
    for (grn,) in bind.execute(sa.select(goods.c.grn).where(goods.c.grn.is_not(None))):
        found = GRN.match(str(grn))
        if found:
            key = f"grn-{found.group(1)}"
            counters[key] = max(counters.get(key, 0), int(found.group(2)))
    return counters


def _backfill(bind) -> None:
    goods, moves, acts, sequences, receipts, serials = _tables()
    counters = _counters(bind, goods, sequences)
    touched: set[str] = set()

    def reserve(when: datetime) -> str:
        key = f"grn-{when.year % 100:02d}"
        counters[key] = counters.get(key, 0) + 1
        touched.add(key)
        return f"ГП.{when.year % 100:02d}.{counters[key]:07d}"

    by_good: dict = defaultdict(list)
    for row in bind.execute(sa.select(
        moves.c.id, moves.c.finished_good_id, moves.c.quantity, moves.c.actor, moves.c.occurred_at, moves.c.reason,
    ).where(moves.c.kind == "accept")):
        by_good[row.finished_good_id].append(row)

    rows: list[dict] = []
    boxes: list[tuple] = []  # (КБ, штук, когда, ГРН акта) — сдача коробки
    report: list[str] = []
    before = {"moves": 0, "quantity": ZERO, "before_erp": ZERO}
    split = 0
    for good in bind.execute(sa.select(goods).order_by(goods.c.project, goods.c.item_index, goods.c.id)):
        accepts = sorted(by_good.get(good.id, ()), key=lambda move: (_moscow(move.occurred_at), str(move.id)))
        if not accepts:
            continue
        skip = Decimal(good.before_erp or 0)
        due = sum((Decimal(move.quantity) for move in accepts), ZERO) - skip
        taken: dict = {}
        for marked in (True, False):  # до ERP — сначала с помеченных так ходов, остаток — со старых
            for move in accepts:
                if skip <= 0:
                    break
                if (str(move.reason or "").strip() in BEFORE_ERP_REASONS) is marked:
                    take = min(skip, Decimal(move.quantity) - taken.get(move.id, ZERO))
                    taken[move.id] = taken.get(move.id, ZERO) + take
                    skip -= take
        grn = good.grn
        made = []
        for move in accepts:
            quantity = Decimal(move.quantity) - taken.get(move.id, ZERO)
            if quantity <= 0:
                continue
            when = _moscow(move.occurred_at)
            if made:
                number = reserve(when)
            elif grn:
                number = grn
            else:
                number = grn = reserve(when)
                bind.execute(sa.update(goods).where(goods.c.id == good.id).values(grn=number))
            made.append((number, quantity))
            rows.append({
                "id": uuid5(move.id, "acceptance-act"), "finished_good_id": good.id, "move_id": move.id,
                "grn": number, "quantity": quantity, "received_at": when.date(), "location": good.location or "",
                "actor": move.actor, "created_at": move.occurred_at,
            })
            if str(move.reason or "").startswith("коробка "):
                boxes.append((good.id, quantity, when, number))
        before["moves"] += len(accepts)
        before["quantity"] += sum((Decimal(move.quantity) for move in accepts), ZERO)
        before["before_erp"] += sum(taken.values(), ZERO)
        if len(made) > 1:
            split += 1
        if made:
            report.append(
                f"{good.project} {good.item_index}: {_plain(sum((q for _n, q in made), ZERO))} = "
                + " + ".join(f"{_plain(quantity)} {number}" for number, quantity in made)
            )
    if rows:
        bind.execute(sa.insert(acts), rows)
    for key in sorted(touched):
        updated = bind.execute(sa.update(sequences).where(sequences.c.name == key).values(value=counters[key]))
        if not updated.rowcount:
            bind.execute(sa.insert(sequences).values(name=key, value=counters[key]))
    boxes_renamed = _rename_boxes(bind, receipts, serials, boxes)
    print(
        f"{revision}: до — ходов «принято» {before['moves']}, штук {_plain(before['quantity'])}, из них отгружено до "
        f"ERP {_plain(before['before_erp'])}; после — актов {len(rows)}, штук {_plain(sum((row['quantity'] for row in rows), ZERO))}; КБ, принятых "
        f"несколькими актами, {split}; коробкам дан ГРН акта {boxes_renamed}; последовательности: "
        + (", ".join(f"{key} = {counters[key]}" for key in sorted(touched)) or "без изменений") + "."
    )
    for line in report:
        print(f"{revision}:   {line}")


def _rename_boxes(bind, receipts, serials, boxes: list[tuple]) -> int:
    """Сдача коробки: её строка сдачи и серийные номера коробки — под ГРН своего акта (строка сдачи — того же КБ и
    количества, ближайшая по времени)."""
    if not boxes:
        return 0
    found = list(bind.execute(sa.select(
        receipts.c.id, receipts.c.source_box_id, receipts.c.finished_good_id, receipts.c.grn, receipts.c.quantity,
        receipts.c.received_at,
    )))
    used: set = set()
    renamed = 0
    for good_id, quantity, when, number in boxes:
        candidates = [
            row for row in found
            if row.id not in used and row.finished_good_id == good_id and Decimal(row.quantity) == quantity
        ]
        if not candidates:
            continue
        receipt = min(candidates, key=lambda row: abs((_moscow(row.received_at) - when).total_seconds()))
        used.add(receipt.id)
        if receipt.grn == number:
            continue
        bind.execute(sa.update(receipts).where(receipts.c.id == receipt.id).values(grn=number))
        bind.execute(sa.update(serials).where(
            serials.c.current_box_id == receipt.source_box_id, serials.c.finished_goods_grn == receipt.grn,
        ).values(finished_goods_grn=number))
        renamed += 1
    return renamed


def upgrade() -> None:
    op.create_table(
        ACTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("finished_good_id", sa.Uuid(), sa.ForeignKey("finished_goods.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("move_id", sa.Uuid(), sa.ForeignKey("finished_goods_moves.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("grn", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("received_at", sa.Date(), nullable=False),
        sa.Column("location", sa.String(255), nullable=False, server_default=""),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("move_id", name="uq_finished_goods_acts_move_id"),
        sa.UniqueConstraint("grn", name="uq_finished_goods_acts_grn"),
        sa.CheckConstraint("quantity > 0", name="ck_finished_goods_acts_quantity"),
    )
    op.create_index("ix_finished_goods_acts_finished_good_id", ACTS, ["finished_good_id"])
    _backfill(op.get_bind())


def downgrade() -> None:
    op.drop_index("ix_finished_goods_acts_finished_good_id", table_name=ACTS)
    op.drop_table(ACTS)

"""Регистр готовой продукции: журнал ходов КБ по состояниям (владелец 12.10.2026).

Штуки КБ идут В производстве → Принято складом → Упаковано → Отгружено. Остаток по состояниям остаётся в счётчиках
`finished_goods` (accepted — принято всего, shipped — упаковано всего, sent — отгружено всего), а каждый их сдвиг
пишется ходом в новую таблицу `finished_goods_moves` в той же транзакции.

Что делает миграция:
- `finished_goods` получает проверки порядка: принято ≥ упаковано ≥ отгружено ≥ отгружено до ERP ≥ 0. Строка, которая
  их нарушает, останавливает миграцию с перечнем КБ (ничего не переписывается — сначала разобраться руками);
- создаётся `finished_goods_moves` (ход: вид accept / pack / unpack / ship / correction, штук > 0, из какого состояния
  в какое — только разрешённые пары, отправка, кто, когда, причина; откат — только с причиной);
- журнал заполняется из того, что уже есть (причина «перенос 0111»):
  * отгружено до ERP (before_erp) — принято + упаковано + отгружено, без отправки, «перенос 0111: отгружено до ERP»;
  * остальное принятое — один ход «принято» датой приёмки строки;
  * набранное в отправки — ход «упаковано» на каждую строку отправки (датой упаковки или заведения отправки), у
    отправок «Отгружена»/«Закрыта» — ещё ход «отгружено» (датой отгрузки);
  * если строки отправок не сходятся со счётчиками строки ГП — счётчик главнее: лишнее по отправкам не пишется,
    недостающее — ходом без отправки «перенос 0111: без отправки». Так сумма журнала по КБ всегда равна счётчикам.
- печатается сверка: число КБ и штуки в каждом состоянии.
DESTRUCTIVE = False: ничего не удаляется. Downgrade удаляет журнал и проверки (счётчики остаются как есть).
"""

import re
from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Sequence
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0111_finished_goods_register"
down_revision: str | None = "0110_stage_warehouse_accepted"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

GOODS = "finished_goods"
MOVES = "finished_goods_moves"
REASON = "перенос 0111"
ACTOR = "миграция 0111"
LEFT_STATUSES = ("Отгружена", "Закрыта")
# Literal copies of app.modules.shipping.infrastructure (a migration never imports app code that moves on).
GOODS_CHECKS = {
    "ck_finished_goods_sent_nonneg": "before_erp >= 0 AND sent >= before_erp",
    "ck_finished_goods_packed_over_sent": "shipped >= sent",
    "ck_finished_goods_accepted_over_packed": "accepted >= shipped",
}
ROUTES = (
    ("accept", "production", "accepted"),
    ("correction", "accepted", "production"),
    ("correction", "packed", "accepted"),
    ("correction", "shipped", "packed"),
    ("pack", "accepted", "packed"),
    ("ship", "packed", "shipped"),
    ("unpack", "packed", "accepted"),
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


MOVE_CHECKS = {
    "ck_finished_goods_moves_kind": _in("kind", ("accept", "pack", "unpack", "ship", "correction")),
    "ck_finished_goods_moves_from_state": _in("from_state", ("production", "accepted", "packed", "shipped")),
    "ck_finished_goods_moves_to_state": _in("to_state", ("accepted", "production", "packed", "shipped")),
    "ck_finished_goods_moves_quantity": "quantity > 0",
    "ck_finished_goods_moves_route": " OR ".join(
        f"(kind = '{kind}' AND from_state = '{source}' AND to_state = '{target}')" for kind, source, target in ROUTES
    ),
    "ck_finished_goods_moves_correction_reason": "kind <> 'correction' OR reason <> ''",
}

_HOMOGLYPHS = str.maketrans("KCEOPAHTMBXY", "КСЕОРАНТМВХУ")


def _project_key(value: object) -> str:
    """Literal copy of app.shared.project_number.normalize_project_number at 0111 (a NULL key of a row written past
    the ORM)."""
    normalized = re.sub(r"\s+", "", str(value or "").strip().upper()).translate(_HOMOGLYPHS)
    if re.fullmatch(r"[1-9]\d*(?:К[А-Я]?|МФ)(?:\.[А-Я]{1,3})?\.?", normalized):
        return normalized.rstrip(".") + ".1"
    return normalized


def _broken(bind) -> list[str]:
    where = " OR ".join(f"NOT ({sql})" for sql in GOODS_CHECKS.values())
    rows = bind.execute(sa.text(
        f"SELECT project, item_index, accepted, shipped, sent, before_erp FROM {GOODS} WHERE {where}"
        " ORDER BY project, item_index"
    )).all()
    return [
        f"{project} {item}: принято {accepted}, упаковано {shipped}, отгружено {sent}, до ERP {before}"
        for project, item, accepted, shipped, sent, before in rows
    ]


def _when(value, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return fallback
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.combine(value, time(9, 0), tzinfo=timezone.utc)


def _backfill(bind) -> None:
    goods = sa.table(
        GOODS, sa.column("id", sa.Uuid()), sa.column("project", sa.String()), sa.column("item_index", sa.String()),
        sa.column("accepted", sa.Numeric(18, 6)), sa.column("shipped", sa.Numeric(18, 6)),
        sa.column("sent", sa.Numeric(18, 6)), sa.column("before_erp", sa.Numeric(18, 6)),
        sa.column("received_at", sa.Date()), sa.column("project_key", sa.String()),
    )
    shipments = sa.table(
        "shipments", sa.column("id", sa.Uuid()), sa.column("status", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)), sa.column("packed_at", sa.Date()),
        sa.column("shipped_at", sa.Date()),
    )
    lines = sa.table(
        "shipment_lines", sa.column("shipment_id", sa.Uuid()), sa.column("finished_good_id", sa.Uuid()),
        sa.column("picked", sa.Numeric(18, 6)), sa.column("position", sa.Integer()),
    )
    moves = sa.table(
        MOVES, sa.column("id", sa.Uuid()), sa.column("finished_good_id", sa.Uuid()),
        sa.column("project_key", sa.String()), sa.column("item_index", sa.String()), sa.column("kind", sa.String()),
        sa.column("quantity", sa.Numeric(18, 6)), sa.column("from_state", sa.String()),
        sa.column("to_state", sa.String()), sa.column("shipment_id", sa.Uuid()), sa.column("actor", sa.String()),
        sa.column("actor_id", sa.Uuid()), sa.column("occurred_at", sa.DateTime(timezone=True)),
        sa.column("reason", sa.Text()),
    )
    now = datetime.now(timezone.utc)
    picks: dict = {}
    for good_id, shipment_id, picked, status, created_at, packed_at, shipped_at in bind.execute(
        sa.select(lines.c.finished_good_id, lines.c.shipment_id, lines.c.picked, shipments.c.status,
                  shipments.c.created_at, shipments.c.packed_at, shipments.c.shipped_at)
        .select_from(lines.join(shipments, shipments.c.id == lines.c.shipment_id))
        .where(lines.c.picked > 0)
        .order_by(shipments.c.created_at, shipments.c.id, lines.c.position)
    ):
        picks.setdefault(good_id, []).append((shipment_id, Decimal(picked), status, created_at, packed_at, shipped_at))

    rows: list[dict] = []
    kbs = 0
    totals = {"accepted": Decimal(0), "packed": Decimal(0), "shipped": Decimal(0)}
    unmatched = 0
    for good in bind.execute(sa.select(goods).order_by(goods.c.project, goods.c.item_index)):
        accepted, packed, shipped = Decimal(good.accepted), Decimal(good.shipped), Decimal(good.sent)
        before = Decimal(good.before_erp)
        if not (accepted or packed or shipped):
            continue
        kbs += 1
        totals["accepted"] += accepted - packed
        totals["packed"] += packed - shipped
        totals["shipped"] += shipped
        key = good.project_key or _project_key(good.project)

        def add(kind, quantity, source, target, when, shipment_id=None, reason=REASON):
            if quantity > 0:
                rows.append({
                    "id": uuid4(), "finished_good_id": good.id, "project_key": key, "item_index": good.item_index,
                    "kind": kind, "quantity": quantity, "from_state": source, "to_state": target,
                    "shipment_id": shipment_id, "actor": ACTOR, "actor_id": None, "occurred_at": when,
                    "reason": reason,
                })

        received = _when(good.received_at, now)
        legacy = f"{REASON}: отгружено до ERP"
        add("accept", before, "production", "accepted", received, reason=legacy)
        add("pack", before, "accepted", "packed", received, reason=legacy)
        add("ship", before, "packed", "shipped", received, reason=legacy)
        add("accept", accepted - before, "production", "accepted", received)
        packed_left, shipped_left = packed - before, shipped - before
        for shipment_id, picked, status, created_at, packed_at, shipped_at in picks.get(good.id, []):
            take = min(picked, packed_left)
            add("pack", take, "accepted", "packed", _when(packed_at, _when(created_at, now)), shipment_id)
            packed_left -= max(take, Decimal(0))
            if status in LEFT_STATUSES:
                gone = min(take, shipped_left)
                add("ship", gone, "packed", "shipped", _when(shipped_at, _when(created_at, now)), shipment_id)
                shipped_left -= max(gone, Decimal(0))
            if take != picked:
                unmatched += 1
        if packed_left > 0 or shipped_left > 0:
            unmatched += 1
        add("pack", packed_left, "accepted", "packed", now, reason=f"{REASON}: без отправки")
        add("ship", shipped_left, "packed", "shipped", now, reason=f"{REASON}: без отправки")
    if rows:
        bind.execute(sa.insert(moves), rows)
    print(
        f"{revision}: КБ с движением ГП — {kbs}; штук: принято складом (на складе) {totals['accepted'].normalize():f}, "
        f"упаковано (не уехало) {totals['packed'].normalize():f}, отгружено {totals['shipped'].normalize():f}; "
        f"ходов журнала записано {len(rows)}; расхождений строк отправок со счётчиками {unmatched}."
    )


def upgrade() -> None:
    bind = op.get_bind()
    broken = _broken(bind)
    if broken:
        raise RuntimeError(
            f"{revision}: у {len(broken)} строк готовой продукции счётчики не по порядку "
            "(принято ≥ упаковано ≥ отгружено ≥ до ERP ≥ 0) — миграция остановлена, поправьте руками: "
            + "; ".join(broken[:20])
        )
    with op.batch_alter_table(GOODS) as batch:
        for name, sql in GOODS_CHECKS.items():
            batch.create_check_constraint(name, sql)
    op.create_table(
        MOVES,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("finished_good_id", sa.Uuid(), sa.ForeignKey("finished_goods.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_key", sa.String(255), nullable=False),
        sa.Column("item_index", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("from_state", sa.String(16), nullable=False),
        sa.Column("to_state", sa.String(16), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), sa.ForeignKey("shipments.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        *(sa.CheckConstraint(sql, name=name) for name, sql in MOVE_CHECKS.items()),
    )
    op.create_index("ix_finished_goods_moves_kb", MOVES, ["project_key", "item_index"])
    op.create_index("ix_finished_goods_moves_finished_good_id", MOVES, ["finished_good_id"])
    op.create_index("ix_finished_goods_moves_shipment_id", MOVES, ["shipment_id"])
    op.create_index("ix_finished_goods_moves_occurred_at", MOVES, ["occurred_at"])
    _backfill(bind)


def downgrade() -> None:
    op.drop_index("ix_finished_goods_moves_occurred_at", table_name=MOVES)
    op.drop_index("ix_finished_goods_moves_shipment_id", table_name=MOVES)
    op.drop_index("ix_finished_goods_moves_finished_good_id", table_name=MOVES)
    op.drop_index("ix_finished_goods_moves_kb", table_name=MOVES)
    op.drop_table(MOVES)
    with op.batch_alter_table(GOODS) as batch:
        for name in GOODS_CHECKS:
            batch.drop_constraint(name, type_="check")

"""Что к чему привязывали: `python -m app.workflows.article_bindings_report [файл.csv]`.

Артикулы на складе ещё не нормализованы, и комплектовщик привязывает партию к позиции КД «на глаз». Каждая такая
привязка пишется в журнал событий (`fulfillment.lot_bound` — позиция заявки, `fulfillment.kit_reserve_held` — ГРН в
спецификации проекта) с артикулом КД, наименованием позиции, ГРН и пакетом, артикулом и наименованием партии и видом
совпадения (`exact` · `fuzzy-<правило>` · `manual`). Команда читает эти события и печатает CSV пар «артикул КД →
артикул на складе» с числом привязок, видом совпадения, последней датой и примерами наименований: по нему склад
нормализуют. Только чтение; база — `ERP_DATABASE_URL`, как у приложения.
"""

from __future__ import annotations

import csv
import io
import os
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.persistence import AuditEventRow

BINDING_EVENTS = ("fulfillment.lot_bound", "fulfillment.kit_reserve_held")
HEADER = [
    "артикул КД", "артикул на складе", "вид совпадения", "привязок", "последняя привязка",
    "пример наименования КД", "пример наименования на складе", "ГРН (примеры)", "кто привязывал",
]


@dataclass
class _Pair:
    count: int = 0
    last: str = ""
    position_name: str = ""
    lot_name: str = ""
    grns: Counter | None = None
    who: Counter | None = None


def collect(rows: Iterable[tuple[dict, object, str]]) -> dict[tuple[str, str, str], _Pair]:
    """Group (details, occurred_at, actor name) of binding events by (КД article, lot article, match kind).
    An event written before the log carried the fields (no `kd_article`) is left out."""
    pairs: dict[tuple[str, str, str], _Pair] = {}
    for details, occurred_at, actor in rows:
        if "kd_article" not in details:
            continue
        key = (str(details.get("kd_article") or ""), str(details.get("lot_article") or ""), str(details.get("match_kind") or ""))
        pair = pairs.setdefault(key, _Pair(grns=Counter(), who=Counter()))
        pair.count += 1
        stamp = occurred_at.strftime("%d.%m.%Y %H:%M") if hasattr(occurred_at, "strftime") else str(occurred_at)
        pair.last = stamp  # rows come oldest first, the last one wins
        pair.position_name = pair.position_name or str(details.get("position_name") or "")
        pair.lot_name = pair.lot_name or str(details.get("lot_name") or "")
        if details.get("lot_grn"):
            pair.grns[str(details["lot_grn"])] += 1
        pair.who[actor] += 1
    return pairs


def to_csv(pairs: dict[tuple[str, str, str], _Pair]) -> str:
    """The pairs, the most bound first; a КД article the warehouse already spells the same (exact) goes last."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(HEADER)
    ordered = sorted(pairs.items(), key=lambda item: (item[0][2] == "exact", -item[1].count, item[0]))
    for (kd, lot, kind), pair in ordered:
        writer.writerow([
            kd, lot, kind, pair.count, pair.last, pair.position_name, pair.lot_name,
            ", ".join(grn for grn, _ in pair.grns.most_common(5)), ", ".join(name for name, _ in pair.who.most_common(3)),
        ])
    return out.getvalue()


def report(session: Session) -> str:
    rows = session.execute(
        select(AuditEventRow.details, AuditEventRow.occurred_at, AuditEventRow.actor_name)
        .where(AuditEventRow.event_type.in_(BINDING_EVENTS))
        .order_by(AuditEventRow.occurred_at)
    ).all()
    return to_csv(collect((details, occurred_at, actor) for details, occurred_at, actor in rows))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import build_engine

    engine = build_engine(url)
    try:
        with Session(engine) as session:
            text = report(session)
    finally:
        engine.dispose()
    if args:
        with open(args[0], "w", encoding="utf-8-sig", newline="") as handle:
            handle.write(text)
        print(f"Записано: {args[0]}")
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""«Кабели»: серии многожильных кабелей наследуют общие значения (owner 12.10.2026).

    ERP_DATABASE_URL=... python -m app.workflows.cable_series --dry-run   # «было → стало» по строкам, без записи
    ERP_DATABASE_URL=... python -m app.workflows.cable_series --apply     # записать одной транзакцией + аудит

Данные — `backend/data/cables/cable-series.json`: у каждого значения свой URL источника, чего в источнике нет, того в файле
нет. Строка БД.ПРОВ относится к серии по началу марки (`aliases`). Пишутся ТОЛЬКО ПУСТЫЕ ячейки: стандарт, изоляция (жилы /
оболочка), экран, напряжение, температура, «Категория» (группа на экране «Кабели» — только у серий, которые классификатор
экрана кладёт не туда), «Производители» (дополняются), а по размеру (число жил × сечение строки) — наружный Ø и AWG.
Использованные URL дописываются в «Источник данных». Строки монтажных проводов (серии из hookup-wires.json) не трогаются.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from app.modules.references.application import UnitOfWork, _audit, _search_text
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.wire_makers import CATEGORY_FIELD, MAKERS_FIELD, STANDARD_FIELD, join_makers, parse_makers
from app.shared.clock import moscow_now
from app.shared.identity import Actor

DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "cables" / "cable-series.json"
SOURCE_FIELD = "Источник данных"
VALUE_CELLS = {  # series key → record cell
    "standard": STANDARD_FIELD, "insulation": "Изоляция", "screen": "Экран", "voltage_v": "Напряжение, В",
    "temp_min_c": "Темп. от, °C", "temp_max_c": "Темп. до, °C", "category": CATEGORY_FIELD,
}
# The «Кабели» groups — a literal copy of CATEGORIES in frontend/src/cableCategory.ts (a test keeps them equal).
CATEGORIES = (
    "Витая пара / Ethernet", "Шлейфы плоские", "Коммуникационные многожильные", "Контрольные / управления",
    "Силовые", "USB / интерфейсные", "Коаксиальные / комбинированные", "Прочие",
)


class SeriesFileError(ValueError):
    pass


def _norm(text: object) -> str:
    return re.sub(r"[\s\-_]+", "", str(text or "")).lower().replace("ё", "е")


def _number(text: object) -> str:
    return str(text or "").strip().replace(".", ",")


def _given(item: object, where: str) -> tuple[str, str] | None:
    """{value, source} → (value, source); null = the source does not have it. A value without a source is refused."""
    if item is None:
        return None
    if not isinstance(item, dict) or not str(item.get("value", "")).strip() or not str(item.get("source", "")).startswith("http"):
        raise SeriesFileError(f"{where}: значение без источника-URL")
    return str(item["value"]).strip(), str(item["source"]).strip()


def load_series(path: Path = DATA_FILE) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("series"), list):
        raise SeriesFileError("нет version 1 или списка series")
    for series in data["series"]:
        key = series.get("key", "?")
        for name in (*VALUE_CELLS, ):
            _given(series.get(name), f"{key}.{name}")
        category = _given(series.get("category"), f"{key}.category")
        if category and category[0] not in CATEGORIES:
            raise SeriesFileError(f"{key}.category: «{category[0]}» — не группа экрана «Кабели»")
        for index, maker in enumerate(series.get("makers") or []):
            _given(maker, f"{key}.makers[{index}]")
        for size in series.get("sizes") or []:
            _given(size.get("outer_dia_mm"), f"{key}.{size.get('cores')}x{size.get('section_mm2')}")
    return data["series"]


def series_of(mark: str, series: list[dict]) -> dict | None:
    """The series whose alias the mark starts with (the longest alias wins; «Li2YCY» is not «LiYCY»)."""
    text = _norm(mark)
    best: tuple[int, dict] | None = None
    for item in series:
        for alias in item.get("aliases") or [item["key"]]:
            key = _norm(alias)
            if text.startswith(key) and (best is None or len(key) > best[0]):
                best = (len(key), item)
    return best[1] if best else None


_LEADING_NUMBER = re.compile(r"\s*(\d+(?:[.,]\d+)?)")


def _same_number(a: object, b: object) -> bool:
    """The leading numbers are equal: «8 (4 пары)» is 8 cores, «0,50» is 0.5."""
    first, second = _LEADING_NUMBER.match(str(a or "")), _LEADING_NUMBER.match(str(b or ""))
    return bool(first and second) and float(first.group(1).replace(",", ".")) == float(second.group(1).replace(",", "."))


def inherited(fields: dict[str, str], item: dict) -> tuple[dict[str, str], list[str]]:
    """Cells to write into the empty ones of the row, and the source URLs they come from."""
    wanted: dict[str, str] = {}
    sources: list[str] = []

    def fill(cell: str, given: tuple[str, str] | None) -> None:
        if given and not str(fields.get(cell) or "").strip():
            wanted[cell] = _number(given[0]) if cell != STANDARD_FIELD and given[0].replace(".", "", 1).isdigit() else given[0]
            sources.append(given[1])

    for name, cell in VALUE_CELLS.items():
        fill(cell, _given(item.get(name), name))
    makers = [_given(maker, "makers") for maker in item.get("makers") or []]
    have = parse_makers(fields.get(MAKERS_FIELD) or "")
    fresh = [given[0] for given in makers if given and given[0].casefold() not in {name.casefold() for name in have}]
    if fresh:
        wanted[MAKERS_FIELD] = join_makers(have + fresh)
        sources += [given[1] for given in makers if given and given[0] in fresh]
    cores, section = str(fields.get("Число жил") or ""), str(fields.get("Сечение, мм2") or "")
    for size in item.get("sizes") or []:
        if _same_number(size.get("cores"), cores) and _same_number(size.get("section_mm2"), section):
            outer = _given(size.get("outer_dia_mm"), "size")
            if outer and not str(fields.get("Наружный диаметр, мм") or "").strip():
                wanted["Наружный диаметр, мм"] = outer[0]
                sources.append(outer[1])
            if size.get("awg") and not str(fields.get("AWG") or "").strip():
                wanted["AWG"] = str(size["awg"])
            break
    return wanted, list(dict.fromkeys(sources))


@dataclass
class Plan:
    rows: list[tuple[TechOperationRecord, dict[str, str]]] = field(default_factory=list)  # (record, new fields)
    seen: dict[str, int] = field(default_factory=dict)  # series key → rows of it in the base
    total: int = 0

    @property
    def empty(self) -> bool:
        return not self.rows


def build_plan(records: list[TechOperationRecord], series: list[dict], hookup_marks: set[str] | None = None) -> Plan:
    plan = Plan(total=len(records))
    for record in records:
        mark = str(record.fields.get("Марка") or record.display_text)
        if hookup_marks and mark in hookup_marks:
            continue
        item = series_of(mark, series)
        if item is None:
            continue
        plan.seen[item["key"]] = plan.seen.get(item["key"], 0) + 1
        wanted, sources = inherited(dict(record.fields), item)
        if not wanted:
            continue
        fields = {**record.fields, **wanted}
        merged = [part for part in str(record.fields.get(SOURCE_FIELD) or "").split("; ") if part]
        merged += [url for url in sources if url not in merged]
        fields[SOURCE_FIELD] = "; ".join(merged)
        plan.rows.append((record, fields))
    return plan


def format_plan(plan: Plan, series: list[dict], *, applied: bool) -> str:
    lines = [f"БД.ПРОВ: записей {plan.total}; строк серий кабелей {sum(plan.seen.values())}, "
             f"получат значения {len(plan.rows)} ({'сделано' if applied else 'будет'})"]
    for item in series:
        lines.append(f"  серия {item['key']}: строк в базе {plan.seen.get(item['key'], 0)}")
    for record, fields in plan.rows:
        changes = ", ".join(f"{name}: «{record.fields.get(name, '')}» → «{value}»" for name, value in fields.items()
                            if name != SOURCE_FIELD and record.fields.get(name, "") != value)
        lines.append(f"  {record.display_text}: {changes}")
    if plan.empty:
        lines.append("Делать нечего: пустых ячеек, которые серия может заполнить, нет.")
    elif not applied:
        lines.append("Проверка без записи. Записать — та же команда с --apply.")
    return "\n".join(lines)


def run(uow: UnitOfWork, actor: Actor, now: datetime, *, apply: bool, series: list[dict] | None = None) -> Plan:
    series = series if series is not None else load_series()
    with uow:
        records = list(uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100_000))
        plan = build_plan(records, series)
        if not apply or plan.empty:
            return plan
        for record, fields in plan.rows:
            updated = replace(record, fields=fields, edited_in_erp=True)
            uow.tech_operations.save(replace(updated, normalized_search=_search_text(updated)))
            _audit("references.cable_series_inherited", actor, now, {
                "record_id": str(record.id), "mark": record.display_text,
                "cells": [name for name, value in fields.items() if record.fields.get(name, "") != value],
            }, uow)
        uow.commit()
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="«Кабели»: серии наследуют общие значения в пустые ячейки строк.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="только напечатать «было → стало» (по умолчанию)")
    mode.add_argument("--apply", action="store_true", help="записать одной транзакцией с аудитом")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import build_engine, build_references_uow_factory

    series = load_series()
    engine = build_engine(url)
    try:
        plan = run(build_references_uow_factory(engine)(), Actor(id=uuid.uuid4(), name="серии кабелей", department=""),
                   moscow_now(), apply=args.apply, series=series)
    finally:
        engine.dispose()
    print(format_plan(plan, series, applied=args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())

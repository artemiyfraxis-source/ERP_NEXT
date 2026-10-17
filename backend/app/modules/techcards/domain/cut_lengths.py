"""Длины резки из расчёта (SUPPLYMAP TcGenInline.html:2270-2290, calcCutLengths_).

Сметчик заполняет лист расчёта КБn строками CUT_WIRE/CUT_COAX: материал
(«comp») и длина («L=…мм» в требовании, иначе колонка L расчёта в метрах).
Диалог сборщика подставляет проводу изделия длину резки по совпадению
материала:

  · точное совпадение — «comp» строки расчёта совпадает целиком с артикулом
    или наименованием провода (без учёта регистра, кавычек и пробелов) —
    всегда в приоритете;
  · мягкое — применяется, только если точного нет ни у одного провода
    набора: каждое слово «comp» входит в «артикул + наименование» провода
    (кириллическая «х», «×» и потерянный при вставке в таблицу символ «�» —
    все читаются как латинскую «x», кавычки отбрасываются), и такой провод
    в наборе ровно один — иначе материал неоднозначен, длину не подставляем.

Провод с разными длинами в расчёте (несколько разных строк резки подошли)
тоже неоднозначен — длину даст чертёж, а не расчёт.

Перенесено из 59e18ff (SUPPLYMAP, DRIFT-59e18ff): владелец переписал
01e7dba — добавил «×» и потерянный при вставке символ «�» к кириллической
«х» в мягком совпадении (сметчик вставлял названия проводов из внешних
таблиц, кодировка ломалась).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

_CUT_CODE = re.compile(r"^CUT_(WIRE|COAX)", re.IGNORECASE)
_STRIP_QUOTES_AND_SPACE = re.compile(r'["«»\s]')
_STRIP_QUOTES = re.compile(r'["«»]')
_LOOSE_X = re.compile(r"[х×�]")  # кириллица «х», «×», replacement character (потеря при вставке)
_LEN_MM = re.compile(r"L\s*=\s*(\d+(?:[.,]\d+)?)\s*мм", re.IGNORECASE)


@dataclass(frozen=True)
class CalcWire:
    """Провод изделия, которому подбирается длина резки."""

    id: str
    art: str = ""
    name: str = ""


@dataclass(frozen=True)
class CalcRow:
    """Строка резки из calcOps листа расчёта КБn."""

    code: str
    comp: str = ""
    req: str = ""
    l_m: object = None  # колонка L расчёта (метры), как в SUPPLYMAP D.calcOps


def _norm(value: str | None) -> str:
    """Точное сравнение: без регистра, кавычек и пробелов."""

    return _STRIP_QUOTES_AND_SPACE.sub("", str(value or "").lower())


def _loose(value: str | None) -> str:
    """Мягкое сравнение: «х»/«×»/«�» → «x», кавычки отбрасываются, пробелы остаются."""

    text = _LOOSE_X.sub("x", str(value or "").lower())
    return _STRIP_QUOTES.sub("", text)


def _words(value: str | None) -> list[str]:
    return [token for token in _loose(value).split() if token]


def _parse_decimal(value: object) -> float | None:
    try:
        return float(str(value or "").replace(",", "."))
    except ValueError:
        return None


def _cut_mm(op: CalcRow) -> float:
    match = _LEN_MM.search(op.req or "")
    if match:
        parsed = _parse_decimal(match.group(1))
        return parsed if parsed is not None else 0.0
    metres = _parse_decimal(op.l_m)
    return round(metres * 1000) if metres and metres > 0 else 0.0


def _exact(wire: CalcWire, op: CalcRow) -> bool:
    keys = {key for key in (_norm(wire.art), _norm(wire.name)) if key}
    return _norm(op.comp) in keys


def _within(wire: CalcWire, op: CalcRow) -> bool:
    haystack = _loose(f"{wire.art} {wire.name}")
    return all(token in haystack for token in _words(op.comp))


def cut_lengths_from_calc(
    wires: Sequence[CalcWire], calc_ops: Sequence[CalcRow]
) -> dict[str, float]:
    """Длина резки (мм) на id провода — только там, где совпадение однозначно."""

    cuts = [op for op in calc_ops if op.comp and _CUT_CODE.match(op.code or "")]
    out: dict[str, float] = {}
    for wire in wires:
        mine = [
            op
            for op in cuts
            if _exact(wire, op)
            or (
                not any(_exact(other, op) for other in wires)
                and _within(wire, op)
                and sum(1 for other in wires if _within(other, op)) == 1
            )
        ]
        lens = {value for value in (_cut_mm(op) for op in mine) if value > 0}
        if len(lens) == 1:
            out[wire.id] = next(iter(lens))
    return out

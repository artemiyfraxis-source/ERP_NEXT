"""Опции стенда (Витя, 05.10.2026): гибридные блоки закупок и склада включаются по одному, чтобы на тестах вернуть
«как было» или принять новое.

State lives in the JSON file named by ERP_STAGE_OPTIONS (the stage sets it; no migration, no table). Without the
variable — production, CI — every option is off and nothing can switch it on, so code behind an option behaves
exactly as before. Unknown keys in the file are ignored (the file is shared by every branch the stage runs)."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StageOption:
    key: str
    block: str
    title: str
    before: str
    after: str


OPTIONS: tuple[StageOption, ...] = (
    StageOption(
        "procurement.today", "Закупки", "«Сегодня» закупщика",
        "Очереди и блоки «Мои позиции» — что делать, решает закупщик сам.",
        "Вкладка «Сегодня»: просрочки, приезжает сегодня/завтра, ждём счёт, ждёт оплаты, карантин — по срочности, со ссылкой на строку.",
    ),
    StageOption(
        "procurement.price_history", "Закупки", "«Покупали ранее»",
        "Цена прошлых закупок не видна.",
        "У строки — последние закупки той же позиции: цена с НДС, поставщик, дата; отклонение текущей цены от прошлой.",
    ),
    StageOption(
        "procurement.kpi", "Закупки", "Показатели закупщика",
        "Показателей нет.",
        "Вкладка «Показатели»: в срок к ожидаемой дате, сроки этапов, экономия к расчётной цене (с НДС) — по закупщику и периоду.",
    ),
    StageOption(
        "warehouse.word_search", "Склад", "Поиск по словам",
        "Поиск — подстрока в артикуле/наименовании.",
        "Слова в любом порядке, каждое ищется в артикуле, наименовании, ГРН, месте, серии, назначении.",
    ),
    StageOption(
        "warehouse.strict_quantities", "Склад", "Количества только движениями",
        "«Правка строки» меняет приход, списано и резерв напрямую.",
        "Приход/списано/резерв в правке строки заблокированы — только через списание, возврат, перемещение (журнал полный).",
    ),
)
BY_KEY = {option.key: option for option in OPTIONS}
ENV = "ERP_STAGE_OPTIONS"
_lock = threading.Lock()


class StageOptionsUnavailable(RuntimeError):
    pass


def _path() -> Path | None:
    raw = os.getenv(ENV, "").strip()
    return Path(raw) if raw else None


def available() -> bool:
    return _path() is not None


def _read() -> dict[str, bool]:
    path = _path()
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: bool(value) for key, value in data.items() if key in BY_KEY} if isinstance(data, dict) else {}


def enabled(key: str) -> bool:
    if key not in BY_KEY:
        raise KeyError(key)
    return _read().get(key, False)


def states() -> dict[str, bool]:
    current = _read()
    return {option.key: current.get(option.key, False) for option in OPTIONS}


def set_enabled(key: str, value: bool) -> dict[str, bool]:
    path = _path()
    if path is None:
        raise StageOptionsUnavailable("Опции переключаются только на стенде")
    if key not in BY_KEY:
        raise KeyError(key)
    with _lock:
        raw: dict = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                raw = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                raw = {}
        raw[key] = bool(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as handle:
            json.dump(raw, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return states()

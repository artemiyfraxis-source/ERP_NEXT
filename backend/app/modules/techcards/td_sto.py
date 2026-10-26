"""СТО ФРАК-001-2026 «Типовые дефекты и критерии приёмки» (backend/data/sto-frak-001-2026.json) и раздел СТО по коду
операции ЭТК — для ссылки «Дефекты и критерии приёмки — по СТО ФРАК-001-2026, разд. 4.3» в комплекте ТД.

Код операции — код карты библиотеки (БД.ОП «Номер»: CUT_WIRE+1strip, PRS_TERMINAL_auto, INS_housing_GX …). Раздел
ищется по правилам сверху вниз, первое совпадение решает; частные правила стоят выше общих префиксов:
- 4.8 «Коаксиальные разъёмы» — монтаж корпуса коаксиала (INS_housing_COAX*), опрессовка крышки коаксиала
  (PRS_CAP_COAX); пайка пинов и корпусов коаксиала —
  4.4.5, обжим гильзы (PRS_SLEEVE_coax) — 4.3, как велит область 4.8;
- 4.4 «Лужение и пайка» — по подразделам: SDR_tin → 4.4.1, SDR_W_W → 4.4.4, SDR_COAX_* → 4.4.5, прочая пайка → 4.4
  (общие дефекты 4.4.2 и особенности разъёмов 4.4.3 печатаются вместе);
- маркировка (INS_MARK*, INS_ПМЛ, LASER_MARK, PRINT_MARK*) → 4.6; корпуса, терминалы, фиксаторы, RJ (INS_housing*,
  INS_terminal*, INS_lock, INS_RJ*) → 4.7; стяжки и гофра (INS_стяжки, INS_гофра*), раскладка на плазе (Раскладка),
  ручная сборка жгута (Handjob) и смотка в бухту (TWIST_BAY) → 4.9; скрутка экрана на станке снятия оболочки
  (TWST_SHILED) — разделка экрана → 4.2; изоляция (HEAT_*, INS_TUT,
  INS_SLEEVE*, INS_WRAP, INS_BRAID, INS_NEYLON, INS_CAP, INS_END_CAP, INS_tape, INS_TAPE*) → 4.5;
- по префиксу: CUT_ → 4.1 (и совмещённые CUT_WIRE+1strip…), STRIP_ → 4.2, PRS_ → 4.3, TWIST/TWST → 4.10,
  MOLD_ / SEAL / PLASM / US_WELD → 4.11, PREP_FOLD → 4.12.
ОТК, упаковка и покупка (OTK_*, BOX, BUY) — не операции изготовления: раздела у них нет, и нормоконтроль о них не
предупреждает (`outside_sto`).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

STO_FILE = Path(__file__).resolve().parents[3] / "data" / "sto-frak-001-2026.json"

_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple((re.compile(pattern), section) for pattern, section in (
    (r"^INS_housing_COAX", "4.8"),
    (r"^PRS_CAP_COAX$", "4.8"),
    (r"^SDR_tin$", "4.4.1"),
    (r"^SDR_W_W$", "4.4.4"),
    (r"^SDR_COAX", "4.4.5"),
    (r"^SDR_", "4.4"),
    (r"^(INS_MARK|INS_ПМЛ$|LASER_MARK$|PRINT_MARK)", "4.6"),
    (r"^(INS_housing|INS_terminal|INS_lock$|INS_RJ)", "4.7"),
    (r"^(INS_стяжки$|INS_гофра|Раскладка$|Handjob$|TWIST_BAY$)", "4.9"),
    (r"^TWST_SHILED$", "4.2"),
    (r"^(HEAT_|INS_TUT$|INS_SLEEVE|INS_WRAP$|INS_BRAID$|INS_NEYLON$|INS_CAP$|INS_END_CAP$|INS_tape$|INS_TAPE)", "4.5"),
    (r"^CUT_", "4.1"),
    (r"^STRIP_", "4.2"),
    (r"^PRS_", "4.3"),
    (r"^(TWIST|TWST_)", "4.10"),
    (r"^(MOLD_|SEAL$|PLASM$|US_WELD$)", "4.11"),
    (r"^PREP_FOLD$", "4.12"),
))
_OUTSIDE_STO = re.compile(r"^(OTK_|BOX$|BUY$)", re.IGNORECASE)


@lru_cache(maxsize=1)
def sto() -> dict[str, Any]:
    """СТО как есть (читается один раз за процесс)."""
    return json.loads(STO_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _titles() -> dict[str, str]:
    found: dict[str, str] = {}

    def walk(sections: list[dict[str, Any]]) -> None:
        for section in sections:
            found[str(section["code"])] = str(section["title"])
            walk(section.get("subsections") or [])

    walk(sto()["sections"])
    return found


@lru_cache(maxsize=1)
def sto_row_codes() -> frozenset[str]:
    """Коды строк СТО («4.3.14»): к ним привязаны рисунки стандартов (std_figures)."""
    found: set[str] = set()

    def walk(sections: list[dict[str, Any]]) -> None:
        for section in sections:
            found.update(str(row["code"]) for row in section.get("rows") or [])
            walk(section.get("subsections") or [])

    walk(sto()["sections"])
    return frozenset(found)


def _section_codes(code: str) -> list[str]:
    """Коды разделов СТО для кода операции по порядку; пусто — раздела нет."""
    code = str(code or "").strip()
    if not code:
        return []
    for pattern, section in _RULES:
        if pattern.search(code):
            found = section
            break
    else:
        return []
    # Совмещённая операция «CUT_WIRE+1crimp+tin» — резка, опрессовка и лужение в одном: даём все разделы СТО.
    return [found, *(part for part, pattern in _COMBINED if pattern.search(code) and part != found)]


def sto_section(code: str) -> dict[str, str] | None:
    """{section, title} раздела СТО для кода операции; None — раздела нет."""
    sections = _section_codes(code)
    if not sections:
        return None
    return {"section": ", ".join(sections), "title": "; ".join(_titles()[part] for part in sections)}


@lru_cache(maxsize=1)
def _section_norms() -> dict[str, list[dict[str, str]]]:
    found: dict[str, list[dict[str, str]]] = {}

    def walk(sections: list[dict[str, Any]]) -> None:
        for section in sections:
            found[str(section["code"])] = list(section.get("norms") or [])
            walk(section.get("subsections") or [])

    walk(sto()["sections"])
    return found


def _clause_items(clauses: str) -> list[str]:
    """«п. 5.5, 5.6 (прил. А, табл. 1)» → [«5.5», «5.6 (прил. А, табл. 1)»]: запятые в скобках не делят."""
    body = re.sub(r"^\s*п\.\s*", "", clauses.strip())
    items, depth, current = [], 0, ""
    for char in body:
        depth += (char == "(") - (char == ")")
        if char == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += char
    return [*items, current.strip()] if current.strip() else items


def sto_norms(code: str) -> list[dict[str, str]]:
    """Нормативные требования раздела(ов) СТО для кода операции: [{doc, clauses, subject}].

    Совмещённые коды склеивают нормы своих разделов (документ — один раз, пункты — по порядку без повторов);
    общая пайка (4.4) — нормы 4.4.2 и 4.4.3."""
    sections = _section_codes(code)
    sources = [part for section in sections for part in (("4.4.2", "4.4.3") if section == "4.4" else (section,))]
    merged: dict[str, dict[str, Any]] = {}
    for source in sources:
        for norm in _section_norms().get(source, []):
            entry = merged.setdefault(norm["doc"], {"doc": norm["doc"], "items": [], "subject": norm.get("subject", "")})
            entry["items"].extend(item for item in _clause_items(norm["clauses"]) if item not in entry["items"])
    return [{"doc": entry["doc"], "clauses": "п. " + ", ".join(entry["items"]), "subject": entry["subject"]} for entry in merged.values()]


_COMBINED = (
    ("4.2", re.compile(r"\+\d*strip", re.IGNORECASE)),
    ("4.3", re.compile(r"\+\d*crimp", re.IGNORECASE)),
    ("4.4.1", re.compile(r"\+tin\b", re.IGNORECASE)),
    ("4.6", re.compile(r"\+\d*mark", re.IGNORECASE)),
)


def outside_sto(code: str) -> bool:
    """ОТК, упаковка, покупка: СТО их не нормирует, отсутствие раздела — не замечание."""
    return bool(_OUTSIDE_STO.search(str(code or "").strip()))


def sto_reference() -> dict[str, str]:
    return {"designation": str(sto()["designation"]), "revision": str(sto()["revision"]), "title": str(sto()["title"])}

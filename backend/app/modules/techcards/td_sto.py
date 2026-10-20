"""СТО ФРАК-001-2026 «Типовые дефекты и критерии приёмки» (backend/data/sto-frak-001-2026.json) и раздел СТО по коду
операции ЭТК — для ссылки «Дефекты и критерии приёмки — по СТО ФРАК-001-2026, разд. 4.3» в комплекте ТД.

Код операции — код карты библиотеки (БД.ОП «Номер»: CUT_WIRE+1strip, PRS_TERMINAL_auto, INS_housing_GX …). Раздел
ищется по правилам сверху вниз, первое совпадение решает; частные правила стоят выше общих префиксов:
- 4.8 «Коаксиальные разъёмы» — монтаж корпуса коаксиала (INS_housing_COAX*), опрессовка крышки коаксиала
  (PRS_CAP_COAX) и коаксиальные сборки целиком (Str-Str, R/A-MHF1, MHF1-MHF1 …); пайка пинов и корпусов коаксиала —
  4.4.5, обжим гильзы (PRS_SLEEVE_coax) — 4.3, как велит область 4.8;
- 4.4 «Лужение и пайка» — по подразделам: SDR_tin → 4.4.1, SDR_W_W → 4.4.4, SDR_COAX_* → 4.4.5, прочая пайка → 4.4
  (общие дефекты 4.4.2 и особенности разъёмов 4.4.3 печатаются вместе);
- маркировка (INS_MARK*, INS_ПМЛ, LASER_MARK, PRINT_MARK) → 4.6; корпуса, терминалы, фиксаторы, RJ (INS_housing*,
  INS_terminal*, INS_lock, INS_RJ*) → 4.7; стяжки и гофра (INS_стяжки, INS_гофра*) → 4.9; изоляция (HEAT_*, INS_TUT,
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
    (r"^(Str|R/A|MHF1)-", "4.8"),
    (r"^SDR_tin$", "4.4.1"),
    (r"^SDR_W_W$", "4.4.4"),
    (r"^SDR_COAX", "4.4.5"),
    (r"^SDR_", "4.4"),
    (r"^(INS_MARK|INS_ПМЛ$|LASER_MARK$|PRINT_MARK$)", "4.6"),
    (r"^(INS_housing|INS_terminal|INS_lock$|INS_RJ)", "4.7"),
    (r"^(INS_стяжки$|INS_гофра)", "4.9"),
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


def sto_section(code: str) -> dict[str, str] | None:
    """{section, title} раздела СТО для кода операции; None — раздела нет."""
    code = str(code or "").strip()
    if not code:
        return None
    for pattern, section in _RULES:
        if pattern.search(code):
            return {"section": section, "title": _titles()[section]}
    return None


def outside_sto(code: str) -> bool:
    """ОТК, упаковка, покупка: СТО их не нормирует, отсутствие раздела — не замечание."""
    return bool(_OUTSIDE_STO.search(str(code or "").strip()))


def sto_reference() -> dict[str, str]:
    return {"designation": str(sto()["designation"]), "revision": str(sto()["revision"])}

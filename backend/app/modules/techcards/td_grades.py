"""Профессия и разряд исполнителя операции комплекта ТД (МК, графы «Проф.» и «Р»; шапка ОК).

Источник — строка БД.ОП по коду операции: поля «Профессия» (opProfession) и «Разряд» (opGrade, целое 1–6). Чего в
БД.ОП нет, берётся по умолчанию по семейству кода (ЕТКС, «Монтажник радиоэлектронной аппаратуры и приборов»):
- 2: резка (CUT_), зачистка (STRIP_), подготовка (PREP_FOLD), стяжки, лента, гофра (INS_стяжки, INS_tape*, INS_гофра*);
- 3: опрессовка (PRS_), монтаж и изоляция (INS_, HEAT_), скрутка (TWIST, TWST_), герметизация, плазма, маркировка
  (SEAL, PLASM, LASER_MARK, PRINT_MARK), пайка (SDR_);
- 4: пайка коаксиала (SDR_COAX*), сборка коаксиала (раздел 4.8 СТО: INS_housing_COAX*, PRS_CAP_COAX, Str-/R/A-/MHF1-),
  ультразвуковая сварка (US_WELD), литьё (MOLD_);
- ОТК (OTK_) — «Контролёр радиоэлектронной аппаратуры и приборов», 4.
Совмещённая операция (CUT_WIRE+1crimp+tin) берёт больший разряд из своих частей: опрессовка и лужение — 3.
Неизвестный код — профессия по умолчанию, разряд пуст (нормоконтроль предупреждает).

Снимок выпуска хранит итог: `snapshot["td_ops"] = {operation_key: {"profession", "grade", "source"}}`, source —
«БД.ОП» (разряд из БД.ОП) или «по умолчанию».
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

FITTER = "Монтажник радиоэлектронной аппаратуры и приборов"
CONTROLLER = "Контролёр радиоэлектронной аппаратуры и приборов"
FROM_REFERENCE = "БД.ОП"
BY_DEFAULT = "по умолчанию"
GRADES = ("1", "2", "3", "4", "5", "6")

_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(pattern, flags), profession, grade) for pattern, flags, profession, grade in (
        (r"^OTK_", re.IGNORECASE, CONTROLLER, "4"),
        (r"^(INS_housing_COAX|PRS_CAP_COAX$|(Str|R/A|MHF1)-)", 0, FITTER, "4"),
        (r"^SDR_COAX", 0, FITTER, "4"),
        (r"^(US_WELD$|MOLD_)", 0, FITTER, "4"),
        (r"^(CUT_|STRIP_|PREP_FOLD$|INS_стяжки$|INS_гофра)", 0, FITTER, "2"),
        (r"^INS_tape", re.IGNORECASE, FITTER, "2"),
        (r"^(PRS_|INS_|HEAT_|TWIST|TWST_|SEAL$|PLASM$|LASER_MARK$|PRINT_MARK$|SDR_)", 0, FITTER, "3"),
    )
)
_COMBINED_THREE = re.compile(r"\+\d*(crimp|tin|mark)\b", re.IGNORECASE)


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def default_grade(code: str) -> tuple[str, str]:
    """(профессия, разряд) по семейству кода операции; разряд "" — семейство неизвестно."""
    code = _text(code)
    for pattern, profession, grade in _RULES:
        if code and pattern.search(code):
            if grade == "2" and _COMBINED_THREE.search(code):
                grade = "3"
            return profession, grade
    return FITTER, ""


def reference_grades(records: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    """Поля строк БД.ОП → {код операции: {profession, grade}}; первая строка кода выигрывает, пустое не пишется."""
    found: dict[str, dict[str, str]] = {}
    for fields in records:
        code = _text(fields.get("opNumber") or fields.get("Номер"))
        if not code or code in found:
            continue
        grade = _text(fields.get("opGrade"))
        found[code] = {
            "profession": _text(fields.get("opProfession")),
            "grade": grade if grade in GRADES else "",
        }
    return found


def operation_grade(code: str, reference: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    """Профессия и разряд одной операции: БД.ОП по коду, недостающее — по умолчанию."""
    profession, grade = default_grade(code)
    row = reference.get(_text(code)) or {}
    own_grade, own_profession = _text(row.get("grade")), _text(row.get("profession"))
    return {
        "profession": own_profession or profession,
        "grade": own_grade or grade,
        "source": FROM_REFERENCE if own_grade else BY_DEFAULT,
    }

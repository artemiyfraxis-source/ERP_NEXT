"""ОБ.019: лазерная маркировка из ЭТК → регистры станка (owner 20.11.2026).

ЭТК-операция резки (`cutWire` / `cutWireStrip`) несёт `fields.laserMark = {mode: "ends"|"full", front, back, middle,
frontMm, backMm, stepMm}`; нет ключа — в ЭТК лазерной маркировки нет, и перенос не трогает регистры маркировки станка.
Режимы: «Без маркировки» (`none`), «По краям» (`ends`), «По всей длине» (`full`). Правила тут чистые, без БД."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal

from app.modules.equipment_setup.recipes import decimal_value
from app.modules.production import machines as rules

WIRE_CUTS = {"cutWire", "cutWireStrip"}
MODES = {"none": "Без маркировки", "ends": "По краям", "full": "По всей длине"}
DEFAULT_MARGIN_MM = Decimal(20)
DEFAULT_STEP_MM = Decimal(100)
MIN_MIDDLE_MM = Decimal(20)  # запас между краевыми метками: короче — метки не лягут
TEXT_KEYS = ("mark_front", "mark_middle", "mark_back")
SERIAL_TOKEN = "{№"


def group_laser_mark(recipe: Mapping) -> dict | None:
    """The `laserMark` of the first wire cut of a frozen cutting recipe; None — the ETK has no laser marking."""
    for op in (recipe or {}).get("operations") or []:
        if op.get("type") in WIRE_CUTS:
            mark = (op.get("fields") or {}).get("laserMark")
            if isinstance(mark, Mapping):
                return dict(mark)
    return None


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _number(raw, default: Decimal, label: str, blocking: list[str]) -> Decimal:
    if raw in (None, ""):
        return default
    value = decimal_value(raw)
    if value is None or value < 0:
        blocking.append(f"{label}: нужно неотрицательное число")
        return default
    return value


def _text(raw, label: str, translate: Callable[[str], str], warnings: list[str], blocking: list[str]) -> str:
    text = " ".join(str(raw or "").split())
    if SERIAL_TOKEN in text:
        blocking.append("Серийные номера на лазер — не поддерживается")
        return ""
    result = translate(text)
    # `translate` cuts at the machine's limit, so the length is measured character by character before the cut.
    full = sum(len(translate(char)) if char != " " else 1 for char in text)
    if full > rules.TEXT_CHARS:
        blocking.append(f"{label}: не длиннее {rules.TEXT_CHARS} знаков (в латинице {full})")
    if result != text:
        warnings.append(f"Текст переведён в латиницу: «{text}» → «{result}»")
    return result


def marking_plan(mark: Mapping | None, length: Decimal, translate: Callable[[str], str]) -> dict | None:
    """→ {mode, source, rows: [{key, label, value}], warnings, blocking}; None — no marking in the ETK.
    A cleared text is a row with value "" (written as zeros, so no rest of an old text stays on the machine)."""
    if mark is None:
        return None
    warnings: list[str] = []
    blocking: list[str] = []
    mode = str(mark.get("mode") or "none").strip().lower()
    if mode not in MODES:
        blocking.append(f"Режим маркировки «{mode}» не поддерживается")
        mode = "none"
    values: list[tuple[str, str]] = [("marking", "Выкл" if mode == "none" else "Вкл")]
    if mode == "none":
        # Disable the previous job completely: an old text must not return when marking is enabled again.
        values += [("mark_by_step", "Выкл"), ("mark_count", "0"), *((key, "") for key in TEXT_KEYS)]
    else:
        front_mm = _number(mark.get("frontMm"), DEFAULT_MARGIN_MM, "Метка у начала", blocking)
        back_mm = _number(mark.get("backMm"), DEFAULT_MARGIN_MM, "Метка у конца", blocking)
        step = _number(mark.get("stepMm"), DEFAULT_STEP_MM, "Шаг средних меток", blocking)
        if mode == "full" and step <= 0:
            blocking.append("Шаг средних меток: нужно положительное число")
        # Only the texts the mode prints are checked; the others are cleared.
        used = ("mark_middle",) if mode == "full" else ("mark_front", "mark_back")
        texts = {key: _text(mark.get(key.removeprefix("mark_")), rules.OB019_FIELDS[key].label, translate, warnings,
                            blocking) for key in used}
        if length < front_mm + back_mm + MIN_MIDDLE_MM:
            warnings.append("Провод короче меток")
        if mode == "ends":
            values += [("mark_by_step", "Выкл"), ("mark_count", "0"), ("mark_front", texts["mark_front"]),
                       ("mark_back", texts["mark_back"]), ("mark_middle", "")]
        else:
            values += [("mark_by_step", "Вкл"), ("mark_step_mm", _plain(step)), ("mark_middle", texts["mark_middle"]),
                       ("mark_front", ""), ("mark_back", ""), ("mark_count", "0")]
        values += [("mark_pos_front_mm", _plain(front_mm)), ("mark_pos_back_mm", _plain(back_mm))]
    # The machine's own limits (range, 0,01 precision) checked here, so the plan shows them instead of a 422 at transfer.
    for key, value in values:
        if key in ("mark_pos_front_mm", "mark_pos_back_mm", "mark_step_mm"):
            try:
                rules.encode_field(key, value)
            except rules.MachineWriteInvalid as error:
                blocking.append(str(error))
    rows = [{"key": key, "label": rules.OB019_FIELDS[key].label, "value": value} for key, value in values]
    return {"mode": mode, "mode_label": MODES[mode], "source": "ЭТК", "rows": rows, "warnings": warnings,
            "blocking": blocking}

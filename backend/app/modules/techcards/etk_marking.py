"""«Маркировка» ЭТК по видам меток (владелец 12.10.2026: «дефекты должны быть в зависимости от выбранного типа
маркировки», «у нас станок для хлястиков есть», «для трубок их надо сначала нарезать»).

Вид метки — по её материалу из таблицы «Метки по КД» генератора: хлястик, термоусадочная или ПВХ-трубка, бирка,
маркировочные кольца, печать на изоляции; остальное — «Прочие метки». Операция insMark собирает ход операции и
типовые дефекты из блоков тех видов, что есть в таблице, — каждый один раз, в порядке первой метки вида. Тексты
блоков — backend/data/etk-marking.json. Оборудование («Оборудование» справочника) и нормы БД.ОП печати хлястиков,
печати и нарезки трубок, усадки кладёт в операцию сборщик конфигурации (workflows/techcard_build.py, `markKit`):
по ним в оснастке встаёт оборудование вида, а во времени операции — свои строки. Без `markKit` (справочник не
загружен) — названия по умолчанию («станок для хлястиков») и строки времени не добавляются.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

CATALOG_FILE = Path(__file__).resolve().parents[3] / "data" / "etk-marking.json"
TYPES: tuple[dict[str, Any], ...] = tuple(json.loads(CATALOG_FILE.read_text(encoding="utf-8"))["types"])
_BY_ID = {item["id"]: item for item in TYPES}
OTHER = "other"

# Порядок важен: «Бирка маркировочная TMS-SCE…» — термоусаживаемая (коэф. усадки 3:1 в БД.МАТ), «Термотрубка» — не ПВХ.
_KIND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("flag", re.compile(r"хляст|флаж|флаш", re.IGNORECASE)),
    # «Маркировка "5" 4/2мм» (713К.4 КБ1): размер «до/после усадки» — термоусадочная трубка (генератор markTubeShrinks).
    ("heat", re.compile(r"термо|\bтут|\bттк|усаж|tms-?sce|\d+(?:[.,]\d+)?\s*/\s*\d+(?:[.,]\d+)?\s*мм", re.IGNORECASE)),
    ("pvc", re.compile(r"кембрик|пвх|трубк", re.IGNORECASE)),
    ("ring", re.compile(r"кольц|колеч", re.IGNORECASE)),
    ("tag", re.compile(r"бирк|этикет|ярлык", re.IGNORECASE)),
    ("print", re.compile(r"печат|на изоляц|лазер", re.IGNORECASE)),
)

# Оборудование вида: ключ `markKit.equipment` → название, когда в справочнике его нет.
EQUIPMENT_DEFAULTS = {
    "flagPrinter": "станок для хлястиков", "tubePrinter": "принтер для трубок",
    "tubeCutter": "станок резки трубок", "heater": "термофен",
}
_TYPE_EQUIPMENT = {
    "flag": {"printer": "flagPrinter"},
    "heat": {"printer": "tubePrinter", "cutter": "tubeCutter", "heater": "heater"},
    "pvc": {"printer": "tubePrinter", "cutter": "tubeCutter"},
}
# Строки времени по видам: ключ нормы `markKit.norms`, название строки, какие виды её дают.
TIME_ROWS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("flagPrint", "Печать хлястиков", frozenset({"flag"})),
    ("tubePrint", "Печать на трубках", frozenset({"heat", "pvc"})),
    ("tubeCut", "Нарезка трубок", frozenset({"heat", "pvc"})),
    ("heat", "Усадка трубок", frozenset({"heat"})),
)


def mark_kind(material: str) -> str:
    """Вид метки по её материалу; не распознан — «Прочие метки»."""
    text = str(material or "")
    return next((kind for kind, pattern in _KIND_PATTERNS if pattern.search(text)), OTHER)


def _amount(value: object) -> Decimal:
    try:
        number = Decimal(str(value or "").strip().replace(",", "."))
    except InvalidOperation:
        return Decimal(0)
    return number if number.is_finite() else Decimal(0)


def _qty(item: Mapping[str, Any]) -> Decimal:
    number = _amount(item.get("qty"))
    return number if number > 0 else Decimal(1)


def _format(value: Decimal, places: int = 2) -> str:
    number = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return (format(number, "f").rstrip("0").rstrip(".") or "0").replace(".", ",")


def _kinds(marks: Sequence[Mapping[str, Any]]) -> dict[str, Decimal]:
    """Виды меток в порядке первой метки вида → штук на изделие."""
    counts: dict[str, Decimal] = {}
    for item in marks:
        kind = mark_kind(str(item.get("name") or item.get("art") or ""))
        counts[kind] = counts.get(kind, Decimal(0)) + _qty(item)
    return counts


def _kit(op: Mapping[str, Any]) -> Mapping[str, Any] | None:
    kit = op.get("markKit")
    return kit if isinstance(kit, Mapping) else None


def _equipment(op: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    kit = _kit(op)
    found = kit.get("equipment", {}).get(key) if kit and isinstance(kit.get("equipment"), Mapping) else None
    return found if isinstance(found, Mapping) and found.get("name") else None


def _split(name: str) -> tuple[str, str]:
    """«ZCUT-RT430/Маркиратор проводов флашковый» (поле «Для базы») → модель и название."""
    model, slash, title = str(name).partition("/")
    return (model.strip(), title.strip()) if slash else ("", model.strip())


def _step_name(op: Mapping[str, Any], key: str) -> str:
    found = _equipment(op, key)
    if not found:
        return EQUIPMENT_DEFAULTS[key]
    model, title = _split(str(found["name"]))
    return f"{model} «{title}»" if model and title else model or title


def _tool_name(name: str) -> str:
    model, title = _split(name)
    return f"{model} | {title}" if model and title else model or title


def _lead(text: str) -> str:
    """Шаг после «Хлястик — »: со строчной, кроме слова капсом («ВИЗУАЛЬНЫЙ КОНТРОЛЬ»)."""
    return text[:1].lower() + text[1:] if len(text) > 1 and text[1].islower() else text


TUBE_KINDS = frozenset({"heat", "pvc"})


def mark_length(item: Mapping[str, Any]) -> Decimal:
    """Длина отрезка трубки, мм (`lengthMm` строки меток); не задана, не число или не больше нуля — 0. Только у трубок."""
    if mark_kind(str(item.get("name") or item.get("art") or "")) not in TUBE_KINDS:
        return Decimal(0)
    number = _amount(item.get("lengthMm"))
    return number if number > 0 else Decimal(0)


# «№ изделия» в надписи (генератор, frontend markSerial.ts): `{№}` или `{№:3}` — номер изделия, его разворачивает
# задание. В шагах ЭТК надпись — шаблоном «{№}», без разрядности.
_SERIAL = re.compile(r"\{№:[1-6]\}")


def _label(item: Mapping[str, Any]) -> str:
    return _SERIAL.sub("{№}", str(item.get("text") or item.get("target") or "").strip()) or "метка"


def _sizes(marks: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """Длины трубок одного вида для шага нарезки: («: X1 — 25 мм, 25 мм — 3 шт. на изделие», допуск). Метки с одной
    длиной в группе из нескольких сводятся в «25 мм — 3 шт. на изделие», одиночные — «X1 — 25 мм». Метки без длины
    оставляют допуск «длина не задана» (с их надписями, если часть длин всё же есть)."""
    groups: dict[Decimal, list[Mapping[str, Any]]] = {}
    unset: list[str] = []
    for item in marks:
        length = mark_length(item)
        if length:
            groups.setdefault(length, []).append(item)
        else:
            unset.append(_label(item))
    if not groups:
        return "", "длина не задана"
    parts = [
        f"{_label(items[0])} — {_format(length)} мм" if len(items) == 1
        else f"{_format(length)} мм — {_format(sum((_qty(item) for item in items), Decimal(0)))} шт. на изделие"
        for length, items in groups.items()
    ]
    tolerance = f"длина не задана: {', '.join(unset)}" if unset else "по КД"
    return ": " + ", ".join(parts), tolerance


def _steps(counts: Mapping[str, Decimal], op: Mapping[str, Any], batch: Decimal,
           marks: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, str]]:
    mixed = len(counts) > 1
    steps: list[dict[str, str]] = []
    for kind, per_item in counts.items():
        block = _BY_ID[kind]
        names = {slot: _step_name(op, key) for slot, key in _TYPE_EQUIPMENT.get(kind, {}).items()}
        sizes, length_tolerance = _sizes([item for item in marks if mark_kind(str(item.get("name") or item.get("art") or "")) == kind])
        for step in block["steps"]:
            text = str(step["text"]).format(count=f"{_format(per_item * batch)} шт.", sizes=sizes, **{
                slot: names.get(slot, "") for slot in ("printer", "cutter", "heater")})
            steps.append({
                "number": str(len(steps) + 1), "kind": step["kind"],
                "text": f"{block['title']} — {_lead(text)}" if mixed else text,
                "tolerance": str(step["tolerance"]).format(length_tolerance=length_tolerance),
            })
    return steps


def _defects(counts: Mapping[str, Decimal]) -> str:
    """Дефекты группами по видам: «Хлястик:» и строки «дефект: причина» (etkOperation.parseDefects)."""
    return "\n".join(
        "\n".join([f"{_BY_ID[kind]['title']}:", *(f"{name}: {cause}" for name, cause in _BY_ID[kind]["defects"])])
        for kind in counts
    )


def _tooling(tooling: Sequence[Mapping[str, Any]], counts: Mapping[str, Decimal], op: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Оснастка карты + оборудование видов в «Оборудование»: станок хлястиков, принтер и резка трубок, усадка только
    у термоусадочной. Нет в справочнике — название по умолчанию с пометкой."""
    result = [dict(tool) for tool in tooling]
    keys = list(dict.fromkeys(key for kind in counts for key in _TYPE_EQUIPMENT.get(kind, {}).values()))
    for key in keys:
        found = _equipment(op, key)
        name = _tool_name(str(found["name"])) if found else EQUIPMENT_DEFAULTS[key]
        if any(str(tool.get("name") or "") == name for tool in result):
            continue
        number = sum(1 for tool in result if tool.get("section") == "Оборудование") + 1
        result.append({
            "section": "Оборудование", "number": str(number), "name": name,
            "inventory_number": str(found.get("inventory") or "") if found else "", "program": "",
            "parameters": "" if found else "нет в справочнике «Оборудование»", "norm": "", "unit": "",
        })
    return result


def _time_rows(counts: Mapping[str, Decimal], op: Mapping[str, Any], batch: Decimal, position: int, code: str,
               warnings: list[str]) -> list[dict[str, str]]:
    kit = _kit(op)
    if kit is None:
        return []
    norms = kit.get("norms") if isinstance(kit.get("norms"), Mapping) else {}
    rows: list[dict[str, str]] = []
    for key, label, kinds in TIME_ROWS:
        pieces = sum((count for kind, count in counts.items() if kind in kinds), Decimal(0))
        if not pieces:
            continue
        norm = norms.get(key) if isinstance(norms.get(key), Mapping) else None
        seconds = _amount(norm.get("operation_seconds")) if norm else Decimal(0)
        if not seconds:
            missing = str(norm.get("code") or "") if norm else ""
            warnings.append(f"Операция {position} ({code}): нет нормы времени «{label}»{f' ({missing})' if missing else ''} — в расчётное время не вошла")
            continue
        minutes = seconds * pieces * batch / 60
        rows.append({"name": label, "norm": _format(minutes)})
    return rows


def apply_marking(operation: dict[str, Any], op: Mapping[str, Any], marks: Sequence[Mapping[str, Any]], batch: Decimal,
                  warnings: list[str]) -> None:
    """ЭТК-операция маркировки по КД: ход операции, дефекты и оборудование по видам меток, строки времени печати,
    нарезки и усадки после строки монтажа."""
    counts = _kinds(marks)
    if not counts:
        return
    card = operation.get("card")
    if isinstance(card, dict):
        card["steps"] = _steps(counts, op, batch, marks)
        card["defects"] = _defects(counts)
        card["tooling"] = _tooling(card.get("tooling") or [], counts, op)
    operation["time"] = [*operation.get("time", []), *_time_rows(
        counts, op, batch, int(operation.get("position") or 0), str(operation.get("code") or ""), warnings)]

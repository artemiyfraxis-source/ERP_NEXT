"""Label templates for the label designer (owner 23.10.2026: «надо переработать шаблон печати, желательно сделать
конструктор»).

A template is a list of elements placed in millimetres on one label format; `render.py` draws it into printer dots and
`defaults.py` holds the built-in template of every kind × format (today's layouts). This module is the contract the
designer screen works against:

- `LabelTemplate {elements: [Element]}`; an element is a field of the label's data, a free text with `{field}`
  placeholders, the QR or the Code128 of the ГРН, a line or a box. Unknown keys are refused, in Russian.
- `FIELDS` — the registry for «lot» (warehouse lot), «intake» (finished-goods receipt) and «instrument» (СИ/accessory).
  Codes carry the ГРН or inventory number, so they take no field.
- `LabelContent` — the values a label is drawn with: `lot_content` / `intake_content` build them from `GrnLabelData`."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .layout import GrnLabelData

TemplateKind = Literal["lot", "intake", "instrument"]
ElementKind = Literal["field", "text", "qr", "code128", "line", "box", "logo"]

TEMPLATE_KINDS: tuple[str, ...] = ("lot", "intake", "instrument")
KIND_LABELS: dict[str, str] = {"lot": "Партия склада", "intake": "Приёмка с производства", "instrument": "СИ и принадлежности"}
# The label formats of `settings.LABEL_SIZES` by key, width × height in mm.
FORMATS: dict[str, tuple[float, float]] = {"58x30": (58, 30), "58x40": (58, 40), "100x50": (100, 50)}
ELEMENTS_MAX = 40
TEXT_MAX = 200
# Floats from a screen: 58.00000001 is still the label's edge.
EPSILON = 1e-6

# key → the Russian name the designer shows. The order is the designer's list order.
FIELDS: dict[str, dict[str, str]] = {
    "lot": {
        "grn": "ГРН",
        "quantity": "Количество",
        "name": "Наименование",
        "article": "Артикул",
        "place": "Место",
        "warehouse": "Склад",
        "place_warehouse": "Место · склад",
        "manufacturer_place": "Производитель · место · склад",
        "received": "Дата поступления",
        "series": "Серия изготовителя",
        "received_series": "Поступление и серия",
        "manufacturer": "Производитель",
        "purpose": "Назначение",
    },
    "intake": {
        "grn": "ГРН ГП",
        "quantity": "Количество",
        "name": "Изделие",
        "project": "Проект",
        "item_index": "КБ",
        "project_item": "Проект и КБ",
        "place": "Место",
        "warehouse": "Склад",
        "place_warehouse": "Место · склад",
        "received": "Дата приёмки",
    },
    "instrument": {
        "connector_line": "Разъёмы / краткое название",
        "range_line": "Частотный диапазон",
        "table_bands": "Таблица: диапазоны, ГГц",
        "table_vswr": "Таблица: КСВН по диапазонам",
        "table_loss": "Таблица: потери по диапазонам",
        "parameters_line": "Частотный диапазон (прежнее поле)",
        "loss_line": "Потери",
        "measurement_date": "Дата замера",
        "impedance_warning": "Предупреждение о сопротивлении",
        "footer_line": "Поверка / сопротивление",
        "inventory_number": "Инвентарный номер",
        "manufacturer": "Производитель",
        "manufacturer_line": "Изготовитель: …",
        "type_line": "Вид изделия (ПЕРЕХОД КОАКСИАЛЬНЫЙ)",
        "model_line": "Модель / артикул",
        "serial_full": "Заводской № …",
        "date_full": "Измерение: дата",
        "date_short": "Изм. дата",
        "next_short": "Контроль до",
        "table_bands_long": "Таблица (полная): частоты",
        "table_vswr_long": "Таблица (полная): КСВН",
        "table_loss_long": "Таблица (полная): вносимые потери",
        "info_1": "Строка под разъёмами 1 (зав. №, изготовитель, ресурс, поверка — по порядку)",
        "info_2": "Строка под разъёмами 2",
        "info_3": "Строка под разъёмами 3",
        "resource_line": "Ресурс сочленений и температура",
        "dates_line": "Изм. и контроль одной строкой",
        "table_band_head": "Сетка: «Частота до, ГГц»",
        "table_vswr_head": "Сетка: «КСВН ≤»",
        "table_loss_head": "Сетка: «Потери, дБ ≤»",
        **{f"table_band_{n}": f"Сетка: частота, столбец {n}" for n in range(1, 6)},
        **{f"table_vswr_{n}": f"Сетка: КСВН, столбец {n}" for n in range(1, 6)},
        **{f"table_loss_{n}": f"Сетка: потери, столбец {n}" for n in range(1, 6)},
        "name": "Разъёмы / наименование",
        "model": "Модель",
        "serial_number": "Серийный номер",
        "verification_due": "Поверка до",
    },
}

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def format_key(width_mm: float, height_mm: float) -> str:
    """58 × 40 → «58x40» (the settings hold only the formats of `FORMATS`)."""
    return f"{width_mm:g}x{height_mm:g}"


class TemplateError(ValueError):
    """A template the designer sent is not valid; the message is Russian and goes to the screen as is."""


class Element(BaseModel):
    """One thing on the label. Coordinates are the element's box from the label's top left corner, mm."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=40)
    kind: ElementKind
    field: str | None = None  # kind «field»: a key of FIELDS[kind]
    text: str | None = Field(default=None, max_length=TEXT_MAX)  # kind «text»: may carry {field} placeholders
    x_mm: float = Field(ge=0, allow_inf_nan=False)
    y_mm: float = Field(ge=0, allow_inf_nan=False)
    w_mm: float = Field(ge=0, allow_inf_nan=False)
    h_mm: float = Field(ge=0, allow_inf_nan=False)
    # Capital-letter height, mm (the size a ruler measures on the printed label).
    font_mm: float = Field(default=3.0, ge=1.0, le=20.0, allow_inf_nan=False)
    bold: bool = False
    align: Literal["left", "center", "right"] = "left"
    max_lines: int = Field(default=1, ge=1, le=4)
    # «shrink» — the type shrinks down to half its size to fit, then the last line ends with «…»; «ellipsis» — the size
    # stays and the text is cut with «…».
    fit: Literal["shrink", "ellipsis"] = "shrink"
    mono: bool = False  # monospace (the ГРН)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("id не может быть пустым")
        return value

    @model_validator(mode="after")
    def _shape(self) -> Element:
        if self.kind == "field" and not (self.field or "").strip():
            raise ValueError("у элемента «поле» не выбрано поле")
        if self.kind == "text" and not (self.text or "").strip():
            raise ValueError("у элемента «текст» пустой текст")
        if self.kind == "line":
            if self.w_mm <= 0 and self.h_mm <= 0:
                raise ValueError("у линии нулевая длина")
        elif self.w_mm <= 0 or self.h_mm <= 0:
            raise ValueError("ширина и высота должны быть больше нуля")
        return self


class LabelTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elements: list[Element] = Field(min_length=1, max_length=ELEMENTS_MAX)

    @model_validator(mode="after")
    def _unique_ids(self) -> LabelTemplate:
        seen: set[str] = set()
        for element in self.elements:
            if element.id in seen:
                raise ValueError(f"id «{element.id}» повторяется")
            seen.add(element.id)
        return self


_NAMES = {
    "elements": "элементы", "id": "id", "kind": "тип", "field": "поле", "text": "текст", "x_mm": "X", "y_mm": "Y",
    "w_mm": "ширина", "h_mm": "высота", "font_mm": "высота букв", "bold": "жирный", "align": "выравнивание",
    "max_lines": "строк", "fit": "вписывание", "mono": "моноширинный",
}


def _number(value: Any) -> str:
    return format(value, "g").replace(".", ",") if isinstance(value, float) else str(value)


def _message(error: dict) -> str:
    kind, context = error["type"], error.get("ctx") or {}
    if kind == "extra_forbidden":
        return "неизвестное поле"
    if kind == "missing":
        return "не задано"
    if kind == "literal_error":
        allowed = re.findall(r"'([^']*)'", str(context.get("expected", "")))
        return "недопустимое значение" + (f" (допустимо: {', '.join(allowed)})" if allowed else "")
    if kind in ("greater_than_equal", "greater_than"):
        return f"не меньше {_number(context.get('ge', context.get('gt')))}"
    if kind in ("less_than_equal", "less_than"):
        return f"не больше {_number(context.get('le', context.get('lt')))}"
    if kind == "string_too_short":
        return "не может быть пустым"
    if kind == "string_too_long":
        return f"не длиннее {context.get('max_length')} знаков"
    if kind == "too_short":
        return "нет ни одного элемента"
    if kind == "too_long":
        return f"не больше {context.get('max_length')} элементов"
    if kind == "finite_number":
        return "должно быть конечным числом"
    if kind in ("value_error", "assertion_error"):
        return str(context.get("error", error.get("msg", "")))
    if kind == "model_type" or kind == "dict_type":
        return "ожидается объект"
    if kind == "list_type":
        return "ожидается список"
    if kind.endswith("_type") or kind.endswith("_parsing"):
        return "неверный тип значения"
    return "недопустимое значение"


def _where(location: tuple, raw: Any) -> str:
    parts: list[str] = []
    index = 0
    while index < len(location):
        key = location[index]
        if key == "elements" and index + 1 < len(location) and isinstance(location[index + 1], int):
            number = location[index + 1]
            ident = ""
            try:
                ident = str(raw["elements"][number].get("id") or "")
            except (KeyError, IndexError, TypeError, AttributeError):
                pass
            parts.append(f"элемент {number + 1}" + (f" «{ident}»" if ident else ""))
            index += 2
            continue
        parts.append(f"«{_NAMES.get(str(key), str(key))}»")
        index += 1
    return ", ".join(parts)


def _russian(error: ValidationError, raw: Any) -> str:
    messages = []
    for item in error.errors()[:5]:
        where = _where(tuple(item.get("loc", ())), raw)
        messages.append(f"{where}: {_message(item)}" if where else _message(item))
    return "Шаблон этикетки — " + "; ".join(messages)


def check_template(template: LabelTemplate, kind: str, label_format: str) -> LabelTemplate:
    """What only the kind and the format know: the fields exist for this kind, every box lies on the label."""
    if kind not in FIELDS:
        raise TemplateError(f"Нет такого вида этикетки: «{kind}»")
    if label_format not in FORMATS:
        raise TemplateError(f"Нет такого формата этикетки: «{label_format}»")
    fields = FIELDS[kind]
    width, height = FORMATS[label_format]
    for number, element in enumerate(template.elements, start=1):
        name = f"Элемент {number} «{element.id}»"
        if element.kind == "field" and element.field not in fields:
            raise TemplateError(f"{name}: у этикетки «{KIND_LABELS[kind]}» нет поля «{element.field}»")
        if element.kind == "text":
            unknown = [key for key in PLACEHOLDER.findall(element.text or "") if key not in fields]
            if unknown:
                raise TemplateError(f"{name}: в тексте неизвестное поле {{{unknown[0]}}}")
        if element.x_mm + element.w_mm > width + EPSILON or element.y_mm + element.h_mm > height + EPSILON:
            raise TemplateError(f"{name} выходит за край этикетки {width:g} × {height:g} мм")
    return template


def parse_template(raw: Any, kind: str, label_format: str) -> LabelTemplate:
    """The designer's JSON → a checked template. TemplateError (Russian) — not a valid template for this kind/format."""
    try:
        template = LabelTemplate.model_validate(raw)
    except ValidationError as error:
        raise TemplateError(_russian(error, raw)) from error
    return check_template(template, kind, label_format)


@dataclass(frozen=True)
class LabelContent:
    """What one label is drawn with: its kind, what the codes carry (the ГРН) and the text of every field."""

    kind: str
    code: str
    values: Mapping[str, str]

    def value(self, key: str) -> str:
        return self.values.get(key, "")

    def substitute(self, text: str) -> str:
        return PLACEHOLDER.sub(lambda match: self.values.get(match.group(1), ""), text)


def _received(data: GrnLabelData) -> str:
    return f"поступило {data.received_on.strftime('%d.%m.%Y')}" if data.received_on else ""


def _common(data: GrnLabelData) -> dict[str, str]:
    return {
        "grn": data.grn,
        "quantity": f"{data.quantity}{f' {data.unit}' if data.unit else ''}",
        "name": data.item_name,
        "place": data.place,
        "warehouse": data.warehouse,
        "place_warehouse": " · ".join(part for part in (data.place, data.warehouse) if part),
        "received": _received(data),
    }


def _place(data: GrnLabelData) -> str:
    return " · ".join(part for part in (data.place, data.warehouse) if part)


def lot_content(data: GrnLabelData) -> LabelContent:
    """A warehouse lot. The article is left out only when it is the name itself (it is already printed)."""
    series = f"серия {data.manufacturer_batch}" if data.manufacturer_batch else ""
    values = _common(data) | {
        "article": data.sku if data.sku and data.sku != data.item_name else "",
        "series": series,
        "received_series": " · ".join(part for part in (_received(data), series) if part),
        "manufacturer": data.manufacturer,
        "manufacturer_place": " · ".join(part for part in (data.manufacturer, _place(data)) if part),
        "purpose": data.purpose,
    }
    return LabelContent("lot", data.grn, values)


def intake_content(data: GrnLabelData, project: str, item_index: str) -> LabelContent:
    """An act of «Приёмка с производства»: «проект КБ» rides where a lot has its article."""
    values = _common(data) | {
        "project": project,
        "item_index": item_index,
        "project_item": " ".join(part for part in (project, item_index) if part),
    }
    return LabelContent("intake", data.grn, values)


GRID_COLUMNS = 5  # the band grid of the 58 mm instrument label: 1 / 3 / 6 / 12 / 18 GHz


def grid_bands(bands: tuple[tuple[str, str, str], ...]) -> tuple[tuple[str, str, str], ...]:
    """At most GRID_COLUMNS bands: more keep the first ones and the last (the full range stays on the label)."""
    bands = tuple(bands)
    return bands if len(bands) <= GRID_COLUMNS else bands[:GRID_COLUMNS - 1] + bands[-1:]


def _celsius(value: float) -> str:
    text = format(abs(value), "g").replace(".", ",")
    return "0" if value == 0 else ("−" if value < 0 else "+") + text


def resource_line(mating_resource: int | None = None, mating_count: int | None = None,
                  temp_min_c: float | None = None, temp_max_c: float | None = None) -> str:
    """«Ресурс 3000 сочл. · −60…+85 °C»; with a mating counter «Ресурс: осталось N из 3000»; "" — both unknown."""
    parts = []
    if mating_resource:
        parts.append(f"Ресурс: осталось {max(0, mating_resource - mating_count)} из {mating_resource}"
                     if mating_count is not None else f"Ресурс {mating_resource} сочл.")
    elif mating_count is not None:
        parts.append(f"Сочленений {mating_count}")
    if temp_min_c is not None and temp_max_c is not None:
        parts.append(f"{_celsius(temp_min_c)}…{_celsius(temp_max_c)} °C")
    return " · ".join(parts)


def instrument_content(inventory_number: str, name: str, serial_number: str = "", verification_due: date | None = None,
                       *, model: str = "", accessory: bool = False, in_house_serial: bool = False,
                       connector_line: str = "", parameters_line: str = "", loss_line: str = "",
                       measurement_date: str = "", impedance_warning: str = "", card_url: str = "",
                       range_line: str = "", table: tuple[str, str, str] = ("", "", ""),
                       manufacturer: str = "", type_line: str = "", model_line: str = "",
                       measured_on: date | None = None, next_control: date | None = None,
                       table_long: tuple[str, str, str] = ("", "", ""),
                       bands: tuple[tuple[str, str, str], ...] = (),
                       mating_resource: int | None = None, mating_count: int | None = None,
                       temp_min_c: float | None = None, temp_max_c: float | None = None,
                       control_short: str = "", control_line: str = "") -> LabelContent:
    """An instrument card's printable values; codes carry the inventory number even without an ERP connection.

    The caller supplies the domain's classification to keep serial-number policy outside Warehouse. `bands` —
    (frequency up to, VSWR ≤, loss ≤) per band, already formatted; `mating_count` — matings done so far;
    `control_short`/`control_line` — the next control by date or matings, whichever first (Production decides).
    """
    serial = serial_number.strip()
    values = {
        "inventory_number": inventory_number.strip(),
        "name": name.strip(),
        "model": model.strip(),
        "manufacturer": manufacturer.strip(),
        "manufacturer_line": f"Изготовитель: {manufacturer.strip()}" if manufacturer.strip() else "",
        "type_line": type_line.strip().upper(),
        "model_line": model_line.strip() or model.strip(),
        "serial_full": f"Заводской № {serial}" if serial else "",
        "date_full": " · ".join(filter(None, (f"Измерение: {measured_on:%d.%m.%Y}" if measured_on else "",
                                              control_line or (f"следующий контроль: {next_control:%d.%m.%Y}"
                                                               if next_control else "")))),
        "date_short": f"Изм. {measured_on:%d.%m.%Y}" if measured_on else "",
        "next_short": control_short or (f"Контроль {next_control:%d.%m.%Y}" if next_control else ""),
        "table_bands_long": table_long[0],
        "table_vswr_long": table_long[1],
        "table_loss_long": table_long[2],
        "serial_number": (f"№ {serial}" if in_house_serial else f"Зав. № {serial}") if serial else "",
        "verification_due": f"Поверка до {verification_due:%d.%m.%Y}" if verification_due and not accessory else "",
        "connector_line": connector_line or name.strip(),
        "parameters_line": parameters_line or ("Не измерен" if accessory else ""),
        "loss_line": loss_line,
        "range_line": range_line or parameters_line or ("Не измерен" if accessory else ""),
        "table_bands": table[0],
        "table_vswr": table[1],
        "table_loss": table[2],
        "measurement_date": measurement_date,
        "impedance_warning": impedance_warning,
    }
    values["footer_line"] = " · ".join(filter(None, (values["verification_due"], impedance_warning)))
    values["resource_line"] = resource_line(mating_resource, mating_count, temp_min_c, temp_max_c)
    # The 58 mm card's three lines under the connectors, no empty rows: serial, maker, resource, verification/impedance
    # in this order; when all four are known the maker gives way (the least needed at the bench).
    lines = {"serial": values["serial_number"], "maker": values["manufacturer_line"],
             "resource": values["resource_line"], "footer": values["footer_line"]}
    kept = [key for key in ("serial", "footer", "resource", "maker") if lines[key]][:3]
    info = [lines[key] for key in ("serial", "maker", "resource", "footer") if key in kept]
    values.update({f"info_{n}": (info[n - 1] if n <= len(info) else "") for n in range(1, 4)})
    cells = grid_bands(bands)
    values["dates_line"] = " · ".join(filter(None, (values["date_short"], values["next_short"])))
    if not cells and values["range_line"]:
        # No measured bands: the grid stays empty, the state («Не измерен», «Не годен…») goes on the dates line.
        values["dates_line"] = " · ".join(filter(None, (values["date_short"], values["range_line"])))
    values.update({"table_band_head": "Частота до, ГГц" if cells else "", "table_vswr_head": "КСВН ≤" if cells else "",
                   "table_loss_head": "Потери, дБ ≤" if cells else ""})
    first = GRID_COLUMNS - len(cells) + 1  # packed to the right: the numbers keep their columns under the header
    for n in range(1, GRID_COLUMNS + 1):
        band = cells[n - first] if n >= first else ("", "", "")
        values.update({f"table_band_{n}": band[0], f"table_vswr_{n}": band[1], f"table_loss_{n}": band[2]})
    return LabelContent("instrument", card_url or values["inventory_number"], values)

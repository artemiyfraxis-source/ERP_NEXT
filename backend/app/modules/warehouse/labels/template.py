"""Label templates for the label designer (owner 23.10.2026: «надо переработать шаблон печати, желательно сделать
конструктор»).

A template is a list of elements placed in millimetres on one label format; `render.py` draws it into printer dots and
`defaults.py` holds the built-in template of every kind × format (today's layouts). This module is the contract the
designer screen works against:

- `LabelTemplate {elements: [Element]}`; an element is a field of the label's data, a free text with `{field}`
  placeholders, the QR or the Code128 of the ГРН, a line or a box. Unknown keys are refused, in Russian.
- `FIELDS` — the one registry of what each kind of label can show: «lot» (a warehouse lot) and «intake» (an act of
  «Приёмка с производства», ГРН «ГП.YY.NNNNNNN»). The codes always carry the ГРН, so they take no field.
- `LabelContent` — the values a label is drawn with: `lot_content` / `intake_content` build them from `GrnLabelData`."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .layout import GrnLabelData

TemplateKind = Literal["lot", "intake"]
ElementKind = Literal["field", "text", "qr", "code128", "line", "box"]

TEMPLATE_KINDS: tuple[str, ...] = ("lot", "intake")
KIND_LABELS: dict[str, str] = {"lot": "Партия склада", "intake": "Приёмка с производства"}
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


def lot_content(data: GrnLabelData) -> LabelContent:
    """A warehouse lot. The article is left out when the name already carries it (names made from price books)."""
    series = f"серия {data.manufacturer_batch}" if data.manufacturer_batch else ""
    values = _common(data) | {
        "article": data.sku if data.sku and data.sku not in data.item_name else "",
        "series": series,
        "received_series": " · ".join(part for part in (_received(data), series) if part),
        "manufacturer": data.manufacturer,
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

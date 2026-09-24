"""Lossless SUPPLYMAP operation-card templates with parsed execution semantics."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Sequence


SPECIAL_CODES = {
    "PRS_CAP | Опрессовка крышки коакс": "PRS_CAP_COAX",
    "INS_MARK | Монтаж маркировки": "INS_MARK_HARNESS",
}
SIDE_PATTERN = re.compile(r"\s*\(([AА]|[BВ])\)$")
TOOL_COLUMNS = {
    "Инструмент": {"number": 1, "name": 2, "inventory_number": 6},
    "Матрицы": {"number": 1, "name": 2, "inventory_number": 6},
    "Оснастка": {"number": 1, "name": 2, "inventory_number": 6},
    "Оборудование": {"number": 1, "name": 2, "inventory_number": 5, "program": 6},
    "Расходники": {"number": 1, "name": 2, "parameters": 3, "norm": 4, "unit": 6},
}


def _cell(rows: Sequence[Sequence[object]], row: int, column: int) -> str:
    if row >= len(rows) or column >= len(rows[row]):
        return ""
    value = rows[row][column]
    return "" if value is None else str(value).strip()


@dataclass(frozen=True)
class OperationCardStep:
    number: str
    kind: str
    text: str
    tolerance: str


@dataclass(frozen=True)
class OperationCardTool:
    section: str
    number: str
    name: str
    inventory_number: str = ""
    program: str = ""
    parameters: str = ""
    norm: str = ""
    unit: str = ""


@dataclass(frozen=True)
class OperationCardTemplate:
    source_id: str
    code: str
    side: str
    title: str
    name: str
    category: str
    designation: str
    revision: str
    safety: str
    defects: str
    steps: tuple[OperationCardStep, ...]
    tooling: tuple[OperationCardTool, ...]
    raw_rows: tuple[tuple[str, ...], ...]

    def document(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "title": self.title,
            "name": self.name,
            "category": self.category,
            "designation": self.designation,
            "revision": self.revision,
            "safety": self.safety,
            "defects": self.defects,
            "steps": [step.__dict__ for step in self.steps],
            "tooling": [tool.__dict__ for tool in self.tooling],
        }


def parse_operation_template(
    *, source_id: str, title: str, category: str, rows: Sequence[Sequence[object]],
) -> OperationCardTemplate:
    clean_id = str(source_id or "").strip()
    clean_title = str(title or _cell(rows, 0, 0)).strip()
    if not clean_id or not clean_title or not rows:
        raise ValueError("У шаблона техкарты нет id, заголовка или строк")
    side_match = SIDE_PATTERN.search(clean_title)
    side = ""
    if side_match:
        side = "A" if side_match.group(1) in {"A", "А"} else "B"
    base_title = SIDE_PATTERN.sub("", clean_title).strip()
    code = SPECIAL_CODES.get(base_title, base_title.split(" | ", 1)[0].strip())
    name = base_title.split(" | ", 1)[1].strip() if " | " in base_title else ""
    if not code:
        raise ValueError("В заголовке шаблона нет кода операции")

    safety = ""
    defects = ""
    steps = []
    tooling = []
    for row_index, row in enumerate(rows):
        for column in range(len(row)):
            value = _cell(rows, row_index, column)
            if not safety and re.match(r"^ВАЖНО!\s*ТРЕБОВАНИЕ БЕЗОПАСНОСТИ", value, re.IGNORECASE):
                safety = value
            if not defects and re.match(r"^ТИПОВЫЕ ДЕФЕКТЫ", value, re.IGNORECASE):
                defects = value
        section = _cell(rows, row_index, 0)
        if section in {"Операция", "Контроль"}:
            text = _cell(rows, row_index, 2)
            if text:
                steps.append(OperationCardStep(
                    number=_cell(rows, row_index, 1),
                    kind=section,
                    text=text,
                    tolerance=_cell(rows, row_index, 6),
                ))
        columns = TOOL_COLUMNS.get(section)
        if columns:
            values = {key: _cell(rows, row_index, column) for key, column in columns.items()}
            if values.get("name"):
                tooling.append(OperationCardTool(section=section, **values))
    if not steps:
        raise ValueError(f"В шаблоне {clean_title} нет шагов операции")

    raw_rows = tuple(tuple("" if cell is None else str(cell) for cell in row) for row in rows)
    return OperationCardTemplate(
        source_id=clean_id,
        code=code,
        side=side,
        title=clean_title,
        name=name,
        category=str(category or "").strip(),
        designation=_cell(rows, 2, 2),
        revision=_cell(rows, 2, 5),
        safety=safety,
        defects=defects,
        steps=tuple(steps),
        tooling=tuple(tooling),
        raw_rows=raw_rows,
    )


def parse_operation_library(items: Sequence[Mapping[str, object]]) -> tuple[OperationCardTemplate, ...]:
    ids = [str(item.get("id") or "").strip() for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("В библиотеке есть повторяющийся id шаблона")
    cards = []
    for item in items:
        rows = item.get("rows")
        if not isinstance(rows, list) or not all(isinstance(row, list) for row in rows):
            raise ValueError("Строки шаблона должны быть матрицей")
        cards.append(parse_operation_template(
            source_id=str(item.get("id") or ""),
            title=str(item.get("title") or ""),
            category=str(item.get("category") or ""),
            rows=rows,
        ))
    return tuple(cards)

"""Lossless SUPPLYMAP operation-card templates with parsed execution semantics."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
from typing import Mapping, Sequence


SPECIAL_CODES = {
    "PRS_CAP | Опрессовка крышки коакс": "PRS_CAP_COAX",
    "INS_MARK | Монтаж маркировки": "INS_MARK_HARNESS",
}
SIDE_PATTERN = re.compile(r"\s*\(([AА]|[BВ])\)$")
# Плейсхолдер ревизии из шаблона («‹рев.›») — не значение (SUPPLYMAP TcRecord.gs tcRecordNormalize_,
# TcRecordDb.gs tcRecordDbRows_): пишем пусто вместо него.
REVISION_PLACEHOLDER_PATTERN = re.compile(r"^‹.*›$")
TOOL_COLUMNS = {
    "Инструмент": {"number": 1, "name": 2, "inventory_number": 6},
    "Матрицы": {"number": 1, "name": 2, "inventory_number": 6},
    "Оснастка": {"number": 1, "name": 2, "inventory_number": 6},
    "Оборудование": {"number": 1, "name": 2, "inventory_number": 5, "program": 6},
    "Расходники": {"number": 1, "name": 2, "parameters": 3, "norm": 4, "unit": 6},
}
# Где стороны блока (A)/(B) расходятся по содержанию — чья сторона владеет записью
# (SUPPLYMAP TcRecord.gs TC_REC_SIDE_PICK_, решение владельца 17.09).
RECORD_SIDE_PICK = {
    "SDR_COAX_housing": "B",
    "SDR_COAX_pin": "A",
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
class OperationCardImage:
    """A picture anchored over the template grid (SUPPLYMAP TcTemplates.gs tcLibraryBlocks_).

    SUPPLYMAP keeps the actual file in a Google Drive cache; ERP has no such storage yet, so only
    the placement/reference metadata is carried through (решено агентом — проверить: revisit once
    ERP has its own file storage for template images)."""

    row: int
    col: int
    drive_file_id: str
    width: float | int | None = None
    height: float | int | None = None


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
    images: tuple[OperationCardImage, ...] = ()

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
            "images": [image.__dict__ for image in self.images],
        }


def _parse_images(images: object) -> tuple[OperationCardImage, ...]:
    if not isinstance(images, Sequence) or isinstance(images, (str, bytes)):
        return ()
    result = []
    for item in images:
        if not isinstance(item, Mapping):
            continue
        result.append(OperationCardImage(
            row=int(item.get("row") or 0),
            col=int(item.get("col") or 0),
            drive_file_id=str(item.get("driveFileId") or "").strip(),
            width=item.get("width"),
            height=item.get("height"),
        ))
    return tuple(result)


def parse_operation_template(
    *, source_id: str, title: str, category: str, rows: Sequence[Sequence[object]],
    images: object = None,
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
        revision="" if REVISION_PLACEHOLDER_PATTERN.match(_cell(rows, 2, 5)) else _cell(rows, 2, 5),
        safety=safety,
        defects=defects,
        steps=tuple(steps),
        tooling=tuple(tooling),
        raw_rows=raw_rows,
        images=_parse_images(images),
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
            images=item.get("images"),
            rows=rows,
        ))
    return tuple(cards)


@dataclass(frozen=True)
class OperationCardRecord:
    """One БД.ОП-style record — a template's (A)/(B) block pair resolved to a single code
    (SUPPLYMAP TcRecord.gs tcRecordsByCode_, used both at first transfer and by
    tcRecordResyncFromTemplate since commit e272b7d)."""

    code: str
    name: str
    category: str
    designation: str
    revision: str
    safety: str
    defects: str
    steps: tuple[OperationCardStep, ...]
    tooling: tuple[OperationCardTool, ...]
    images: tuple[OperationCardImage, ...]
    sides: tuple[str, ...]


def _record_of(card: OperationCardTemplate) -> OperationCardRecord:
    return OperationCardRecord(
        code=card.code, name=card.name, category=card.category, designation=card.designation,
        revision=card.revision, safety=card.safety, defects=card.defects,
        steps=card.steps, tooling=card.tooling, images=card.images,
        sides=(card.side,) if card.side else (),
    )


def _record_signature(record: OperationCardRecord) -> str:
    """Content used to tell whether two (A)/(B) blocks of the same code actually agree
    (SUPPLYMAP TcRecord.gs tcRecContent_, restricted to fields a template block carries)."""
    return json.dumps({
        "designation": record.designation,
        "revision": record.revision,
        "safety": record.safety,
        "defects": record.defects,
        "steps": [step.__dict__ for step in record.steps],
        "tooling": [tool.__dict__ for tool in record.tooling],
    }, ensure_ascii=False, sort_keys=True)


def build_operation_records(cards: Sequence[OperationCardTemplate]) -> tuple[OperationCardRecord, ...]:
    """Group (A)/(B) blocks of the same operation code into one record (SUPPLYMAP TcRecord.gs
    tcRecordsByCode_). A code without a side, or with only one side present, passes through
    unchanged. Two blocks under one code with identical content merge silently; if they
    disagree, RECORD_SIDE_PICK must say whose side owns the record, otherwise this raises —
    same as GAS refusing a silent pick."""
    by_code: dict[str, OperationCardRecord] = {}
    for card in cards:
        record = _record_of(card)
        previous = by_code.get(card.code)
        if previous is None:
            by_code[card.code] = record
            continue
        if not card.side or not previous.sides:
            raise ValueError(f"Два шаблона под кодом {card.code} без сторон (A)/(B)")
        combined_sides = previous.sides + record.sides
        if _record_signature(previous) == _record_signature(record):
            by_code[card.code] = replace(previous, sides=combined_sides, images=previous.images + record.images)
            continue
        pick = RECORD_SIDE_PICK.get(card.code)
        if pick is None:
            raise ValueError(f"Стороны {card.code} расходятся, а выбора владельца нет")
        winner = record if pick == card.side else previous
        by_code[card.code] = replace(winner, sides=combined_sides, images=previous.images + record.images)
    return tuple(by_code[code] for code in sorted(by_code))

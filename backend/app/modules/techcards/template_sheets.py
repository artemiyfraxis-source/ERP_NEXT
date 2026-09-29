"""Шаблоны-листы БД.ТК: каталог и черновики (SUPPLYMAP TcTemplates.gs, портал openTcTplSheets).

В SUPPLYMAP шаблон — блок ячеек в скрытом листе-хранилище `_TC_STORE`, строка каталога `_TC_LIBRARY`
(id, название, категория, описание, размер, дата), а правка идёт через видимый лист «Черновик — …»:
портал создаёт лист (копию шаблона или пустой), инженер правит его в таблице, портал «сохраняет»
(захват B2:конец → каталог, лист сносится) или сносит черновик без сохранения. В ERP лист — запись:
шаблон — строка `techcard_operation_templates` (сетка значений `raw_rows`), черновик — строка
`techcard_operation_template_drafts` с той же сеткой, которую правят на экране «Шаблоны-листы».
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Iterable, Sequence
from uuid import UUID


DRAFT_PREFIX = "Черновик — "
MAX_DRAFT_ROWS = 400
MAX_DRAFT_COLUMNS = 40


@dataclass(frozen=True)
class TemplateDraft:
    id: UUID
    name: str
    existing_template_id: str
    title: str
    category: str
    description: str
    rows: tuple[tuple[str, ...], ...]
    images: tuple[dict, ...]
    created_at: datetime
    created_by: str
    updated_at: datetime


def draft_name(title: str) -> str:
    return DRAFT_PREFIX + title


def normalize_text(value: object) -> str:
    """SUPPLYMAP normalizeString_: строка без крайних пробелов."""
    return "" if value is None else str(value).strip()


def new_draft_title(title: object, now: datetime) -> str:
    """tcTemplateNewDraft: пустое название → «Новый шаблон dd.MM HH:mm» (время Москвы)."""
    return normalize_text(title) or f"Новый шаблон {now:%d.%m %H:%M}"


def slugify(value: object) -> str:
    """SUPPLYMAP TcUtils.gs slugify_."""
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9а-яё]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return re.sub(r"-+", "-", text)


def make_template_id(title: str, existing_ids: Iterable[str]) -> str:
    """SUPPLYMAP makeTemplateId_: slug названия, при занятом — «-2», «-3»…"""
    taken = set(existing_ids)
    base = slugify(title) or "template"
    candidate, index = base, 2
    while candidate in taken:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def normalize_grid(rows: object) -> tuple[tuple[str, ...], ...]:
    """Сетка черновика — матрица строк; None → пусто. Размер ограничен, чтобы опечатка не раздула запись."""
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("Сетка черновика должна быть матрицей")
    if len(rows) > MAX_DRAFT_ROWS:
        raise ValueError(f"В черновике больше {MAX_DRAFT_ROWS} строк")
    result = []
    for row in rows:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            raise ValueError("Сетка черновика должна быть матрицей")
        if len(row) > MAX_DRAFT_COLUMNS:
            raise ValueError(f"В черновике больше {MAX_DRAFT_COLUMNS} колонок")
        result.append(tuple("" if cell is None else str(cell) for cell in row))
    return tuple(result)


def captured_block(rows: Sequence[Sequence[str]]) -> tuple[tuple[str, ...], ...]:
    """tcTemplateSaveFromDraft: захват от начала сетки (B2 листа) до последней заполненной строки и
    колонки. Хвостовые пустые ячейки строк отбрасываются (как в зеркале БД.ТК, из которого приходит
    библиотека); пустой черновик — ошибка."""
    trimmed = [list(row) for row in rows]
    for row in trimmed:
        while row and not str(row[-1]).strip():
            row.pop()
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    if not trimmed:
        raise ValueError("Черновик пуст — заполни сетку шаблона с первой ячейки.")
    return tuple(tuple(row) for row in trimmed)


class TemplateSheetNotFound(LookupError):
    """Шаблон или черновик не найден (404)."""


class TemplateDraftConflict(Exception):
    """Черновик с таким названием уже открыт (409)."""


def size_label(rows: Sequence[Sequence[str]]) -> str:
    """Размер блока для каталога («высота×ширина»)."""
    return f"{len(rows)}×{max((len(row) for row in rows), default=0)}"

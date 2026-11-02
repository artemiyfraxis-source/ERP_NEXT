"""Full saved ЗКП evidence, separate from purchase requests and stock obligations."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.project_number import normalize_project_number

from .domain import ZPK_UNIT_BY_TYPE, ValidationError, project_reference_valid, spreadsheet_id_from_url

if TYPE_CHECKING:
    from .application import CalcExtract

MAX_COMPOSITION_ROWS = 5000
MAX_CELL_LENGTH = 5000
MAX_COMPOSITION_BYTES = 4 * 1024 * 1024


class CompositionConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class CompositionSeed:
    project: str
    source_id: str
    rows: list[dict]

    @property
    def digest(self) -> str:
        # Recorded request links are ERP evidence, not part of the upstream source fingerprint.
        rows = [{key: value for key, value in row.items() if key not in {"request_id", "request_link_state"}}
                for row in self.rows]
        return hashlib.sha256(json.dumps(
            [self.project, self.source_id, rows], ensure_ascii=False, sort_keys=True,
        ).encode()).hexdigest()


@dataclass(frozen=True)
class CalcComposition:
    id: UUID
    project: str
    source_id: str
    version: int
    content_hash: str
    imported_at: datetime
    imported_by: UUID
    rows: list[dict]


class CompositionRepository(Protocol):
    def get(self, project: str) -> CalcComposition | None: ...
    def save(self, value: CalcComposition, expected_version: int) -> None: ...


def _number(raw: str) -> str | None:
    value = re.sub(r"\s", "", raw).replace(",", ".")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    # Unlike the legacy importer, evidence must not turn '12 unknown' into an asserted quantity.
    return str(parsed) if parsed.is_finite() and 0 <= parsed < Decimal("1e12") else None


def composition_seed(project: str, url: str, extract: CalcExtract) -> CompositionSeed:
    project = normalize_project_number(project)
    if not project_reference_valid(project) or normalize_project_number(extract.project) != project:
        raise ValidationError("Номер проекта в расчёте не совпадает с выбранным проектом")
    source = spreadsheet_id_from_url(url)
    if not source or len(source) > 255:
        raise ValidationError("Не похоже на ссылку Google Таблицы")
    if not extract.items or len(extract.items) > MAX_COMPOSITION_ROWS:
        raise ValidationError("Состав ЗКП должен содержать от 1 до 5000 позиций")
    rows = []
    seen = set()
    for item in extract.items:
        raw = {key: getattr(item, key) for key in (
            "name", "item_type", "unit", "quantity", "price", "price_with_vat", "link", "country",
        )}
        raw["unit"] = item.source_unit
        unit = item.source_unit.strip()
        unit_source = "explicit" if unit else "unknown"
        if not unit and item.item_type.strip() in ZPK_UNIT_BY_TYPE:
            unit = ZPK_UNIT_BY_TYPE[item.item_type.strip()]
            unit_source = "derived_known"
        if any(len(value) > MAX_CELL_LENGTH for value in raw.values()):
            raise ValidationError("Ячейка ЗКП превышает 5000 символов")
        if item.source_row is None or item.source_row < 1 or item.source_row in seen:
            raise ValidationError("Не определена уникальная исходная строка ЗКП")
        seen.add(item.source_row)
        parsed = {key: _number(raw[key]) for key in ("quantity", "price", "price_with_vat")}
        issues = [key for key, value in parsed.items() if value is None and (key == "quantity" or raw[key].strip())]
        if unit_source == "unknown":
            issues.append("unit")
        rows.append({
            **raw, **parsed, "unit": unit, "unit_source": unit_source,
            "raw": raw, "source_row": item.source_row, "issues": issues,
            "request_id": None, "request_link_state": "unlinked",
        })
    if len(json.dumps(rows, ensure_ascii=False).encode()) > MAX_COMPOSITION_BYTES:
        raise ValidationError("Состав ЗКП превышает 4 МБ")
    return CompositionSeed(project=project, source_id=source, rows=rows)


def save_composition(seed, expected_version, actor, now, uow, *, request_ids=None) -> CalcComposition:
    """Caller owns the transaction; only composition + audit are changed. No request/stock/outbox writes."""
    old = uow.compositions.get(seed.project)
    if (old.version if old else 0) != expected_version:
        raise CompositionConflict("Состав ЗКП изменён; обновите данные перед сохранением")
    if old is not None and old.content_hash == seed.digest and not request_ids:
        return old
    links = request_ids or {}
    link_state = "source_changed" if old and any(
        row["request_id"] or row.get("request_link_state") == "source_changed" for row in old.rows
    ) else "unlinked"
    value = CalcComposition(
        id=old.id if old else uuid4(), project=seed.project, source_id=seed.source_id,
        version=expected_version + 1, content_hash=seed.digest, imported_at=now, imported_by=actor.id,
        rows=[{**row, "request_id": str(links[row["source_row"]]) if row["source_row"] in links else None,
               "request_link_state": "recorded" if row["source_row"] in links else link_state}
              for row in seed.rows],
    )
    uow.compositions.save(value, expected_version)
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=value.id, event_type="request.composition_refreshed", actor=actor, occurred_at=now,
        details={"project": seed.project, "version": str(value.version), "content_hash": value.content_hash},
    ))
    return value

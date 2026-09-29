"""HTTP contract for editable maps and the technology catalog."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, references_edit_principal, references_view_principal

from .application import (
    CreateRecordCommand, EditRecordCommand, FieldEdit, ReferencePair, ReplaceMapsCommand, ReplaceSnapshotCommand, TechRecordInput,
    StaleRecordError, UnitOfWork, create_record, edit_record, replace_maps, replace_snapshot,
)
from .domain import BASE_COLUMNS, MAX_ROW_EDITS, VIEW_ONLY_BASES, ReferenceKind, ReferenceValidationError, TechOperationTab
from .technology import (
    TECH_SNAPSHOT_SCHEMA_VERSION,
    TerminalRecord,
    fold_wire_section,
    import_supplymap_snapshot,
    resolve_terminal,
    wire_section,
)


router = APIRouter(prefix="/api/references", tags=["references"])


class PairPayload(BaseModel):
    key: str = Field(min_length=1, max_length=2048)
    value: str = Field(min_length=1, max_length=512)


class MapsPayload(BaseModel):
    suppliers: list[PairPayload] = Field(max_length=5000)
    countries: list[PairPayload] = Field(max_length=5000)
    gost_awg: list[PairPayload] = Field(max_length=500)


class TechRecordPayload(BaseModel):
    tab: TechOperationTab
    display_text: str = Field(min_length=1, max_length=2000)
    search_text: str = Field(default="", max_length=5000)
    source_sheet: str = Field(default="", max_length=255)
    sort_key: str = Field(default="", max_length=2000)
    fields: dict[str, str] = Field(default_factory=dict)


class SnapshotPayload(BaseModel):
    schema_version: int = Field(ge=1)
    source_updated_at: datetime | None = None
    records: list[TechRecordPayload] = Field(max_length=10_000)


class FieldEditPayload(BaseModel):
    field: str = Field(min_length=1, max_length=255)
    old: str = Field(default="", max_length=5000)
    value: str = Field(default="", max_length=5000)


class RecordEditPayload(BaseModel):
    edits: list[FieldEditPayload] = Field(min_length=1, max_length=MAX_ROW_EDITS)


class RecordCreatePayload(BaseModel):
    tab: str = Field(min_length=1, max_length=32)
    fields: dict[str, str] = Field(max_length=100)


class TerminalResolutionPayload(BaseModel):
    article: str = Field(min_length=1, max_length=512)
    wires: list[str] = Field(min_length=1, max_length=200)
    gost_awg: dict[str, int] = Field(default_factory=dict)
    folded: bool = False


class SupplymapSnapshotPayload(BaseModel):
    meta: dict[str, Any]
    records: list[dict[str, Any]] = Field(max_length=10_000)
    # БД.ПРОВ / БД.МАТ as sheet tables: {"prov": {"header": [...], "formulaCols": [], "rows": [[...]]}}. The GAS
    # snapshot builder does not emit them; build one from the mirror TSV with `parse_mirror_tsv`.
    tables: dict[str, Any] | None = None


def references_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "references_uow_factory", None)
    if not callable(factory): raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def _run(operation):
    try: return operation()
    except ReferenceValidationError as error: raise HTTPException(status_code=422, detail=str(error)) from error
    except IntegrityError as error: raise HTTPException(status_code=409, detail="Справочник изменён конкурентно") from error


def _maps(entries) -> dict:
    return {
        "suppliers": [{"key": item.key, "value": item.value} for item in entries if item.kind == ReferenceKind.SUPPLIER],
        "countries": [{"key": item.key, "value": item.value} for item in entries if item.kind == ReferenceKind.COUNTRY],
        "gost_awg": [{"key": item.key, "value": item.value} for item in entries if item.kind == ReferenceKind.GOST_AWG],
    }


@router.get("/maps")
def maps(_principal: Annotated[Principal, Depends(references_view_principal)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)]) -> dict:
    with uow_factory() as uow: entries = uow.references.list()
    return _maps(entries)


@router.put("/maps")
def save_maps(payload: MapsPayload, principal: Annotated[Principal, Depends(references_edit_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)]) -> dict:
    entries = _run(lambda: replace_maps(ReplaceMapsCommand(
        suppliers=tuple(ReferencePair(item.key, item.value) for item in payload.suppliers),
        countries=tuple(ReferencePair(item.key, item.value) for item in payload.countries),
        gost_awg=tuple(ReferencePair(item.key, item.value) for item in payload.gost_awg),
        actor=principal.actor, now=now,
    ), uow_factory()))
    return _maps(entries)


@router.get("/tech-operations")
def tech_operations(
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
    tab: TechOperationTab | None = None,
    q: Annotated[str, Query(max_length=255)] = "",
    limit: Annotated[int, Query(ge=1, le=5000)] = 200,
) -> dict:
    with uow_factory() as uow:
        records = uow.tech_operations.list(tab=tab, query=q, limit=limit)
        metadata = uow.tech_operations.metadata()
    return {
        "metadata": None if metadata is None else {
            **metadata,
            "source_updated_at": metadata["source_updated_at"].isoformat() if metadata["source_updated_at"] else None,
            "imported_at": metadata["imported_at"].isoformat(),
        },
        # Known sheet schema first (so an empty tab still has a create form), else the first row's keys.
        "columns": list(BASE_COLUMNS[tab]) if tab in BASE_COLUMNS else (list(records[0].fields) if tab and records else []),
        "records": [{
            "id": str(item.id), "tab": item.tab.value, "display_text": item.display_text,
            "source_sheet": item.source_sheet, "fields": item.fields,
        } for item in records],
    }


@router.post("/technology/resolve-terminal")
def resolve_terminal_norm(
    payload: TerminalResolutionPayload,
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        metadata = uow.tech_operations.metadata()
        records = uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=10_000)
    if metadata is None or metadata["schema_version"] != TECH_SNAPSHOT_SCHEMA_VERSION:
        raise HTTPException(
            status_code=409,
            detail=f"Загрузите снимок БД.Технология v{TECH_SNAPSHOT_SCHEMA_VERSION}",
        )
    sections = tuple(wire_section(value, payload.gost_awg) for value in payload.wires)
    if payload.folded:
        sections = tuple(fold_wire_section(section) for section in sections)
    result = resolve_terminal(
        (TerminalRecord.from_fields(record.fields) for record in records),
        payload.article,
        sections,
    )
    return {
        "applicator": result.applicator,
        "crimp_height": result.crimp_height,
        "pull_force": result.pull_force,
        "by_section": [
            {
                "section": item.section,
                "crimp_height": item.crimp_height,
                "pull_force": item.pull_force,
                "applicator": item.applicator,
                "range_only": item.range_only,
                "found": item.found,
            }
            for item in result.by_section
        ],
    }


@router.put("/tech-operations/snapshot")
def save_snapshot(payload: SnapshotPayload, principal: Annotated[Principal, Depends(references_edit_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)]) -> dict:
    count = _run(lambda: replace_snapshot(ReplaceSnapshotCommand(
        schema_version=payload.schema_version, source_updated_at=payload.source_updated_at,
        records=tuple(TechRecordInput(
            tab=item.tab, display_text=item.display_text, search_text=item.search_text,
            source_sheet=item.source_sheet, sort_key=item.sort_key, fields=item.fields,
        ) for item in payload.records), actor=principal.actor, now=now,
    ), uow_factory()))
    return {"records": count}


@router.put("/tech-operations/supplymap-snapshot")
def import_supplymap_technology_snapshot(
    payload: SupplymapSnapshotPayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    try:
        snapshot = import_supplymap_snapshot(payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    count = _run(lambda: replace_snapshot(ReplaceSnapshotCommand(
        schema_version=snapshot.schema_version,
        source_updated_at=snapshot.source_updated_at,
        declared_tabs=tuple(TechOperationTab(tab) for tab in snapshot.declared_tabs),
        records=tuple(TechRecordInput(
            tab=TechOperationTab(item.tab),
            display_text=item.display_text,
            search_text=item.search_text,
            source_sheet=item.source_sheet,
            sort_key=item.sort_key,
            fields=item.fields,
        ) for item in snapshot.records),
        actor=principal.actor,
        now=now,
    ), uow_factory()))
    return {"records": count, "schema_version": snapshot.schema_version}


_BASE_LABELS = {
    "op": "БД.ОП — операции", "ob": "БД.ОБ — станки и оборудование", "ter": "БД.ТЕР — терминалы",
    "coax": "БД.КОАКС — коаксиал", "prov": "БД.ПРОВ — провода", "mat": "БД.МАТ — материалы",
    "spr_kab": "СПР.КАБ — сечения и диаметры кабеля", "tk_templates": "БД.ТК — шаблоны ТК",
}


def _record_json(item) -> dict:
    return {"id": str(item.id), "tab": item.tab.value, "display_text": item.display_text,
            "source_sheet": item.source_sheet, "fields": item.fields}


def _run_edit(operation):
    try: return _run(operation)
    except StaleRecordError as error: raise HTTPException(status_code=409, detail=str(error)) from error
    except LookupError as error: raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/bases")
def bases(_principal: Annotated[Principal, Depends(references_view_principal)]) -> dict:
    """Portal «Справочник» catalogue: editable snapshot bases and view-only ones."""
    return {"bases": [
        {"id": key, "label": label, "editable": key not in VIEW_ONLY_BASES}
        for key, label in _BASE_LABELS.items()
    ]}


@router.patch("/tech-operations/{record_id}")
def edit_tech_record(
    record_id: UUID,
    payload: RecordEditPayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    record = _run_edit(lambda: edit_record(EditRecordCommand(
        record_id=record_id,
        edits=tuple(FieldEdit(item.field, item.old, item.value) for item in payload.edits),
        actor=principal.actor, now=now,
    ), uow_factory()))
    return _record_json(record)


@router.post("/tech-operations", status_code=201)
def create_tech_record(
    payload: RecordCreatePayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    if payload.tab in VIEW_ONLY_BASES:
        raise HTTPException(status_code=422, detail="База только для просмотра")
    try: tab = TechOperationTab(payload.tab)
    except ValueError as error: raise HTTPException(status_code=422, detail="Неизвестная база") from error
    record = _run_edit(lambda: create_record(CreateRecordCommand(
        tab=tab, fields=payload.fields, actor=principal.actor, now=now,
    ), uow_factory()))
    return _record_json(record)

"""HTTP contract for editable maps and the technology catalog."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Sequence

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.shared.clock import moscow_now
from app.shared.http_auth import (
    Principal, references_edit_principal, references_import_principal, references_maps_edit_principal,
    references_maps_view_principal, references_view_principal,
)

from .application import (
    CreateRecordCommand, EditRecordCommand, FieldEdit, ReferencePair, ReplaceMapsCommand, ReplaceSnapshotCommand, TechRecordInput,
    SetCableKindCommand, SetCoaxDiametersCommand, StaleRecordError, UnitOfWork, create_record, edit_record, list_cables,
    replace_maps, replace_snapshot, set_cable_kind, set_coax_diameters,
)
from .cables import DIAMETERS, CableKind, CableRow, cable_counts
from .domain import (
    BASE_COLUMNS, MAX_ROW_EDITS, SUPPLIER_BY_LINK, VIEW_ONLY_BASES, ReferenceKind, ReferenceValidationError, TechOperationRecord,
    TechOperationTab,
)
from .technology import (
    TECH_SNAPSHOT_SCHEMA_VERSION,
    TerminalRecord,
    fold_wire_section,
    import_supplymap_snapshot,
    resolve_terminal,
    schema_keys,
    wire_section,
)


router = APIRouter(prefix="/api/references", tags=["references"])


class PairPayload(BaseModel):
    # An empty row is dropped on save (References.gs saveReferences, RF-2), so empty strings are accepted here.
    key: str = Field(default="", max_length=2048)
    value: str = Field(default="", max_length=512)


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
    suppliers = [{"key": item.key, "value": item.value} for item in entries if item.kind == ReferenceKind.SUPPLIER]
    countries = [{"key": item.key, "value": item.value} for item in entries if item.kind == ReferenceKind.COUNTRY]
    return {
        "suppliers": suppliers,
        "countries": countries,
        "gost_awg": [{"key": item.key, "value": item.value} for item in entries if item.kind == ReferenceKind.GOST_AWG],
    }


def _map_rights(principal: Principal) -> dict[str, bool]:
    """Who changes which map: supplier/country — the buyer (portalCanWork_ = `procurement` edit), ГОСТ→AWG — the
    reference book (`erpref` edit)."""
    return {
        "suppliers": principal.permissions.get("procurement") == "edit",
        "gost_awg": principal.permissions.get("erpref") == "edit",
    }


@router.get("/maps")
def maps(principal: Annotated[Principal, Depends(references_maps_view_principal)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)]) -> dict:
    with uow_factory() as uow: entries = uow.references.list()
    result = _maps(entries)
    # getReferences: nothing saved yet → the SUPPLIER_BY_LINK seed (shown, stored on the first save).
    if not result["suppliers"] and not result["countries"]:
        result["suppliers"] = [{"key": key, "value": value} for key, value in SUPPLIER_BY_LINK]
    result["can_edit"] = _map_rights(principal)
    return result


@router.put("/maps")
def save_maps(payload: MapsPayload, principal: Annotated[Principal, Depends(references_maps_edit_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)]) -> dict:
    rights = _map_rights(principal)
    kinds = frozenset(
        ({ReferenceKind.SUPPLIER, ReferenceKind.COUNTRY} if rights["suppliers"] else set())
        | ({ReferenceKind.GOST_AWG} if rights["gost_awg"] else set())
    )
    entries = _run(lambda: replace_maps(ReplaceMapsCommand(
        suppliers=tuple(ReferencePair(item.key, item.value) for item in payload.suppliers),
        countries=tuple(ReferencePair(item.key, item.value) for item in payload.countries),
        gost_awg=tuple(ReferencePair(item.key, item.value) for item in payload.gost_awg),
        actor=principal.actor, now=now, kinds=kinds,
    ), uow_factory()))
    return {**_maps(entries), "can_edit": rights}


def _columns(tab: TechOperationTab | None, records: Sequence[TechOperationRecord]) -> list[str]:
    """Known sheet schema first (so an empty tab still has a create form). ОБ/ОП/ТЕР/КОАКС: the sheet's own columns
    kept by the import, without the schema values derived from them; a snapshot without them shows the schema keys."""
    if tab in BASE_COLUMNS:
        return list(BASE_COLUMNS[tab])
    if not tab or not records:
        return []
    keys = schema_keys(tab.value)
    richest = max(records, key=lambda item: sum(1 for key in item.fields if key not in keys))
    sheet = [key for key in richest.fields if key not in keys]
    return sheet or list(richest.fields)


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
        "columns": _columns(tab, records),
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
def save_snapshot(payload: SnapshotPayload, principal: Annotated[Principal, Depends(references_import_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)]) -> dict:
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
    principal: Annotated[Principal, Depends(references_import_principal)],
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
# PortalRef.gs REF_BASES_: tile icon and the line under it.
_BASE_TILES = {
    "op": ("⏱", "нормы времени · станки · подготовка"),
    "ob": ("🛠", "инструмент · станки · инв. номера"),
    "ter": ("🔩", "обжим · аппликаторы · зачистка"),
    "coax": ("📡", "разделка D/L · пин и экран"),
    "prov": ("🧵", "марки · сечения · диаметры"),
    "mat": ("🧪", "трубки · оплётки · стяжки"),
    "spr_kab": ("📏", "Ø изоляции по AWG · слои коакса"),
    "tk_templates": ("🧩", "шаблоны техкарт · только просмотр"),
}
_BASE_TABS = {
    "op": (TechOperationTab.OPERATION,), "ob": (TechOperationTab.EQUIPMENT,), "ter": (TechOperationTab.TERMINAL,),
    "coax": (TechOperationTab.COAX,), "prov": (TechOperationTab.WIRE,), "mat": (TechOperationTab.MATERIAL,),
    "spr_kab": (TechOperationTab.KAB_COAX, TechOperationTab.KAB_AWG),
}


def _record_json(item) -> dict:
    return {"id": str(item.id), "tab": item.tab.value, "display_text": item.display_text,
            "source_sheet": item.source_sheet, "fields": item.fields}


def _run_edit(operation):
    try: return _run(operation)
    except StaleRecordError as error: raise HTTPException(status_code=409, detail=str(error)) from error
    except LookupError as error: raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/bases")
def bases(
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    """Portal «Справочник» tiles (portalRefList): each base with its record count; view-only ones marked. БД.ТК
    templates live in «Шаблоны техкарт», so their count is not known here (null)."""
    with uow_factory() as uow:
        counts = uow.tech_operations.counts()
    return {
        "bases": [
            {
                "id": key, "label": label, "editable": key not in VIEW_ONLY_BASES,
                "icon": _BASE_TILES[key][0], "sub": _BASE_TILES[key][1],
                "count": sum(counts.get(tab, 0) for tab in _BASE_TABS[key]) if key in _BASE_TABS else None,
            }
            for key, label in _BASE_LABELS.items()
        ],
        # Tab chips of «База данных»: every stored tab, and the three cable lists («Кабели и провода»).
        "tabs": {tab.value: counts.get(tab, 0) for tab in TechOperationTab},
        "cables": cable_counts(list_cables(uow_factory())),
    }


def _dia_json(record) -> dict | None:
    if record is None:
        return None
    return {"id": str(record.id), "cable": record.fields.get("Кабель", record.display_text),
            **{name: record.fields.get(name, "") for name in DIAMETERS}}


def _cable_json(row: CableRow) -> dict:
    return {
        "id": str(row.record.id) if row.record else f"dia:{row.dia.id}",
        "kind": row.kind.value, "auto_kind": row.auto.value, "reason": row.reason, "manual": row.manual,
        "cable": row.cable, "record": _record_json(row.record) if row.record else None,
        "dia": _dia_json(row.dia), "dia_hint": _dia_json(row.dia_hint),
    }


@router.get("/cables")
def cables(
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    """«Кабели и провода»: БД.ПРОВ rows split into коаксиальные / монтажные / прочие (`cables.auto_kind`, or where the
    user moved them), each coax cable joined with the D1·D2·D3 row the generator uses for its name; D-table cables
    without a БД.ПРОВ row are coax rows of their own."""
    rows = list_cables(uow_factory())
    return {"columns": list(BASE_COLUMNS[TechOperationTab.WIRE]), "counts": cable_counts(rows),
            "rows": [_cable_json(row) for row in rows]}


class CableKindPayload(BaseModel):
    kind: CableKind


@router.put("/cables/{record_id}/kind")
def move_cable(
    record_id: UUID,
    payload: CableKindPayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    row = _run_edit(lambda: set_cable_kind(SetCableKindCommand(record_id, payload.kind, principal.actor, now), uow_factory()))
    return _cable_json(row)


class DiametersPayload(BaseModel):
    cable: str = Field(min_length=1, max_length=512)
    dia_id: UUID | None = None
    old: dict[str, str] = Field(default_factory=dict, max_length=3)
    value: dict[str, str] = Field(max_length=3)


@router.put("/cables/diameters")
def save_cable_diameters(
    payload: DiametersPayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    record = _run_edit(lambda: set_coax_diameters(SetCoaxDiametersCommand(
        cable=payload.cable, dia_id=payload.dia_id, old=payload.old, value=payload.value, actor=principal.actor, now=now,
    ), uow_factory()))
    return _dia_json(record)


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

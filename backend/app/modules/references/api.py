"""HTTP contract for editable maps and the technology catalog."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Sequence

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.shared.clock import moscow_now
from app.workflows.stock_article import read_stock_article
from app.shared.http_auth import (
    Principal, references_edit_principal, references_import_principal, references_maps_edit_principal,
    references_maps_view_principal, references_view_principal, refuse_api_tokens,
)

from .application import (
    ColumnCommand, CreateRecordCommand, EditRecordCommand, FieldEdit, ReferencePair, ReplaceMapsCommand, ReplaceSnapshotCommand, TechRecordInput,
    SetCableKindCommand, SetCoaxDiametersCommand, SetCoveringGroupCommand, StaleRecordError, UnitOfWork, create_record, edit_record, list_cables,
    SaveColumnViewCommand, ViewColumn, column_views, save_column_view,
    replace_maps, replace_snapshot, set_cable_kind, add_column, custom_columns, remove_column, set_coax_diameters, set_covering_group, coverings_catalog,
)
from .crimps import change_target, change_terminal, crimp_codes, crimp_overview
from .equipment import EXTRA_KEYS, next_instance_inventory, next_model_inventory, next_row_number
from .application import LinkStockCommand, link_stock_article, stock_links
from .application import (
    CableCatalog, duplicate_equipment, SaveGaugeCommand, SaveHookupCommand, cable_catalog, coax_dia_rows, delete_gauge,
    import_coax_cards, import_coax_names, save_gauge, save_hookup,
)
from .cable_structure import (
    LAYER_LABELS, CoaxCard, HookupWire, WireGauge, parse_hookup,
)
from .cables import CONNECTOR_FIELDS, DIAMETERS, CableKind, CableRow, cable_counts, compatible_connectors
from .domain import (
    BASE_COLUMNS, COAX_DROPPED_COLUMNS, MAX_ROW_EDITS, SUPPLIER_BY_LINK, merge_country_pairs, VIEW_ONLY_BASES, ReferenceKind, ReferenceValidationError, TechOperationRecord,
    StockLinkEntry, TechOperationTab,
)
from .technology import (
    TECH_SNAPSHOT_SCHEMA_VERSION,
    TerminalRecord,
    is_coax_cable_column,
    fold_wire_section,
    import_supplymap_snapshot,
    normalize_terminal_fields,
    resolve_terminal,
    schema_keys,
    terminal_card,
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
    cable_kind: CableKind | None = None  # БД.ПРОВ: the cable list the row is created in (none — by the rule)


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


def _shown_maps(saved: dict) -> dict:
    """What «Соответствия» shows: nothing saved yet → the SUPPLIER_BY_LINK seed (stored on the first save); the
    country map is always saved rows + the COUNTRY_BY_MANUFACTURER seed for the keys not saved (owner 12.10.2026)."""
    shown = dict(saved)
    if not saved["suppliers"] and not saved["countries"]:
        shown["suppliers"] = [{"key": key, "value": value} for key, value in SUPPLIER_BY_LINK]
    shown["countries"] = [
        {"key": key, "value": value} for key, value in merge_country_pairs((item["key"], item["value"]) for item in saved["countries"])
    ]
    return shown


def _map_rights(principal: Principal) -> dict[str, bool]:
    """Who changes which map: supplier/country — the buyer (portalCanWork_ = `procurement` edit). ГОСТ→AWG is not
    saved here any more (owner 10.10.2026): it follows «Сечения AWG ↔ мм²» (`save_gauge`), so a maps save — often from
    a page loaded before a size edit — never writes a stale copy back."""
    return {
        "suppliers": principal.permissions.get("procurement") == "edit",
        "gost_awg": False,
    }


@router.get("/maps")
def maps(principal: Annotated[Principal, Depends(references_maps_view_principal)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)]) -> dict:
    with uow_factory() as uow: entries = uow.references.list()
    return {**_shown_maps(_maps(entries)), "can_edit": _map_rights(principal)}


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
    return {**_shown_maps(_maps(entries)), "can_edit": rights}


def _columns(tab: TechOperationTab | None, records: Sequence[TechOperationRecord], own: Sequence[str] = ()) -> list[str]:
    """The tab's columns, the owner's own («+ Столбец») after the sheet's."""
    base = _sheet_columns(tab, records)
    return base + [name for name in own if name not in base]


def _sheet_columns(tab: TechOperationTab | None, records: Sequence[TechOperationRecord]) -> list[str]:
    """Known sheet schema first (so an empty tab still has a create form). ОБ/ОП/ТЕР/КОАКС: the sheet's own columns
    kept by the import, without the schema values derived from them; a snapshot without them shows the schema keys."""
    if tab in BASE_COLUMNS:
        return list(BASE_COLUMNS[tab])
    if not tab or not records:
        return []
    keys = schema_keys(tab.value)
    richest = max(records, key=lambda item: sum(1 for key in item.fields if key not in keys))
    sheet = [key for key in richest.fields if key not in keys]
    if tab == TechOperationTab.COAX:  # D1–D3 are the cable's («Коаксиальные кабели»), not a connector column; the
        # columns dropped 23.10 and 25.10.2026 (`COAX_DROPPED_COLUMNS`) neither — their values stay in the records
        sheet = [key for key in sheet if not is_coax_cable_column(key) and key not in COAX_DROPPED_COLUMNS]
    if tab == TechOperationTab.EQUIPMENT:  # the fields ERP adds to the sheet's columns (code, tool, links)
        sheet += [key for key in EXTRA_KEYS if key not in sheet]
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
        own = custom_columns(tab, uow) if tab is not None else []
    if tab == TechOperationTab.COAX:  # an own column removed from the table 25.10.2026 («Дата цены») is no column
        own = [name for name in own if name not in COAX_DROPPED_COLUMNS]
    return {
        "metadata": None if metadata is None else {
            **metadata,
            "source_updated_at": metadata["source_updated_at"].isoformat() if metadata["source_updated_at"] else None,
            "imported_at": metadata["imported_at"].isoformat(),
        },
        # Known sheet schema first (so an empty tab still has a create form), else the first row's keys.
        "columns": _columns(tab, records, own),
        "custom_columns": own,
        "records": [{
            "id": str(item.id), "tab": item.tab.value, "display_text": item.display_text,
            "source_sheet": item.source_sheet, **({"cable_kind": item.cable_kind} if item.cable_kind else {}),
            "fields": normalize_terminal_fields(item.fields) if item.tab == TechOperationTab.TERMINAL else item.fields,
            # «Терминалы»: the contact card by protocol v5.2, read from the record as the generator reads it.
            **({"card": terminal_card(item.fields)} if item.tab == TechOperationTab.TERMINAL else {}),
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
            "source_sheet": item.source_sheet, "fields": item.fields,
            **({"cable_kind": item.cable_kind} if item.cable_kind else {})}


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
        gauges = len(uow.cable_structure.gauges())
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
        "gauges": gauges,
    }


def _dia_json(record) -> dict | None:
    if record is None:
        return None
    return {"id": str(record.id), "cable": record.fields.get("Кабель", record.display_text),
            **{name: record.fields.get(name, "") for name in DIAMETERS}}


def _num(value) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    number = float(value)
    return int(number) if number.is_integer() else number


def _gauge_json(gauge: WireGauge, usage: int = 0) -> dict:
    return {"id": str(gauge.id), "awg": gauge.awg or "", "section_mm2": _num(gauge.section_mm2),
            "conductor_d_mm": _num(gauge.conductor_d_mm), "label": gauge.label, "wires": usage}


def _hookup_json(wire: HookupWire | None) -> dict | None:
    if wire is None:
        return None
    return {"id": str(wire.id), "series": wire.series, "gauge_id": str(wire.gauge_id), "insulation": wire.insulation,
            "outer_d_mm": _num(wire.outer_d_mm), "voltage_v": wire.voltage_v, "temp_min_c": wire.temp_min_c,
            "temp_max_c": wire.temp_max_c, "conductor_material": wire.conductor_material,
            "insulation_thickness_mm": _num(wire.insulation_thickness_mm)}


def _card_json(card: CoaxCard | None) -> dict | None:
    if card is None:
        return None
    return {
        "id": str(card.id), "name": card.name, "family": card.family,
        "layers": [{"layer": item.layer, "label": LAYER_LABELS[item.layer], "material": item.material,
                    "diameter_mm": _num(item.diameter_mm)} for item in card.layers],
        "connectors": [{"family": item.family, "max_ghz": _num(item.max_ghz), "max_note": item.max_note,
                        "span_to_ghz": _num(item.span_to_ghz)} for item in card.connectors],
        "markers": [{"freq_mhz": _num(item.freq_mhz), "peak": item.freq_mhz is None, "vswr_max": _num(item.vswr_max),
                     "vswr_ref": _num(item.vswr_ref), "note": item.note} for item in card.markers],
        "attenuation": [{"freq_mhz": _num(item.freq_mhz), "kind": item.kind, "value": _num(item.value), "unit": item.unit,
                         "ref_length_m": _num(item.ref_length_m), "temp_c": _num(item.temp_c),
                         "loss_db_m": _num(item.loss_db_m), "derived": item.derived, "interpolated": item.interpolated,
                         "source": item.source, "basis": item.basis} for item in card.attenuation],
        "span_from_mhz": _num(card.span_from_mhz), "span_to_ghz": _num(card.span_to_ghz), "span_note": card.span_note,
        "fc_ghz": _num(card.fc_ghz), "fc_note": card.fc_note, "max_operating_ghz": _num(card.max_operating_ghz),
        "max_operating_note": card.max_operating_note, "notes": card.notes, "sources": list(card.sources),
        "confidence": card.confidence, "verified": card.verified, "changes": list(card.changes),
        "params": {key: _num(value) for key, value in card.params.items()}, "param_notes": dict(card.param_notes),
        "full_name": card.full_name,
        "aliases": [{"alias": item.alias, "kind": item.kind, "supplier": item.supplier, "url": item.url}
                    for item in card.aliases],
    }


def _connector_cells(record) -> dict:
    """The cells «Совместимые разъёмы» shows of a БД.КОАКС row; of «Источник данных» — only its first part, the site
    the supplier is named by (the rest is the research log: 1.4 MB over the list, 26.10.2026)."""
    cells = {name: record.fields[name] for name in CONNECTOR_FIELDS if record.fields.get(name)}
    if "Источник данных" in cells:
        cells["Источник данных"] = cells["Источник данных"].split(",")[0].strip()
    return cells


def _cable_json(row: CableRow, catalog: CableCatalog | None = None, cells: dict[str, dict] | None = None) -> dict:
    """A row of «Кабели и провода». With `cells` (the list), a coax row names its connectors by id (`connector_ids`) and
    each connector's cells go once into `cells` — 127 coax rows share 881 connectors 4 475 times (26.10.2026: 5.5 of the
    list's 11 MB); without it (one row) the connectors come with the row (`connectors`)."""
    result = {
        "id": str(row.record.id) if row.record else f"dia:{row.dia.id}" if row.dia else f"card:{row.card.id}",
        "kind": row.kind.value, "auto_kind": row.auto.value, "reason": row.reason, "manual": row.manual,
        "cable": row.cable, "record": _record_json(row.record) if row.record else None,
        "dia": _dia_json(row.dia), "dia_hint": _dia_json(row.dia_hint),
        "card": _card_json(row.card), "card_exact": row.card_exact,
    }
    if catalog is not None and row.kind == CableKind.COAX:
        connectors, family = compatible_connectors(row, catalog.connectors)
        if cells is None:
            result["connectors"] = [{"id": str(record.id), "fields": _connector_cells(record)} for record in connectors]
        else:
            result["connector_ids"] = [str(record.id) for record in connectors]
            for record in connectors:
                if str(record.id) not in cells:
                    cells[str(record.id)] = _connector_cells(record)
        result["connectors_family"] = family
    if catalog is not None and row.kind == CableKind.WIRE:
        wire = catalog.hookups.get(row.key)
        result["hookup"] = _hookup_json(wire)
        if wire is None:  # not structured yet: what the БД.ПРОВ row says, and why it was not parsed
            parsed = parse_hookup(row.cable, row.record.fields if row.record else {}, catalog.gauges)
            result["hookup_problem"] = parsed.problem or ("нет серии" if not parsed.series else "")
            result["hookup_suggestion"] = {
                "series": parsed.series, "gauge_id": str(parsed.gauge.id) if parsed.gauge else "",
                "insulation": parsed.insulation, "outer_d_mm": _num(parsed.outer_d_mm), "voltage_v": parsed.voltage_v,
                "temp_min_c": parsed.temp_min_c, "temp_max_c": parsed.temp_max_c,
                "conductor_material": "", "insulation_thickness_mm": None,
            }
    return result


@router.get("/cables")
def cables(
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    """«Кабели и провода»: БД.ПРОВ rows in the list each stores — коаксиальные / монтажные / многожильные; each list has
    its own columns (`columns_by_kind`: the list's, then the owner's of it; `custom_columns_by_kind` — the owner's;
    `removed_columns_by_kind` — removed 25.10.2026: no column, only the row's card shows and edits them).
    `columns` / `custom_columns` — of all lists together. A coax cable is joined with the D1·D2·D3 row the generator uses for its name and
    its card and the БД.КОАКС connectors made for it (`cables.compatible_connectors`); D-table cables and cards without
    a БД.ПРОВ row are coax rows of their own. A монтажный wire carries its
    structure (series, size row, …) or, not structured yet, what its БД.ПРОВ row suggests. `gauges` — «Сечения AWG ↔
    мм²» with the number of wires on each size. `connector_cells` — every coax row's connectors once (id → cells),
    the rows name theirs in `connector_ids`."""
    catalog = cable_catalog(uow_factory())
    cells: dict[str, dict] = {}
    rows = [_cable_json(row, catalog, cells) for row in catalog.rows]
    return {"columns": list(BASE_COLUMNS[TechOperationTab.WIRE]) + catalog.columns, "custom_columns": catalog.columns,
            "columns_by_kind": catalog.columns_by_kind, "custom_columns_by_kind": catalog.custom_by_kind,
            "removed_columns_by_kind": catalog.removed_by_kind,
            "counts": cable_counts(catalog.rows),
            "rows": rows,
            "connector_cells": cells,
            "gauges": [_gauge_json(gauge, catalog.usage.get(gauge.id, 0)) for gauge in catalog.gauges]}


class GaugePayload(BaseModel):
    awg: str = Field(default="", max_length=8)
    section_mm2: str = Field(min_length=1, max_length=32)
    conductor_d_mm: str = Field(default="", max_length=32)  # an old client's «strands» is ignored (column dropped, 0114)


def _save_gauge(gauge_id: UUID | None, payload: GaugePayload, principal: Principal, now: datetime, uow_factory) -> dict:
    gauge = _run_edit(lambda: save_gauge(SaveGaugeCommand(
        gauge_id=gauge_id, awg=payload.awg, section_mm2=payload.section_mm2, conductor_d_mm=payload.conductor_d_mm,
        actor=principal.actor, now=now,
    ), uow_factory()))
    return _gauge_json(gauge)


@router.post("/wire-gauges", status_code=201)
def create_wire_gauge(
    payload: GaugePayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    return _save_gauge(None, payload, principal, now, uow_factory)


@router.put("/wire-gauges/{gauge_id}")
def update_wire_gauge(
    gauge_id: UUID,
    payload: GaugePayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    return _save_gauge(gauge_id, payload, principal, now, uow_factory)


# A token never deletes (owner 12.10.2026): `refs:write` loads and edits rows, removing one stays with a person.
@router.delete("/wire-gauges/{gauge_id}", status_code=204, dependencies=[Depends(refuse_api_tokens("Токен API не удаляет строки справочника."))])
def remove_wire_gauge(
    gauge_id: UUID,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> None:
    _run_edit(lambda: delete_gauge(gauge_id, principal.actor, now, uow_factory()))


class HookupValues(BaseModel):
    series: str = Field(default="", max_length=255)
    gauge_id: UUID
    insulation: str = Field(default="", max_length=512)
    outer_d_mm: str = Field(default="", max_length=32)
    voltage_v: str = Field(default="", max_length=32)
    temp_min_c: str = Field(default="", max_length=32)
    temp_max_c: str = Field(default="", max_length=32)


class HookupPayload(HookupValues):
    # What the screen showed (null — the wire was not structured yet); a different current value → 409.
    old: HookupValues | None = None
    # Not sent — the stored value stays (the screen does not edit them yet; the file import fills them).
    conductor_material: str | None = Field(default=None, max_length=255)
    insulation_thickness_mm: str | None = Field(default=None, max_length=32)


def _old_hookup(record_id: UUID, old: HookupValues | None, uow_factory) -> HookupWire | None:
    if old is None:
        return None
    with uow_factory() as uow:
        record = uow.tech_operations.get(record_id)
    mark = str(record.fields.get("Марка") or record.display_text) if record else "?"
    try:
        return HookupWire.create(mark=mark, series=old.series, gauge_id=old.gauge_id, insulation=old.insulation,
                                 outer_d_mm=old.outer_d_mm, voltage_v=old.voltage_v, temp_min_c=old.temp_min_c,
                                 temp_max_c=old.temp_max_c)
    except ReferenceValidationError as error:
        raise HTTPException(status_code=409, detail="Провод уже поменяли — откройте его заново") from error


@router.put("/cables/{record_id}/hookup")
def save_hookup_wire(
    record_id: UUID,
    payload: HookupPayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    """«Монтажные провода»: the structure of a БД.ПРОВ wire; its БД.ПРОВ cells are written in step."""
    old = _old_hookup(record_id, payload.old, uow_factory)
    wire, record = _run_edit(lambda: save_hookup(SaveHookupCommand(
        record_id=record_id, series=payload.series, gauge_id=payload.gauge_id, insulation=payload.insulation,
        outer_d_mm=payload.outer_d_mm, voltage_v=payload.voltage_v, temp_min_c=payload.temp_min_c,
        temp_max_c=payload.temp_max_c, old=old, actor=principal.actor, now=now,
        conductor_material=payload.conductor_material, insulation_thickness_mm=payload.insulation_thickness_mm,
    ), uow_factory()))
    return {"hookup": _hookup_json(wire), "record": _record_json(record)}


@router.post("/coax-cards/import")
def import_coax(
    principal: Annotated[Principal, Depends(references_import_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    """Reloads the coax cards, then their full names and aliases, from the committed files (`load_cables`), as the
    server does on every update."""
    from .load_cables import ARTICLES_FILE, read_seed

    try:
        document, articles = read_seed(), read_seed(ARTICLES_FILE)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=f"Файл карточек не прочитан: {error}") from error
    cards = _run(lambda: import_coax_cards(document, principal.actor, now, uow_factory()))
    return {**cards, "names": _run(lambda: import_coax_names(articles, principal.actor, now, uow_factory()))}


@router.get("/coax-dia")
def coax_dia(
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    """СПР.КАБ · коаксиал as the ETK generator reads it: the D1·D2·D3 rows, then one row per other name of a cable
    (full name, supplier article, another spelling of the mark) with that cable's D1–D3 (`coax_dia_rows`)."""
    with uow_factory() as uow:
        rows = coax_dia_rows(uow)
    return {"metadata": None, "columns": ["Кабель", *DIAMETERS], "records": [
        {"id": "", "tab": TechOperationTab.KAB_COAX.value, "display_text": str(row.get("Кабель") or ""),
         "source_sheet": "", "fields": row} for row in rows]}


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


@router.get("/coverings")
def coverings(
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    """«Оболочки»: БД.МАТ rows that cover a harness, split into ТУТ / ТТК / Оплётки / Гофра / Прочее, parsed per group
    and sorted by series, then Ø (`coverings.py`)."""
    return coverings_catalog(uow_factory())


class CoveringGroupPayload(BaseModel):
    group: str = Field(default="", max_length=32)


@router.put("/coverings/{record_id}/group")
def put_covering_group(
    record_id: UUID,
    payload: CoveringGroupPayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    record = _run_edit(lambda: set_covering_group(SetCoveringGroupCommand(
        record_id=record_id, group=payload.group, actor=principal.actor, now=now), uow_factory()))
    return _record_json(record)


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


class ColumnPayload(BaseModel):
    tab: TechOperationTab
    name: str = Field(min_length=1, max_length=200)  # the limit proper (60) is the use case's, with its own words
    cable_kind: CableKind | None = None  # БД.ПРОВ: the cable list whose column it is (required there)


@router.post("/tech-operations/columns", status_code=201, dependencies=[Depends(refuse_api_tokens("Токен API не меняет состав столбцов."))])
def add_tech_column(
    payload: ColumnPayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    """«+ Столбец»: an empty column of the owner's own in every record of the table (БД.ПРОВ, БД.МАТ, БД.КОАКС, БД.ТЕР, БД.ОП)."""
    records = _run_edit(lambda: add_column(ColumnCommand(
        tab=payload.tab, name=payload.name, actor=principal.actor, now=now, cable_kind=payload.cable_kind), uow_factory()))
    return {"tab": payload.tab.value, "name": " ".join(payload.name.split()), "records": records,
            "cable_kind": payload.cable_kind.value if payload.cable_kind else None}


@router.post("/tech-operations/columns/remove", dependencies=[Depends(refuse_api_tokens("Токен API не меняет состав столбцов."))])
def remove_tech_column(
    payload: ColumnPayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    """Removes the owner's own column; its values go to the journal."""
    records = _run_edit(lambda: remove_column(ColumnCommand(
        tab=payload.tab, name=payload.name, actor=principal.actor, now=now, cable_kind=payload.cable_kind), uow_factory()))
    return {"tab": payload.tab.value, "name": payload.name, "records": records,
            "cable_kind": payload.cable_kind.value if payload.cable_kind else None}


class ViewColumnPayload(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    visible: bool


class ColumnViewPayload(BaseModel):
    columns: list[ViewColumnPayload] = Field(max_length=300)  # empty — drop the shared set


@router.get("/column-views")
def get_column_views(
    principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    """The shared column set of every table that has one (owner 22.10.2026) and whether the caller may change it."""
    with uow_factory() as uow:
        views = column_views(uow)
    return {"views": views, "can_edit": principal.permissions.get("erpref") == "edit"}


@router.put("/column-views/{view}", dependencies=[Depends(refuse_api_tokens("Токен API не меняет столбцы таблиц."))])
def put_column_view(
    view: str,
    payload: ColumnViewPayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    """«Сохранить для всех»: which columns of a table are shown to everyone, and in what order."""
    columns = _run(lambda: save_column_view(SaveColumnViewCommand(
        view=view, columns=tuple(ViewColumn(item.key, item.visible) for item in payload.columns),
        actor=principal.actor, now=now), uow_factory()))
    return {"view": view, "columns": columns}


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
    if payload.cable_kind is not None and tab != TechOperationTab.WIRE:
        raise HTTPException(status_code=422, detail="Список кабеля бывает только у строки БД.ПРОВ")
    record = _run_edit(lambda: create_record(CreateRecordCommand(
        tab=tab, fields=payload.fields, actor=principal.actor, now=now, cable_kind=payload.cable_kind,
    ), uow_factory()))
    return _record_json(record)


# --- «Склад → база данных»: a stock article tied to / made into a record, and the journal of it (T14) ---

class StockLinkPayload(BaseModel):
    """`record_id` + no `edits` — tie the lot to the record; `record_id` + `edits` — change the record from the lot;
    no `record_id` — a new record in `tab` from `fields`."""
    lot_id: UUID
    record_id: UUID | None = None
    tab: str | None = Field(default=None, max_length=32)
    fields: dict[str, str] = Field(default_factory=dict, max_length=100)
    edits: list[FieldEditPayload] = Field(default_factory=list, max_length=MAX_ROW_EDITS)


def _stock_link_json(entry: StockLinkEntry) -> dict:
    article = entry.article
    return {
        "id": str(entry.id), "occurred_at": entry.occurred_at.isoformat(), "event": entry.event.value,
        "lot_id": str(article.lot_id), "warehouse": article.warehouse, "grn": article.grn, "package": article.package,
        "article": article.article, "name": article.name,
        "record_id": None if entry.record_id is None else str(entry.record_id), "record_tab": entry.record_tab.value,
        "record_text": entry.record_text,
        "changes": [{"field": change.field, "old": change.old, "new": change.new} for change in entry.changes],
        "actor_name": entry.actor_name,
    }


def _tab_of(value: str | None) -> TechOperationTab | None:
    if value is None or value == "":
        return None
    if value in VIEW_ONLY_BASES:
        raise HTTPException(status_code=422, detail="База только для просмотра")
    try: return TechOperationTab(value)
    except ValueError as error: raise HTTPException(status_code=422, detail="Неизвестная база") from error


@router.post("/stock-links")
def link_stock(
    payload: StockLinkPayload,
    request: Request,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    tab = _tab_of(payload.tab)
    warehouse = getattr(request.app.state, "warehouse_uow_factory", None)
    if not callable(warehouse):
        raise HTTPException(status_code=503, detail="Склад ERP не настроен")
    article = read_stock_article(warehouse, payload.lot_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Партия склада не найдена")
    result = _run_edit(lambda: link_stock_article(LinkStockCommand(
        article=article, actor=principal.actor, now=now, record_id=payload.record_id, tab=tab,
        fields=payload.fields, edits=tuple(FieldEdit(item.field, item.old, item.value) for item in payload.edits),
    ), uow_factory()))
    return {"record": _record_json(result.record),
            "entry": None if result.entry is None else _stock_link_json(result.entry)}


@router.get("/stock-links")
def stock_link_journal(
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
    record_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> dict:
    return {"entries": [_stock_link_json(entry) for entry in stock_links(uow_factory(), record_id=record_id, limit=limit)]}


# --- БД.ОБ: numbering, «Ещё такой же», «что обжимает» ---

class TargetPayload(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    key: str = Field(min_length=1, max_length=255)
    label: str = Field(default="", max_length=255)


@router.get("/tech-operations/equipment-number")
def equipment_number(
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
    type: Annotated[str, Query(max_length=255)] = "",
) -> dict:
    """The number a new record of this type gets if «Инв. номер» is left empty, and its row number."""
    with uow_factory() as uow:
        rows = [item.fields for item in uow.tech_operations.list(tab=TechOperationTab.EQUIPMENT, query="", limit=10_000)]
    return {"inventory": next_model_inventory(type.strip(), rows), "row": next_row_number(rows)}


@router.post("/tech-operations/{record_id}/duplicate", status_code=201)
def duplicate_tech_record(
    record_id: UUID,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    return _record_json(_run_edit(lambda: duplicate_equipment(record_id, principal.actor, now, uow_factory())))


@router.get("/tech-operations/crimp-codes")
def crimp_code_list(
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    return _run_edit(lambda: crimp_codes(uow_factory()))


@router.get("/tech-operations/{record_id}/crimps")
def crimp_list(
    record_id: UUID,
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    return _run_edit(lambda: crimp_overview(record_id, uow_factory()))


@router.post("/tech-operations/{record_id}/crimps/terminals/{terminal_id}")
def crimp_add_terminal(
    record_id: UUID, terminal_id: UUID,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    return _run_edit(lambda: change_terminal(record_id, terminal_id, add=True, actor=principal.actor, now=now, uow=uow_factory()))


@router.delete("/tech-operations/{record_id}/crimps/terminals/{terminal_id}",
               dependencies=[Depends(refuse_api_tokens("Токен API не снимает связи инструмента с контактами."))])
def crimp_remove_terminal(
    record_id: UUID, terminal_id: UUID,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    return _run_edit(lambda: change_terminal(record_id, terminal_id, add=False, actor=principal.actor, now=now, uow=uow_factory()))


@router.post("/tech-operations/{record_id}/crimps/targets")
def crimp_add_target(
    record_id: UUID, payload: TargetPayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    items = _run_edit(lambda: change_target(record_id, payload.model_dump(), add=True, actor=principal.actor, now=now, uow=uow_factory()))
    return {"targets": items}


@router.post("/tech-operations/{record_id}/crimps/targets/remove")
def crimp_remove_target(
    record_id: UUID, payload: TargetPayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    items = _run_edit(lambda: change_target(record_id, payload.model_dump(), add=False, actor=principal.actor, now=now, uow=uow_factory()))
    return {"targets": items}

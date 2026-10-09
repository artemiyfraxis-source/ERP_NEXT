"""SQLAlchemy persistence for editable maps and technology snapshots."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Sequence
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, Uuid,
    delete, false as sa_false, func, select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.shared.persistence import Base, SqlAlchemyAuditRepository

from .cable_structure import (
    LAYERS, PARAMS, CoaxAttenuation, CoaxCard, CoaxConnector, CoaxLayer, CoaxMarker, HookupWire, WireGauge,
)
from .domain import ReferenceEntry, ReferenceKind, TechOperationRecord, TechOperationTab


class ReferenceEntryRow(Base):
    __tablename__ = "reference_entries"
    __table_args__ = (
        Index("ux_reference_entries_kind_key", "kind", "normalized_key", unique=True),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(2048), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(512), nullable=False)
    value: Mapped[str] = mapped_column(String(512), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class TechOperationRecordRow(Base):
    __tablename__ = "tech_operation_records"
    __table_args__ = (
        Index("ix_tech_operation_records_tab_sort", "tab", "sort_key"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tab: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    display_text: Mapped[str] = mapped_column(String(2000), nullable=False)
    normalized_search: Mapped[str] = mapped_column(Text(), nullable=False)
    source_sheet: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_key: Mapped[str] = mapped_column(String(2000), nullable=False)
    fields: Mapped[dict] = mapped_column(JSON, nullable=False)
    mirror_key: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    edited_in_erp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())


class TechOperationSnapshotRow(Base):
    __tablename__ = "tech_operation_snapshots"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)


class SqlAlchemyReferenceRepository:
    def __init__(self, session: Session): self.session = session
    def list(self) -> list[ReferenceEntry]:
        rows = self.session.scalars(select(ReferenceEntryRow).order_by(ReferenceEntryRow.kind, ReferenceEntryRow.position, ReferenceEntryRow.id))
        return [ReferenceEntry(row.id, ReferenceKind(row.kind), row.key, row.normalized_key, row.value, row.position) for row in rows]
    def replace(self, entries: tuple[ReferenceEntry, ...]) -> None:
        self.session.execute(delete(ReferenceEntryRow))
        self.session.add_all(ReferenceEntryRow(
            id=entry.id, kind=entry.kind.value, key=entry.key, normalized_key=entry.normalized_key,
            value=entry.value, position=entry.position,
        ) for entry in entries)


class SqlAlchemyTechOperationRepository:
    def __init__(self, session: Session): self.session = session
    @staticmethod
    def _domain(row: TechOperationRecordRow) -> TechOperationRecord:
        return TechOperationRecord(row.id, TechOperationTab(row.tab), row.display_text, row.normalized_search, row.source_sheet, row.sort_key, row.fields, row.mirror_key, row.edited_in_erp)
    def list(self, *, tab: TechOperationTab | None, query: str, limit: int) -> list[TechOperationRecord]:
        statement = select(TechOperationRecordRow)
        if tab is not None: statement = statement.where(TechOperationRecordRow.tab == tab.value)
        normalized = " ".join(str(query or "").casefold().split())
        if normalized: statement = statement.where(func.lower(TechOperationRecordRow.normalized_search).contains(normalized))
        rows = self.session.scalars(statement.order_by(TechOperationRecordRow.tab, TechOperationRecordRow.sort_key, TechOperationRecordRow.id).limit(limit))
        return [self._domain(row) for row in rows]
    def counts(self) -> dict[TechOperationTab, int]:
        rows = self.session.execute(select(TechOperationRecordRow.tab, func.count()).group_by(TechOperationRecordRow.tab))
        return {TechOperationTab(tab): count for tab, count in rows}
    def get(self, record_id: UUID) -> TechOperationRecord | None:
        row = self.session.get(TechOperationRecordRow, record_id)
        return None if row is None else self._domain(row)
    def save(self, record: TechOperationRecord) -> None:
        row = self.session.get(TechOperationRecordRow, record.id)
        if row is None: row = TechOperationRecordRow(id=record.id); self.session.add(row)
        row.tab = record.tab.value; row.display_text = record.display_text
        row.normalized_search = record.normalized_search; row.source_sheet = record.source_sheet
        row.sort_key = record.sort_key; row.fields = dict(record.fields)
        row.mirror_key = record.mirror_key; row.edited_in_erp = record.edited_in_erp
    def replace(self, records: tuple[TechOperationRecord, ...], *, schema_version: int, source_updated_at: datetime | None, imported_at: datetime, keep_tabs: tuple[TechOperationTab, ...] = (), preserve_erp_edits: bool = False) -> dict[str, int]:
        """Replaces the records of every tab not in `keep_tabs`. With `preserve_erp_edits` the rows edited/created in
        ERP stay untouched and a mirror record whose `mirror_key` belongs to an edited row is not inserted again;
        returns {tab: skipped mirror records}."""
        skipped: dict[str, int] = {}
        scope = TechOperationRecordRow.tab.not_in([tab.value for tab in keep_tabs]) if keep_tabs else None
        statement = delete(TechOperationRecordRow)
        if scope is not None: statement = statement.where(scope)
        if preserve_erp_edits:
            statement = statement.where(TechOperationRecordRow.edited_in_erp.is_(False))
            protected = {(row.tab, row.mirror_key) for row in self.session.execute(
                select(TechOperationRecordRow.tab, TechOperationRecordRow.mirror_key).where(TechOperationRecordRow.edited_in_erp.is_(True), TechOperationRecordRow.mirror_key != ""))}
            kept = []
            for record in records:
                if (record.tab.value, record.mirror_key) in protected:
                    skipped[record.tab.value] = skipped.get(record.tab.value, 0) + 1
                else:
                    kept.append(record)
            records = tuple(kept)
        self.session.execute(statement)
        self.session.add_all(TechOperationRecordRow(
            id=record.id, tab=record.tab.value, display_text=record.display_text,
            normalized_search=record.normalized_search, source_sheet=record.source_sheet,
            sort_key=record.sort_key, fields=record.fields, mirror_key=record.mirror_key, edited_in_erp=False,
        ) for record in records)
        meta = self.session.get(TechOperationSnapshotRow, "current")
        if meta is None: meta = TechOperationSnapshotRow(name="current"); self.session.add(meta)
        meta.schema_version = schema_version; meta.source_updated_at = source_updated_at
        meta.imported_at = imported_at
        meta.record_count = len(records)
        self.session.flush()
        if keep_tabs or preserve_erp_edits:  # rows this snapshot did not touch still count
            meta.record_count = self.session.scalar(select(func.count()).select_from(TechOperationRecordRow)) or 0
        return skipped
    def metadata(self) -> dict | None:
        row = self.session.get(TechOperationSnapshotRow, "current")
        if row is None: return None
        return {"schema_version": row.schema_version, "source_updated_at": row.source_updated_at, "imported_at": row.imported_at, "record_count": row.record_count}


# --- structured cable reference (cable_structure.py; migration 0089) ---

class WireGaugeRow(Base):
    __tablename__ = "wire_gauges"
    __table_args__ = (
        UniqueConstraint("awg", name="ux_wire_gauges_awg"),
        UniqueConstraint("section_mm2", name="ux_wire_gauges_section"),
        CheckConstraint("section_mm2 > 0", name="ck_wire_gauges_section_positive"),
        CheckConstraint("conductor_d_mm IS NULL OR conductor_d_mm > 0", name="ck_wire_gauges_conductor_positive"),
        CheckConstraint("awg IS NULL OR awg <> ''", name="ck_wire_gauges_awg_not_blank"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    awg: Mapped[str | None] = mapped_column(String(8), nullable=True)
    section_mm2: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    conductor_d_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    strands: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class HookupWireRow(Base):
    __tablename__ = "hookup_wires"
    __table_args__ = (
        UniqueConstraint("mark_key", name="ux_hookup_wires_mark_key"),
        CheckConstraint("series <> ''", name="ck_hookup_wires_series_not_blank"),
        CheckConstraint("outer_d_mm IS NULL OR outer_d_mm > 0", name="ck_hookup_wires_outer_positive"),
        CheckConstraint("voltage_v IS NULL OR voltage_v > 0", name="ck_hookup_wires_voltage_positive"),
        CheckConstraint("temp_min_c IS NULL OR temp_max_c IS NULL OR temp_min_c < temp_max_c", name="ck_hookup_wires_temp_range"),
        Index("ix_hookup_wires_gauge_id", "gauge_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    mark: Mapped[str] = mapped_column(String(512), nullable=False)
    mark_key: Mapped[str] = mapped_column(String(512), nullable=False)
    series: Mapped[str] = mapped_column(String(255), nullable=False)
    gauge_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("wire_gauges.id", ondelete="RESTRICT"), nullable=False)
    insulation: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    outer_d_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    voltage_v: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temp_min_c: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temp_max_c: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CoaxCableRow(Base):
    __tablename__ = "coax_cables"
    __table_args__ = (
        UniqueConstraint("name_key", name="ux_coax_cables_name_key"),
        CheckConstraint("name <> ''", name="ck_coax_cables_name_not_blank"),
        CheckConstraint("span_from_mhz IS NULL OR span_from_mhz > 0", name="ck_coax_cables_span_from_positive"),
        CheckConstraint("span_to_ghz IS NULL OR span_to_ghz > 0", name="ck_coax_cables_span_to_positive"),
        CheckConstraint("span_from_mhz IS NULL OR span_to_ghz IS NULL OR span_from_mhz < span_to_ghz * 1000",
                        name="ck_coax_cables_span_order"),
        CheckConstraint("fc_ghz IS NULL OR fc_ghz > 0", name="ck_coax_cables_fc_positive"),
        CheckConstraint("impedance_ohm IS NULL OR impedance_ohm > 0", name="ck_coax_cables_impedance_positive"),
        CheckConstraint("velocity_pct IS NULL OR (velocity_pct > 0 AND velocity_pct <= 100)", name="ck_coax_cables_velocity_pct"),
        CheckConstraint("shielding_db IS NULL OR shielding_db > 0", name="ck_coax_cables_shielding_positive"),
        CheckConstraint("bend_static_mm IS NULL OR bend_static_mm > 0", name="ck_coax_cables_bend_static_positive"),
        CheckConstraint("bend_dynamic_mm IS NULL OR bend_dynamic_mm > 0", name="ck_coax_cables_bend_dynamic_positive"),
        CheckConstraint("temp_min_c IS NULL OR temp_max_c IS NULL OR temp_min_c < temp_max_c", name="ck_coax_cables_temp_range"),
        CheckConstraint("voltage_max_v IS NULL OR voltage_max_v > 0", name="ck_coax_cables_voltage_positive"),
        CheckConstraint("weight_g_m IS NULL OR weight_g_m > 0", name="ck_coax_cables_weight_positive"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_key: Mapped[str] = mapped_column(String(255), nullable=False)
    family: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    span_from_mhz: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    span_to_ghz: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    span_note: Mapped[str] = mapped_column(String(2000), nullable=False, default="", server_default="")
    fc_ghz: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    impedance_ohm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    velocity_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    shielding_db: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    bend_static_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    bend_dynamic_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    temp_min_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 1), nullable=True)
    temp_max_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 1), nullable=True)
    voltage_max_v: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    weight_g_m: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    notes: Mapped[str] = mapped_column(Text(), nullable=False, default="", server_default="")
    sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    changes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CoaxCableLayerRow(Base):
    __tablename__ = "coax_cable_layers"
    __table_args__ = (
        UniqueConstraint("cable_id", "layer", name="ux_coax_cable_layers_cable_layer"),
        CheckConstraint("layer IN ('core', 'dielectric', 'shield', 'jacket')", name="ck_coax_cable_layers_layer"),
        CheckConstraint("diameter_mm IS NULL OR diameter_mm > 0", name="ck_coax_cable_layers_diameter_positive"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    cable_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("coax_cables.id", ondelete="CASCADE"), nullable=False)
    layer: Mapped[str] = mapped_column(String(16), nullable=False)
    material: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    diameter_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)


class CoaxCableConnectorRow(Base):
    __tablename__ = "coax_cable_connectors"
    __table_args__ = (
        UniqueConstraint("cable_id", "family", name="ux_coax_cable_connectors_cable_family"),
        CheckConstraint("family <> ''", name="ck_coax_cable_connectors_family_not_blank"),
        CheckConstraint("max_ghz IS NULL OR max_ghz > 0", name="ck_coax_cable_connectors_max_positive"),
        CheckConstraint("span_to_ghz IS NULL OR span_to_ghz > 0", name="ck_coax_cable_connectors_span_positive"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    cable_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("coax_cables.id", ondelete="CASCADE"), nullable=False)
    family: Mapped[str] = mapped_column(String(128), nullable=False)
    max_ghz: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    max_note: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    span_to_ghz: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class CoaxCableMarkerRow(Base):
    __tablename__ = "coax_cable_markers"
    __table_args__ = (
        UniqueConstraint("cable_id", "freq_mhz", name="ux_coax_cable_markers_cable_freq"),
        CheckConstraint("freq_mhz IS NULL OR freq_mhz > 0", name="ck_coax_cable_markers_freq_positive"),
        CheckConstraint("vswr_max IS NULL OR vswr_max >= 1", name="ck_coax_cable_markers_vswr"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    cable_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("coax_cables.id", ondelete="CASCADE"), nullable=False)
    freq_mhz: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)  # NULL — the «Peak» marker
    vswr_max: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    note: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class CoaxCableAttenuationRow(Base):
    __tablename__ = "coax_cable_attenuation"
    __table_args__ = (
        UniqueConstraint("cable_id", "freq_mhz", "kind", name="ux_coax_cable_attenuation_cable_freq_kind"),
        CheckConstraint("freq_mhz > 0", name="ck_coax_cable_attenuation_freq_positive"),
        CheckConstraint("kind IN ('nominal', 'max')", name="ck_coax_cable_attenuation_kind"),
        CheckConstraint("value > 0", name="ck_coax_cable_attenuation_value_positive"),
        CheckConstraint("unit IN ('dB/m', 'dB/100m', 'dB/100ft', 'dB/ft')", name="ck_coax_cable_attenuation_unit"),
        CheckConstraint("ref_length_m > 0", name="ck_coax_cable_attenuation_length_positive"),
        CheckConstraint("loss_db_m > 0", name="ck_coax_cable_attenuation_loss_positive"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    cable_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("coax_cables.id", ondelete="CASCADE"), nullable=False)
    freq_mhz: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    ref_length_m: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    temp_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 1), nullable=True)
    loss_db_m: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    derived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    interpolated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    source: Mapped[str] = mapped_column(String(1000), nullable=False, default="", server_default="")


def _gauge(row: WireGaugeRow) -> WireGauge:
    return WireGauge(row.id, row.awg, Decimal(row.section_mm2), None if row.conductor_d_mm is None else Decimal(row.conductor_d_mm), row.strands, row.position)


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class SqlAlchemyCableStructureRepository:
    def __init__(self, session: Session): self.session = session

    def gauges(self) -> list[WireGauge]:
        rows = self.session.scalars(select(WireGaugeRow).order_by(WireGaugeRow.position, WireGaugeRow.section_mm2))
        return [_gauge(row) for row in rows]

    def save_gauge(self, gauge: WireGauge) -> None:
        row = self.session.get(WireGaugeRow, gauge.id)
        if row is None: row = WireGaugeRow(id=gauge.id); self.session.add(row)
        row.awg = gauge.awg; row.section_mm2 = gauge.section_mm2; row.conductor_d_mm = gauge.conductor_d_mm
        row.strands = gauge.strands; row.position = gauge.position
        self.session.flush()

    def delete_gauge(self, gauge_id: UUID) -> None:
        self.session.execute(delete(WireGaugeRow).where(WireGaugeRow.id == gauge_id))
        self.session.flush()

    def gauge_usage(self) -> dict[UUID, int]:
        rows = self.session.execute(select(HookupWireRow.gauge_id, func.count()).group_by(HookupWireRow.gauge_id))
        return {gauge_id: count for gauge_id, count in rows}

    def hookups(self) -> list[HookupWire]:
        return [self._hookup(row) for row in self.session.scalars(select(HookupWireRow).order_by(HookupWireRow.mark_key))]

    def hookup(self, mark_key: str) -> HookupWire | None:
        row = self.session.scalar(select(HookupWireRow).where(HookupWireRow.mark_key == mark_key))
        return None if row is None else self._hookup(row)

    @staticmethod
    def _hookup(row: HookupWireRow) -> HookupWire:
        return HookupWire(row.id, row.mark, row.mark_key, row.series, row.gauge_id, row.insulation, _dec(row.outer_d_mm),
                          row.voltage_v, row.temp_min_c, row.temp_max_c)

    def save_hookup(self, wire: HookupWire, now: datetime) -> None:
        row = self.session.get(HookupWireRow, wire.id)
        if row is None: row = HookupWireRow(id=wire.id); self.session.add(row)
        row.mark = wire.mark; row.mark_key = wire.mark_key; row.series = wire.series; row.gauge_id = wire.gauge_id
        row.insulation = wire.insulation; row.outer_d_mm = wire.outer_d_mm; row.voltage_v = wire.voltage_v
        row.temp_min_c = wire.temp_min_c; row.temp_max_c = wire.temp_max_c; row.updated_at = now
        self.session.flush()

    def cards(self) -> list[CoaxCard]:
        cables = list(self.session.scalars(select(CoaxCableRow).order_by(CoaxCableRow.position, CoaxCableRow.name)))
        ids = [row.id for row in cables]
        children: dict[UUID, dict[str, list]] = {cable_id: {"layers": [], "connectors": [], "markers": [], "attenuation": []} for cable_id in ids}
        if ids:
            order = {layer: index for index, layer in enumerate(LAYERS)}
            for row in self.session.scalars(select(CoaxCableLayerRow).where(CoaxCableLayerRow.cable_id.in_(ids))):
                children[row.cable_id]["layers"].append(row)
            for row in self.session.scalars(select(CoaxCableConnectorRow).where(CoaxCableConnectorRow.cable_id.in_(ids)).order_by(CoaxCableConnectorRow.position)):
                children[row.cable_id]["connectors"].append(row)
            for row in self.session.scalars(select(CoaxCableMarkerRow).where(CoaxCableMarkerRow.cable_id.in_(ids)).order_by(CoaxCableMarkerRow.position)):
                children[row.cable_id]["markers"].append(row)
            for row in self.session.scalars(select(CoaxCableAttenuationRow).where(CoaxCableAttenuationRow.cable_id.in_(ids)).order_by(CoaxCableAttenuationRow.freq_mhz, CoaxCableAttenuationRow.kind)):
                children[row.cable_id]["attenuation"].append(row)
        else:
            order = {}
        result = []
        for row in cables:
            own = children[row.id]
            result.append(CoaxCard(
                id=row.id, name=row.name, name_key=row.name_key, family=row.family,
                layers=tuple(CoaxLayer(item.layer, item.material, _dec(item.diameter_mm))
                             for item in sorted(own["layers"], key=lambda item: order.get(item.layer, 9))),
                connectors=tuple(CoaxConnector(item.family, _dec(item.max_ghz), item.max_note, _dec(item.span_to_ghz)) for item in own["connectors"]),
                markers=tuple(CoaxMarker(_dec(item.freq_mhz), _dec(item.vswr_max), item.note) for item in own["markers"]),
                attenuation=tuple(CoaxAttenuation(
                    _dec(item.freq_mhz), item.kind, _dec(item.value), item.unit, _dec(item.ref_length_m), _dec(item.temp_c),
                    _dec(item.loss_db_m), item.derived, item.interpolated, item.source) for item in own["attenuation"]),
                span_from_mhz=_dec(row.span_from_mhz), span_to_ghz=_dec(row.span_to_ghz), span_note=row.span_note,
                fc_ghz=_dec(row.fc_ghz), notes=row.notes, sources=tuple(row.sources or ()), confidence=row.confidence,
                verified=row.verified, changes=tuple(row.changes or ()),
                params={key: _dec(getattr(row, key)) for key in PARAMS if getattr(row, key) is not None},
                position=row.position,
            ))
        return result

    def replace_cards(self, cards: Sequence[CoaxCard], now: datetime) -> dict[str, int]:
        """Idempotent seed: a card per name key — kept id, its rows and children replaced by the file's; cards the
        file no longer names are removed with their children."""
        existing = {row.name_key: row for row in self.session.scalars(select(CoaxCableRow))}
        keys = {card.name_key for card in cards}
        removed = [row.id for key, row in existing.items() if key not in keys]
        if removed:
            self.session.execute(delete(CoaxCableRow).where(CoaxCableRow.id.in_(removed)))
        created = updated = 0
        for card in cards:
            row = existing.get(card.name_key)
            if row is None:
                row = CoaxCableRow(id=card.id); self.session.add(row); created += 1
            else:
                updated += 1
                for child in (CoaxCableLayerRow, CoaxCableConnectorRow, CoaxCableMarkerRow, CoaxCableAttenuationRow):
                    self.session.execute(delete(child).where(child.cable_id == row.id))
            row.name = card.name; row.name_key = card.name_key; row.family = card.family
            row.span_from_mhz = card.span_from_mhz; row.span_to_ghz = card.span_to_ghz; row.span_note = card.span_note
            row.fc_ghz = card.fc_ghz; row.notes = card.notes; row.sources = list(card.sources)
            row.confidence = card.confidence; row.verified = card.verified; row.changes = list(card.changes)
            row.position = card.position; row.imported_at = now
            for key in PARAMS:
                setattr(row, key, card.params.get(key))
            self.session.flush()
            self.session.add_all(CoaxCableLayerRow(id=uuid4(), cable_id=row.id, layer=item.layer, material=item.material,
                                                   diameter_mm=item.diameter_mm) for item in card.layers)
            self.session.add_all(CoaxCableConnectorRow(id=uuid4(), cable_id=row.id, family=item.family, max_ghz=item.max_ghz,
                                                       max_note=item.max_note, span_to_ghz=item.span_to_ghz, position=index)
                                 for index, item in enumerate(card.connectors))
            self.session.add_all(CoaxCableMarkerRow(id=uuid4(), cable_id=row.id, freq_mhz=item.freq_mhz, vswr_max=item.vswr_max,
                                                    note=item.note, position=index) for index, item in enumerate(card.markers))
            self.session.add_all(CoaxCableAttenuationRow(
                id=uuid4(), cable_id=row.id, freq_mhz=item.freq_mhz, kind=item.kind, value=item.value, unit=item.unit,
                ref_length_m=item.ref_length_m, temp_c=item.temp_c, loss_db_m=item.loss_db_m, derived=item.derived,
                interpolated=item.interpolated, source=item.source) for item in card.attenuation)
        self.session.flush()
        return {"created": created, "updated": updated, "removed": len(removed)}


class SqlAlchemyReferencesUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory; self.session: Session | None = None; self.committed = False
    def __enter__(self):
        self.committed = False; self.session = self.session_factory()
        self.references = SqlAlchemyReferenceRepository(self.session)
        self.tech_operations = SqlAlchemyTechOperationRepository(self.session)
        self.cable_structure = SqlAlchemyCableStructureRepository(self.session)
        self.audit = SqlAlchemyAuditRepository(self.session)
        return self
    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.session is None: return
        if exc_type is not None or not self.committed: self.session.rollback()
        self.session.close()
    def commit(self) -> None:
        if self.session is None: raise RuntimeError("Unit of Work has not been entered")
        self.session.commit(); self.committed = True

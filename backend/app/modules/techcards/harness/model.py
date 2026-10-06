"""The ERP harness model: one harness design — connectors with contacts, wires with two ends, splices, screens,
pairs, multicore cables, coax strip profiles, protective coverings and the manufacturing route.

Pure data, no web/DB imports. The vocabulary follows our generator: a wire runs from side A (`end_a`) to side B
(`end_b`). The shape mirrors the TECHMAP-GRAPHER harness document (schemaVersion 1, © Василий, SaveOurSouls,
commit e63befb, src/Techmap.Client/src/editor/model.ts) so its export reads in and writes back losslessly: every
entity keeps the original JSON object in `raw`, and the serializer only overwrites the fields modelled here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal, Mapping

EndKind = Literal["contact", "junction", "screen", "free"]
Side = Literal["A", "B"]

EMPTY: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class CatalogPin:
    """A catalog record pinned to an immutable snapshot (wire, cable, covering, strip profile, operation, terminal)."""
    entity_type: str
    source_key: str
    display_name: str
    source_id: str = ""
    snapshot_id: str = ""
    record_id: str = ""


@dataclass(frozen=True)
class TerminalSnapshot:
    """Terminal data kept with a contact. `l_minus_mm` is signed (≤ 0): it always shortens the blank."""
    manufacturer: str = ""
    series: str = ""
    description: str = ""
    article: str = ""
    l_plus_mm: Decimal | None = None
    l_minus_mm: Decimal | None = None


@dataclass(frozen=True)
class Contact:
    id: str
    number: int | None
    circuit: str = ""
    contact_type: str = ""
    name: str = ""
    terminal_article: str = ""
    terminal: TerminalSnapshot | None = None
    wire: str = ""
    wire_section: str = ""
    color: str = ""
    status: str = ""
    raw: Mapping[str, Any] = field(default=EMPTY, repr=False, compare=False)


@dataclass(frozen=True)
class Connector:
    id: str
    designation: str
    part_number: str
    contacts: tuple[Contact, ...] = ()
    library: str = "free"          # template | series | free
    template_code: str = ""
    template_name: str = ""
    template_article: str = ""
    template_version: int | None = None
    raw: Mapping[str, Any] = field(default=EMPTY, repr=False, compare=False)

    def contact(self, contact_id: str) -> Contact | None:
        return next((item for item in self.contacts if item.id == contact_id), None)


@dataclass(frozen=True)
class Endpoint:
    kind: EndKind
    connector_id: str = ""
    contact_id: str = ""
    junction_id: str = ""
    screen_id: str = ""
    screen_side: str = ""


@dataclass(frozen=True)
class StripLayer:
    """L of one coax layer, cumulative from the cut end; layers run from the centre conductor outwards."""
    index: int
    length_mm: Decimal
    diameter_mm: Decimal | None = None


@dataclass(frozen=True)
class StripProfile:
    pin: CatalogPin
    layers: tuple[StripLayer, ...]
    raw: Mapping[str, Any] = field(default=EMPTY, repr=False, compare=False)


@dataclass(frozen=True)
class Wire:
    id: str
    end_a: Endpoint
    end_b: Endpoint
    circuit: str = ""
    color: str = ""
    material: CatalogPin | None = None
    length_mm: Decimal | None = None             # drawing length; None = not measured yet
    correction_a_mm: Decimal = Decimal(0)        # signed technological allowance at end A
    correction_b_mm: Decimal = Decimal(0)
    rounding_step_mm: Decimal = Decimal(1)
    strip_a: StripProfile | None = None
    strip_b: StripProfile | None = None
    raw: Mapping[str, Any] = field(default=EMPTY, repr=False, compare=False)

    def end(self, side: Side) -> Endpoint:
        return self.end_a if side == "A" else self.end_b

    def correction(self, side: Side) -> Decimal:
        return self.correction_a_mm if side == "A" else self.correction_b_mm

    def strip(self, side: Side) -> StripProfile | None:
        return self.strip_a if side == "A" else self.strip_b


@dataclass(frozen=True)
class Junction:
    """A splice: two or more wires joined at one point (wires may end in it or pass through)."""
    id: str
    label: str
    wire_ids: tuple[str, ...]
    raw: Mapping[str, Any] = field(default=EMPTY, repr=False, compare=False)


@dataclass(frozen=True)
class Screen:
    id: str
    label: str
    wire_ids: tuple[str, ...]
    terminal_side: str = "above"
    raw: Mapping[str, Any] = field(default=EMPTY, repr=False, compare=False)


@dataclass(frozen=True)
class DiffPair:
    """A differential pair; on the drawing it is a twisted pair (the only twist entity of the source)."""
    id: str
    wire_ids: tuple[str, str]
    step: Decimal | None = None
    amplitude: Decimal | None = None
    variant: int = 1
    raw: Mapping[str, Any] = field(default=EMPTY, repr=False, compare=False)


@dataclass(frozen=True)
class Cable:
    """A multicore cable: its cores stay wires; the blank, cut and sheath strip belong to the cable."""
    id: str
    member_wire_ids: tuple[str, ...]
    material: CatalogPin | None = None
    length_mm: Decimal | None = None
    correction_a_mm: Decimal = Decimal(0)
    correction_b_mm: Decimal = Decimal(0)
    rounding_step_mm: Decimal = Decimal(1)
    has_sheath_strip: bool = False
    sheath_a_mm: Decimal | None = None
    sheath_b_mm: Decimal | None = None
    raw: Mapping[str, Any] = field(default=EMPTY, repr=False, compare=False)


@dataclass(frozen=True)
class CoveringSpan:
    segment_id: str
    start: Decimal
    stop: Decimal
    start_anchor: int | None = None
    stop_anchor: int | None = None


@dataclass(frozen=True)
class Covering:
    """Heat-shrink, braid, nylon sleeve, tape… laid over physical segments of the harness."""
    id: str
    name: str
    kind: str
    spans: tuple[CoveringSpan, ...]
    material: CatalogPin | None = None
    color: str = ""
    length_mode: str = ""                    # manual | auto | "" (legacy: manual when a length is stored)
    length_mm: Decimal | None = None
    raw: Mapping[str, Any] = field(default=EMPTY, repr=False, compare=False)


@dataclass(frozen=True)
class Dimension:
    segment_id: str
    start: int
    stop: int
    length_mm: Decimal | None
    auxiliary: bool = False


@dataclass(frozen=True)
class Topology:
    """Physical layout reduced to what production needs: which connector sits at each node, which nodes a segment
    joins, which segments a common pipe covers, the segments each wire runs through, and drawing dimensions."""
    node_connector: Mapping[str, str]
    segment_nodes: Mapping[str, tuple[str, str]]
    pipe_segments: Mapping[str, tuple[str, ...]]
    wire_segments: Mapping[str, tuple[str, ...]]
    coverings: tuple[Covering, ...]
    dimensions: tuple[Dimension, ...]
    raw: Mapping[str, Any] = field(default=EMPTY, repr=False, compare=False)


@dataclass(frozen=True)
class RouteOperation:
    id: str
    mode: str
    note: str = ""
    operation: CatalogPin | None = None      # БД.ОП record: source_key is the operation code


@dataclass(frozen=True)
class TerminalRequirement:
    wire_id: str
    side: Side
    terminal_article: str
    strip_length_mm: Decimal | None = None


@dataclass(frozen=True)
class RouteRow:
    """A block of the route: a semi-finished product or an assembly, with its inputs and operations."""
    id: str
    kind: str                                # semiFinished | assembly
    title: str
    sources: tuple[tuple[str, str], ...]     # (kind, id): wire | cable | covering | connector
    depends_on: tuple[str, ...]
    operations: tuple[RouteOperation, ...]
    role: str = ""
    index: str = ""
    quantity: Decimal | None = None
    reserve: Decimal | None = None
    operation_time_min: Decimal | None = None
    comment: str = ""
    prepared: bool = False
    terminal_requirements: tuple[TerminalRequirement, ...] = ()
    raw: Mapping[str, Any] = field(default=EMPTY, repr=False, compare=False)


@dataclass(frozen=True)
class Route:
    status: str
    rows: tuple[RouteRow, ...]
    contract_version: int | None = 1
    legacy_expanded: bool = False
    raw: Mapping[str, Any] = field(default=EMPTY, repr=False, compare=False)


@dataclass(frozen=True)
class HarnessDesign:
    connectors: tuple[Connector, ...] = ()
    wires: tuple[Wire, ...] = ()
    cables: tuple[Cable, ...] = ()
    junctions: tuple[Junction, ...] = ()
    screens: tuple[Screen, ...] = ()
    diff_pairs: tuple[DiffPair, ...] = ()
    topology: Topology | None = None
    route: Route | None = None
    schema_version: int = 1
    writer_contract: int | None = None
    raw: Mapping[str, Any] = field(default=EMPTY, repr=False, compare=False)

    def connector(self, connector_id: str) -> Connector | None:
        return next((item for item in self.connectors if item.id == connector_id), None)

    def wire(self, wire_id: str) -> Wire | None:
        return next((item for item in self.wires if item.id == wire_id), None)

    def contact_at(self, end: Endpoint) -> tuple[Connector | None, Contact | None]:
        connector = self.connector(end.connector_id) if end.kind == "contact" else None
        return connector, connector.contact(end.contact_id) if connector else None

    def cable_of(self, wire_id: str) -> Cable | None:
        return next((cable for cable in self.cables if wire_id in cable.member_wire_ids), None)


@dataclass(frozen=True)
class SheetRow:
    """A row of «Данные из листа» (the position's specification): what the drawing is checked against."""
    id: str
    type: str = ""
    name: str = ""
    article: str = ""
    kd_article: str = ""                     # «по КД» article
    quantity: Decimal | None = None

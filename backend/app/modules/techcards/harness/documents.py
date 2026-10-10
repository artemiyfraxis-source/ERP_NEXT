"""The documents of a harness drawing, built on the server from the drawing itself: «Таблица соединений» (one row per
wire), «Карта резки» (one row per physical blank — a wire outside a cable, a cable, a covering — with its cut/strip
sheet) and «Спецификация» (the BOM: connectors, terminals, wire and cable material, coverings, extra positions).

Pure: a design document in (the stored JSON), rows out; nothing is saved. Cut length comes from `rules.cut_length`
(one source of truth with «Ручная сборка»), covering length from `rules.covering_length`, strip steps from
`rules.strip_steps` — over the model `snapshot.parse_design` reads.

The builders are TECHMAP-GRAPHER's (© Василий, SaveOurSouls, used with the author's permission), ported from
src/Techmap.Client/src/editor: drawing-documents.ts (buildDrawingBom, connectionTableEndLabel, connectionWireColor,
connectionWireMark/Section), cut-diagram.ts (buildCutDiagram), live-cut-list.ts (buildLiveCutList),
drawing-object-indices.ts, wire-reference-catalog.ts (builtInWireColors, resolveWireColorHex). The BOM row keys are
the browser's exactly — the drawing's leaders, order and edited texts are keyed by them. Golden rows of the upstream
test scenarios pin the port (backend/tests/techcards/harness_documents_golden.json). Deliberate differences: a value
the length rules refuse leaves the length unknown with a warning instead of failing the whole document; a covering
with no spans has no measured length (rules.covering_length).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import json
import re
from typing import Any, Mapping, Sequence

from .model import Cable, Connector, Contact, Endpoint, HarnessDesign, StripProfile, Wire
from .rules import HarnessRuleError, blank_cut, covering_length, sheath_strip, strip_steps
from .snapshot import parse_design

KINDS = ("connections", "cut", "bom")
UNITS = ("шт.", "м", "г", "кг", "л")

# Column ids and titles of «Таблица соединений» (connection-table-settings.ts, connectionTableColumnLabels).
CONNECTION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("index", "Индекс (Wn)"), ("mark", "Марка"), ("section", "Сечение"), ("marking", "Маркировка"),
    ("from", "Откуда"), ("color", "Цвет"), ("to", "Куда"), ("length", "Длина, мм"), ("route", "Маршрут"),
)

# builtInWireColors (wire-reference-catalog.ts): name → hex.
BUILT_IN_COLORS: tuple[tuple[str, str], ...] = (
    ("красный", "#D32F2F"), ("черный", "#202124"), ("белый", "#FFFFFF"), ("оранжевый", "#F57C00"),
    ("зеленый", "#388E3C"), ("фиолетовый", "#7B1FA2"), ("желтый", "#FBC02D"), ("голубой", "#29B6F6"),
    ("синий", "#1976D2"), ("коричневый", "#795548"), ("серый", "#757575"), ("розовый", "#EC407A"),
    ("бирюзовый", "#00A6A6"),
)
COVERING_PREFIX = {"heat-shrink": "ТУ", "nylon": "Н", "braid": "ОПЛ", "metal-braid": "МО", "tape": "Л", "band": "Б"}

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_HEX = re.compile(r"^#[0-9a-f]{6}$", re.IGNORECASE)
_LENGTH_PREFIXED = re.compile(r"^\d+:")


# ── small helpers ────────────────────────────────────────────────────────────────────────────────────────────────


def _map(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def key_of(*values: object) -> str:
    """JSON.stringify of the key parts, as the browser builds BOM row keys (no spaces, non-ASCII kept)."""
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def _metres(micrometres: int) -> int | float:
    """Micrometres of length → metres as a JSON number (Number(BigInt)/1e6 in the original — correctly rounded)."""
    value = micrometres / 1_000_000
    return int(value) if value == int(value) else value


def _um(value: Decimal) -> int:
    return int((value * 1000).to_integral_value())


def decode_article_parts(article: str) -> list[str]:
    """Length-prefixed БД key «n:text|n:text…» → its parts; [] when the text is not such a key (decodeArticleParts)."""
    parts: list[str] = []
    offset = 0
    while offset < len(article):
        colon = article.find(":", offset)
        if colon < 0 or not re.fullmatch(r"\d+", article[offset:colon]):
            return []
        end = colon + 1 + int(article[offset:colon])
        if end > len(article):
            return []
        parts.append(article[colon + 1:end])
        if end == len(article):
            return parts
        if article[end] != "|":
            return []
        offset = end + 1
    return []


def terminal_article_label(key: str) -> str:
    """terminalArticleLabel: a complete 4-part БД.ТЕР key shows the reel article, else the piece one; else as is."""
    parts = decode_article_parts(key)
    return (parts[1] or parts[2] or key) if len(parts) == 4 else key


def _normalize_search(value: str) -> str:
    text = value.lower().replace("ё", "е").replace(",", ".")
    text = re.sub(r"мм[²2]", "мм2", text)
    return re.sub(r"[^a-zа-я0-9.]+", " ", text, flags=re.IGNORECASE).strip()


def color_hex(value: str, fallback: str) -> str:
    """resolveWireColorHex over the built-in palette: #RRGGBB as is (upper case), a palette name → its hex."""
    text = value.strip()
    if _HEX.match(text):
        return text.upper()
    wanted = _normalize_search(text)
    return next((hex_ for name, hex_ in BUILT_IN_COLORS if _normalize_search(name) == wanted), fallback)


def _color_name(value: str) -> str:
    return next((name for name, hex_ in BUILT_IN_COLORS if hex_.lower() == value.lower()), value)


def _human_label(value: str, fallback: str) -> str:
    """Operator-facing names only: an internal UUID never shows in a table (humanTableLabel)."""
    text = value.strip()
    if not text or _UUID.fullmatch(text):
        return fallback
    return re.sub(r"\s{2,}", " ", _UUID.sub("", text)).strip() or fallback


# ── model access ─────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Doc:
    design: HarnessDesign
    raw: Mapping[str, Any]
    drawing: Mapping[str, Any] = field(default_factory=dict)

    def end_contacts(self, wire: Wire) -> list[Contact]:
        return [contact for end in (wire.end_a, wire.end_b) if (contact := self.design.contact_at(end)[1]) is not None]

    def segments_of(self, item_id: str) -> list[str]:
        topology = _map(self.raw.get("physicalTopology"))
        return [_str(_map(s).get("id")) for s in _list(topology.get("segments"))
                if _map(s).get("specificationItemId") == item_id]


def _read(document: Mapping[str, Any]) -> _Doc:
    design, _warnings = parse_design(document)
    return _Doc(design, document, _map(document.get("drawingDocuments")))


def _cut(item: Wire | Cable) -> tuple[Any, str | None]:
    """The blank's cut by `rules.cut_length`; a value the rules refuse → no length and the reason."""
    try:
        return blank_cut(item), None
    except HarnessRuleError as error:
        return None, str(error)


# ── «Таблица соединений» ─────────────────────────────────────────────────────────────────────────────────────────


def end_label(doc: _Doc, end: Endpoint) -> str:
    """connectionEndLabel: «Узел», «Экран» or «<соединитель>:<номер контакта>» without internal ids."""
    if end.kind == "junction":
        return "Узел"
    if end.kind == "screen":
        return "Экран"
    connector, contact = doc.design.contact_at(end) if end.kind == "contact" else (None, None)
    number = contact.number if contact is not None and contact.number is not None else "Контакт"
    return f"{_human_label(connector.designation if connector else '', 'Соединитель')}:{number}"


def table_end_label(doc: _Doc, end: Endpoint) -> str:
    """connectionTableEndLabel: the connector's designation with its article, «A (ARTICLE-A):1»."""
    if end.kind in ("junction", "screen"):
        return end_label(doc, end)
    connector, contact = doc.design.contact_at(end) if end.kind == "contact" else (None, None)
    article = connector.part_number.strip() if connector else ""
    designation = f"{connector.designation}{f' ({article})' if article else ''}" if connector else "Соединитель"
    number = contact.number if contact is not None and contact.number is not None else "Контакт"
    return f"{designation}:{number}"


def wire_color(doc: _Doc, wire: Wire) -> tuple[str, str]:
    """connectionWireColor: (label, hex) — the first end contact's colour (and second colour), else the wire's own."""
    contacts = doc.end_contacts(wire)
    primary = next((c.color.strip() for c in contacts if c.color.strip()), _str(wire.raw.get("color")) or wire.color)
    secondary = next((_str(c.raw.get("secondaryColor")).strip() for c in contacts
                      if _str(c.raw.get("secondaryColor")).strip()), "")
    label = " / ".join(item for item in (_color_name(primary), _color_name(secondary) if secondary else "") if item)
    return label or "—", color_hex(primary, _str(wire.raw.get("color")) or "#D9E2E7")


def wire_mark(doc: _Doc, wire: Wire) -> str:
    """The «Марка» of a wire: the first end contact's wire mark, else the pinned material's article."""
    mark = next((c.wire for c in doc.end_contacts(wire) if c.wire.strip()), "")
    if mark:
        return mark.strip()
    return _str(_map(wire.raw.get("materialBinding")).get("sourceKey")) if "materialBinding" in wire.raw else ""


def wire_section(doc: _Doc, wire: Wire) -> str:
    return next((c.wire_section for c in doc.end_contacts(wire) if c.wire_section.strip()), "").strip()


def connection_rows(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """«Таблица соединений»: one row per wire, in the drawing's order; the index is the wire's place, W1…Wn."""
    doc = _read(document)
    routed = doc.design.topology.wire_segments if doc.design.topology else {}
    rows = []
    for position, wire in enumerate(doc.design.wires, start=1):
        label, hex_ = wire_color(doc, wire)
        length = wire.raw.get("lengthMm")
        rows.append({
            "wire_id": wire.id, "index": f"W{position}", "mark": wire_mark(doc, wire) or "—",
            "section": wire_section(doc, wire) or "—", "marking": wire.circuit or "—",
            "from": table_end_label(doc, wire.end_a), "to": table_end_label(doc, wire.end_b),
            "color": label, "color_hex": hex_,
            "length_mm": length if isinstance(length, (int, float)) and not isinstance(length, bool) else None,
            "route": "Задан" if routed.get(wire.id) else "Не задан",
        })
    return rows


# ── «Карта резки» ────────────────────────────────────────────────────────────────────────────────────────────────


def _blanks(design: HarnessDesign) -> list[Wire | Cable]:
    """Physical blanks: a cable's cores are cut as the cable, so they are not blanks of their own (cutBlankIds)."""
    members = {wire_id for cable in design.cables for wire_id in cable.member_wire_ids}
    return [*(wire for wire in design.wires if wire.id not in members), *design.cables]


def _end(doc: _Doc, wire: Wire, side: str) -> dict[str, Any]:
    profile: StripProfile | None = wire.strip_a if side == "A" else wire.strip_b
    steps = [{"index": layer.index, "diameter_mm": _number(layer.diameter_mm), "length_mm": _number(layer.length_mm),
              "step_mm": _number(step)} for layer, step in strip_steps(profile.layers)] if profile else []
    name = profile.raw.get("displayName") if profile else None
    return {"label": end_label(doc, wire.end_a if side == "A" else wire.end_b),
            "profile": name if isinstance(name, str) else None, "steps": steps}


def _total_metres(cut_mm: Decimal | None, quantity: int) -> int | float | None:
    return None if cut_mm is None else _metres(_um(cut_mm) * quantity)


def cut_sheet(doc: _Doc, blank: Wire | Cable, quantity: int) -> dict[str, Any]:
    """One blank's cut and strip sheet (buildCutDiagram): length arithmetic, both ends with their strip layers, the
    cable sheath strip and member cores, and what to check."""
    cable = blank if isinstance(blank, Cable) else None
    wires = [w for w in doc.design.wires if w.id in cable.member_wire_ids] if cable else [blank]
    calculation, refused = _cut(blank)
    conductors = [{"id": w.id, "a": _end(doc, w, "A"), "b": _end(doc, w, "B")} for w in wires]  # type: ignore[arg-type]
    if cable is None:
        a, b = conductors[0]["a"], conductors[0]["b"]
    else:
        a = {"label": ", ".join(dict.fromkeys(c["a"]["label"] for c in conductors)), "profile": None, "steps": []}
        b = {"label": ", ".join(dict.fromkeys(c["b"]["label"] for c in conductors)), "profile": None, "steps": []}
    warnings: list[str] = []
    cut_mm = calculation.cut_mm if calculation else None
    if refused:
        warnings.append(f"{refused}.")
    if cut_mm is None:
        warnings.append("Исходная физическая длина не задана.")
    if blank.material is None:
        warnings.append("Материал заготовки не закреплён.")
    sheath = None
    if cable is not None:
        try:
            strip = sheath_strip(cable)
        except HarnessRuleError as error:
            strip, sheath = None, None
            warnings.append(f"{error}.")
        else:
            if strip is None:
                warnings.append("Снятие общей оболочки не задано.")
            else:
                total = calculation.unrounded_mm if calculation else None
                complete = strip.cut_a_mm is not None and strip.cut_b_mm is not None and total is not None
                sheath = {"a_mm": _number(strip.cut_a_mm), "b_mm": _number(strip.cut_b_mm), "total_mm": _number(total),
                          "complete": complete}
                if not complete:
                    warnings.append("Снятие общей оболочки заполнено не полностью.")
    for wire, conductor in zip(wires, conductors):
        missing_a, missing_b = not conductor["a"]["steps"], not conductor["b"]["steps"]
        if missing_a or missing_b:
            sides = "A/B" if missing_a and missing_b else "A" if missing_a else "B"
            warnings.append(f"{conductor['id']}: профиль разделки {sides} не задан.")
        if cut_mm is not None:
            stripped = sum(_um(profile.layers[-1].length_mm) for profile in (wire.strip_a, wire.strip_b)  # type: ignore[union-attr]
                           if profile is not None and profile.layers)
            if stripped > _um(cut_mm):
                warnings.append(f"{conductor['id']}: сумма разделки A/B превышает длину заготовки.")
    return {
        "object_id": blank.id, "member_wire_ids": [w.id for w in wires], "kind": "cable" if cable else "wire",
        "title": blank.id if cable else (blank.circuit or blank.id),  # type: ignore[union-attr]
        "material": blank.material.display_name if blank.material else None,
        "a": a, "b": b, "sheath": sheath, "conductors": conductors, "warnings": warnings,
    }


def _cut_item(blank_id: str, kind: str, title: str, pin: Mapping[str, Any] | None, length: Decimal | None,
              correction_a: Decimal, correction_b: Decimal, rounding: Decimal, cut_mm: Decimal | None,
              unrounded: Decimal | None, quantity: int) -> dict[str, Any]:
    warnings = [*(["material-missing"] if not pin else []), *(["length-missing"] if cut_mm is None else [])]
    return {
        "id": blank_id, "kind": kind, "title": title,
        "material": _str(pin.get("displayName")) if pin else None,
        "material_key": _str(pin.get("sourceKey")) if pin else None,
        "length_mm": _number(length), "correction_a_mm": _number(correction_a),
        "correction_b_mm": _number(correction_b), "rounding_step_mm": _number(rounding),
        "cut_mm": _number(cut_mm), "unrounded_mm": _number(unrounded), "pieces": quantity,
        "total_m": _total_metres(cut_mm, quantity), "status": "ready" if cut_mm is not None else "incomplete",
        "warnings": warnings,
    }


def cut_list(document: Mapping[str, Any], quantity: int = 1) -> dict[str, Any]:
    """«Карта резки» for `quantity` harnesses: wires outside cables, cables, then coverings (buildLiveCutList), and
    the cut/strip sheet of every wire and cable blank."""
    doc = _read(document)
    items, sheets = [], []
    for blank in _blanks(doc.design):
        calculation, _refused = _cut(blank)
        pin = _map(blank.raw.get("materialBinding")) or None
        title = blank.circuit if isinstance(blank, Wire) else "Кабель"
        items.append(_cut_item(
            blank.id, "wire" if isinstance(blank, Wire) else "cable", title, pin, blank.length_mm,
            blank.correction_a_mm, blank.correction_b_mm, blank.rounding_step_mm,
            calculation.cut_mm if calculation else None, calculation.unrounded_mm if calculation else None, quantity,
        ))
        sheets.append(cut_sheet(doc, blank, quantity))
    topology = doc.design.topology
    for covering in topology.coverings if topology else ():
        length = covering_length(covering, topology.dimensions)
        items.append(_cut_item(covering.id, "covering", _str(covering.raw.get("name")), _map(covering.raw.get("material")) or None,
                               length, Decimal(0), Decimal(0), Decimal("0.001"), length, length, quantity))
    return {"quantity": quantity, "items": items, "sheets": sheets,
            "status": "incomplete" if any(item["warnings"] for item in items) else "ready"}


# ── «Спецификация» ───────────────────────────────────────────────────────────────────────────────────────────────


@dataclass
class _Row:
    name: str
    unit: str
    note: str
    index: list[str] = field(default_factory=list)
    designation: list[str] = field(default_factory=list)
    micrometres: int = 0
    unknown: bool = False
    object_ids: list[str] = field(default_factory=list)

    def own(self, object_id: str) -> None:
        if object_id not in self.object_ids:
            self.object_ids.append(object_id)


class _Bom:
    def __init__(self) -> None:
        self.rows: dict[str, _Row] = {}

    def add(self, key: str, object_id: str, designation: str, name: str, amount_um: int | None, unit: str, note: str,
            index: str | None = None) -> _Row:
        index = designation if index is None else index
        row = self.rows.setdefault(key, _Row(name, unit, note))
        row.own(object_id)
        if index and index not in row.index:
            row.index.append(index)
        if designation and designation not in row.designation:
            row.designation.append(designation)
        row.unknown = row.unknown or amount_um is None
        if amount_um is not None:
            row.micrometres += amount_um
        return row


def _material_key(kind: str, pin: Mapping[str, Any], *extra: object) -> str:
    return key_of(kind, *(pin.get(name) for name in ("sourceId", "snapshotId", "snapshotSha256", "recordId", "sourceKey")),
                  *extra)


def _object_indices(doc: _Doc) -> dict[str, str]:
    """Readable indices (buildDrawingObjectIndices): wires W1…, cables K1…, coverings by kind ТУ1, ОПЛ1…"""
    result = {wire.id: f"W{i}" for i, wire in enumerate(doc.design.wires, start=1)}
    for i, cable in enumerate(doc.design.cables, start=1):
        result[cable.id] = f"K{i}"
    sequences: dict[str, int] = {}
    for covering in doc.design.topology.coverings if doc.design.topology else ():
        raw_kind = covering.raw.get("kind")
        kind = raw_kind if isinstance(raw_kind, str) else (
            "heat-shrink" if re.search("термоусад", _str(covering.raw.get("name")), re.IGNORECASE) else "")
        prefix = COVERING_PREFIX.get(kind, "ОБ")
        sequences[prefix] = sequences.get(prefix, 0) + 1
        result[covering.id] = f"{prefix}{sequences[prefix]}"
    return result


def _readable_article(article: str, display_name: str | None) -> str:
    parts = decode_article_parts(article)
    if parts:
        return " ".join(part for part in parts if part)
    return (display_name if display_name is not None else "—") if _LENGTH_PREFIXED.match(article) else article


def _connector_rows(bom: _Bom, connector: Connector) -> None:
    raw = connector.raw
    binding = _map(raw.get("libraryBinding"))
    mode = binding.get("mode")
    article = _map(binding.get("article"))
    designation = _str(raw.get("designation"))
    if mode == "template":
        key = key_of("connector", binding.get("templateId"), binding.get("templateVersion"),
                     binding.get("versionSha256"), article.get("sourceId"), article.get("entityType"),
                     article.get("articleKey"))
        description = _map(binding.get("snapshot")).get("name")
        note = f"Библиотека v{binding.get('templateVersion')}"
    elif mode == "series":
        key = key_of("series", binding.get("seriesId"), raw.get("partNumber"))
        description = raw.get("libraryCode") or binding.get("seriesId")
        note = "Встроенная серия"
    else:
        key = key_of("free", connector.id)
        description, note = None, "Без библиотечной привязки"
    description = "Соединитель" if description is None else str(description)
    contacts = _list(raw.get("contacts"))
    bom.add(key, connector.id, _str(raw.get("partNumber")) or "—", f"{len(contacts)} конт. — {description}",
            1_000_000, "шт.", note, designation)
    for contact in map(_map, contacts):
        terminal = _str(contact.get("terminalArticle"))
        if not terminal:
            continue
        if mode == "template":
            identity = key_of("terminal", binding.get("templateId"), binding.get("templateVersion"),
                              binding.get("versionSha256"), terminal,
                              _map(raw.get("terminalCatalog")).get("versionSha256") or "")
        else:
            identity = key_of("terminal-unpinned", connector.id, contact.get("id"))
        details = contact.get("terminalDetails")
        details = details if isinstance(details, Mapping) else None
        label = (_str(details.get("article")) if details else "") or terminal_article_label(terminal)
        if details:
            name = " · ".join(_str(details.get(k)) for k in ("manufacturer", "series", "description")
                              if _str(details.get(k)))
        else:
            parts = decode_article_parts(terminal)
            name = " · ".join(p for p in (parts[0] if parts else "", parts[3] if len(parts) > 3 else "") if p) or "Терминал"
        number = contact.get("number")
        bom.add(identity, connector.id, label, name, 1_000_000, "шт.",
                "Контакт закреплённой серии" if mode == "template" else "Терминал без закреплённого источника",
                f"{designation}:{'undefined' if number is None else _js_number(number)}")


def _js_number(value: object) -> str:
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def _blank_rows(bom: _Bom, doc: _Doc, blank: Wire | Cable, indices: Mapping[str, str]) -> None:
    calculation, _refused = _cut(blank)
    cut_mm = calculation.cut_mm if calculation else None
    is_wire = isinstance(blank, Wire)
    related = [blank] if is_wire else [w for wid in blank.member_wire_ids if (w := doc.design.wire(wid))]  # type: ignore[union-attr]
    contacts = [c for wire in related for c in doc.end_contacts(wire)]  # type: ignore[arg-type]
    marks = list(dict.fromkeys(c.wire.strip() for c in contacts if c.wire.strip()))
    sections = list(dict.fromkeys(c.wire_section.strip() for c in contacts if c.wire_section.strip()))
    colors = list(dict.fromkeys(
        joined for c in contacts
        if (joined := "/".join(v for v in (_str(c.raw.get("color")), _str(c.raw.get("secondaryColor"))) if v))
    ))
    attributes = ", ".join(v for v in ("/".join(marks), "/".join(sections), "/".join(colors)) if v)
    pin = _map(blank.raw.get("materialBinding")) or None
    key = _material_key("material", pin, attributes) if pin else key_of("material-unpinned", blank.id)
    article = _str(pin.get("sourceKey")) if pin else ""
    if is_wire:
        index = ", ".join(i for w in related if (i := indices.get(w.id, "")))
    else:
        index = indices.get(blank.id, "")
    display = pin.get("displayName") if pin else None
    readable = _readable_article(article, display if isinstance(display, str) else None)
    designation = " — ".join(v for v in (readable, attributes) if v) if pin else (attributes or "—")
    color_name = f", цвет {'/'.join(colors)}" if colors else ""
    name = f"{display if isinstance(display, str) else 'Материал не назначен'}{color_name}"
    row = bom.add(key, blank.id, designation, name, None if cut_mm is None else _um(cut_mm), "м",
                  "По длине заготовки" if pin else "Нет закреплённого материала", index)
    for segment in doc.segments_of(blank.id):
        row.own(segment)


def _covering_rows(bom: _Bom, doc: _Doc, indices: Mapping[str, str]) -> None:
    topology = doc.design.topology
    for covering in topology.coverings if topology else ():
        pin = _map(covering.raw.get("material")) or None
        encoded = _str(pin.get("sourceKey")) if pin else ""
        display = _str(pin.get("displayName")) if pin and isinstance(pin.get("displayName"), str) else None
        parts = decode_article_parts(encoded)
        if parts:
            article = " ".join(p for p in parts if p)
        elif encoded:
            article = (display if display is not None else "—") if _LENGTH_PREFIXED.match(encoded) else encoded
        else:
            article = "—"
        detail = (display or "").strip()
        suffix = ""
        if detail and detail != article:
            suffix = re.sub(rf"^{re.escape(article)}\s*(?:[·—,-]\s*)?", "", detail, count=1, flags=re.IGNORECASE).strip()
        designation = f"{article} — {suffix}" if suffix else article
        key = _material_key("protection", pin) if pin else key_of("protection-unpinned", covering.id)
        length = covering_length(covering, topology.dimensions)  # type: ignore[union-attr]
        name = display if display is not None else _str(covering.raw.get("name"))
        bom.add(key, covering.id, designation, name, None if length is None else _um(length), "м",
                "Защитное покрытие" if pin else "Материал защиты не назначен",
                indices.get(covering.id) or _str(covering.raw.get("name")))


def _extra_rows(bom: _Bom, doc: _Doc) -> None:
    for item in map(_map, _list(doc.drawing.get("specificationItems"))):
        if item.get("kind") == "abstract":
            continue
        item_id = _str(item.get("id"))
        amount = item.get("amount")
        amount_um = round(amount * 1_000_000) if isinstance(amount, (int, float)) and not isinstance(amount, bool) else None
        unit = item.get("unit") if item.get("unit") in UNITS else "шт."
        row = bom.add(key_of("specification", item_id), item_id, _str(item.get("designation")), _str(item.get("name")),
                      amount_um, unit, _str(item.get("note")) or "Дополнительная позиция")
        for segment in doc.segments_of(item_id):
            row.own(segment)


def bom_rows(document: Mapping[str, Any], quantity: int = 1) -> list[dict[str, Any]]:
    """«Спецификация» for `quantity` harnesses (buildDrawingBom): rows in the drawing's own order (`bomOrder`, the
    rest after it), texts edited on the drawing (`bomText`) over the computed ones. Rows aggregate by source identity
    — the same article from another catalog snapshot is another row; a material cable's cores are not counted again."""
    doc = _read(document)
    bom = _Bom()
    indices = _object_indices(doc)
    for connector in doc.design.connectors:
        _connector_rows(bom, connector)
    for blank in _blanks(doc.design):
        _blank_rows(bom, doc, blank, indices)
    _covering_rows(bom, doc, indices)
    _extra_rows(bom, doc)
    order = [key for key in _list(doc.drawing.get("bomOrder")) if isinstance(key, str)]
    rank = {key: position for position, key in reversed(list(enumerate(order)))}
    keys = sorted(bom.rows, key=lambda key: rank.get(key, len(order) + 10**9))
    edits = _map(doc.drawing.get("bomText"))
    result = []
    for position, key in enumerate(keys, start=1):
        row, edit = bom.rows[key], _map(edits.get(key))

        def text(name: str, computed: str) -> str:
            value = edit.get(name)
            return value if isinstance(value, str) else computed

        result.append({
            "key": key, "position": position, "index": text("index", ", ".join(row.index)),
            "designation": text("designation", ", ".join(row.designation)), "name": text("name", row.name),
            "amount": None if row.unknown else _metres(row.micrometres * quantity),
            "unit": row.unit, "note": text("note", row.note + (" · длина не задана" if row.unknown else "")),
            "object_ids": list(row.object_ids),
        })
    return result


# ── documents as tables (screen headers, Excel) ──────────────────────────────────────────────────────────────────


def _ru(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".").replace(".", ",")
    return str(value)


def table(kind: str, document: Mapping[str, Any], quantity: int = 1) -> tuple[str, list[str], list[list[str]]]:
    """(title, headers, rows) of one document as the screen shows it — the source of its Excel download."""
    if kind == "connections":
        rows = connection_rows(document)
        keys = {"length": "length_mm"}
        return ("Таблица соединений", [title for _id, title in CONNECTION_COLUMNS],
                [[_ru(row[keys.get(column, column)]) for column, _title in CONNECTION_COLUMNS] for row in rows])
    if kind == "cut":
        items = cut_list(document, quantity)["items"]
        status = {"material-missing": "Нет материала", "length-missing": "Нет длины"}
        return ("Карта резки", ["Цепь / провод", "Материал", "Исходная длина, мм", "Поправка A, мм", "Поправка B, мм",
                                "Длина резки, мм", "Шт.", "Общий метраж, м", "Статус"],
                [[item["title"] or "Без цепи", item["material"] or "Материал не закреплён", _ru(item["length_mm"]),
                  _ru(item["correction_a_mm"]), _ru(item["correction_b_mm"]), _ru(item["cut_mm"]), _ru(item["pieces"]),
                  _ru(item["total_m"]),
                  status["length-missing"] if "length-missing" in item["warnings"] else
                  status["material-missing"] if "material-missing" in item["warnings"] else "Готово"]
                 for item in items])
    if kind == "bom":
        rows = bom_rows(document, quantity)
        return ("Спецификация", ["Поз.", "Индекс", "Обозначение", "Наименование", "Кол-во", "Ед.", "Примечание"],
                [[str(row["position"]), row["index"], row["designation"], row["name"], _ru(row["amount"]), row["unit"],
                  row["note"]] for row in rows])
    raise ValueError(f"Неизвестный документ чертежа «{kind}»")


def excel_text(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> bytes:
    """The ERP tables' Excel format (DataTable «Excel», drawing windows «Скачать»): tab-separated, header first, CRLF,
    UTF-16LE with BOM — Excel opens Cyrillic without the import wizard."""
    def cell(value: str) -> str:
        return re.sub(r"[\t\r\n]+", " ", value)
    lines = ["\t".join(cell(h) for h in headers), *("\t".join(cell(v) for v in row) for row in rows)]
    return b"\xff\xfe" + ("\r\n".join(lines) + "\r\n").encode("utf-16-le")


def documents(kind: str, document: Mapping[str, Any], quantity: int = 1) -> dict[str, Any]:
    """The JSON of one document kind for the editor's windows."""
    if kind == "connections":
        return {"kind": kind, "rows": connection_rows(document)}
    if kind == "cut":
        return {"kind": kind, **cut_list(document, quantity)}
    if kind == "bom":
        return {"kind": kind, "quantity": quantity, "rows": bom_rows(document, quantity)}
    raise ValueError(f"Неизвестный документ чертежа «{kind}»")

"""TECHMAP-GRAPHER harness document (schemaVersion 1, writer contract 2) ⇄ the ERP harness model.

`parse_design` reads leniently: where the original editor (src/Techmap.Client/src/editor/model.ts,
parseHarnessDesignDocument; manufacturing/route-model.ts, parseManufacturingRoute; editor/physical-coverings.ts,
validateCoverings — © Василий, SaveOurSouls, commit e63befb) refuses a document, we keep what is readable and
return a Russian warning, so an older or partly odd export still imports. `dump_design` writes the model back into
the same JSON, keeping every field the model does not cover (geometry, views, presentation) from `raw`.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any, Callable, Mapping

from .model import (
    Cable, CatalogPin, Connector, Contact, Covering, CoveringSpan, DiffPair, Dimension, Endpoint, HarnessDesign,
    Junction, Route, RouteOperation, RouteRow, Screen, StripProfile, TerminalRequirement, TerminalSnapshot, Topology,
    Wire,
)
from .rules import COVERING_KINDS, DEFAULT_ROUNDING_STEP_MM, covering_kind, normalize_strip_layers, to_decimal

SCHEMA_VERSION = 1
WRITER_CONTRACT = 2
ROUTE_CONTRACT = 1
KNOWN_KEYS = frozenset({
    "schemaVersion", "requiredWriterContractVersion", "drawingDocuments", "physicalTopology", "manufacturingRoute",
    "customWireColors", "connectors", "wires", "cables", "junctions", "diffPairs", "screens", "views",
    # ERP keeps the library placements of this harness inside its document (schema.COMPONENT_GRAPH_KEYS).
    "componentSnapshots", "componentPlacements",
})
SECTIONS = {"cables": "Кабели", "junctions": "Сростки", "diffPairs": "Дифпары", "screens": "Экраны"}
ROUTE_MODES = frozenset({
    "cut", "cut-strip-from", "cut-strip-to", "cut-strip-both", "cut-crimp", "tin",
    "strip-from", "strip-to", "strip-both", "assembly",
})
ROUTE_KINDS = frozenset({"semiFinished", "assembly"})
SOURCE_KINDS = frozenset({"wire", "cable", "covering", "connector"})


def _map(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: object) -> str:
    return "" if value is None or isinstance(value, (Mapping, list, tuple)) else str(value).strip()


def _int(value: object) -> int | None:
    number = to_decimal(value)
    return int(number) if number is not None and number == number.to_integral_value() else None


def _pin(value: object, entity_type: str = "") -> CatalogPin | None:
    item = _map(value)
    if not item:
        return None
    return CatalogPin(
        entity_type=_text(item.get("entityType")) or entity_type, source_key=_text(item.get("sourceKey")),
        display_name=_text(item.get("displayName")), source_id=_text(item.get("sourceId")),
        snapshot_id=_text(item.get("snapshotId")), record_id=_text(item.get("recordId")),
    )


class _Reader:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)

    def length(self, value: object, where: str) -> Decimal | None:
        if value is None or value == "":
            return None
        number = to_decimal(value)
        if number is None or number < 0:
            self.warn(f"{where}: длина «{value}» не число — считается неизвестной")
            return None
        return number

    def correction(self, value: object, where: str) -> Decimal:
        if value is None or value == "":
            return Decimal(0)
        number = to_decimal(value)
        if number is None:
            self.warn(f"{where}: поправка «{value}» не число — взят 0")
            return Decimal(0)
        return number

    def step(self, value: object, where: str) -> Decimal:
        if value is None or value == "":
            return DEFAULT_ROUNDING_STEP_MM
        number = to_decimal(value)
        if number is None or number <= 0:
            self.warn(f"{where}: шаг округления «{value}» неверный — взят 1 мм")
            return DEFAULT_ROUNDING_STEP_MM
        return number

    # --- connectors -----------------------------------------------------------------------------------------
    def connector(self, raw: Mapping[str, Any]) -> Connector:
        binding = _map(raw.get("libraryBinding"))
        snapshot = _map(binding.get("snapshot"))
        names = {
            _text(row.get("logicalContactId")): _text(row.get("name"))
            for row in map(_map, _list(snapshot.get("contacts")))
        }
        contacts = []
        for item in map(_map, _list(raw.get("contacts"))):
            details = _map(item.get("terminalDetails"))
            minus = to_decimal(details.get("lengthMinusMm"))
            # connectorContactName (model.ts): an override, even empty, wins over the template name.
            name = _text(item.get("nameOverride")) if item.get("nameOverride") is not None else (
                names.get(_text(item.get("logicalContactId")), "") if binding.get("mode") == "template" else "")
            contacts.append(Contact(
                id=_text(item.get("id")), number=_int(item.get("number")), circuit=_text(item.get("circuit")),
                contact_type=_text(item.get("contactType")), name=name,
                terminal_article=_text(item.get("terminalArticle")),
                terminal=TerminalSnapshot(
                    manufacturer=_text(details.get("manufacturer")), series=_text(details.get("series")),
                    description=_text(details.get("description")), article=_text(details.get("article")),
                    l_plus_mm=to_decimal(details.get("lengthPlusMm")),
                    l_minus_mm=None if minus is None else -abs(minus),
                ) if details else None,
                wire=_text(item.get("wire")), wire_section=_text(item.get("wireSection")), color=_text(item.get("color")),
                status=_text(item.get("connectionStatus")), raw=item,
            ))
        return Connector(
            id=_text(raw.get("id")), designation=_text(raw.get("designation")), part_number=_text(raw.get("partNumber")),
            contacts=tuple(contacts), library=_text(binding.get("mode")) or "free",
            template_code=_text(snapshot.get("code")), template_name=_text(snapshot.get("name")),
            template_article=_text(_map(binding.get("article")).get("articleKey")) or _text(binding.get("partNumber")),
            template_version=_int(binding.get("templateVersion")), raw=raw,
        )

    # --- wires ----------------------------------------------------------------------------------------------
    def endpoint(self, value: object, where: str) -> Endpoint:
        """parseEndpoint (model.ts): junction, screen or connector contact; anything else is a free end."""
        item = _map(value)
        if item.get("junctionId"):
            return Endpoint("junction", junction_id=_text(item["junctionId"]))
        if item.get("screenId"):
            side = _text(item.get("screenTerminalSide"))
            if side not in ("", "above", "below"):
                self.warn(f"{where}: сторона экрана «{side}» не знакома")
            return Endpoint("screen", screen_id=_text(item["screenId"]), screen_side=side)
        if item.get("connectorId") and item.get("contactId"):
            return Endpoint("contact", connector_id=_text(item["connectorId"]), contact_id=_text(item["contactId"]))
        if item and set(item) - {"connectorId", "contactId"}:
            self.warn(f"{where}: незнакомый тип конца ({', '.join(sorted(item))}) — конец свободный")
        return Endpoint("free")

    def strip_profile(self, value: object, where: str) -> StripProfile | None:
        item = _map(value)
        if not item:
            return None
        if item.get("entityType") not in (None, "coax-termination"):
            self.warn(f"{where}: незнакомый тип профиля разделки «{_text(item.get('entityType'))}»")
        layers, problems = normalize_strip_layers(map(_map, _list(item.get("layers"))))
        for problem in problems:
            self.warn(f"{where}: {problem}")
        if not layers:
            self.warn(f"{where}: в профиле разделки нет слоёв L1…Ln")
        return StripProfile(_pin(item, "coax-termination"), layers, raw=item)

    def wire(self, raw: Mapping[str, Any]) -> Wire:
        wire_id = _text(raw.get("id"))
        where = f"Провод {_text(raw.get('circuit')) or wire_id}"
        strips = _map(raw.get("stripProfiles"))
        return Wire(
            id=wire_id, end_a=self.endpoint(raw.get("from"), where), end_b=self.endpoint(raw.get("to"), where),
            circuit=_text(raw.get("circuit")), color=_text(raw.get("color")),
            material=_pin(raw.get("materialBinding"), "wire"),
            length_mm=self.length(raw.get("lengthMm"), where),
            correction_a_mm=self.correction(raw.get("endCorrectionFromMm"), where),
            correction_b_mm=self.correction(raw.get("endCorrectionToMm"), where),
            rounding_step_mm=self.step(raw.get("cutRoundingStepMm"), where),
            strip_a=self.strip_profile(strips.get("from"), f"{where}, разделка А"),
            strip_b=self.strip_profile(strips.get("to"), f"{where}, разделка Б"),
            raw=raw,
        )

    def cable(self, raw: Mapping[str, Any], index: int) -> Cable:
        where = f"Кабель {index}"
        sheath = raw.get("sheathStrip")
        strip = _map(sheath)
        return Cable(
            id=_text(raw.get("id")), member_wire_ids=tuple(_text(v) for v in _list(raw.get("memberWireIds"))),
            material=_pin(raw.get("materialBinding"), "cable"), length_mm=self.length(raw.get("lengthMm"), where),
            correction_a_mm=self.correction(raw.get("endCorrectionFromMm"), where),
            correction_b_mm=self.correction(raw.get("endCorrectionToMm"), where),
            rounding_step_mm=self.step(raw.get("cutRoundingStepMm"), where),
            has_sheath_strip=isinstance(sheath, Mapping),
            sheath_a_mm=to_decimal(strip.get("fromMm")), sheath_b_mm=to_decimal(strip.get("toMm")), raw=raw,
        )

    # --- topology -------------------------------------------------------------------------------------------
    def covering(self, raw: Mapping[str, Any]) -> Covering:
        name = _text(raw.get("name")) or _text(raw.get("id"))
        kind = covering_kind(_text(raw.get("kind")), name)
        if kind not in COVERING_KINDS:
            self.warn(f"Оболочка {name}: незнакомый вид «{kind}»")
        spans = []
        for span in map(_map, _list(raw.get("spans"))):
            start, stop = to_decimal(span.get("from")), to_decimal(span.get("to"))
            if start is None or stop is None or start >= stop:
                self.warn(f"Оболочка {name}: участок {_text(span.get('segmentId'))} с неверными границами пропущен")
                continue
            spans.append(CoveringSpan(_text(span.get("segmentId")), start, stop,
                                      _int(span.get("fromAnchor")), _int(span.get("toAnchor"))))
        mode = _text(raw.get("lengthMode"))
        if mode not in ("", "auto", "manual"):
            self.warn(f"Оболочка {name}: режим длины «{mode}» не знаком — считается автоматическим")
            mode = "auto"
        return Covering(
            id=_text(raw.get("id")), name=name, kind=kind, spans=tuple(spans),
            material=_pin(raw.get("material"), "protective-covering"), color=_text(raw.get("color")),
            length_mode=mode, length_mm=self.length(raw.get("lengthMm"), f"Оболочка {name}"), raw=raw,
        )

    def topology(self, raw: Mapping[str, Any], drawing: Mapping[str, Any]) -> Topology:
        return Topology(
            node_connector={
                _text(node.get("id")): _text(node.get("connectorId"))
                for node in map(_map, _list(raw.get("nodes"))) if node.get("connectorId")
            },
            segment_nodes={
                _text(seg.get("id")): (_text(seg.get("from")), _text(seg.get("to")))
                for seg in map(_map, _list(raw.get("segments")))
            },
            pipe_segments={
                _text(pipe.get("id")): tuple(
                    _text(s) for member in map(_map, _list(pipe.get("members"))) for s in _list(member.get("segmentIds")))
                for pipe in map(_map, _list(raw.get("joiningPipes")))
            },
            wire_segments={
                _text(route.get("wireId")): tuple(_text(_map(step).get("segmentId")) for step in _list(route.get("steps")))
                for route in map(_map, _list(raw.get("routes")))
            },
            coverings=tuple(self.covering(item) for item in map(_map, _list(raw.get("coverings")))),
            dimensions=tuple(
                Dimension(_text(d.get("segmentId")), _int(d.get("from")) or 0, _int(d.get("to")) or 0,
                          to_decimal(d.get("lengthMm")), bool(d.get("auxiliary")))
                for d in map(_map, _list(drawing.get("dimensions")))
            ),
            raw=raw,
        )

    # --- route ----------------------------------------------------------------------------------------------
    def route(self, raw: Mapping[str, Any]) -> Route:
        version = raw.get("contractVersion")
        if version != ROUTE_CONTRACT:
            self.warn(f"Маршрут: версия контракта {version} не знакома — прочитано что понятно")
        rows, legacy = expand_legacy_rows(_list(raw.get("rows")))
        if legacy:
            self.warn("Маршрут в старом формате (полуфабрикаты внутри строки) — разложен по строкам")
        parsed = []
        for row in map(_map, rows):
            title = _text(row.get("title")) or _text(row.get("id"))
            kind = _text(row.get("kind"))
            if kind not in ROUTE_KINDS:
                self.warn(f"Маршрут «{title}»: незнакомый вид строки «{kind}»")
            operations = []
            for op in map(_map, _list(row.get("operations"))):
                mode = _text(op.get("mode"))
                if mode not in ROUTE_MODES:
                    self.warn(f"Маршрут «{title}»: незнакомый режим операции «{mode}»")
                pin = _pin(op.get("binding"), "operation")
                if pin is None:
                    self.warn(f"Маршрут «{title}»: операция без кода БД.ОП")
                operations.append(RouteOperation(_text(op.get("id")), mode, _text(op.get("note")), pin))
            sources = []
            for ref in map(_map, _list(row.get("sourceObjects"))):
                if _text(ref.get("kind")) not in SOURCE_KINDS:
                    self.warn(f"Маршрут «{title}»: незнакомый вид объекта «{_text(ref.get('kind'))}»")
                sources.append((_text(ref.get("kind")), _text(ref.get("id"))))
            requirements = []
            for item in map(_map, _list(row.get("terminalRequirements"))):
                end = _text(item.get("end"))
                if end not in ("from", "to"):
                    self.warn(f"Маршрут «{title}»: конец «{end}» требования к терминалу не знаком")
                    continue
                requirements.append(TerminalRequirement(
                    _text(item.get("wireId")), "A" if end == "from" else "B", _text(item.get("terminalArticle")),
                    to_decimal(item.get("stripLengthMm"))))
            parsed.append(RouteRow(
                id=_text(row.get("id")), kind=kind, title=title, sources=tuple(sources),
                depends_on=tuple(_text(v) for v in _list(row.get("dependsOn"))), operations=tuple(operations),
                role=_text(row.get("role")), index=_text(row.get("index")), quantity=to_decimal(row.get("quantity")),
                reserve=to_decimal(row.get("reserve")), operation_time_min=to_decimal(row.get("operationTimeMinutes")),
                comment=_text(row.get("comment")), prepared=bool(row.get("prepared")),
                terminal_requirements=tuple(requirements), raw=row,
            ))
        ids = {row.id for row in parsed}
        for row in parsed:
            for missing in [item for item in row.depends_on if item not in ids]:
                self.warn(f"Маршрут «{row.title}»: зависит от отсутствующей строки {missing}")
        return Route(_text(raw.get("status")), tuple(parsed), version if isinstance(version, int) else None,
                     legacy, raw=raw)


def expand_legacy_rows(rows: list) -> tuple[list[Mapping[str, Any]], bool]:
    """A row saved by the old merge command (`components` inside one row) becomes one semi-finished row per
    component plus a shared assembly row depending on them (expandLegacyComponents, route-model.ts)."""
    if not any(isinstance(row, Mapping) and "components" in row for row in rows):
        return [_map(row) for row in rows], False
    existing = {_text(_map(row).get("id")) for row in rows}
    result: list[Mapping[str, Any]] = []
    for row in map(_map, rows):
        if "components" not in row:
            result.append(row)
            continue
        row_id = _text(row.get("id"))
        made = []
        for index, component in enumerate(map(_map, _list(row.get("components"))), start=1):
            new_id, suffix = f"{row_id}-pf-{index}"[:128], 2
            while new_id in existing:
                new_id, suffix = f"{row_id[:110]}-pf-{index}-{suffix}", suffix + 1
            existing.add(new_id)
            ref = _map(component.get("ref"))
            made.append({
                "id": new_id, "kind": "semiFinished", "title": component.get("title"),
                **({"index": component["index"]} if "index" in component else {}),
                "quantity": component.get("quantity", 1), "reserve": component.get("reserve", 0),
                "operationTimeMinutes": component.get("operationTimeMinutes", 0), "comment": "",
                "sourceObjects": [dict(ref)], "dependsOn": list(_list(row.get("dependsOn"))), "operations": [],
                "prepared": False,
                "terminalRequirements": [
                    item for item in _list(row.get("terminalRequirements"))
                    if ref.get("kind") == "wire" and _map(item).get("wireId") == ref.get("id")
                ],
            })
        result.extend(made)
        result.append({
            **{k: v for k, v in row.items() if k not in ("components", "terminalRequirements", "wireBlankSelections")},
            "kind": "assembly", "role": "sharedOperation", "index": f"ОП-{row_id[:32]}",
            "title": f"Общая операция · {_text(row.get('title'))}"[:512], "quantity": 1, "reserve": 0,
            "operationTimeMinutes": 0, "sourceObjects": [], "dependsOn": [item["id"] for item in made],
            "assemblyInputs": [{"id": f"row-{i}", "kind": "row", "rowId": item["id"]} for i, item in enumerate(made, 1)],
            "prepared": False,
        })
    return result, True


def parse_design(content: Mapping[str, Any]) -> tuple[HarnessDesign, list[str]]:
    """The harness model of one design document plus Russian warnings about what was odd, missing or unknown."""
    reader = _Reader()
    writer = content.get("requiredWriterContractVersion")
    if writer is None:
        reader.warn("Экспорт из старой версии TECHMAP-GRAPHER (без версии контракта) — часть разделов может отсутствовать")
    elif writer not in (1, WRITER_CONTRACT):
        reader.warn(f"Экспорт новее, чем знает ERP (контракт {writer}) — незнакомые поля пропущены")
    for key, label in SECTIONS.items():
        if key not in content:
            reader.warn(f"В чертеже нет раздела «{label}» (старый экспорт) — раздел пуст")
    if "physicalTopology" not in content:
        reader.warn("В чертеже нет физической топологии — оболочки и трассы проводов не прочитаны")
    for key in sorted(set(content) - KNOWN_KEYS):
        reader.warn(f"Незнакомый раздел чертежа «{key}» пропущен")

    connectors = tuple(reader.connector(item) for item in map(_map, _list(content.get("connectors"))) if item.get("id"))
    wires = tuple(reader.wire(item) for item in map(_map, _list(content.get("wires"))) if item.get("id"))
    cables = tuple(reader.cable(item, i) for i, item in enumerate(map(_map, _list(content.get("cables"))), 1))
    junctions = tuple(
        Junction(_text(item.get("id")), f"Сросток {i}", tuple(_text(v) for v in _list(item.get("wireIds"))), raw=item)
        for i, item in enumerate(map(_map, _list(content.get("junctions"))), 1)
    )
    screens = tuple(
        Screen(_text(item.get("id")), _text(item.get("label")), tuple(_text(v) for v in _list(item.get("wireIds"))),
               _text(item.get("terminalSide")) or "above", raw=item)
        for item in map(_map, _list(content.get("screens")))
    )
    pairs = []
    for item in map(_map, _list(content.get("diffPairs"))):
        ids = tuple(_text(v) for v in _list(item.get("wireIds")))
        if len(ids) != 2 or ids[0] == ids[1]:
            reader.warn(f"Дифпара {_text(item.get('id'))}: нужны два разных провода — пропущена")
            continue
        pairs.append(DiffPair(_text(item.get("id")), (ids[0], ids[1]), to_decimal(item.get("step")),
                              to_decimal(item.get("amplitude")), _int(item.get("variant")) or 1, raw=item))
    topology = (
        reader.topology(_map(content.get("physicalTopology")), _map(content.get("drawingDocuments")))
        if "physicalTopology" in content else None
    )
    route = reader.route(_map(content.get("manufacturingRoute"))) if "manufacturingRoute" in content else None
    design = HarnessDesign(
        connectors=connectors, wires=wires, cables=cables, junctions=junctions, screens=screens,
        diff_pairs=tuple(pairs), topology=topology, route=route,
        schema_version=_int(content.get("schemaVersion")) or SCHEMA_VERSION,
        writer_contract=writer if isinstance(writer, int) else None, raw=content,
    )
    _check_links(design, reader.warn)
    return design, reader.warnings


def _check_links(design: HarnessDesign, warn: Callable[[str], None]) -> None:
    """References that must resolve (validateJunctions and friends, model.ts) — reported, never fatal."""
    wire_ids = {wire.id for wire in design.wires}
    junctions = {junction.id: junction for junction in design.junctions}
    screens = {screen.id for screen in design.screens}
    for wire in design.wires:
        label = wire.circuit or wire.id
        for side, end in (("А", wire.end_a), ("Б", wire.end_b)):
            if end.kind == "contact" and design.contact_at(end)[1] is None:
                warn(f"Провод {label}, конец {side}: контакт не найден в соединителе")
            if end.kind == "junction":
                junction = junctions.get(end.junction_id)
                if junction is None:
                    warn(f"Провод {label}, конец {side}: сросток не найден")
                elif wire.id not in junction.wire_ids:
                    warn(f"{junction.label}: в нём кончается провод {label}, но сросток его не содержит")
            if end.kind == "screen" and end.screen_id not in screens:
                warn(f"Провод {label}, конец {side}: экран не найден")
    for junction in design.junctions:
        if len(junction.wire_ids) < 2:
            warn(f"{junction.label}: соединяет меньше двух проводов")
        missing = [item for item in junction.wire_ids if item not in wire_ids]
        if missing:
            warn(f"{junction.label}: нет проводов {', '.join(missing)}")
    for group, label in ((design.screens, "Экран"), (design.diff_pairs, "Дифпара"), (design.cables, "Кабель")):
        for item in group:
            members = item.member_wire_ids if isinstance(item, Cable) else item.wire_ids
            missing = [wire_id for wire_id in members if wire_id not in wire_ids]
            if missing:
                warn(f"{label} {getattr(item, 'label', '') or item.id}: нет проводов {', '.join(missing)}")


# --- writing back ---------------------------------------------------------------------------------------------
def _number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def _pin_json(pin: CatalogPin, raw: Mapping[str, Any]) -> dict[str, Any]:
    return {**raw, "sourceId": pin.source_id, "snapshotId": pin.snapshot_id, "recordId": pin.record_id,
            "entityType": pin.entity_type, "sourceKey": pin.source_key, "displayName": pin.display_name}


def _endpoint_json(end: Endpoint, raw: object) -> dict[str, Any]:
    if end.kind == "contact":
        return {"connectorId": end.connector_id, "contactId": end.contact_id}
    if end.kind == "junction":
        return {"junctionId": end.junction_id, "connectorId": "", "contactId": ""}
    if end.kind == "screen":
        return {"screenId": end.screen_id, **({"screenTerminalSide": end.screen_side} if end.screen_side else {}),
                "connectorId": "", "contactId": ""}
    return dict(_map(raw))


def _strip_json(profile: StripProfile) -> dict[str, Any]:
    return {**_pin_json(profile.pin, profile.raw), "layers": [
        {"index": layer.index, "diameterMm": _number(layer.diameter_mm), "stripLengthMm": _number(layer.length_mm)}
        for layer in profile.layers
    ]}


def _contact_json(contact: Contact) -> dict[str, Any]:
    result = {**contact.raw, "id": contact.id, "number": contact.number, "circuit": contact.circuit,
              "contactType": contact.contact_type, "terminalArticle": contact.terminal_article, "wire": contact.wire,
              "color": contact.color}
    if contact.wire_section or "wireSection" in contact.raw:
        result["wireSection"] = contact.wire_section
    if contact.terminal is not None:
        terminal = contact.terminal
        details = {**_map(contact.raw.get("terminalDetails")), "manufacturer": terminal.manufacturer,
                   "series": terminal.series, "description": terminal.description}
        if terminal.article or "article" in details:
            details["article"] = terminal.article
        for key, value in (("lengthPlusMm", terminal.l_plus_mm), ("lengthMinusMm", terminal.l_minus_mm)):
            if value is not None or key in details:
                details[key] = _number(value)
        result["terminalDetails"] = details
    return result


def _wire_json(wire: Wire) -> dict[str, Any]:
    result = {**wire.raw, "id": wire.id, "from": _endpoint_json(wire.end_a, wire.raw.get("from")),
              "to": _endpoint_json(wire.end_b, wire.raw.get("to")), "circuit": wire.circuit, "color": wire.color,
              "lengthMm": _number(wire.length_mm), "endCorrectionFromMm": _number(wire.correction_a_mm),
              "endCorrectionToMm": _number(wire.correction_b_mm), "cutRoundingStepMm": _number(wire.rounding_step_mm)}
    if wire.material is not None:
        result["materialBinding"] = _pin_json(wire.material, _map(wire.raw.get("materialBinding")))
    strips = {key: _strip_json(profile) for key, profile in (("from", wire.strip_a), ("to", wire.strip_b)) if profile}
    if strips:
        result["stripProfiles"] = strips
    else:
        result.pop("stripProfiles", None)
    return result


def _cable_json(cable: Cable) -> dict[str, Any]:
    result = {**cable.raw, "id": cable.id, "memberWireIds": list(cable.member_wire_ids),
              "lengthMm": _number(cable.length_mm), "endCorrectionFromMm": _number(cable.correction_a_mm),
              "endCorrectionToMm": _number(cable.correction_b_mm), "cutRoundingStepMm": _number(cable.rounding_step_mm)}
    if cable.material is not None:
        result["materialBinding"] = _pin_json(cable.material, _map(cable.raw.get("materialBinding")))
    if cable.has_sheath_strip:
        result["sheathStrip"] = {"fromMm": _number(cable.sheath_a_mm), "toMm": _number(cable.sheath_b_mm)}
    else:
        result.pop("sheathStrip", None)
    return result


def _covering_json(covering: Covering) -> dict[str, Any]:
    spans = []
    for span in covering.spans:
        item: dict[str, Any] = {"segmentId": span.segment_id, "from": _number(span.start), "to": _number(span.stop)}
        if span.start_anchor is not None:
            item["fromAnchor"] = span.start_anchor
        if span.stop_anchor is not None:
            item["toAnchor"] = span.stop_anchor
        spans.append(item)
    result = {**covering.raw, "id": covering.id, "name": covering.name, "kind": covering.kind, "spans": spans,
              "color": covering.color, "lengthMm": _number(covering.length_mm)}
    if covering.length_mode:
        result["lengthMode"] = covering.length_mode
    if covering.material is not None:
        result["material"] = _pin_json(covering.material, _map(covering.raw.get("material")))
    return result


def _route_json(route: Route) -> dict[str, Any]:
    rows = []
    for row in route.rows:
        operations = []
        for op in row.operations:
            raw_op = next((item for item in map(_map, _list(row.raw.get("operations"))) if item.get("id") == op.id), {})
            operations.append({**raw_op, "id": op.id, "mode": op.mode, "note": op.note,
                               "binding": None if op.operation is None else _pin_json(op.operation, _map(raw_op.get("binding")))})
        item = {**row.raw, "id": row.id, "kind": row.kind, "title": row.title, "comment": row.comment,
                "sourceObjects": [{"kind": kind, "id": ref_id} for kind, ref_id in row.sources],
                "dependsOn": list(row.depends_on), "operations": operations, "prepared": row.prepared}
        for key, value in (("index", row.index), ("role", row.role)):
            if value or key in row.raw:
                item[key] = value
        for key, value in (("quantity", row.quantity), ("reserve", row.reserve),
                           ("operationTimeMinutes", row.operation_time_min)):
            if value is not None or key in row.raw:
                item[key] = _number(value)
        if row.terminal_requirements or "terminalRequirements" in row.raw:
            raw_requirements = {
                (_text(_map(r).get("wireId")), _text(_map(r).get("end"))): _map(r)
                for r in _list(row.raw.get("terminalRequirements"))
            }
            item["terminalRequirements"] = [
                {**raw_requirements.get((req.wire_id, "from" if req.side == "A" else "to"), {"binding": None}),
                 "wireId": req.wire_id, "end": "from" if req.side == "A" else "to",
                 "terminalArticle": req.terminal_article, "stripLengthMm": _number(req.strip_length_mm)}
                for req in row.terminal_requirements
            ]
        rows.append(item)
    return {**route.raw, "contractVersion": route.contract_version or ROUTE_CONTRACT, "status": route.status,
            "rows": rows}


def dump_design(design: HarnessDesign) -> dict[str, Any]:
    """The model back as a schemaVersion 1 document; fields the model does not cover come from `raw` unchanged."""
    result = deepcopy(dict(design.raw))
    result["schemaVersion"] = design.schema_version
    if design.writer_contract is not None:
        result["requiredWriterContractVersion"] = design.writer_contract
    result["connectors"] = [
        {**connector.raw, "id": connector.id, "designation": connector.designation,
         "partNumber": connector.part_number, "contacts": [_contact_json(c) for c in connector.contacts]}
        for connector in design.connectors
    ]
    result["wires"] = [_wire_json(wire) for wire in design.wires]
    result["cables"] = [_cable_json(cable) for cable in design.cables]
    result["junctions"] = [{**j.raw, "id": j.id, "wireIds": list(j.wire_ids)} for j in design.junctions]
    result["screens"] = [{**s.raw, "id": s.id, "label": s.label, "wireIds": list(s.wire_ids)} for s in design.screens]
    result["diffPairs"] = [
        {**p.raw, "id": p.id, "wireIds": list(p.wire_ids), "step": _number(p.step), "amplitude": _number(p.amplitude),
         "variant": p.variant}
        for p in design.diff_pairs
    ]
    if design.topology is not None:
        result["physicalTopology"] = {**design.topology.raw,
                                      "coverings": [_covering_json(c) for c in design.topology.coverings]}
    if design.route is not None:
        result["manufacturingRoute"] = _route_json(design.route)
    return result

"""What a harness drawing must hold to be stored (pure). The save is refused (422) with these Russian messages.

Ported, with the author's permission, from TECHMAP-GRAPHER (© Василий, SaveOurSouls, branch codex/integration-m5-22 at
9cf3b88) — the checks its server runs before it stores a design and its editor runs when it opens one:
- ElectricalGraphValidator.cs and the editor's parseHarnessDesignDocument / validateJunctions / validateParsedGroups
  (model.ts): ids, wire ends, splices, screens, differential pairs;
- SqliteHarnessDesignDocumentStore.ValidateCableInstances: cables and their member wires, sheath strip;
- HarnessStripProfileValidator.cs: coax strip layers (ERP reading: an L of 0 is «no layer», `rules.normalize_strip_layers`);
- TemplateContactNumberingValidator.cs: contact numbers of a library connector that opted into source numbering;
- HarnessPhysicalTopologyValidator.cs: nodes, segments, coverings and wire routes — references and continuity only;
  geometry, colours and widths are presentation and stay with the editor.
The rules that need geometry (a pair's common parallel span, a line through a splice) stay in the editor.

References to our books (БД.ПРОВ, БД.ТЕР, БД.МАТ, БД.КОАКС) are checked separately (`reference_pins`, `ReferenceBook`):
only the references a write brings in are looked up, so a book edited later never locks an existing drawing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .rules import COVERING_KINDS, MAX_LENGTH_MM, HarnessRuleError, micrometres, normalize_strip_layers, to_decimal

MAX_ERRORS = 50
COLLECTIONS = {
    "connectors": "Соединители", "wires": "Провода", "cables": "Кабели", "junctions": "Сростки",
    "diffPairs": "Дифпары", "screens": "Экраны",
}
SCREEN_SIDES = ("above", "below", "both")
SIDES = (("from", "А"), ("to", "Б"))


def _map(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


class _Check:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def fail(self, message: str) -> None:
        if message not in self.errors and len(self.errors) < MAX_ERRORS:
            self.errors.append(message)

    def number(self, value: object, where: str, *, nullable: bool = False, positive: bool = False,
               signed: bool = False) -> Decimal | None:
        """A length in mm as the editor stores it: a JSON number, exact to 0.001 mm, within the editor's range."""
        if value is None and nullable:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            self.fail(f"{where}: нужно число в миллиметрах")
            return None
        number = to_decimal(value)
        if number is None or abs(number) > MAX_LENGTH_MM:
            self.fail(f"{where}: число вне допустимого диапазона")
            return None
        if (positive and number <= 0) or (not signed and not positive and number < 0):
            self.fail(f"{where}: должна быть {'больше нуля' if positive else 'не меньше нуля'}")
            return None
        try:
            micrometres(number, where)
        except HarnessRuleError as error:
            self.fail(str(error))
            return None
        return number


def _items(check: _Check, content: Mapping[str, Any], key: str) -> dict[str, Mapping[str, Any]]:
    """A collection of the document by id; a missing section is empty, a broken one is reported."""
    value = content.get(key)
    if value is None:
        return {}
    label = COLLECTIONS.get(key, key)
    if not isinstance(value, list):
        check.fail(f"Раздел «{label}» должен быть списком")
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for position, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            check.fail(f"{label}: запись {position} — не объект")
            continue
        item_id = _text(item.get("id"))
        if not item_id:
            check.fail(f"{label}: у записи {position} нет id")
            continue
        if item_id in result:
            check.fail(f"{label}: id «{item_id}» повторяется")
            continue
        result[item_id] = item
    return result


def _wire_label(wire_id: str, wire: Mapping[str, Any]) -> str:
    circuit = _text(wire.get("circuit"))
    return f"Провод {circuit} ({wire_id})" if circuit and circuit != wire_id else f"Провод {wire_id}"


def _members(check: _Check, owner: str, value: object, wires: Mapping[str, Any], *, minimum: int,
             maximum: int | None = None, noun: str = "проводов") -> list[str]:
    if not isinstance(value, list):
        check.fail(f"{owner}: нет списка проводов")
        return []
    ids = [_text(item) for item in value]
    if len(ids) < minimum or (maximum is not None and len(ids) > maximum):
        need = f"ровно {minimum}" if maximum == minimum else f"не меньше {minimum}"
        check.fail(f"{owner}: нужно {need} {noun}, в нём {len(ids)}")
    if len(set(ids)) != len(ids):
        check.fail(f"{owner}: провод указан дважды")
    for item in dict.fromkeys(ids):
        if item not in wires:
            check.fail(f"{owner}: провода «{item or '(пусто)'}» нет в чертеже")
    return [item for item in dict.fromkeys(ids) if item in wires]


def _contact_numbering(check: _Check, connector_id: str, connector: Mapping[str, Any], label: str) -> None:
    """TemplateContactNumberingValidator: a library connector with `contactNumbering: source-v1` carries exactly its
    template's contacts, each id `<connector>:contact:<logical id>`, numbered as the template says (1…300)."""
    binding = _map(connector.get("libraryBinding"))
    if "contactNumbering" not in binding:
        return
    if binding.get("contactNumbering") != "source-v1" or binding.get("mode") != "template":
        check.fail(f"{label}: нумерация контактов по шаблону задана неверно")
        return
    sources = _map(binding.get("snapshot")).get("contacts")
    contacts = connector.get("contacts")
    if not isinstance(sources, list) or not isinstance(contacts, list) or len(sources) != len(contacts):
        check.fail(f"{label}: контакты не совпадают с шаблоном соединителя")
        return
    numbers: dict[str, int] = {}
    for source in map(_map, sources):
        logical, text = _text(source.get("logicalContactId")), _text(source.get("sourceNumber"))
        number = int(text) if text.isascii() and text.isdigit() and len(text) <= 3 else 0
        if not logical or not 1 <= number <= 300:
            check.fail(f"{label}: номер контакта шаблона «{text}» — нужно целое от 1 до 300")
            return
        if number in numbers.values() or logical in numbers:
            check.fail(f"{label}: номера контактов шаблона повторяются")
            return
        numbers[logical] = number
    seen: set[str] = set()
    for contact in map(_map, contacts):
        logical = _text(contact.get("logicalContactId"))
        if logical in seen or logical not in numbers:
            check.fail(f"{label}: контакт «{logical or '(пусто)'}» не из шаблона соединителя")
            continue
        seen.add(logical)
        if _text(contact.get("id")) != f"{connector_id}:contact:{logical}":
            check.fail(f"{label}: id контакта {logical} должен быть «{connector_id}:contact:{logical}»")
        number = contact.get("number")
        if isinstance(number, bool) or number != numbers[logical]:
            check.fail(f"{label}: контакт {logical} должен иметь номер {numbers[logical]}, в чертеже — {number}")


def _connectors(check: _Check, connectors: Mapping[str, Mapping[str, Any]]) -> dict[str, set[str] | None]:
    """Contact ids of each connector (None — a historical connector shell without a contact list)."""
    result: dict[str, set[str] | None] = {}
    for connector_id, connector in connectors.items():
        label = f"Соединитель {_text(connector.get('designation')) or connector_id}"
        contacts = connector.get("contacts")
        if contacts is None:
            result[connector_id] = None
            continue
        if not isinstance(contacts, list):
            check.fail(f"{label}: контакты должны быть списком")
            result[connector_id] = set()
            continue
        ids: set[str] = set()
        for position, contact in enumerate(contacts, start=1):
            contact_id = _text(_map(contact).get("id"))
            if not contact_id:
                check.fail(f"{label}: у контакта {position} нет id")
            elif contact_id in ids:
                check.fail(f"{label}: id контакта «{contact_id}» повторяется")
            ids.add(contact_id)
        result[connector_id] = ids
        _contact_numbering(check, connector_id, connector, label)
    return result


def _endpoint(check: _Check, wire_id: str, wire: Mapping[str, Any], key: str, side: str, *,
              contacts: Mapping[str, set[str] | None], junctions: Mapping[str, list[str]],
              screens: Mapping[str, list[str]], screen_rows: Mapping[str, Mapping[str, Any]]) -> None:
    """One end of a wire: a contact of a connector, a splice that lists the wire, a screen the wire is not inside —
    or nothing at all (a free end, `{connectorId: "", contactId: ""}`)."""
    where = f"{_wire_label(wire_id, wire)}, конец {side}"
    if key not in wire:
        check.fail(f"{where}: нет конца провода")
        return
    end = wire.get(key)
    if not isinstance(end, Mapping):
        check.fail(f"{where}: конец провода задан неверно")
        return
    junction, screen = end.get("junctionId"), end.get("screenId")
    connector_id, contact_id = _text(end.get("connectorId")), _text(end.get("contactId"))
    if junction is not None and screen is not None:
        check.fail(f"{where}: конец не может быть и на сростке, и на экране")
        return
    if junction is not None or screen is not None:
        if connector_id or contact_id:
            check.fail(f"{where}: конец на сростке или экране не может указывать ещё и контакт")
        if junction is not None:
            members = junctions.get(_text(junction))
            if members is None:
                check.fail(f"{where}: сростка «{_text(junction)}» нет в чертеже")
            elif wire_id not in members:
                check.fail(f"{where}: сросток «{_text(junction)}» не содержит этот провод")
            return
        screen_id = _text(screen)
        members = screens.get(screen_id)
        if members is None:
            check.fail(f"{where}: экрана «{screen_id}» нет в чертеже")
            return
        if wire_id in members:
            check.fail(f"{where}: провод не может подключаться к экрану, в который сам входит")
        configured = _text(screen_rows[screen_id].get("terminalSide")) or "above"
        requested = end.get("screenTerminalSide")
        if requested is not None and (requested not in ("above", "below")
                                      or (configured != "both" and requested != configured)):
            check.fail(f"{where}: у экрана нет такой точки подключения")
        return
    if not connector_id and not contact_id:
        return  # a free end
    if wire.get("drawingEndpoints") is not None:
        return  # an independent drawing copy: its ends are points of the drawing, not contacts
    if not connector_id or not contact_id:
        check.fail(f"{where}: указан {'контакт' if contact_id else 'соединитель'}, но не "
                   f"{'соединитель' if contact_id else 'контакт'}")
        return
    if connector_id not in contacts:
        check.fail(f"{where}: соединителя «{connector_id}» нет в чертеже")
        return
    known = contacts[connector_id]
    if known is not None and contact_id not in known:
        check.fail(f"{where}: контакта «{contact_id}» нет в соединителе «{connector_id}»")


def _strip_profile(check: _Check, where: str, value: object) -> None:
    profile = _map(value)
    if not profile:
        check.fail(f"{where}: профиль разделки задан неверно")
        return
    if profile.get("entityType") not in (None, "coax-termination"):
        check.fail(f"{where}: это не профиль разделки коаксиала")
    layers = profile.get("layers")
    if not isinstance(layers, list) or len(layers) > 64:
        check.fail(f"{where}: слои разделки заданы неверно")
        return
    for layer in layers:
        for key, name in (("diameterMm", "D"), ("stripLengthMm", "L")):
            item = _map(layer).get(key)
            if item is not None and (isinstance(item, bool) or not isinstance(item, (int, float))):
                check.fail(f"{where}: {name}{_map(layer).get('index', '')} — нужно число в миллиметрах")
    parsed, problems = normalize_strip_layers(map(_map, layers))
    for problem in problems:
        check.fail(f"{where}: {problem}")
    if not parsed:
        check.fail(f"{where}: в профиле разделки нет слоёв L1…Ln")


def _wire(check: _Check, wire_id: str, wire: Mapping[str, Any]) -> None:
    label = _wire_label(wire_id, wire)
    length = check.number(wire.get("lengthMm"), f"{label}: длина", nullable=True)
    corrections = [check.number(wire.get(key, 0), f"{label}: поправка конца {side}", signed=True)
                   for key, side in (("endCorrectionFromMm", "А"), ("endCorrectionToMm", "Б"))]
    if "cutRoundingStepMm" in wire:
        check.number(wire.get("cutRoundingStepMm"), f"{label}: шаг округления длины резки", positive=True)
    if length is not None and None not in corrections and length + sum(corrections) < 0:  # type: ignore[arg-type]
        check.fail(f"{label}: длина с поправками получилась отрицательной")
    strips = wire.get("stripProfiles")
    if strips is None:
        return
    if not isinstance(strips, Mapping):
        check.fail(f"{label}: профили разделки заданы неверно")
        return
    for key, side in SIDES:
        if strips.get(key) is not None:
            _strip_profile(check, f"{label}, разделка {side}", strips[key])


def _cables(check: _Check, cables: Mapping[str, Mapping[str, Any]], wires: Mapping[str, Any]) -> None:
    """ValidateCableInstances: member wires exist, once per cable and in one cable only; length, corrections and the
    sheath strip as `rules.sheath_strip` takes them."""
    claimed: dict[str, str] = {}
    for position, (cable_id, cable) in enumerate(cables.items(), start=1):
        label = f"Кабель {position} ({cable_id})"
        for wire_id in _members(check, label, cable.get("memberWireIds"), wires, minimum=0):
            if wire_id in claimed:
                check.fail(f"{label}: провод «{wire_id}» уже входит в кабель «{claimed[wire_id]}»")
            claimed.setdefault(wire_id, cable_id)
        length = check.number(cable.get("lengthMm"), f"{label}: длина", nullable=True)
        corrections = [check.number(cable.get(key, 0), f"{label}: поправка конца {side}", signed=True) or Decimal(0)
                       for key, side in (("endCorrectionFromMm", "А"), ("endCorrectionToMm", "Б"))]
        if "cutRoundingStepMm" in cable:
            check.number(cable.get("cutRoundingStepMm"), f"{label}: шаг округления длины резки", positive=True)
        sheath = cable.get("sheathStrip")
        if sheath is None:
            continue
        if not isinstance(sheath, Mapping) or "fromMm" not in sheath or "toMm" not in sheath:
            check.fail(f"{label}: снятие оболочки задано неверно")
            continue
        ends = [check.number(sheath.get(key), f"{label}: снятие оболочки {side}", nullable=True)
                for key, side in (("fromMm", "А"), ("toMm", "Б"))]
        corrected = [Decimal(0) if end in (None, 0) else end + correction for end, correction in zip(ends, corrections)]
        if any(value < 0 for value in corrected):
            check.fail(f"{label}: снятие оболочки с поправкой получилось отрицательным")
        if length is not None and (sum(end or 0 for end in ends) > length
                                   or sum(corrected) > length + sum(corrections)):
            check.fail(f"{label}: снятие оболочки длиннее кабеля")


def _topology(check: _Check, topology: object, connectors: Mapping[str, Any], wires: Mapping[str, Any],
              contacts: Mapping[str, set[str] | None]) -> None:
    """HarnessPhysicalTopologyValidator, references only: nodes at existing connectors, segments between two
    different existing nodes, coverings over existing segments with sane spans, one route per wire that runs
    through adjacent segments and starts / ends at the wire's own connectors."""
    if not isinstance(topology, Mapping):
        check.fail("Физическая топология задана неверно")
        return
    taken = set(connectors) | set(wires)

    def unique(kind: str, item_id: str) -> bool:
        if not item_id:
            check.fail(f"Топология: у записи «{kind}» нет id")
            return False
        if item_id in taken:
            check.fail(f"Топология: id «{item_id}» уже занят другим объектом чертежа")
            return False
        taken.add(item_id)
        return True

    def listed(key: str) -> list[Mapping[str, Any]]:
        value = topology.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            check.fail(f"Топология: раздел «{key}» должен быть списком")
            return []
        return [_map(item) for item in value]

    nodes: dict[str, str] = {}
    for node in listed("nodes"):
        node_id = _text(node.get("id"))
        if not unique("узел", node_id):
            continue
        connector = _text(node.get("connectorId"))
        if node.get("connectorId") is not None and connector not in connectors:
            check.fail(f"Топология: узел «{node_id}» стоит на соединителе «{connector or '(пусто)'}», которого нет")
        directions = node.get("contactDirections")
        if directions is not None:
            known = contacts.get(connector) or set()
            unknown = [key for key in _map(directions) if key not in known]
            if not connector or not isinstance(directions, Mapping) or unknown:
                check.fail(f"Топология: у узла «{node_id}» направления контактов, которых нет в соединителе")
        nodes[node_id] = connector
    segments: dict[str, tuple[str, str]] = {}
    for segment in listed("segments"):
        segment_id = _text(segment.get("id"))
        if not unique("участок", segment_id):
            continue
        start, stop = _text(segment.get("from")), _text(segment.get("to"))
        if start not in nodes or stop not in nodes:
            check.fail(f"Топология: участок «{segment_id}» ссылается на отсутствующий узел")
        elif start == stop:
            check.fail(f"Топология: участок «{segment_id}» начинается и кончается в одном узле")
        segments[segment_id] = (start, stop)
    pipes = {_text(pipe.get("id")) for pipe in listed("joiningPipes") if _text(pipe.get("id"))}
    for pipe_id in pipes:
        unique("соединительная труба", pipe_id)
    for covering in listed("coverings"):
        covering_id = _text(covering.get("id"))
        if not unique("оболочка", covering_id):
            continue
        name = f"Оболочка {_text(covering.get('name')) or covering_id}"
        kind = covering.get("kind")
        if kind is not None and kind not in COVERING_KINDS:
            check.fail(f"{name}: незнакомый вид «{kind}»")
        if covering.get("lengthMode") not in (None, "auto", "manual"):
            check.fail(f"{name}: режим длины «{covering.get('lengthMode')}» не знаком")
        check.number(covering.get("lengthMm"), f"{name}: длина", nullable=True)
        spans = covering.get("spans")
        if not isinstance(spans, list) or not spans:
            check.fail(f"{name}: не лежит ни на одном участке")
            continue
        seen: set[str] = set()
        for span in map(_map, spans):
            segment_id = _text(span.get("segmentId"))
            if segment_id not in segments and segment_id not in pipes:
                check.fail(f"{name}: участка «{segment_id or '(пусто)'}» нет в топологии")
            elif segment_id in seen:
                check.fail(f"{name}: участок «{segment_id}» указан дважды")
            seen.add(segment_id)
            start, stop = to_decimal(span.get("from")), to_decimal(span.get("to"))
            if start is None or stop is None or start >= stop:
                check.fail(f"{name}: на участке «{segment_id}» начало не меньше конца")
    routed: set[str] = set()
    for route in listed("routes"):
        wire_id = _text(route.get("wireId"))
        if wire_id not in wires:
            check.fail(f"Топология: трасса провода «{wire_id or '(пусто)'}», которого нет в чертеже")
            continue
        label = _wire_label(wire_id, wires[wire_id])
        if wire_id in routed:
            check.fail(f"{label}: у провода две трассы")
            continue
        routed.add(wire_id)
        steps = route.get("steps")
        if not isinstance(steps, list) or not steps:
            check.fail(f"{label}: трасса без участков")
            continue
        first = previous = None
        visited: set[str] = set()
        broken = False
        for step in map(_map, steps):
            segment_id = _text(step.get("segmentId"))
            if segment_id not in segments or segment_id in visited:
                check.fail(f"{label}: трасса проходит по отсутствующему или повторному участку «{segment_id}»")
                broken = True
                break
            visited.add(segment_id)
            start, stop = segments[segment_id]
            if step.get("reverse") is True:
                start, stop = stop, start
            if previous is not None and start != previous:
                check.fail(f"{label}: трасса разорвана на участке «{segment_id}»")
                broken = True
                break
            first = first or start
            previous = stop
        if broken:
            continue
        for node, key, side in ((first, "from", "А"), (previous, "to", "Б")):
            connector = nodes.get(node or "", "")
            end = _map(wires[wire_id].get(key))
            if connector and _text(end.get("connectorId")) != connector:
                check.fail(f"{label}: трасса у конца {side} приходит к соединителю «{connector}», а провод — нет")


def validate_design(content: Mapping[str, Any]) -> list[str]:
    """Russian messages of everything that must not be stored; empty — the drawing may be saved."""
    check = _Check()
    for key in ("connectors", "wires"):
        if not isinstance(content.get(key), list):
            check.fail(f"В чертеже нет раздела «{COLLECTIONS[key]}»")
    collections = {key: _items(check, content, key) for key in COLLECTIONS}
    connectors, wires = collections["connectors"], collections["wires"]
    contacts = _connectors(check, connectors)
    junctions = {item_id: _members(check, f"Сросток {item_id}", item.get("wireIds"), wires, minimum=2)
                 for item_id, item in collections["junctions"].items()}
    screens: dict[str, list[str]] = {}
    for screen_id, screen in collections["screens"].items():
        label = f"Экран {_text(screen.get('label')) or screen_id}"
        screens[screen_id] = _members(check, label, screen.get("wireIds"), wires, minimum=1)
        if screen.get("terminalSide") is not None and screen.get("terminalSide") not in SCREEN_SIDES:
            check.fail(f"{label}: сторона подключения «{screen.get('terminalSide')}» не знакома")
    paired: dict[str, str] = {}
    for pair_id, pair in collections["diffPairs"].items():
        for wire_id in _members(check, f"Дифпара {pair_id}", pair.get("wireIds"), wires, minimum=2, maximum=2):
            if wire_id in paired:
                check.fail(f"{_wire_label(wire_id, wires[wire_id])} входит в две дифпары: {paired[wire_id]} и {pair_id}")
            paired.setdefault(wire_id, pair_id)
    for wire_id, wire in wires.items():
        for key, side in SIDES:
            _endpoint(check, wire_id, wire, key, side, contacts=contacts, junctions=junctions, screens=screens,
                      screen_rows=collections["screens"])
        _wire(check, wire_id, wire)
    _cables(check, collections["cables"], wires)
    if content.get("physicalTopology") is not None:
        _topology(check, content["physicalTopology"], connectors, wires, contacts)
    return check.errors


# ── References to the ERP books ───────────────────────────────────────────────────────────────────────────────────

BOOKS = {"wire": "БД.ПРОВ", "terminal": "БД.ТЕР", "covering": "БД.МАТ", "strip": "БД.КОАКС"}
NOUNS = {"wire": "провода", "terminal": "терминала", "covering": "материала", "strip": "разделки"}


def _key(value: object) -> str:
    return " ".join(_text(value).split()).casefold()


@dataclass(frozen=True)
class ReferenceBook:
    """Keys of the ERP books the drawing may reference (`harness_catalog.reference_book` builds it)."""
    wires: frozenset[str] = frozenset()
    terminals: frozenset[str] = frozenset()
    coverings: frozenset[str] = frozenset()
    strips: frozenset[str] = frozenset()
    keys: Mapping[str, frozenset[str]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "keys", {
            "wire": frozenset(map(_key, self.wires)), "terminal": frozenset(map(_key, self.terminals)),
            "covering": frozenset(map(_key, self.coverings)), "strip": frozenset(map(_key, self.strips)),
        })

    def missing(self, pins: Mapping[tuple[str, str], str]) -> list[str]:
        """Messages for the references the books do not have."""
        return [f"{where}: {NOUNS[kind]} «{key}» нет в {BOOKS[kind]}"
                for (kind, key), where in pins.items() if _key(key) not in self.keys[kind]]


def reference_pins(content: Mapping[str, Any]) -> dict[tuple[str, str], str]:
    """(kind, key) → where it is first referenced: wire / cable materials, contact terminals, covering materials,
    coax strip profiles."""
    pins: dict[tuple[str, str], str] = {}

    def add(kind: str, key: object, where: str) -> None:
        text = _text(key)
        if text:
            pins.setdefault((kind, text), where)

    def rows(value: object) -> Iterable[Mapping[str, Any]]:
        return (_map(item) for item in value) if isinstance(value, list) else ()

    for wire in rows(content.get("wires")):
        label = _wire_label(_text(wire.get("id")), wire)
        add("wire", _map(wire.get("materialBinding")).get("sourceKey"), label)
        for key, side in SIDES:
            add("strip", _map(_map(wire.get("stripProfiles")).get(key)).get("sourceKey"), f"{label}, разделка {side}")
    for position, cable in enumerate(rows(content.get("cables")), start=1):
        add("wire", _map(cable.get("materialBinding")).get("sourceKey"), f"Кабель {position}")
    for connector in rows(content.get("connectors")):
        label = f"Соединитель {_text(connector.get('designation')) or _text(connector.get('id'))}"
        for contact in rows(connector.get("contacts")):
            add("terminal", contact.get("terminalArticle"), f"{label}, контакт {contact.get('number') or contact.get('id')}")
    for covering in rows(_map(content.get("physicalTopology")).get("coverings")):
        add("covering", _map(covering.get("material")).get("sourceKey"),
            f"Оболочка {_text(covering.get('name')) or _text(covering.get('id'))}")
    return pins

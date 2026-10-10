"""What a harness drawing must hold to be stored (`harness.validate`): the rules ported from TECHMAP-GRAPHER's server
validators and editor parser, each with its Russian message; and the references to the ERP books."""

from copy import deepcopy

import pytest

from app.modules.techcards.harness.validate import ReferenceBook, reference_pins, validate_design

from . import grapher_fixture as fx


def _errors(change) -> list[str]:
    design = fx.design()
    change(design)
    return validate_design(design)


def _wire(design, wire_id):
    return next(wire for wire in design["wires"] if wire["id"] == wire_id)


def test_the_fixture_drawing_holds_every_rule():
    assert validate_design(fx.design()) == []


def test_a_free_end_and_an_independent_drawing_copy_are_not_dangling():
    def change(design):
        _wire(design, "w9")["to"] = {"connectorId": "", "contactId": ""}  # a free end
        _wire(design, "w10")["to"] = {"connectorId": "isolated:w10:to", "contactId": "free"}
        _wire(design, "w10")["drawingEndpoints"] = {"from": {"x": 0, "y": 0}, "to": {"x": 5, "y": 0}}
    assert _errors(change) == []


@pytest.mark.parametrize(("change", "message"), [
    (lambda d: _wire(d, "w1").update({"to": fx.at("x2", "x2c99")}),
     "Провод +5V (w1), конец Б: контакта «x2c99» нет в соединителе «x2»"),
    (lambda d: _wire(d, "w1").update({"from": fx.at("x9", "x9c1")}),
     "Провод +5V (w1), конец А: соединителя «x9» нет в чертеже"),
    (lambda d: _wire(d, "w1").update({"to": {"connectorId": "x2", "contactId": ""}}),
     "Провод +5V (w1), конец Б: указан соединитель, но не контакт"),
    (lambda d: _wire(d, "w1").pop("to"), "Провод +5V (w1), конец Б: нет конца провода"),
    (lambda d: _wire(d, "w1").update({"to": fx.splice("j9")}), "Провод +5V (w1), конец Б: сростка «j9» нет в чертеже"),
    (lambda d: _wire(d, "w1").update({"to": fx.splice("j1")}),
     "Провод +5V (w1), конец Б: сросток «j1» не содержит этот провод"),
    (lambda d: _wire(d, "w5").update({"from": {"screenId": "s1", "connectorId": "", "contactId": ""}}),
     "Провод D+ (w5), конец А: провод не может подключаться к экрану, в который сам входит"),
    (lambda d: _wire(d, "w7")["from"].update({"screenTerminalSide": "above"}),
     "Провод Экран (w7), конец А: у экрана нет такой точки подключения"),
    (lambda d: _wire(d, "w1").update({"to": {"junctionId": "j1", "screenId": "s1"}}),
     "Провод +5V (w1), конец Б: конец не может быть и на сростке, и на экране"),
])
def test_wire_ends_must_lead_somewhere_that_exists(change, message):
    assert message in _errors(change)


@pytest.mark.parametrize(("change", "message"), [
    (lambda d: d["wires"].append(deepcopy(_wire(d, "w1"))), "Провода: id «w1» повторяется"),
    (lambda d: d["connectors"].append({"id": "x1", "contacts": []}), "Соединители: id «x1» повторяется"),
    (lambda d: d["connectors"][0]["contacts"].append({"id": "x1c1"}), "Соединитель X1: id контакта «x1c1» повторяется"),
    (lambda d: d["wires"].append({"from": fx.at("x1", "x1c1")}), "Провода: у записи 11 нет id"),
    (lambda d: d.update(junctions="j1"), "Раздел «Сростки» должен быть списком"),
    (lambda d: d.pop("wires"), "В чертеже нет раздела «Провода»"),
])
def test_ids_are_present_and_unique(change, message):
    assert message in _errors(change)


@pytest.mark.parametrize(("change", "message"), [
    (lambda d: d["junctions"][0].update(wireIds=["w2"]), "Сросток j1: нужно не меньше 2 проводов, в нём 1"),
    (lambda d: d["junctions"][0].update(wireIds=["w2", "w3", "w4", "w2"]), "Сросток j1: провод указан дважды"),
    (lambda d: d["diffPairs"][0].update(wireIds=["w5", "w6", "w1"]), "Дифпара p1: нужно ровно 2 проводов, в нём 3"),
    (lambda d: d["diffPairs"].append({"id": "p2", "wireIds": ["w6", "w1"]}),
     "Провод D- (w6) входит в две дифпары: p1 и p2"),
    (lambda d: d["screens"][0].update(wireIds=["w5", "w42"]), "Экран Э1: провода «w42» нет в чертеже"),
    (lambda d: d["cables"][0].update(memberWireIds=["w9", "w9"]), "Кабель 1 (c1): провод указан дважды"),
    (lambda d: d["cables"].append({"id": "c2", "memberWireIds": ["w10"]}),
     "Кабель 2 (c2): провод «w10» уже входит в кабель «c1»"),
    (lambda d: d["cables"][0].update(sheathStrip={"fromMm": 600, "toMm": 500}), "Кабель 1 (c1): снятие оболочки длиннее кабеля"),
    (lambda d: d["cables"][0].update(sheathStrip={"fromMm": 30}), "Кабель 1 (c1): снятие оболочки задано неверно"),
])
def test_groups_and_cables_reference_existing_wires_once(change, message):
    assert message in _errors(change)


@pytest.mark.parametrize(("change", "message"), [
    (lambda d: _wire(d, "w1").update(lengthMm=-5), "Провод +5V (w1): длина: должна быть не меньше нуля"),
    (lambda d: _wire(d, "w1").update(lengthMm="300"), "Провод +5V (w1): длина: нужно число в миллиметрах"),
    (lambda d: _wire(d, "w1").update(lengthMm=300.0001), "Провод +5V (w1): длина: точность не более 0,001 мм"),
    (lambda d: _wire(d, "w1").update(cutRoundingStepMm=0),
     "Провод +5V (w1): шаг округления длины резки: должна быть больше нуля"),
    (lambda d: _wire(d, "w1").update(lengthMm=10, endCorrectionFromMm=-20),
     "Провод +5V (w1): длина с поправками получилась отрицательной"),
    (lambda d: _wire(d, "w8")["stripProfiles"]["from"]["layers"].__setitem__(1, {"index": 2, "diameterMm": 1.6, "stripLengthMm": 1}),
     "Провод RF (w8), разделка А: L2: слои должны идти по возрастанию номера, длины и диаметра"),
    (lambda d: _wire(d, "w8")["stripProfiles"]["from"].update(layers=[{"index": 1, "stripLengthMm": 0}]),
     "Провод RF (w8), разделка А: в профиле разделки нет слоёв L1…Ln"),
])
def test_lengths_and_strip_layers_hold_the_cut_rules(change, message):
    assert message in _errors(change)


@pytest.mark.parametrize(("change", "message"), [
    (lambda d: d["physicalTopology"]["nodes"].append({"id": "n4", "connectorId": "x9"}),
     "Топология: узел «n4» стоит на соединителе «x9», которого нет"),
    (lambda d: d["physicalTopology"]["segments"].append({"id": "s3", "from": "n1", "to": "n1"}),
     "Топология: участок «s3» начинается и кончается в одном узле"),
    (lambda d: d["physicalTopology"]["segments"].append({"id": "s3", "from": "n1", "to": "n9"}),
     "Топология: участок «s3» ссылается на отсутствующий узел"),
    (lambda d: d["physicalTopology"]["nodes"].append({"id": "w1"}), "Топология: id «w1» уже занят другим объектом чертежа"),
    (lambda d: d["physicalTopology"]["coverings"][0]["spans"][0].update(segmentId="s9"),
     "Оболочка Термоусадка 6/3: участка «s9» нет в топологии"),
    (lambda d: d["physicalTopology"]["coverings"][0].update(spans=[]), "Оболочка Термоусадка 6/3: не лежит ни на одном участке"),
    (lambda d: d["physicalTopology"]["coverings"][0].update(kind="glue"), "Оболочка Термоусадка 6/3: незнакомый вид «glue»"),
    (lambda d: d["physicalTopology"]["routes"][0]["steps"].reverse(), "Провод +5V (w1): трасса разорвана на участке «s1»"),
    (lambda d: d["physicalTopology"]["routes"].append({"wireId": "w1", "steps": [{"segmentId": "s1"}]}),
     "Провод +5V (w1): у провода две трассы"),
    (lambda d: d["physicalTopology"]["routes"].append({"wireId": "w9", "steps": [{"segmentId": "s1", "reverse": False}]}),
     "Провод A (w9): трасса у конца А приходит к соединителю «x1», а провод — нет"),
])
def test_topology_references_and_routes_hold(change, message):
    assert message in _errors(change)


def _numbered(design, **overrides):
    connector = design["connectors"][3]  # X4, two contacts
    binding = {"mode": "template", "contactNumbering": "source-v1", "snapshot": {"contacts": [
        {"logicalContactId": "a", "sourceNumber": "1"}, {"logicalContactId": "b", "sourceNumber": "2"}]}}
    binding.update(overrides)
    connector["libraryBinding"] = binding
    connector["contacts"] = [
        {"id": "x4:contact:a", "logicalContactId": "a", "number": 1},
        {"id": "x4:contact:b", "logicalContactId": "b", "number": 2},
    ]
    _wire(design, "w9")["from"] = fx.at("x4", "x4:contact:a")
    _wire(design, "w10")["from"] = fx.at("x4", "x4:contact:b")
    return connector


def test_library_connector_contacts_follow_the_template_numbering():
    assert _errors(_numbered) == []
    assert "Соединитель X4: контакт b должен иметь номер 2, в чертеже — 3" in _errors(
        lambda d: _numbered(d)["contacts"][1].update(number=3))
    assert "Соединитель X4: id контакта a должен быть «x4:contact:a»" in _errors(
        lambda d: _numbered(d)["contacts"][0].update(id="x4c1"))
    assert "Соединитель X4: номер контакта шаблона «301» — нужно целое от 1 до 300" in _errors(
        lambda d: _numbered(d, snapshot={"contacts": [{"logicalContactId": "a", "sourceNumber": "301"},
                                                      {"logicalContactId": "b", "sourceNumber": "2"}]}))
    assert "Соединитель X4: нумерация контактов по шаблону задана неверно" in _errors(lambda d: _numbered(d, mode="free"))


def test_the_book_check_names_what_is_missing_and_where():
    pins = reference_pins(fx.design())
    assert pins[("wire", "ПВАМ 0,35")] == "Провод +5V (w1)"
    assert pins[("terminal", "T-DS1069-SC600")] == "Соединитель X1, контакт 1"
    assert pins[("covering", "ТУТ 6/3мм")] == "Оболочка Термоусадка 6/3"
    assert pins[("strip", "RG-316/SMA")] == "Провод RF (w8), разделка А"
    assert pins[("wire", "ПВВС 2х0,35")] == "Кабель 1"
    book = ReferenceBook(wires=frozenset({"пвам  0,35", "ПВВС 2х0,35"}), terminals=frozenset({"T-DS1069-SC600", "PLS-T"}),
                         coverings=frozenset({"ТУТ 6/3мм"}), strips=frozenset())
    assert book.missing(pins) == [
        "Провод RF (w8), разделка А: разделки «RG-316/SMA» нет в БД.КОАКС",
        f"Соединитель X2, контакт 1: терминала «{fx.SHF_KEY}» нет в БД.ТЕР",
    ]

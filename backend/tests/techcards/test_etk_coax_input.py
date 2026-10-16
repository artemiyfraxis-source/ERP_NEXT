import json
from copy import deepcopy
from pathlib import Path

from app.modules.techcards.etk_builder import build_etk, fill_generator_chain
from app.modules.techcards.operation_cards import parse_operation_library

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])

CODES = {
    "coaxCut": "CUT_WIRE_auto", "coaxStrip": "STRIP_COAX_auto", "coaxCutTut": "CUT_TUT_short", "coaxPin": "SDR_COAX_pin",
    "coaxInsTut": "INS_TUT", "coaxInsSleeve": "INS_SLEEVE_coax", "coaxHousing": "INS_housing_COAX",
    "coaxShield": "PRS_SLEEVE_coax", "coaxHeat": "HEAT_TYT_short",
}
PAIRS = {"coaxCutTut", "coaxInsTut", "coaxInsSleeve", "coaxShield", "coaxHeat"}


def _owner_coax_etk(**coax_extra: object) -> list[dict]:
    """Кабельная сборка MMCX-Male — N-Male на RG316, 1 м, 20 шт. (проект 796К.2, владелец 15.10): сборка вкладки «Коакс»."""
    cards = {card.code: card for card in CARDS}
    sequence = [("coaxCut", ""), ("coaxStrip", "A"), ("coaxStrip", "B"), ("coaxCutTut", ""), ("coaxPin", "A"), ("coaxInsTut", ""),
                ("coaxInsSleeve", ""), ("coaxHousing", "A"), ("coaxPin", "B"), ("coaxHousing", "B"), ("coaxShield", ""), ("coaxHeat", "")]
    ops = [{"type": kind, "side": end, "units": 2 if kind in PAIRS else 1, "templateId": cards[CODES[kind]].source_id}
           for kind, end in sequence]
    config = {"mode": "coax", "partQty": 20, "coax": {
        "cableLength": 1010, "tutLength": 20, "tutArt": "ТУТ 3/1.5", "tutName": "Термоусадочная трубка 3/1,5",
        "cableArt": "RG316", "cableName": "Кабель RG316", "sleeveArt": "SL-316", "sleeveName": "Втулка экрана RG316",
        "sideA": {"article": "MMCX-Male", "connName": "Разъём MMCX-Male"},
        "sideB": {"article": "N-Male", "connName": "Разъём N-Male"}, **coax_extra,
    }, "ops": ops}
    return build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])["operations"]


def _inputs(operation: dict) -> dict[str, tuple[str, str]]:
    return {row["art"]: (row["norm"], row["unit"]) for row in operation["components"]}


def test_coax_cut_input_is_the_cable_in_meters_and_params_carry_the_cut_length():
    # Владелец 15.10: «не появилось кол-во» — «Вход» резки коаксиала был пуст, в «Общих» не было длины и допуска.
    cut = _owner_coax_etk()[0]
    assert _inputs(cut) == {"RG316": ("20,2", "М.")}
    assert cut["fields"]["lengthKd"] == "1010мм"
    assert cut["fields"]["cutTolerance"] == "±8 мм"


def test_every_coax_operation_gets_its_parts_in_the_input():
    by_type: dict[tuple[str, str], dict] = {}
    for operation in _owner_coax_etk():
        by_type.setdefault((operation["type"], operation["side"]), operation)
    assert _inputs(by_type[("coaxCutTut", "")]) == {"ТУТ 3/1.5": ("40", "Шт.")}
    assert _inputs(by_type[("coaxPin", "A")]) == {"MMCX-Male": ("20", "Шт.")}
    assert _inputs(by_type[("coaxPin", "B")]) == {"N-Male": ("20", "Шт.")}
    assert _inputs(by_type[("coaxInsSleeve", "")]) == {"SL-316": ("40", "Шт.")}
    assert by_type[("coaxStrip", "A")]["components"] == []


def test_old_coax_config_without_cable_length_builds_without_input_rows():
    operations = _owner_coax_etk(cableLength="", cableArt="", sleeveArt="", sleeveName="")
    assert operations[0]["components"] == [] and "lengthKd" not in operations[0]["fields"]


# Владелец 15.10 о ТК КБ1 проекта 796К.2 (коакс, 20 шт.): «нет результата» — у операций коакса не было «Результата»,
# «Вход» не брал полуфабрикат прошлой операции, время было на штуку, а не на партию.
PROD_SEQUENCE = [("coaxCut", ""), ("coaxStrip", "A"), ("coaxStrip", "B"), ("coaxCutTut", ""), ("coaxPin", "A"), ("coaxPin", "B"),
                 ("coaxInsTut", ""), ("coaxInsSleeve", ""), ("coaxHousing", "A"), ("coaxHousing", "B"), ("coaxShield", ""), ("coaxHeat", "")]
SECONDS = {"coaxCut": ("180", "3.50"), "coaxStrip": ("600", "10.00"), "coaxCutTut": ("300", "1.00"), "coaxPin": ("120", "25.00"),
           "coaxInsTut": ("120", "3.00"), "coaxInsSleeve": ("0", "3.00"), "coaxHousing": ("120", "10.00"),
           "coaxShield": ("120", "8.00"), "coaxHeat": ("120", "9.00")}


def _prod_config(**op_extra: dict) -> dict:
    """The build_config of 796К.2 КБ1 as issued on prod (MMCX-Male — N-111L on RG316, 1010 mm, 20 pcs)."""
    cards = {card.code: card for card in CARDS}
    ops = [{"type": kind, "side": end, "units": 2 if kind in PAIRS else 1, "templateId": cards[CODES[kind]].source_id,
            "opNum": CODES[kind], "tPrep": SECONDS[kind][0], "tOp": SECONDS[kind][1], **op_extra.get(kind, {})}
           for kind, end in PROD_SEQUENCE]
    return {"mode": "coax", "assemblyName": "Кабельная сборка MMCX-Male to N-Male RG316 1m", "partQty": 20, "coax": {
        "cableLength": 1010, "tutLength": 20, "tutArt": "ТУТ 5/2,5мм, черная", "tutName": "ТУТ 5/2,5мм, черная",
        "cableArt": "RG316", "cableName": "RG316",
        "sideA": {"article": "MMCX-Male RG316", "connName": "MMCX-Male RG316, арт.1440", "pinType": "пайка",
                  "l1": "1,8", "l2": "4,5", "l3": "10", "d1": "0,5", "d2": "1,48", "d3": "1,95"},
        "sideB": {"article": "N-111L", "connName": "N-111L", "pinType": "пайка", "l1": "4", "l2": "8", "l3": "16"},
    }, "ops": ops}


def _build(config: dict) -> dict:
    return build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])


def test_every_coax_operation_hands_on_its_semi_finished_cable_and_the_next_one_takes_it():
    operations = _build(_prod_config())["operations"]
    cut, strip_a, strip_b, tut_cut, pin_a, pin_b, put_tut, sleeve, housing_a, housing_b, shield, heat = operations
    assert [(row["name"], row["norm"], row["unit"]) for row in cut["result"]] == [("Кабель RG316 нарезан: 1010 мм", "20", "Шт.")]
    assert strip_a["result"][0]["name"] == "Кабель RG316 нарезан: 1010 мм; сторона А разделана под MMCX-Male RG316"
    # Та же работа на стороне В сразу после стороны А — одной частью названия.
    assert strip_b["result"][0]["name"] == "Кабель RG316 нарезан: 1010 мм; концы разделаны: А — под MMCX-Male RG316, В — под N-111L"
    assert pin_a["result"][0]["name"].endswith("; сторона А: припаян центральный контакт")
    assert pin_b["result"][0]["name"].endswith("; центральные контакты припаяны с двух сторон")
    assert housing_b["result"][0]["name"].endswith("; корпуса разъёмов установлены с двух сторон")
    assert shield["result"][0]["name"].endswith("; втулки экрана обжаты")
    assert (heat["result_label"], heat["result"][0]["name"]) == ("Изделие", "Кабельная сборка MMCX-Male to N-Male RG316 1m")
    # Вход каждой операции — результат прошлой продвигающей, слово в слово, на партию; резки входа не имеют.
    chain = [cut, strip_a, strip_b, pin_a, pin_b, put_tut, sleeve, housing_a, housing_b, shield, heat]
    for before, after in zip(chain, chain[1:]):
        assert after["semis"][0] == {"name": before["result"][0]["name"], "norm": "20", "unit": "Шт."}, after["position"]
    assert cut["semis"] == [] and tut_cut["semis"] == []
    for operation in operations:
        assert operation["result"] and all(row["name"] and row["norm"] for row in operation["result"]), operation["position"]


def test_coax_tut_pieces_are_a_side_input_of_putting_them_on():
    operations = _build(_prod_config())["operations"]
    tut_cut, put_tut = operations[3], operations[6]
    assert [(row["name"], row["norm"]) for row in tut_cut["result"]] == [("Трубка ТУТ 5/2,5мм, черная нарезана: 2 отрезка по 20 мм", "40")]
    assert put_tut["semis"][1] == {"name": "Трубка ТУТ 5/2,5мм, черная нарезана: 2 отрезка по 20 мм", "norm": "40", "unit": "Шт."}
    # Отрезки ТУТ — побочный полуфабрикат: следующая за резкой ТУТ операция берёт кабель, а не трубки.
    assert operations[4]["semis"] == [{"name": operations[2]["result"][0]["name"], "norm": "20", "unit": "Шт."}]
    # «[L КД] мм» карты резки ТУТ — длина отрезка из генератора, а не голые « мм».
    tolerances = [step["tolerance"] for step in tut_cut["card"]["steps"]]
    assert "20 мм" in tolerances and " мм" not in tolerances
    assert tut_cut["fields"]["lengthKd"] == "20мм"


def test_coax_time_is_for_the_batch_not_for_one_piece():
    times = [operation["time"][0]["norm"] for operation in _build(_prod_config())["operations"]]
    # Пайка: 25 с × 20 шт. / 60 + 2 мин подготовки; ТУТ и втулки — по две на изделие; резка — tOp, без узлов станка.
    assert times == ["4,17", "13,33", "13,33", "5,67", "10,33", "10,33", "4", "2", "5,33", "5,33", "7,33", "8"]


def test_coax_cut_of_a_linear_card_counts_by_the_machine_nodes():
    timing = {"rollSpeed": "3", "toolSeconds": "0,5", "toolOps": "1"}
    operations = _build(_prod_config(coaxCut={"linearTiming": timing}, coaxCutTut={"linearTiming": {**timing, "rollSpeed": "0,5"}}))["operations"]
    # 1,01 м / 3 м/с + 0,5 с = 0,837 с × 20 + 3 мин; ТУТ 0,02 м / 0,5 + 0,5 = 0,54 с × 2 × 20 + 5 мин.
    assert (operations[0]["time"][0]["norm"], operations[3]["time"][0]["norm"]) == ("3,28", "5,36")


def test_coax_operations_carry_what_their_sketch_draws():
    operations = _build(_prod_config())["operations"]
    pin_a, put_tut, sleeve, housing_b = operations[4], operations[6], operations[7], operations[9]
    end = pin_a["fields"]["coaxEnd"]
    assert (end["connector"], end["side"], end["l1"], end["l3"], end["pinType"]) == ("MMCX-Male RG316", "А", "1,8", "10", "пайка")
    assert (housing_b["fields"]["coaxEnd"]["connector"], housing_b["fields"]["coaxEnd"]["side"]) == ("N-111L", "В")
    assert put_tut["fields"]["coaxParts"] == {"kind": "tube", "name": "ТУТ 5/2,5мм, черная", "count": 2, "cable": "RG316", "length": "20"}
    assert (sleeve["fields"]["coaxParts"]["kind"], sleeve["fields"]["coaxParts"]["count"]) == ("sleeve", 2)


def _issued_before_the_fix() -> dict:
    """The stored 796К.2 КБ1 as issued before 15.10: no result rows, no input, unnamed time rows, no sketch data."""
    document = deepcopy(_build(_prod_config()))
    for operation in document["operations"]:
        last = operation["type"] == "coaxHeat"
        if not last:
            operation["result"] = []
        operation["semis"] = []
        operation["time"] = [{**row, "name": row["name"] if last else "", "norm": "0,06"} for row in operation["time"]]
        operation["fields"] = {key: value for key, value in operation["fields"].items() if key not in {"coaxEnd", "coaxParts"}}
    return document


def test_an_issued_coax_etk_gets_its_chain_on_read_without_a_new_revision():
    built, stored = _build(_prod_config()), _issued_before_the_fix()
    read = fill_generator_chain(stored)
    for expected, operation in zip(built["operations"], read["operations"]):
        assert operation["result"] == expected["result"], operation["position"]
        assert operation["semis"] == expected["semis"], operation["position"]
        assert operation["fields"] == expected["fields"], operation["position"]
        # Время — как выдано (по нему планировало производство); имя строки времени — полуфабрикат.
        assert [row["norm"] for row in operation["time"]] == ["0,06"]
        assert operation["time"][0]["name"] == expected["time"][0]["name"]
    assert stored["operations"][1]["result"] == []  # the stored document itself is not touched


def test_the_read_fill_keeps_what_is_there_and_skips_documents_it_cannot_replay():
    stored = _issued_before_the_fix()
    stored["operations"][1]["result"] = [{"name": "Правлено технологом", "norm": "20", "unit": "Шт."}]
    assert fill_generator_chain(stored)["operations"][1]["result"] == [{"name": "Правлено технологом", "norm": "20", "unit": "Шт."}]
    full = _build(_prod_config())
    assert fill_generator_chain(full) is full
    shifted = deepcopy(stored)
    shifted["build_config"]["ops"] = shifted["build_config"]["ops"][1:]
    assert fill_generator_chain(shifted) is shifted
    wire = {"build_config": {"ops": [{"type": "cutWire"}]}, "operations": [{"type": "cutWire", "result": []}]}
    assert fill_generator_chain(wire) is wire
    assert fill_generator_chain(None) is None


# Владелец 16.10: у опрессовки втулки экрана (PRS_SLEEVE_coax) не было размера HEX — он зависит от разъёма и кабеля.
def _shield(**coax_extra: object) -> dict:
    return next(op for op in _owner_coax_etk(**coax_extra) if op["type"] == "coaxShield")


def _hex_step(operation: dict) -> str:
    return next(step["text"] for step in operation["card"]["steps"] if "hex" in step["text"].casefold())


def _dies(operation: dict) -> str:
    return next(tool["name"] for tool in operation["card"]["tooling"] if tool["section"] == "Матрицы")


def test_shield_crimp_without_hex_in_the_base_says_so_instead_of_guessing():
    shield = _shield()
    assert shield["fields"]["crimpHex"] == [{"side": "А", "connector": "MMCX-Male", "hex": ""},
                                            {"side": "В", "connector": "N-Male", "hex": ""}]
    assert _hex_step(shield).endswith("Размер: HEX не задан — по чертежу разъёма.")
    assert _dies(shield) == "Шестигранные матрицы — HEX не задан — по чертежу разъёма"


def test_shield_crimp_shows_the_hex_of_the_connector_on_its_cable():
    same = _shield(sideA={"article": "MMCX-Male", "crimpHex": "3,25"}, sideB={"article": "N-Male", "crimpHex": "3,25"})
    assert _hex_step(same).endswith("Размер: HEX 3,25 мм.")
    assert _dies(same) == "Шестигранные матрицы — HEX 3,25 мм"
    mixed = _shield(sideA={"article": "MMCX-Male", "crimpHex": "3,25"}, sideB={"article": "N-Male"})
    assert _hex_step(mixed).endswith(
        "Размер: А (MMCX-Male) — HEX 3,25 мм; В (N-Male) — HEX не задан — по чертежу разъёма.")
    inches = _shield(sideA={"article": "MMCX-Male", "crimpHex": "0,475″"}, sideB=None)
    assert _dies(inches) == "Шестигранные матрицы — HEX 0,475″"


def test_housing_of_a_collet_contact_does_not_speak_of_a_soldered_pin():
    # N-111C/8D: «центральный контакт - цанговый и не требует пайки» — генератор ставит тип пина «цанга».
    operations = _owner_coax_etk(sideA={"article": "N-111C/8D", "pinType": "цанга"})
    housing_a, housing_b = [op for op in operations if op["type"] == "coaxHousing"]
    text_a = " ".join(step["text"] for step in housing_a["card"]["steps"])
    text_b = " ".join(step["text"] for step in housing_b["card"]["steps"])
    assert housing_a["fields"]["collet"] is True and "collet" not in housing_b["fields"]
    assert "центральный контакт (цанговый, без пайки)" in text_a and "припаянн" not in text_a
    assert "припаянный центральный контакт" in text_b

"""«Заводской номер на изделии» и «Паспорт по ВАЦ» генератора КОАКС (владелец 27.11, 831К.): ЭТК из конфига, который
собирает генератор (frontend/src/__fixtures__/coaxSerialConfigs.json, тот же, что проверяет tcGenSerial.test.ts)."""
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from app.modules.techcards.etk_builder import build_etk
from tests.techcards.test_etk_coax_fakra import _fresh_cards

FIXTURE = json.loads((Path(__file__).resolve().parents[3] / "frontend" / "src" / "__fixtures__" / "coaxSerialConfigs.json")
                     .read_text(encoding="utf-8"))


def _build(kind: str, qty: int = 4):
    config = {**deepcopy(FIXTURE[kind]), "partQty": qty}
    doc = build_etk(config, _fresh_cards(config))
    keys = [op["opKey"] for op in doc["build_config"]["ops"]]
    return doc, {key: doc["operations"][index] for index, key in enumerate(keys)}, keys


def _norms(rows):
    return {row["art"]: Decimal(row["norm"].replace(",", ".")) for row in rows}


def test_label_ops_keep_order_and_end_tut_beside_the_clear_cover():
    doc, ops, keys = _build("tape")
    assert keys == ["coaxCutStrip", "coaxPrintLabel", "coaxProtectCut", "coaxCutTut", "coaxPinA", "coaxPinB", "coaxProtectIns",
                    "coaxInsTut", "coaxInsSleeve", "coaxHousingA", "coaxHousingB", "coaxShield", "coaxHeatTut",
                    "coaxInsLabel", "coaxHeat", "coaxVswr"]
    assert [ops[key]["code"] for key in ("coaxPrintLabel", "coaxProtectCut", "coaxInsLabel", "coaxHeat", "coaxVswr")] == [
        "PRINT_MARK_LABEL", "CUT_TUT_short", "INS_MARK_LABEL", "HEAT_TYT_short", "CTRL_VSWR_coax"]
    # Clear cover = label 40 мм by the ОБ.102 profile + 10 мм, one per item; end TUT keeps its own tube and length.
    assert doc["build_config"]["coax"]["tutLengths"] == {"A": 50, "B": 50, "middle": 50}
    assert _norms(ops["coaxProtectCut"]["components"]) == {"ТУТ 6/3 прозрачная": Decimal("0.2")}
    assert _norms(ops["coaxCutTut"]["components"]) == {"ТУТ 6/3 чёрная": Decimal("0.16")}
    assert ops["coaxPrintLabel"]["components"] == []


def test_print_op_carries_the_serial_template_for_printing():
    _, ops, _ = _build("tape")
    fields = ops["coaxPrintLabel"]["fields"]
    assert [(row["text"], row["qty"], row["batch"]) for row in fields["marks"]] == [("FRX.831К.КБ1.{№}", "1", "4")]
    assert fields["serial"] == {"template": "FRX.831К.КБ1.{№}", "designation": "КБ1", "productKind": "kb", "carrier": "tape",
                                     "qr": True, "profile": {"id": "p40", "name": "40×60", "lengthMm": 40}}
    assert ops["coaxPrintLabel"]["card"]["name"] == "Печать этикетки заводского номера"


def test_passport_flag_reaches_the_vswr_op():
    _, ops, _ = _build("tape")
    vswr = ops["coaxVswr"]
    assert vswr["fields"]["coaxVswrControl"] == {"mode": "all", "every": None, "passport": True}
    text = " ".join(step["text"] for step in vswr["card"]["steps"])
    assert "паспорт по ВАЦ на каждое изделие" in text


def test_printed_tube_is_the_printers_consumable_and_goes_on_before_the_housings():
    doc, ops, keys = _build("tube")
    assert keys.index("coaxInsMarkTube") < keys.index("coaxHousingA")
    assert ops["coaxPrintLabel"]["code"] == "PRINT_MARK_TUBE"
    assert ops["coaxPrintLabel"]["components"] == []
    assert doc["build_config"]["coax"]["tutLengths"] == {"A": 35, "B": 35, "middle": 35}
    material = {row["material"] for row in ops["coaxPrintLabel"]["fields"]["marks"]}
    assert material == {"Маркировочная трубка, принтер Supvan TP80E (ОБ.057)"}


def test_custom_designation_and_tube_carrier_reach_the_print_op_together_with_the_template():
    config = deepcopy(FIXTURE["tube"])
    config["coax"]["serial"].update(template="FRX.831К.34.226.{№}", designation="34.226", productKind="custom")
    doc = build_etk(config, _fresh_cards(config))
    printed = next(op for op in doc["operations"] if op["code"] == "PRINT_MARK_TUBE")
    assert printed["fields"]["serial"] == {"template": "FRX.831К.34.226.{№}", "designation": "34.226", "productKind": "custom",
                                           "carrier": "tube", "qr": True, "tubeLengthMm": 25}


def _steps(operation):
    return " ".join(step["text"] for step in operation["card"]["steps"])


def test_tube_carrier_shrinks_the_marking_tube_first_then_covers_it_with_its_own_clear_tube_op():
    for short in (False, True):
        config = deepcopy(FIXTURE["tube"])
        if short:
            config["coax"]["shortCards"] = True
        doc = build_etk(config, _fresh_cards(config))
        keys = [op["opKey"] for op in doc["build_config"]["ops"]]
        ops = dict(zip(keys, doc["operations"]))
        assert (keys.index("coaxInsMarkTube") < keys.index("coaxHeatMarkTube") < keys.index("coaxProtectIns")
                < keys.index("coaxHousingA") < keys.index("coaxHeat"))
        assert ops["coaxHeatMarkTube"]["components"] == [] and ops["coaxInsMarkTube"]["components"] == []
        # 831К.: трубка 25 мм + 10 = прозрачная ТУТ 35 мм, одна на изделие; партия 1 в фикстуре.
        assert doc["build_config"]["coax"]["tutLengths"] == {"A": 35, "B": 35, "middle": 35}
        assert _norms(ops["coaxProtectCut"]["components"]) == {"ТУТ 6/3 прозрачная": Decimal("0.035")}
        assert [ops[key]["code"] for key in ("coaxPrintLabel", "coaxInsMarkTube", "coaxHeatMarkTube", "coaxProtectIns", "coaxHeat")] == [
            "PRINT_MARK_TUBE", "INS_TUT", "HEAT_TYT_short", "INS_TUT", "HEAT_TYT_short"]
        assert "прозрачн" not in _steps(ops["coaxInsMarkTube"]).replace("прозрачная ТУТ — после неё", "")
        assert "маркировочн" in _steps(ops["coaxProtectIns"])
        assert "маркировочн" in _steps(ops["coaxHeat"])
        assert "этикет" not in _steps(ops["coaxHeat"])

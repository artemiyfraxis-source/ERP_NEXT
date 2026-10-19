"""Прижимной экран коакса (владелец 19.10): «Надеть» (гайка, шайба, уплотнитель, зажимное кольцо) → контакт → одна операция
«Монтаж корпуса с прижимом» вместо втулки экрана и её опрессовки; HEX у прижимного разъёма не читается, даже если он есть в базе."""
import csv
import json
from pathlib import Path

from app.modules.techcards.etk_builder import build_etk, coax_crimp_hex
from app.modules.techcards.operation_cards import parse_operation_library

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
ERP_LIBRARY = json.loads((ROOT / "data" / "techcard-library-erp.json").read_text(encoding="utf-8"))
ERP_CARDS = parse_operation_library(ERP_LIBRARY["templates"])
CARDS = {card.code: card for card in parse_operation_library(LIBRARY["templates"])}
CLAMP_HOUSING = {card.title: card for card in ERP_CARDS if card.code == "INS_housing_COAX_clamp"}
ALL_CARDS = [*CARDS.values(), *ERP_CARDS]


def _end(article: str, shield: str, hex_value: str = "", pin: str = "пайка") -> dict:
    return {"article": article, "type": "N-male", "l1": "6", "l2": "10", "l3": "10", "pinType": pin, "shield": shield,
            **({"crimpHex": hex_value} if hex_value else {})}


def _housing(side: str) -> dict:
    card = next(card for title, card in CLAMP_HOUSING.items() if title.endswith(f"({side})"))
    return {"type": "coaxHousing", "side": side, "templateId": card.source_id}


def _config(shield_a: str = "прижим", shield_b: str = "прижим", hex_a: str = "", hex_b: str = "", pin: str = "пайка") -> dict:
    ops = [
        {"type": "coaxInsSleeve", "units": 1, "clampSides": ["A"], "templateId": CARDS["INS_SLEEVE_coax"].source_id},
        {"type": "coaxPin", "side": "A", "templateId": CARDS["SDR_COAX_pin"].source_id},
        _housing("A"),
    ]
    return {"mode": "coax", "partQty": 10, "coax": {
        "cableLength": 500, "cableArt": "RG316", "cableName": "RG316", "sleeveArt": "Втулка 3,25", "shieldType": shield_a,
        "sideA": _end("N-112B", shield_a, hex_a, pin), "sideB": _end("S-111", shield_b, hex_b)}, "ops": ops}


def _built(config: dict) -> list[dict]:
    return build_etk(config, ALL_CARDS)["operations"]


def test_the_clamp_housing_cards_are_in_the_erp_library_as_the_owner_wrote_them():
    assert sorted(CLAMP_HOUSING) == ["INS_housing_COAX_clamp | Монтаж корпуса с прижимом (A)", "INS_housing_COAX_clamp | Монтаж корпуса с прижимом (B)"]
    assert not any(card.code in {"INS_NUT_coax", "INS_BRAID_clamp"} for card in ERP_CARDS)
    for card in CLAMP_HOUSING.values():
        assert card.category == "Монтаж корпуса"
        assert [(step.kind, step.number) for step in card.steps] == [("Операция", str(n)) for n in range(1, 7)] + [("Контроль", "7")]
        text = " ".join(step.text for step in card.steps).casefold()
        assert text.startswith("довести зажимное кольцо (конус) до торца оболочки")
        assert "обрезать оплётку по краю конуса" in text and "по документации производителя" in text
        assert "втулк" not in text and "hex" not in text and "опрессов" not in text
        assert [tool.name for tool in card.tooling if tool.section == "Инструмент"] == [
            "Ножницы для оплётки", "Пинцет антистатический", "Ключи гаечные под размер гайки и корпуса", "Ключ динамометрический"]
        assert not [tool for tool in card.tooling if tool.section in {"Оборудование", "Расходники"}]
        for defect in ("Пряди оплётки на изоляторе", "Оплётка не зажата", "Гайка перетянута", "Пропущен уплотнитель или шайба", "Контакт не дошёл до упора"):
            assert defect in card.defects


def test_a_clamp_side_is_one_put_on_op_the_contact_and_one_housing_op_without_pressing():
    operations = _built(_config())
    assert [operation["type"] for operation in operations] == ["coaxInsSleeve", "coaxPin", "coaxHousing"]
    put_on, _, housing = operations
    assert housing["code"] == "INS_housing_COAX_clamp"
    assert housing["fields"]["coaxEnd"]["shield"] == "прижим" and "crimpHex" not in housing["fields"]
    text = " ".join(step["text"] for step in put_on["card"]["steps"])
    assert "гайку" in text and "уплотнитель" in text and "зажимное кольцо (конус)" in text and "гильз" not in text
    assert put_on["fields"]["coaxParts"]["clampSides"] == ["А"]
    assert put_on["components"] == []  # втулки на прижимной стороне нет
    assert housing["result"][0]["name"] == "Кабель RG316 500 мм с корпусом N-Male (прижим) на стороне А"
    assert not any(operation["type"] in {"coaxShield", "coaxClampNut", "coaxClampBraid"} for operation in operations)


def test_a_mixed_put_on_keeps_the_sleeve_for_the_crimped_end_only():
    config = _config("прижим", "обжим")
    config["ops"][0] = {"type": "coaxInsSleeve", "units": 2, "clampSides": ["A"], "templateId": CARDS["INS_SLEEVE_coax"].source_id}
    put_on = _built(config)[0]
    assert put_on["components"][0]["norm"] == "10"  # одна втулка на изделие, не две
    assert "только на обжимную сторону" in put_on["card"]["steps"][0]["text"]


def test_hex_of_a_clamp_connector_is_ignored_even_when_the_base_has_one():
    config = _config("прижим", "обжим", hex_a="13,51", hex_b="5,41")["coax"]
    assert coax_crimp_hex({"coax": config}) == [{"side": "В", "connector": "S-111", "hex": "5,41 мм"}]
    both = _config("прижим", "прижим", hex_a="13,51", hex_b="9")["coax"]
    assert coax_crimp_hex({"coax": both}) == []
    solder = _config("пайка", "обжим", hex_a="13,51", hex_b="5,41")["coax"]
    assert [item["side"] for item in coax_crimp_hex({"coax": solder})] == ["В"]
    # Старые конфиги без типа экрана у конца — обжим, как было.
    old = {"sideA": {"article": "N-111", "crimpHex": "13,51"}}
    assert coax_crimp_hex({"coax": old}) == [{"side": "А", "connector": "N-111", "hex": "13,51 мм"}]


def test_a_mixed_assembly_puts_the_hex_of_the_crimped_end_only_into_the_sleeve_crimp():
    config = _config("прижим", "обжим", hex_a="13,51", hex_b="5,41")
    config["ops"].append({"type": "coaxShield", "units": 1, "templateId": CARDS["PRS_SLEEVE_coax"].source_id})
    shield = _built(config)[-1]
    assert shield["fields"]["crimpHex"] == [{"side": "В", "connector": "S-111", "hex": "5,41 мм"}]
    text = " ".join(step["text"] for step in shield["card"]["steps"]) + " ".join(tool["name"] for tool in shield["card"]["tooling"])
    assert "13,51" not in text


def test_a_clamp_housing_with_a_crimped_contact_is_built_the_same_way():
    operations = _built(_config(pin="обжим"))
    assert [operation["type"] for operation in operations][-1] == "coaxHousing" and operations[-1]["code"] == "INS_housing_COAX_clamp"


def _norm_row(code: str) -> dict[str, str]:
    with (ROOT / "data" / "reference-mirror" / "БД.ОП.tsv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="	"))
    header = rows[1]
    return next(dict(zip(header, row)) for row in rows[2:] if row and row[0] == code)


def test_the_clamp_housing_has_its_own_time_norm_by_analogy_and_the_etk_carries_it():
    """БД.ОП (19.10): tOp = «Монтаж корпуса» 10 + «Опрессовка втулки» 8 = 18 с, подготовка как у монтажа корпуса; номер строки = код карточки."""
    clamp, base, press = (_norm_row(code) for code in ("INS_housing_COAX_clamp", "INS_housing_COAX", "PRS_SLEEVE_coax"))
    seconds = lambda row: float(row["Время Операции"].replace(",", "."))
    assert seconds(clamp) == seconds(base) + seconds(press) == 18
    assert clamp["Время подготовки, сек"] == base["Время подготовки, сек"] == "120" and clamp["Время машины, сек/оп; сек/м"] == "0,00"
    assert clamp["Время ручных работ для взятия полуфабриката"] == "2" and clamp["Время ручных работ"] == "14"
    assert "уточнить хронометражем" in clamp["Инструкция"]
    assert {card.code for card in CLAMP_HOUSING.values()} == {clamp["Номер"]}
    config = _config()
    config["ops"][-1] = {**config["ops"][-1], "tOp": clamp["Время Операции"].replace(",", "."),
                         "tPrep": clamp["Время подготовки, сек"], "tMachine": "0"}
    housing = _built(config)[-1]
    assert housing["code"] == "INS_housing_COAX_clamp"
    assert housing["times"] == {"operation_seconds": "18.00", "setup_seconds": "120", "machine_seconds": "0"}
    assert housing["time"][0]["norm"] == "3"  # 18 с × партия 10 = 3 мин

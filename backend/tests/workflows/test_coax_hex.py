"""HEX матрицы обжима втулки экрана коакс-сборки из БД.КОАКС (владелец 16.10)."""

from app.workflows.techcard_build import coax_hex_cell, enrich_coax_hex

ROWS = [
    {"coaxArticle": "S-111F", "coaxWire": "RG-58", "HEX обжима, мм": "5,41"},
    {"coaxArticle": "S-111F", "coaxWire": "RG-174", "HEX обжима, мм": ""},
    {"coaxArticle": "F-male для RG-11", "coaxWire": "RG-11", "HEX обжима, мм": "12,07"},
    {"coaxArticle": "N-111L", "coaxWire": "RG-174", "HEX обжима, мм": "3,25"},
    {"coaxArticle": "N-111L", "coaxWire": "RG-316", "HEX обжима, мм": "3,25"},
]


def _config(article_a: str, wire: str = "", cable: str = "RG-58", article_b: str = "") -> dict:
    coax = {"cableArt": cable, "sideA": {"article": article_a, "wire": wire, "crimpHex": "старое"}}
    if article_b:
        coax["sideB"] = {"article": article_b, "wire": ""}
    return {"mode": "coax", "coax": coax}


def test_hex_comes_from_the_row_of_the_connector_on_that_cable():
    enriched = enrich_coax_hex(_config("s-111f", wire="RG58"), ROWS)
    assert enriched["coax"]["sideA"]["crimpHex"] == "5,41"
    # Тот же разъём на кабеле без HEX — ключа нет, а не значение соседней строки.
    assert "crimpHex" not in enrich_coax_hex(_config("S-111F", cable="RG-174"), ROWS)["coax"]["sideA"]


def test_without_the_pair_the_connector_rows_must_agree():
    agreed = enrich_coax_hex(_config("N-111L", cable="RG-178", article_b="F-male для RG-11"), ROWS)["coax"]
    assert agreed["sideA"]["crimpHex"] == "3,25"
    assert agreed["sideB"]["crimpHex"] == "12,07"
    assert "crimpHex" not in enrich_coax_hex(_config("S-111F", cable="RG-213"), ROWS)["coax"]["sideA"]
    assert "crimpHex" not in enrich_coax_hex(_config("НЕТ-ТАКОГО"), ROWS)["coax"]["sideA"]


def test_the_hex_column_is_any_column_named_hex_and_configs_without_coax_pass():
    assert coax_hex_cell({"Артикул": "x", "HEX, мм": " 4,52 "}) == "4,52"
    assert coax_hex_cell({"Артикул": "x"}) == ""
    assert enrich_coax_hex({"mode": "inter", "ops": []}, ROWS) == {"mode": "inter", "ops": []}


DEPTH_ROWS = [
    {"Артикул": "SMA-C174P", "Провод": "RG-174", "Заглубление контакта, мм": "0,38…1,14"},
    {"Артикул": "SMA-C174P", "Провод": "RG-316", "Заглубление контакта, мм": "0,38…1,14"},
    {"Артикул": "SMA-C316F", "Провод": "RG-316", "Заглубление контакта, мм": "1,88…2,41"},
    {"Артикул": "SMA-C316F", "Провод": "RG-174", "Заглубление контакта, мм": "1,88…2,40"},
    {"Артикул": "SMA-JW3", "Провод": "RG-316", "Заглубление контакта, мм": ""},
]


def test_contact_depth_comes_from_the_rows_of_the_connector_when_they_agree():
    from app.workflows.techcard_build import enrich_coax_depth
    config = {"mode": "coax", "coax": {"sideA": {"article": "sma-c174p", "contactDepth": "старое"},
                                       "sideB": {"article": "SMA-C316F"}}}
    coax = enrich_coax_depth(config, DEPTH_ROWS)["coax"]
    assert coax["sideA"]["contactDepth"] == "0,38…1,14"
    assert "contactDepth" not in coax["sideB"]  # строки разъёма расходятся — не гадаем
    for article in ("SMA-JW3", "НЕТ-ТАКОГО"):  # угловой / нет значения
        assert "contactDepth" not in enrich_coax_depth({"coax": {"sideA": {"article": article}}}, DEPTH_ROWS)["coax"]["sideA"]
    assert enrich_coax_depth({"mode": "inter"}, DEPTH_ROWS) == {"mode": "inter"}

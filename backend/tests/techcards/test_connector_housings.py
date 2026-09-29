from app.modules.techcards.connector_housings import attach_housings, housing_grid, housing_of


def test_current_supplymap_connector_families_resolve_wire_side_layouts():
    gh = housing_of("JST GHR-04V-S")
    mini_fit = housing_of("Molex 39012140")
    weipu_socket = housing_of("SA812/S4")
    weipu_pin = housing_of("SA812/P4")

    assert gh and gh["grid"] == [[1, 2, 3, 4]]
    assert mini_fit and mini_fit["n"] == 14 and mini_fit["grid"][1][-1] == 1
    assert weipu_socket and weipu_socket["kind"] == "round"
    assert weipu_pin and weipu_pin["round"][0]["x"] == -weipu_socket["round"][0]["x"]


def test_current_supplymap_family_examples_and_orientation_rules():
    expected = {
        "GHR-04V-S": ("jst-gh", 4, 1, 4),
        "SHR-04V-S-B": ("jst-sh", 4, 1, 4),
        "SMP-02V-BC": ("jst-sm-p", 2, 1, 2),
        "XHP-9": ("jst-xh", 9, 1, 9),
        "Molex 510210600": ("molex-picoblade", 6, 1, 6),
        "Molex 39012140": ("molex-minifit-jr", 14, 2, 7),
        "Molex 43025-0200": ("molex-microfit", 2, 2, 1),
        "I-DS1071-SCB02X20": ("connfly-ds1071", 40, 2, 20),
        "174047-2": ("te-multilock-040", 20, 2, 10),
        "XT60U-F": ("amass-xt", 2, 1, 2),
        "8P8C-SH": ("rj45", 8, 1, 8),
        "USB-A Male 2.0": ("usb-a", 4, 1, 4),
        "НКИ 1.5-6": ("lug", 1, 1, 1),
        "Banana 4mm": ("banana", 1, 1, 1),
    }
    assert {
        article: (layout["family"], layout["n"], layout["rows"], layout["cols"])
        for article in expected if (layout := housing_of(article))
    } == expected
    assert housing_of("SMR-04V-B")["color"] == "#2b2b2b"
    assert housing_of("SMR-04V-N")["color"] == "#efe6cf"
    assert housing_of("SMR-18V-B") is None
    assert housing_of("XYZ-999") is None

    lp12 = housing_of("LP-12")
    assert lp12 and lp12["family"] == "cnlinko-lp12" and lp12["n"] == 2
    assert lp12["conf"] == "low" and "LP-12-C02PE" in lp12["note"]


def test_grid_supports_column_order_and_attach_is_derived_without_mutating_snapshot():
    assert housing_grid(2, 3, 6, "col", "TR") == [[5, 3, 1], [6, 4, 2]]
    document = {"operations": [{
        "type": "insTerm", "connector_article": "XHP-4", "fields": {"wires": []},
    }]}
    enriched = attach_housings(document)

    assert enriched["operations"][0]["housing"]["family"] == "jst-xh"
    assert "housing" not in document["operations"][0]


def test_ds1069_is_jst_xh_analogue_not_sm():
    for article in ("I-DS1069-SCW004", "i-ds1069-scb04", "Connfly I-DS1069-SCW004"):
        layout = housing_of(article)
        assert layout and layout["family"] == "connfly-ds1069" and layout["n"] == 4
        assert "XH" in layout["title"] and "SM" not in layout["title"]
        assert layout["latch"] == "ramp"
        assert layout["grid"] == [[1, 2, 3, 4]]  # pin 1 on the left, as JST XH
    xh = housing_of("XHP-4")
    assert xh["latch"] == "ramp" and xh["grid"] == housing_of("I-DS1069-SCW004")["grid"]


def test_alias_rule_bl2018p_resolves_to_jst_smp_plug():
    for article, count in (("BL2018P-04", 4), ("bl2018p-4", 4), ("BL2018P-10", 10), ("BL2018P-04-X", 4)):
        layout = housing_of(article)
        assert layout and layout["family"] == "jst-sm-p" and layout["n"] == count
        assert layout["conf"] == "high"
    assert housing_of("BL2018P-04")["grid"] == housing_of("SMP-04V-BC")["grid"]
    assert housing_of("BL2018P-14") is None  # SM is capped at 12 contacts
    assert housing_of("SMR-04V-B")["family"] == "jst-sm-r" and housing_of("SMP-02V-BC")["family"] == "jst-sm-p"
    assert housing_of("LP-12")["family"] == "cnlinko-lp12"

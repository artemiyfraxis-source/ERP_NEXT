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


def test_series_bought_for_projects_have_drawings():
    expected = {
        "DS1033-09FUNSISS-CT": ("dsub", 9), "I-DS1033-09M": ("dsub", 9), "DT06-4S": ("deutsch-dt", 4),
        "282106-1": ("te-superseal", 4), "282080-1": ("te-superseal", 2), "GX12M-2A": ("aviation-gx", 2),
        "DC5.5x2.1": ("dc-barrel", 2), "SHR05V-S-B": ("jst-sh", 5), "PHDR-14VS": ("jst-phd", 14),
        "Molex 430200400": ("molex-microfit-plug", 4), "Molex 436400400": ("molex-microfit-plug-1r", 4),
        "MOLEX 5025780600": ("molex-clikmate", 6), "Molex 5011892010": ("molex-picoclasp", 20),
        "Molex 51110-2051": ("molex-milligrid", 20), "FI-X30HL": ("jae-fix", 30), "RJ45": ("rj45", 8),
    }
    assert {
        article: (layout["family"], layout["n"]) for article in expected if (layout := housing_of(article))
    } == expected


def test_dsub_wire_side_mirrors_between_socket_and_pin_sides():
    socket, pins = housing_of("DS1033-09F"), housing_of("DS1033-09M")

    assert socket["grid"] == [[1, 0, 2, 0, 3, 0, 4, 0, 5], [0, 6, 0, 7, 0, 8, 0, 9, 0]]
    assert pins["grid"] == [list(reversed(row)) for row in socket["grid"]]
    assert socket["male"] is False and pins["male"] is True
    assert housing_of("DS1033-11F") is None


def test_round_auto_layout_spreads_pins_on_a_circle():
    layout = housing_of("GX16M-5")
    assert [point["pin"] for point in layout["round"]] == [1, 2, 3, 4, 5]
    assert layout["round"][0] == {"pin": 1, "x": 0.0, "y": -0.55}


def test_jst_sh_follows_the_jst_housing_drawing():
    # eSH «Housing»: top view with the wire end up, Circuit No.1 ▽ on the left -> from the wire side, ▽ face up,
    # contact 1 is on the right. B = A + 4.0 with protrusions (-B), A + 2.0 without; 2.8 thick, 5 long.
    with_protrusions = housing_of("SHR-04V-S-B")
    plain = housing_of("SHR-04V-S")

    assert with_protrusions["grid"] == [[4, 3, 2, 1]] and with_protrusions["conf"] == "high"
    assert with_protrusions["body"]["ears"] == 1.0 and "ears" not in plain["body"]
    assert (with_protrusions["body"]["h"], with_protrusions["body"]["len"]) == (2.8, 5.0)
    width = lambda layout: (layout["n"] - 1) * layout["pitch"] + layout["body"]["extra"] + 2 * layout["body"].get("ears", 0)
    assert housing_of("SHR-02V-S-B") and width(housing_of("SHR-02V-S-B")) == 5.0
    assert width(housing_of("SHR-20V-S-B")) == 23.0 and width(housing_of("SHR-15V-S")) == 16.0


def test_jst_gh_xh_ph_zh_and_microfit_follow_the_maker_drawings():
    # eGH/eXH: front view = lock face, wire end up; eGH has circuit 1 on the right, eXH's mating view (lock up) on the
    # right -> from the wire side, lock up, contact 1 is on the LEFT. ePH/eZH: front view = lance-window face, circuit 1
    # on the left -> from the wire side, windows down, contact 1 on the left. Body widths are the catalogue B (C for XH).
    width = lambda layout: (layout["cols"] - 1) * layout["pitch"] + layout["body"]["extra"]
    cases = {"GHR-04V-S": ("jst-gh", 6.25), "XHP-4": ("jst-xh", 12.3), "PHR-4": ("jst-ph", 9.8), "ZHR-4": ("jst-zh", 7.5)}
    for article, (family, catalogue_width) in cases.items():
        layout = housing_of(article)
        assert layout["family"] == family and layout["conf"] == "high" and layout["src"]
        assert layout["grid"] == [[1, 2, 3, 4]]
        assert round(width(layout), 2) == catalogue_width
    assert housing_of("GHR-15V-S") and round(width(housing_of("GHR-15V-S")), 2) == 20.0
    assert round(width(housing_of("XHP-20")), 2) == 52.3 and round(width(housing_of("PHR-16")), 2) == 33.8
    assert housing_of("PHR-4")["latch"] == housing_of("ZHR-4")["latch"] == "windows"
    assert housing_of("GHR-04V-S")["body"]["lock_h"] and housing_of("XHP-4")["body"]["lock_h"]

    # SDA-43025: mating view, latch up — 1 bottom-left, last top-right; the wire side mirrors it. A = 3.00·cols + 0.85.
    microfit = housing_of("43025-0600")
    assert microfit["conf"] == "high" and microfit["grid"] == [[6, 5, 4], [3, 2, 1]]
    assert round(width(microfit), 2) == 9.85 and round(width(housing_of("43025-2400")), 2) == 36.85


def test_series_without_a_checked_drawing_keep_the_warning():
    # 16.10.2026: PicoBlade 51047 and Micro-Fit 43645 / 43020 / 43640 were read from the Molex drawings (see
    # test_connector_numbering); SL 22-55 (molex.com closed, only part sheets) and D-Sub (the Connfly drawing has no
    # numbers) stay unchecked — D-Sub was then marked checked by the standard (DIN 41652, numbers moulded on the insert).
    for article in ("22-55-2081",):
        assert housing_of(article)["conf"] != "high", article
    # Searched for a maker drawing on 04.10 and found none that fixes the side: the note says what is missing.
    for article in ("XT60H-M", "QS8-S-M", "LP-12-C04PE", "GX12M-4"):
        assert housing_of(article)["conf"] != "high" and housing_of(article)["note"], article


def test_dsub_follows_the_standard_from_the_solder_side():
    plug, socket = housing_of("DB9M"), housing_of("DB9F")
    assert plug["conf"] == socket["conf"] == "high"
    assert plug["grid"][0] == [5, 0, 4, 0, 3, 0, 2, 0, 1] and plug["grid"][1] == [0, 9, 0, 8, 0, 7, 0, 6, 0]
    assert socket["grid"][0] == [1, 0, 2, 0, 3, 0, 4, 0, 5] and socket["grid"][1] == [0, 6, 0, 7, 0, 8, 0, 9, 0]


def test_jst_ze_nsh_pni_rcy_follow_the_jst_drawings():
    # eZE / eNSH / ePNI-WB: front view = lock face, wire end up, circuit 1 at the right -> wire side, lock up: 1 LEFT.
    cases = {"ZER-04V-S": ("jst-ze", 1.5, "lever"), "NSHR-04V-S": ("jst-nsh", 1.0, "ramp"), "PNIRP-04V-S": ("jst-pni", 2.0, "lever")}
    for article, (family, pitch, latch) in cases.items():
        layout = housing_of(article)
        assert (layout["family"], layout["pitch"], layout["latch"]) == (family, pitch, latch), article
        assert layout["grid"] == [[1, 2, 3, 4]] and layout["conf"] == "high" and layout["src"].startswith("JST e"), article

    width = lambda layout: (layout["cols"] - 1) * layout["pitch"] + layout["body"]["extra"]
    assert width(housing_of("NSHR-04V-S")) == 6.0 and width(housing_of("NSHR-15V-S")) == 17.0
    assert width(housing_of("NSHR-02V-S")) == 5.0  # the 3-circuit housing with the middle cavity plugged
    assert round(width(housing_of("PNIRP-04V-S")), 2) == 9.7 and round(width(housing_of("PNIRP-10V-S")), 2) == 21.7

    # eRCY: «1 2» moulded on the flat face; with it up, SYR has 1 on the right, SYP (its mate) on the left.
    receptacle, plug = housing_of("SYR-02T"), housing_of("SYP-02T-1")
    assert (receptacle["family"], receptacle["grid"], receptacle["latch"]) == ("jst-rcy", [[2, 1]], "mark")
    assert (plug["family"], plug["grid"], plug["latch"]) == ("jst-rcy-p", [[1, 2]], "mark")
    assert receptacle["conf"] == plug["conf"] == "high" and receptacle["pitch"] == plug["pitch"] == 2.5
    assert receptacle["color"] == "#c8322d" and housing_of("SYR-02TV")["color"] == "#efe6cf"


def test_rcy_split_keeps_the_warehouse_name():
    from app.workflows.warehouse_article import infer_article_identity

    assert infer_article_identity("SYR-02T").name == infer_article_identity("SYP-02T-1").name == "RCY 2pin (однорядный)"


def test_connfly_ds1069_and_hirose_follow_the_maker_drawings():
    # Connfly DS1069 «Circuit No.1 Mark» on the right end of the mating view = JST eXH: wire side, lock up, 1 LEFT.
    ds1069 = housing_of("I-DS1069-SCW004")
    assert ds1069["conf"] == "high" and ds1069["grid"] == housing_of("XHP-4")["grid"] == [[1, 2, 3, 4]]
    assert ds1069["body"]["extra"] == housing_of("XHP-4")["body"]["extra"] and housing_of("I-DS1069-SCB004")["color"] == "#2b2b2b"

    # DF13: ▽ and every-fifth marks on the front face, locks on the back -> wire side, locks up: 1 RIGHT.
    df13 = housing_of("DF13-4S-1.25C")
    assert (df13["grid"], df13["latch"], df13["pitch"], df13["conf"]) == ([[4, 3, 2, 1]], "ramp", 1.25, "high")
    # DF14: «CONTACT NO.1» ▽ and the lock on the same front face -> wire side, lock up: 1 LEFT.
    df14 = housing_of("DF14-4S-1.25C")
    assert (df14["grid"], df14["latch"], df14["pitch"], df14["conf"]) == ([[1, 2, 3, 4]], "ramp", 1.25, "high")
    # DF11: no latch; ▽ face up — odd contacts along the top row from the left, even ones under them.
    df11 = housing_of("DF11-8DS-2C")
    assert (df11["grid"], df11["latch"], df11["pitch"], df11["conf"]) == ([[1, 3, 5, 7], [2, 4, 6, 8]], "mark", 2.0, "high")
    assert (df11["cols"] - 1) * df11["pitch"] + df11["body"]["extra"] == 10.0  # catalogue A for 8 contacts
    for layout in (df13, df14, df11):
        assert layout["src"].startswith("Hirose EDC")


def test_series_whose_drawing_has_no_contact_one_say_so():
    # FI-X and Connfly DS1071 have no circuit-1 mark on any maker drawing reachable — the warning stays.
    for article in ("FI-X30HL", "I-DS1071-SCB02X5"):
        layout = housing_of(article)
        assert layout["conf"] == "low" and not layout["src"] and "чертеже" in layout["note"], article
    # CZHR: no mark of its own, but eCZ-F makes it interchangeable with the CZ IDC socket, whose ▽ is on the left of the
    # front face (lock on the back face) — wire side, lock up: 1 left, as drawn before.
    cz = housing_of("CZHR-04V-S")
    assert (cz["grid"], cz["latch"], cz["conf"]) == ([[1, 2, 3, 4]], "ramp", "high") and "eCZ-F" in cz["src"]


def test_eh_pa_vh_phd_picoblade_minifit_and_usb_follow_the_maker_drawings():
    # Wire side. eEH: windows/numbers face down, 1 left. ePA-F / eVH: lever up, 1 left. ePHD: ▽ face up, 1 top-left, 2
    # under it. 510210000-SD: windows down (lock bumps up), 1 right. SD-5557-003: latch up, 1 bottom-right, rows right to
    # left. USB 2.0 fig. 6-7/6-9: plastic part down, 1 (+5В) left.
    cases = {
        "EHR-4": ("jst-eh", [[1, 2, 3, 4]], "windows", 2.5),
        "PAP-04V-S": ("jst-pa", [[1, 2, 3, 4]], "lever", 2.0),
        "VHR-4N": ("jst-vh", [[1, 2, 3, 4]], "lever", 3.96),
        "PHDR-10VS": ("jst-phd", [[1, 3, 5, 7, 9], [2, 4, 6, 8, 10]], "mark", 2.0),
        "51021-0400": ("molex-picoblade", [[4, 3, 2, 1]], "windows", 1.25),
        "39-01-2080": ("molex-minifit-jr", [[8, 7, 6, 5], [4, 3, 2, 1]], "lever", 4.2),
        "USB-A Male 2.0": ("usb-a", [[1, 2, 3, 4]], "none", None),
    }
    for article, (family, grid, latch, pitch) in cases.items():
        layout = housing_of(article)
        assert (layout["family"], layout["grid"], layout["latch"], layout["pitch"]) == (family, grid, latch, pitch), article
        assert layout["conf"] == "high" and layout["src"] and layout["note"], article
    assert housing_of("VHR-4M")["family"] == "jst-vh" and housing_of("5557-08R")["grid"] == housing_of("39012080")["grid"]
    usb = housing_of("USB-A")
    assert usb["labels"] == ["+5В", "D−", "D+", "GND"] and "пластик снизу" in usb["title"]

    # Catalogue widths: EH B = A + 4.5, PHD B = A + 3.9, PicoBlade A = B + 3.0, Mini-Fit Jr. A = B + 5.4.
    width = lambda layout: round((layout["cols"] - 1) * layout["pitch"] + layout["body"]["extra"], 2)
    assert [width(housing_of(article)) for article in ("EHR-4", "EHR-15", "PHDR-10VS", "PHDR-34VS")] == [12.0, 39.5, 11.9, 35.9]
    assert [width(housing_of(article)) for article in ("51021-0400", "51021-1500", "39012080", "39012240")] == [6.75, 20.5, 18.0, 51.6]
    minifit = housing_of("39012080")["body"]
    assert (minifit["h"], minifit["lock_w"], minifit["lock_h"]) == (11.6, 5.4, 2.8)
    assert housing_of("PHDR-10VS")["body"]["h"] == 5.0 and housing_of("EHR-4")["body"]["h"] == 3.8


def test_picoblade_plug_51047_is_split_from_the_checked_receptacle():
    plug, receptacle = housing_of("Molex 510470300"), housing_of("Molex 510210300")
    assert plug["family"] == "molex-picoblade-plug" and receptacle["family"] == "molex-picoblade"
    assert plug["title"] == receptacle["title"] == "Molex PicoBlade" and plug["n"] == 3
    assert plug["conf"] == receptacle["conf"] == "high"
    # 510470000-SD: CIRCUIT 1 INDICATOR MARK on the wire end at contact 1 — mark face up, 1 on the LEFT (was TR, no cue).
    assert (plug["grid"], plug["latch"], plug["src"]) == ([[1, 2, 3]], "mark", "Molex 510470000-SD")

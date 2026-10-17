import re

from app.modules.techcards.connector_catalog_data import CATALOG_FAMILIES, CATALOG_SERIES
from app.modules.techcards.connector_housings import CATALOG_SAMPLES, FAMILIES, housing_of
from app.modules.techcards.connector_series import (
    CATEGORIES, JST_SERIES, MAKERS, connector_series, find_in_series, terminals_for, wire_range,
)

from .test_project_cards import project_techcards_api  # noqa: F401 — the shared API fixture

TERMINALS = [
    {"terManufacturer": "JST", "terSeries": "GHDR, GHR", "terComponent": "GHD, GH", "terType": "Female",
     "terArticle": "SSHL-002T-P0.2", "awgFrom": "30", "awgTo": "26", "mm2From": "0,05", "mm2To": "0,12", "terStrip": "1,7"},
    {"terManufacturer": "JST", "terSeries": "GHDR, GHR, NSHR, SHLP", "terComponent": "GHD, GH, NSH, SHL", "terType": "Female",
     "terArticle": "SSHL-003T-P0.2", "awgFrom": "32", "awgTo": "28", "mm2From": "0,03", "mm2To": "0,08"},
    {"terManufacturer": "JST", "terSeries": "SHDP, SHR, APSH, LBT", "terComponent": "SHD, SH", "terType": "Female",
     "terArticle": "SSH-003T-P0.2", "awgFrom": "32", "awgTo": "28"},
    {"terManufacturer": "JST", "terSeries": "GHDR, GHR", "terComponent": "GHD, GH", "terArticle": "SSHL-002T-P0.2"},
    {"terManufacturer": "TE", "terSeries": "Superseal 1.5", "terComponent": "AMP SUPERSEAL 1.5", "terType": "Male",
     "terArticle": "282110-1", "awgFrom": "20", "awgTo": "16"},
]


def _series(records=()):
    return {item["id"]: item for item in connector_series(records)}


def _articles(series, variant=0):
    return [housing["article"] for housing in series["variants"][variant]["housings"]]


def test_jst_series_list_every_housing_of_the_catalogue_table():
    series = _series()
    gh, sh, xh, vh = series["jst-gh"], series["jst-sh"], series["jst-xh"], series["jst-vh"]

    assert _articles(gh) == [f"GHR-{n:02d}V-S" for n in range(2, 16)]
    assert gh["counts"] == list(range(2, 16)) and gh["range_checked"] and gh["checked"]
    assert gh["datasheet"] == "https://www.jst-mfg.com/product/pdf/eng/eGH.pdf"
    # SH: with protrusions 2–16 and 20, without 2–15 (eSH «Housing»).
    assert _articles(sh, 0)[-2:] == ["SHR-16V-S-B", "SHR-20V-S-B"] and _articles(sh, 1)[-1] == "SHR-15V-S"
    assert sh["variants"][0]["housings"][0]["housing"]["body"]["ears"] == 1.0
    assert "ears" not in sh["variants"][1]["housings"][0]["housing"]["body"]
    # XH: a single-circuit housing, 2–16 and 20 — no 17–19 invented.
    assert _articles(xh) == [f"XHP-{n}" for n in (*range(1, 17), 20)]
    # VH M type has no 8, 10, 11 circuits.
    assert [h["n"] for h in vh["variants"][1]["housings"]] == [2, 3, 4, 5, 6, 7, 9]


def test_every_housing_is_drawn_by_its_family_with_the_right_count():
    for series in connector_series():
        for variant in series["variants"]:
            for housing in variant["housings"]:
                layout = housing["housing"]
                assert layout["family"] == variant["family"], housing["article"]
                assert layout["n"] == housing["n"] and layout["art"] == housing["article"]


def test_board_headers_are_attached_by_contact_count():
    gh = _series()["jst-gh"]
    four = next(h for h in gh["variants"][0]["housings"] if h["n"] == 4)
    assert four["headers"] == [
        {"title": "Вилка на плату SMD, вертикальная", "article": "BM04B-GHS-TBT"},
        {"title": "Вилка на плату SMD, угловая", "article": "SM04B-GHS-TB"},
    ]
    xh_one = _series()["jst-xh"]["variants"][0]["housings"][0]
    assert xh_one["article"] == "XHP-1" and xh_one["headers"] == []


def test_series_without_a_catalogue_table_keep_only_known_articles():
    series = _series()
    catalogued = {spec["id"] for spec in CATALOG_SERIES} | {spec["id"] for spec in JST_SERIES}
    for item in series.values():
        if item["id"] in catalogued:
            continue
        assert not item["range_checked"], item["id"]
    assert [h["n"] for h in series["te-superseal"]["variants"][0]["housings"]] == [2, 2, 3, 3, 4, 4, 5, 5, 6, 6]
    assert series["te-superseal"]["datasheet"] == "" and series["te-superseal"]["sources"] == []


def test_no_family_with_a_sample_is_lost():
    shown = {variant["family"] for series in connector_series() for variant in series["variants"]}
    assert {family["id"] for family in FAMILIES if family["id"] in CATALOG_SAMPLES} <= shown


def test_terminals_match_the_housing_prefix_not_a_substring():
    gh = terminals_for(("GHR",), TERMINALS)
    sh = terminals_for(("SHR",), TERMINALS)
    superseal = terminals_for(("Superseal 1.5",), TERMINALS)

    assert [t["article"] for t in gh] == ["SSHL-002T-P0.2", "SSHL-003T-P0.2"]  # the duplicate row is dropped
    assert gh[0]["kind"] == "гнездо" and gh[0]["product"] == "GHD, GH"
    assert [t["article"] for t in sh] == ["SSH-003T-P0.2"]  # NSHR and SHLP are not SHR
    assert superseal[0]["kind"] == "штырь"
    assert wire_range(gh) == {"awg_thin": 32.0, "awg_thick": 26.0, "mm2_min": 0.03, "mm2_max": 0.12}
    assert wire_range([]) is None


def test_terminal_carries_its_record_id_and_stored_cells_for_in_place_editing():
    rows = [{**TERMINALS[0], "__id": "rec-1", "terCrimpHeight": "26 AWG: 0,62"}]
    terminal = terminals_for(("GHR",), rows)[0]

    assert terminal["id"] == "rec-1"
    assert terminal["raw"]["terCrimpHeight"] == "26 AWG: 0,62"
    assert "terArticle" not in terminal["raw"] and "__id" not in terminal["raw"]
    assert terminals_for(("GHR",), TERMINALS)[0]["id"] == ""  # a row without an id is not editable


def test_terminals_reach_the_series_view():
    series = _series(TERMINALS)
    assert [t["article"] for t in series["jst-nsh"]["terminals"]] == ["SSHL-003T-P0.2"]
    assert series["jst-gh"]["wire"]["awg_thick"] == 26.0
    assert series["jst-xh"]["terminals"] == [] and series["jst-xh"]["wire"] is None


def test_find_in_series_by_article():
    series = connector_series()
    assert find_in_series("JST ghr-15v-s", series) == {
        "series": "jst-gh", "article": "GHR-15V-S", "housing": find_in_series("GHR-15V-S", series)["housing"]}
    beyond = find_in_series("XHP-18", series)
    assert beyond["series"] == "jst-xh" and beyond["article"] is None and beyond["housing"]["n"] == 18
    assert find_in_series("Molex 39012080", series)["series"] == "molex-minifit-jr"
    assert find_in_series("XYZ-1", series) is None


def test_connector_series_api(project_techcards_api):
    body = project_techcards_api.get("/api/techcards/connector-series", params={"article": "PHR-4"}).json()
    families = project_techcards_api.get("/api/techcards/connector-families").json()

    assert body["match"]["series"] == "jst-ph" and body["match"]["article"] == "PHR-4"
    assert len(body["series"]) == families["series_count"]
    assert project_techcards_api.get("/api/techcards/connector-series").json()["match"] is None


def test_every_series_has_one_known_category_and_the_list_runs_by_category():
    series = connector_series()
    assert all(item["category"] in CATEGORIES for item in series)
    order = [CATEGORIES.index(item["category"]) for item in series]
    assert order == sorted(order)
    by_id = {item["id"]: item["category"] for item in series}
    assert by_id["jst-gh"] == by_id["molex-picoblade"] == "Провод — плата"
    assert by_id["jst-sm"] == by_id["jst-rcy"] == "Провод — провод"
    assert by_id["deutsch-dt"] == by_id["te-superseal"] == by_id["te-multilock-040"] == "Автомобильные"
    assert by_id["weipu-sa8"] == by_id["cnlinko-lp12"] == by_id["aviation-gx"] == "Цилиндрические"
    assert by_id["amass-xt"] == "Силовые" and by_id["rj45"] == by_id["usb-a"] == by_id["dsub"] == "Интерфейсные"
    assert by_id["dc-barrel"] == by_id["banana"] == "Питание и штекеры"
    assert by_id["lug"] == by_id["flat-quick"] == "Наконечники и клеммы" and by_id["rf-sma"] == "ВЧ"


def test_series_ids_are_unique_and_the_warehouse_connector_has_its_series():
    ids = [item["id"] for item in connector_series()]
    assert len(ids) == len(set(ids))
    assert "connfly-ds1066" in ids  # the warehouse lots I-DS1066-SCW02 / 03 (type «Соединитель»)


def test_series_known_only_from_the_reference_data_have_no_invented_housings():
    series = _series(TERMINALS + [
        {"terManufacturer": "TE", "terSeries": "MQS", "terComponent": "MQS", "terType": "Female", "terArticle": "929939-3"},
    ])
    mqs = series["te-mqs"]
    assert mqs["variants"] == [] and mqs["housing_count"] == 0 and mqs["counts"] == [] and not mqs["checked"]
    assert [t["article"] for t in mqs["terminals"]] == ["929939-3"]
    assert mqs["sample"] is not None and mqs["sample"]["article"] == ""  # a picture of the closest shape, not a model number
    assert mqs["pitch"] is None and mqs["category"] == "Автомобильные" and mqs["sources"] == []


def test_ds1066_catalogue_range_and_the_warehouse_article_opens_it():
    series = _series()["connfly-ds1066"]
    sample = series["variants"][0]["housings"][0]
    # Connfly C-DS1066: «I-DS1066-SC X 0 X», 02~15 holes — SCW002 … SCW015; the warehouse writes SCW02.
    assert sample["article"] == "I-DS1066-SCW002" and sample["n"] == 2 and sample["housing"]["pitch"] == 2.0
    assert _articles(series)[-1] == "I-DS1066-SCW015" and series["range_checked"]
    assert find_in_series("I-DS1066-SCW03@CONNFLY", connector_series())["series"] == "connfly-ds1066"


def test_coax_articles_fill_the_rf_series_by_type():
    coax = [
        {"coaxArticle": "SMA-male RG-316", "coaxType": "SMA-male", "coaxMfr": "LenoRF", "coaxWire": "RG-316"},
        {"coaxArticle": "RP-SMA-female RG-316", "coaxType": "RP-SMA-female", "coaxMfr": "", "coaxWire": "RG-316"},
        {"coaxArticle": "U.FL", "coaxType": "U.FL-female R/A", "coaxMfr": "", "coaxWire": ""},
        {"coaxArticle": "Fakra Z-Female", "coaxType": "Fakra-female", "coaxMfr": "", "coaxWire": ""},
        {"coaxArticle": "QMA-KPD7", "coaxType": "QMA", "coaxMfr": "", "coaxWire": ""},
    ]
    series = {item["id"]: item for item in connector_series((), coax)}
    assert [c["article"] for c in series["rf-sma"]["catalog"]] == ["SMA-male RG-316"]
    assert [c["article"] for c in series["rf-rpsma"]["catalog"]] == ["RP-SMA-female RG-316"]
    assert [c["article"] for c in series["rf-mhf"]["catalog"]] == ["U.FL"]
    assert [c["article"] for c in series["rf-qma"]["catalog"]] == ["QMA-KPD7"]
    assert series["rf-fakra"]["category"] == "Автомобильные" and len(series["rf-fakra"]["catalog"]) == 1
    assert series["rf-bnc"]["catalog"] == [] and series["rf-bnc"]["housing_count"] == 0


def test_terminal_payload_carries_crimp_height_and_pull_force():
    plain = {"terSeries": "GHR", "terArticle": "P-1", "terCrimpHeight": "0,85 - 0,90", "terPullForceMin": "50", "terPullForceMax": "70"}
    profiled = {"terSeries": "GHR", "terArticle": "P-2", "terCrimpHeight": "26 AWG: 0,62 (15 Н)\n28 AWG: 0,70 (12 Н)"}
    split = {"terSeries": "GHR", "terArticle": "P-3", "terCrimpHeight": "24 AWG: 0.76 - 0.81 22 AWG: 0,84",
             "terPullForceMin": "24 AWG: 29,3\n22 AWG: 44,1", "terPullForceMax": "24 AWG: 35"}
    empty = {"terSeries": "GHR", "terArticle": "P-4"}
    a, b, c, d = terminals_for(("GHR",), [plain, profiled, split, empty])

    assert (a["crimp_height"], a["pull_force"]) == ("0,85–0,90", "50–70")
    assert (b["crimp_height"], b["pull_force"]) == ("26 AWG: 0,62; 28 AWG: 0,70", "26 AWG: 15; 28 AWG: 12")
    assert (c["crimp_height"], c["pull_force"]) == ("24 AWG: 0,76–0,81; 22 AWG: 0,84", "24 AWG: 29,3–35; 22 AWG: 44,1")
    assert (d["crimp_height"], d["pull_force"]) == ("", "")
    assert [t["ins_crimp_height"] for t in (a, b, c, d)] == ["", "", "", ""]


def test_terminal_payload_carries_insulation_crimp_height_per_awg():
    plain = {"terSeries": "GHR", "terArticle": "P-1", "terCrimpHeight": "0,85", "terInsCrimpHeight": "1,50 - 1,65"}
    profiled = {"terSeries": "GHR", "terArticle": "P-2", "terCrimpHeight": "28 AWG: 0,52 - 0,58\n26 AWG: 0,56 - 0,62",
                "terInsCrimpHeight": "28 AWG: 1,30 - 1,45\n26 AWG: 1,35 - 1,50"}
    a, b = terminals_for(("GHR",), [plain, profiled])

    assert a["ins_crimp_height"] == "1,50–1,65"
    assert b["ins_crimp_height"] == "28 AWG: 1,30–1,45; 26 AWG: 1,35–1,50"
    assert b["crimp_height"] == "28 AWG: 0,52–0,58; 26 AWG: 0,56–0,62"


# --- housing catalogues from the makers' documents (connector_catalog_data, 12.10.2026) -------------------------------

def test_every_catalogue_series_lists_each_housing_once_drawn_by_its_family_with_a_source():
    series = _series()
    for spec in CATALOG_SERIES:
        item = series[spec["id"]]
        catalogue = item["variants"][:len(spec["variants"])]  # after them: housings the ERP knows without a drawing
        articles = [h["article"] for variant in catalogue for h in variant["housings"]]
        listed = sum(len(variant[2]) if len(variant) == 3 else len(variant[3]) for variant in spec["variants"])
        assert len(articles) == listed, spec["id"]  # no part number fell out of its family
        assert len(articles) == len(set(articles)), spec["id"]
        # «диапазон сверен» only when the whole series comes from the documents
        assert item["range_checked"] == (spec["range_checked"] and len(item["variants"]) == len(spec["variants"])), spec["id"]
        assert item["sources"] and all(s["url"].startswith("https://") for s in item["sources"])
        assert item["datasheet"] == item["sources"][0]["url"]
        for housing in (h for variant in item["variants"] for h in variant["housings"]):
            headers = [header["article"] for header in housing["headers"]]
            assert len(headers) == len(set(headers)), housing["article"]


def test_catalogue_families_have_unique_ids_samples_and_no_python_only_regex():
    ids = [family["id"] for family in FAMILIES]
    assert len(ids) == len(set(ids))
    for family in CATALOG_FAMILIES:
        assert family["erp_only"] and family["id"] in CATALOG_SAMPLES, family["id"]
        assert housing_of(CATALOG_SAMPLES[family["id"]])["family"] == family["id"]
        assert "(?P" not in family["pattern"]  # the harness editor runs these patterns as JS RegExp
        assert family["conf"] == "low" or family.get("src"), family["id"]  # a checked numbering names its drawing


def test_df1b_catalogue_the_owner_uses_in_the_generator():
    df1b = _series(TERMINALS + [
        {"terManufacturer": "Hirose", "terSeries": "DF1B", "terComponent": "DF1B", "terType": "Female",
         "terArticle": "DF1B-2428SCFA", "terArticleBag": "DF1B-2428SCA", "awgFrom": "28", "awgTo": "24"},
    ])["hirose-df1b"]
    by_title = {variant["title"]: variant for variant in df1b["variants"]}
    double = [h["article"] for h in by_title["Гнездовой корпус, 2 ряда"]["housings"]]
    assert double[:2] == ["DF1B-4DS-2.5RC", "DF1B-6DS-2.5RC"] and "DF1B-18DS-2.5RC" in double
    assert [h["n"] for h in by_title["Гнездовой корпус, 2 ряда"]["housings"]][-4:] == [30, 34, 36, 40]  # no 32, 38
    single = by_title["Гнездовой корпус, 1 ряд"]["housings"]
    assert [h["n"] for h in single] == [*range(2, 17), 18, 20]
    plug = [h["article"] for h in by_title["Штыревой корпус (полный замок), 1 ряд"]["housings"]]
    assert "DF1B-5EP-2.5RC" in plug and plug[-1] == "DF1B-13EP-2.5RC"
    eighteen = next(h for h in by_title["Гнездовой корпус, 2 ряда"]["housings"] if h["n"] == 18)
    assert eighteen["housing"]["rows"] == 2 and eighteen["housing"]["cols"] == 9 and eighteen["housing"]["pitch"] == 2.5
    assert {h["article"] for h in eighteen["headers"]} == {"DF1BZ-18DP-2.5DSA", "DF1BZ-18DP-2.5DS"}
    contacts = {c["article"]: c["in_base"] for c in df1b["contacts"]}
    assert contacts["DF1B-2428SCFA"] and not contacts["DF1-PD2428SCFB"]  # БД.ТЕР has the socket, not the pin
    assert [t["article"] for t in df1b["terminals"]] == ["DF1B-2428SCFA"]
    assert find_in_series("Hirose DF1B-18DS-2.5RC", connector_series())["article"] == "DF1B-18DS-2.5RC"


def test_count_per_row_rows_by_count_and_te_tables():
    isd2 = housing_of("ISD2-05-D-M")
    assert (isd2["n"], isd2["rows"], isd2["cols"]) == (10, 2, 5)  # Samtec: positions per row × 2 rows
    el = {article: housing_of(article) for article in ("ELR-03V", "ELR-06V", "ELR-12V", "ELR-04NV")}
    assert [(h["n"], h["rows"], h["cols"]) for h in el.values()] == [(3, 1, 3), (6, 2, 3), (12, 3, 4), (4, 2, 2)]
    assert housing_of("1-353908-0")["n"] == 10 and housing_of("8-353908-0")["n"] == 20 and housing_of("353908-1") is None
    assert housing_of("2-770849-4")["n"] == 24 and housing_of("5-770849-2")["n"] == 2
    assert housing_of("I-DS1071-SCB01X40")["n"] == 40 and housing_of("I-DS1071-SCB02X3")["rows"] == 2


def test_gallery_runs_by_maker_then_pitch_inside_a_section():
    series = connector_series()
    for category in CATEGORIES:
        items = [item for item in series if item["category"] == category]
        makers = [item["maker"] for item in items]
        ranks = [MAKERS.index(m) if m in MAKERS else len(MAKERS) + (1 if not m else 0) for m in makers]
        assert ranks == sorted(ranks), category
        for maker in set(makers):
            pitches = [item["pitch"] if item["pitch"] is not None else 1e9 for item in items if item["maker"] == maker]
            assert pitches == sorted(pitches), (category, maker)
    wtb = [item["id"] for item in series if item["category"] == "Провод — плата"]
    assert wtb.index("jst-sh") < wtb.index("jst-gh") < wtb.index("jst-vh")  # 1,0 < 1,25 < 3,96 mm


def test_a_checked_catalogue_series_says_where_contact_one_is():
    series = _series()
    for series_id in ("jst-suh", "jst-shj", "jst-ra", "te-minict", "te-sl156", "hirose-df14", "hirose-df11", "hirose-df19",
                      "jst-pud", "jst-pad", "jst-zpd", "jst-nshd", "jst-cz", "hirose-df13", "hirose-df20"):
        assert series[series_id]["checked"], series_id
    for series_id in ("jst-el", "jst-pnd", "jst-gvh", "hirose-df1b", "hirose-df61", "connfly-ds1072", "samtec-isd2"):
        assert not series[series_id]["checked"], series_id  # no circuit-1 mark placed on the wire side: warning stays
    assert re.search(r"контакт 1 справа", housing_of("RA-2011")["note"])


def test_molex_sales_drawing_tables():
    series = _series()
    nano = {variant["title"]: [h["n"] for h in variant["housings"]] for variant in series["molex-nanofit"]["variants"]}
    assert nano["Розетка, 1 ряд, чёрная"] == list(range(2, 9)) and nano["Розетка, 2 ряда, чёрная"] == list(range(4, 17, 2))
    assert _articles(series["molex-nanofit"], 2)[0] == "105308-1204" and series["molex-nanofit"]["range_checked"]
    minifit = series["molex-minifit-jr"]
    assert _articles(minifit, 0)[:2] == ["39-01-2020", "39-01-2040"] and _articles(minifit, 2)[-1] == "39-03-9242"
    assert minifit["checked"] and minifit["range_checked"]  # SD-5557-003 covers the black ones too
    assert find_in_series("Molex 39012080", connector_series())["article"] == "39-01-2080"  # separators ignored
    assert [h["n"] for h in series["molex-microfit"]["variants"][0]["housings"]] == list(range(2, 25, 2))
    # A family whose drawing was not found keeps the ERP's own housing, and the series says «диапазон не сверен».
    clik = series["molex-clikmate"]
    assert [variant["family"] for variant in clik["variants"]] == ["molex-clikmate-20", "molex-clikmate"]
    assert not clik["range_checked"] and _articles(clik, 1) == [f"502578-{n:02d}00" for n in range(2, 16)]
    assert [h["n"] for h in series["molex-clikmate"]["variants"][0]["housings"]] == [*range(2, 11), 12, 13, 14, 15]


def _word(text, word):
    return re.search(rf"(?<!\w){word}(?!\w)", text.lower()) is not None


def test_every_housing_with_known_contacts_is_named_by_them_вилка_for_pins_розетка_for_sockets():
    # Owner 17.10.2026 («давай по-русски»): вилка = pin contacts, розетка = socket contacts, whatever the maker's
    # «plug» / «receptacle» says (JST: the receptacle SMR takes pins — it is the вилка here).
    right = {"pin": "вилка", "socket": "розетка"}
    wrong = {"pin": "розетка", "socket": "вилка"}
    seen = set()
    for series in connector_series(()):
        for variant in series["variants"]:
            for housing in variant["housings"]:
                mate = housing["housing"].get("mate")
                if not mate:
                    continue
                for text in (variant["title"], housing["housing"]["title"]):
                    assert not _word(text, wrong[mate["contacts"]]), (housing["article"], text)
                    if _word(text, right[mate["contacts"]]):
                        seen.add(right[mate["contacts"]])
    assert seen == {"вилка", "розетка"}
    for family in FAMILIES:
        layout = housing_of(CATALOG_SAMPLES.get(family["id"], ""))
        mate = layout and layout.get("mate")
        if mate:
            assert not _word(family["title"], wrong[mate["contacts"]]), family["id"]


def test_jst_sm_el_rcy_sections_follow_the_contacts():
    series = _series()
    assert [v["title"] for v in series["jst-sm"]["variants"]] == [
        "Вилка SMR, чёрная", "Вилка SMR, белая", "Розетка SMP, чёрная", "Розетка SMP, белая"]
    assert [v["title"] for v in series["jst-rcy"]["variants"]][::2] == ["Вилка SYR, штыри", "Розетка SYP, гнёзда"]
    el = [v["title"] for v in series["jst-el"]["variants"]]
    assert el[0].startswith("Вилка ELR") and el[3].startswith("Розетка ELP")
    assert housing_of("SMR-04V-B")["title"] == "JST SM (вилка)" and housing_of("SMP-04V-BC")["title"] == "JST SM (розетка)"
    assert housing_of("43020-0400")["title"].endswith("(вилка)")

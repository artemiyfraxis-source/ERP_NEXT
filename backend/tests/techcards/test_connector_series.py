from app.modules.techcards.connector_housings import CATALOG_SAMPLES, FAMILIES
from app.modules.techcards.connector_series import connector_series, find_in_series, terminals_for, wire_range

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
    for item in series.values():
        if item["maker"] == "JST":
            continue
        assert not item["range_checked"], item["id"]
    assert _articles(series["molex-minifit-jr"]) == ["39012080"]
    assert [h["n"] for h in series["te-superseal"]["variants"][0]["housings"]] == [2, 2, 3, 3, 4, 4, 5, 5, 6, 6]
    assert series["connfly-ds1071"]["datasheet"].endswith("DS1071.pdf")
    assert series["molex-minifit-jr"]["datasheet"] == ""


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

"""Contact numbering of the Molex and Hirose DF1B / DF19 housings against the makers' drawings (13.10.2026).

Each case pins, for one housing of a family, the cavity grid as seen from the wire side (top row first, the latch or the
named face cue at the top), where contact 1 is and the latch kind; `conf` "high" only with the drawing named in `src`.
A family whose drawing shows no contact 1 on the wire side keeps «не сверена» / «сверить по детали» and `conf` "low".
The drawings were read on paper only — no real part was checked here.
"""

import pytest

from app.modules.techcards.connector_housings import housing_of
from app.modules.techcards.connector_series import connector_series, find_in_series

ROW8 = [[1, 2, 3, 4, 5, 6, 7, 8]]

# (article, family, grid from the wire side, latch, conf, a word of the source)
CASES = [
    # Molex — Fit families: latch up
    ("39-03-9082", "molex-minifit-jr-bk", [[8, 7, 6, 5], [4, 3, 2, 1]], "lever", "high", "SD-5557-003"),
    ("105307-1204", "molex-nanofit-1r", [[4, 3, 2, 1]], "lever", "high", "SD-105307-100"),
    ("105308-1208", "molex-nanofit", [[4, 3, 2, 1], [8, 7, 6, 5]], "lever", "high", "SD-105308-100"),
    ("200277-1104", "molex-nanofit-plug-1r", [[1, 2, 3, 4]], "lever", "high", "2002771000-SD"),
    ("201444-1108", "molex-nanofit-plug", [[1, 2, 3, 4], [5, 6, 7, 8]], "lever", "high", "2014441000-SD"),
    ("172708-0008", "molex-minifit-tpa2", [[8, 7, 6, 5], [4, 3, 2, 1]], "lever", "high", "SD-172708-0000"),
    ("200453-1004", "molex-minifit-tpa2-1r", [[4, 3, 2, 1]], "lever", "high", "2004531000"),
    ("215913-1004", "molex-minifit-max-1r", [[4, 3, 2, 1]], "lever", "high", "2159130000-SD"),
    ("172256-1104", "molex-ultrafit-1r", [[1, 2, 3, 4]], "lever", "high", "SD-172256-0001"),
    ("172258-3108", "molex-ultrafit", [[1, 2, 3, 4], [5, 6, 7, 8]], "lever", "high", "1722580003"),
    # Molex — small-pitch families
    ("2053410204", "molex-picolock-20", [[1, 2, 3, 4]], "mark", "high", "2053410000-SD"),
    ("5040510401", "molex-picolock-15", [[1, 2, 3, 4]], "mark", "high", "5040510000-SD"),
    ("5037640401", "molex-picolock-10", [[1, 2, 3, 4]], "mark", "high", "SD-503764-001"),
    ("5055700600", "molex-microlock-20", [[1, 2, 3, 4, 5, 6]], "ramp", "high", "5055700001"),
    ("5055650600", "molex-microlock-125", [[1, 2, 3, 4, 5, 6]], "ramp", "high", "5055650002-SD"),
    ("2145252060", "molex-microlock-125-p", [[6, 5, 4, 3, 2, 1]], "ramp", "high", "2145250000-SD"),
    ("2145260600", "molex-microlock-125-s", [[1, 2, 3, 4, 5, 6]], "ramp", "high", "2145260000-SD"),
    ("510650600", "molex-microlatch", [[1, 2, 3, 4, 5, 6]], "ramp", "high", "510650000-SD"),
    ("502351-0600", "molex-microlatch-502351", [[1, 2, 3, 4, 5, 6]], "ramp", "high", "SD-502351-001"),
    ("50-37-5043", "molex-minispox", [[1, 2, 3, 4]], "windows", "high", "5264-N"),
    ("87439-0400", "molex-picospox", [[1, 2, 3, 4]], "ramp", "high", "874390000-SD"),
    ("2127242040", "molex-microone", [[1, 2, 3, 4]], "lever", "high", "2127240000-SD"),
    ("2140922040", "molex-oneblade", [[1, 2, 3, 4]], "ramp", "high", "2140920000-SD"),
    ("2147192080", "molex-zerohachi", ROW8, "mark", "high", "2147190000-SD"),
    ("22-01-2041", "molex-kk254", [[4, 3, 2, 1]], "ramp", "high", "26950000-SD"),
    ("90123-0106", "molex-cgrid3-1r", [[6, 5, 4, 3, 2, 1]], "none", "high", "SD-90123"),
    ("1511920010", "molex-milligrid-151192", [[9, 7, 5, 3, 1], [10, 8, 6, 4, 2]], "mark", "high", "1511921001"),
    ("502439-0400", "molex-clikmate-20", [[1, 2, 3, 4]], "ramp", "high", "5024391500"),
    # Molex — drawing without contact numbers, or not reachable: unchecked
    ("206461-0800", "molex-microfit-plus", [[1, 2, 3, 4], [5, 6, 7, 8]], "lever", "low", ""),
    ("09-50-8051", "molex-kk396", [[1, 2, 3, 4, 5]], "ramp", "low", ""),
    ("51163-0400", "molex-minilock", [[1, 2, 3, 4]], "lever", "low", ""),
    ("50-57-9404", "molex-sl-1r", [[1, 2, 3, 4]], "ramp", "low", ""),
    ("501646-1000", "molex-igrid", [[1, 3, 5, 7, 9], [2, 4, 6, 8, 10]], "lever", "low", ""),
    ("35023-0004", "molex-boardin", [[1, 2, 3, 4]], "none", "low", ""),
    # Hirose DF1B (catalogue 01.07.2020): face with the contact-1 mark (or the lock lever) up
    ("DF1B-4S-2.5R", "hirose-df1b", [[1, 2, 3, 4]], "mark", "high", "Single Row Socket"),
    ("DF1B-10DS-2.5RC", "hirose-df1b-2r", [[1, 3, 5, 7, 9], [2, 4, 6, 8, 10]], "mark", "high", "Double Row Socket"),
    ("DF1B-4ES-2.5RC", "hirose-df1b-es", [[1, 2, 3, 4]], "mark", "high", "Single Row In-line Socket"),
    ("DF1B-10DES-2.5RC", "hirose-df1b-des", [[1, 3, 5, 7, 9], [2, 4, 6, 8, 10]], "mark", "high", "Double Row In-line Socket"),
    ("DF1B-5EP-2.5RC", "hirose-df1b-p", [[1, 2, 3, 4, 5]], "lever", "high", "Single Row In-line Plug (Full Lock"),
    ("DF1B-10DEP-2.5RC", "hirose-df1b-p2r", [[1, 3, 5, 7, 9], [2, 4, 6, 8, 10]], "lever", "low", ""),
    ("DF1BA-5EP-2.5RC", "hirose-df1ba-p", [[5, 4, 3, 2, 1]], "mark", "high", "Simple Lock"),
    ("DF1BA-10DEP-2.5RC", "hirose-df1ba-p2r", [[9, 7, 5, 3, 1], [10, 8, 6, 4, 2]], "mark", "high", "Simple Lock"),
    # Hirose DF19 (catalogue 01.01.2024): ▽ face up
    ("DF19-8S-1C", "hirose-df19", ROW8, "mark", "high", "Single Row Socket (Cable Type)"),
    ("DF19G-8S-1C(06)", "hirose-df19", ROW8, "mark", "high", "Single Row Socket (Cable Type)"),
]


@pytest.mark.parametrize(("article", "family", "grid", "latch", "conf", "source"), CASES, ids=[case[0] for case in CASES])
def test_wire_side_numbering_follows_the_maker_drawing(article, family, grid, latch, conf, source):
    layout = housing_of(article)

    assert layout["family"] == family
    assert layout["grid"] == grid
    assert (layout["rows"], layout["cols"], layout["n"]) == (len(grid), len(grid[0]), sum(len(row) for row in grid))
    assert layout["latch"] == latch and layout["conf"] == conf
    if conf == "high":
        assert source in layout["src"]  # a checked numbering names the drawing it was read from
        assert "сверить" not in layout["note"] and "не сверен" not in layout["note"]
    else:
        assert layout["src"] == "" and ("сверить по детали" in layout["note"] or "не сверен" in layout["note"])


def _mirror(grid):
    return [list(reversed(row)) for row in grid]


@pytest.mark.parametrize(("receptacle", "plug"), [
    ("105307-1204", "200277-1104"),  # Nano-Fit receptacle ↔ wire-to-wire plug
    ("105308-1208", "201444-1108"),
    ("2145260600", "2145252060"),  # Micro-Lock Plus 1.25 wire-to-wire pair
])
def test_mated_pairs_are_mirror_images_from_their_wire_sides(receptacle, plug):
    # Face to face with the lock on the same side, the two wire sides read the cavities in opposite directions.
    assert housing_of(receptacle)["grid"] == _mirror(housing_of(plug)["grid"])


def test_df1b_in_line_socket_and_plug_mirror_with_the_lock_on_one_side():
    # The ES socket is drawn with its numbered front face up; its lock hook is on the back face. Turned hook up (a flip,
    # left and right swap) it meets the plug's lever face, and the pair reads in opposite directions.
    socket_hook_up = _mirror(housing_of("DF1B-5ES-2.5RC")["grid"])
    assert socket_hook_up == _mirror(housing_of("DF1B-5EP-2.5RC")["grid"]) == [[5, 4, 3, 2, 1]]


def test_df1ba_simple_lock_plugs_are_split_from_the_full_lock_plugs():
    df1b = {item["id"]: item for item in connector_series()}["hirose-df1b"]
    families = {variant["title"]: variant["family"] for variant in df1b["variants"]}
    assert families["Штыревой корпус (простой замок), 1 ряд"] == "hirose-df1ba-p"
    assert families["Штыревой корпус (простой замок), 2 ряда"] == "hirose-df1ba-p2r"
    assert families["Штыревой корпус (полный замок), 1 ряд"] == "hirose-df1b-p"
    assert not df1b["checked"]  # DEP (full lock) has no numbers on its wire end — the series keeps the warning
    assert housing_of("DF1B-5EP-2.5RC")["grid"] != housing_of("DF1BA-5EP-2.5RC")["grid"]


def test_checked_molex_series():
    series = {item["id"]: item for item in connector_series()}
    for series_id in ("molex-nanofit", "molex-minifit-tpa2", "molex-minifit-max", "molex-ultrafit", "molex-picolock",
                      "molex-microlock-plus", "molex-microlatch", "molex-minispox", "molex-picospox", "molex-microone",
                      "molex-oneblade", "molex-zerohachi", "molex-kk254", "molex-cgrid3", "hirose-df19"):
        assert series[series_id]["checked"], series_id
    for series_id in ("molex-microfit-plus", "molex-milligrid", "molex-clikmate", "molex-kk396", "molex-minilock",
                      "molex-sl", "molex-igrid", "molex-boardin"):
        assert not series[series_id]["checked"], series_id


def test_missing_molex_housings_are_in_the_catalogue():
    series = {item["id"]: item for item in connector_series()}

    kk396 = series["molex-kk396"]
    assert [h["article"] for v in kk396["variants"] for h in v["housings"]] == ["09-50-8020", "09-50-8051"]
    assert [h["n"] for v in kk396["variants"] for h in v["housings"]] == [2, 5] and not kk396["range_checked"]
    assert kk396["sources"] and all(source["url"].startswith("https://static.chipdip.ru/") for source in kk396["sources"])
    assert kk396["pitch"] == 3.96

    minilock = series["molex-minilock"]
    assert [h["article"] for v in minilock["variants"] for h in v["housings"]] == ["51163-0400"]
    assert minilock["pitch"] == 2.5

    sl = series["molex-sl"]
    assert [variant["family"] for variant in sl["variants"]] == ["molex-sl-2r", "molex-sl-1r"]
    assert "molex-sl-2r" not in series  # the two-row SL is a variant of the one SL series now

    assert series["molex-igrid"]["variants"][0]["housings"][0]["housing"]["rows"] == 2
    assert series["molex-boardin"]["variants"][0]["housings"][0]["n"] == 4
    for series_id in ("molex-kk396", "molex-minilock", "molex-sl", "molex-igrid", "molex-boardin"):
        assert series[series_id]["maker"] == "Molex" and series[series_id]["category"] == "Провод — плата"
    # No housing part number table was found for CTX50 (Mini50 34967): it keeps only the terminals and the closest shape.
    assert series["molex-ctx50"]["variants"] == [] and series["molex-ctx50"]["sample"]["article"] == ""
    # Owner's reference-data gaps, 14.10.2026: only the housings a document or a distributor listing confirms.
    ez = series["molex-picoezmate"]
    assert [h["article"] for v in ez["variants"] for h in v["housings"]] == [f"78172-000{n}" for n in (2, 3, 4, 5)]
    assert [h["n"] for v in ez["variants"] for h in v["housings"]] == [2, 3, 4, 5] and not ez["range_checked"]
    assert ez["pitch"] == 1.2 and ez["sources"]
    kls = series["kls"]
    assert [h["n"] for v in kls["variants"] for h in v["housings"]] == list(range(2, 21))
    assert kls["variants"][0]["housings"][0]["article"] == "L-KLS1-2.50-02-H" and kls["pitch"] == 2.5
    assert kls["maker"] == "KLS" and kls["contacts"][0]["article"] == "L-KLS1-2.50-T"
    assert kls["variants"][0]["housings"][0]["headers"][0]["article"] == "L-KLS1-2.50-02-S"

    connectors = connector_series()
    assert find_in_series("Molex 0009508051", connectors)["series"] == "molex-kk396"
    assert find_in_series("0511630400", connectors)["series"] == "molex-minilock"
    assert find_in_series("50-57-9410", connectors)["housing"]["n"] == 10


def test_kk396_and_sl_terminals_reach_their_series():
    records = [
        {"terManufacturer": "Molex", "terComponent": "KK 396 (legacy KK 156)", "terSeries": "2139, 3069, 41695",
         "terType": "Female", "terArticle": "08-52-0071", "awgFrom": "22", "awgTo": "18"},
        {"terManufacturer": "Molex", "terComponent": "SL", "terSeries": "70066, 70450", "terType": "Female",
         "terArticle": "16020086"},
    ]
    series = {item["id"]: item for item in connector_series(records)}
    assert [t["article"] for t in series["molex-kk396"]["terminals"]] == ["08-52-0071"]
    assert [t["article"] for t in series["molex-sl"]["terminals"]] == ["16020086"]


@pytest.mark.parametrize(("article", "family", "grid", "latch"), [
    # JST eSM «Contact position location numbers» (wire side): receptacle lever up — 1 on the left; plug, its lock ramp
    # (where the receptacle's lever catches) up — 1 on the right.
    ("SMR-02V-B", "jst-sm-r", [[1, 2]], "lever"),
    ("SMR-04V-B", "jst-sm-r", [[1, 2, 3, 4]], "lever"),
    ("SMR-06V-N", "jst-sm-r", [[1, 2, 3, 4, 5, 6]], "lever"),
    ("SMP-02V-BC", "jst-sm-p", [[2, 1]], "lever"),
    ("SMP-06V-NC", "jst-sm-p", [[6, 5, 4, 3, 2, 1]], "lever"),
    # Connfly DS1071 (Dupont): rows × contacts per row from the part number; no circuit-1 mark on the drawing.
    ("I-DS1071-SCB01X3", "connfly-ds1071", [[3, 2, 1]], "none"),
    ("I-DS1071-SCB02X3", "connfly-ds1071", [[5, 3, 1], [6, 4, 2]], "none"),
])
def test_the_owners_harness_housings_have_a_view(article, family, grid, latch):
    layout = housing_of(article)
    assert (layout["family"], layout["grid"], layout["latch"]) == (family, grid, latch)


def test_sm_and_ds1071_series_name_their_crimp_contacts():
    series = {item["id"]: item for item in connector_series()}
    sm = series["jst-sm"]
    assert [c["article"] for c in sm["contacts"]] == ["SYM-001T-P0.6", "SHF-001T-0.8BS"]
    assert "SMR" in sm["contacts"][0]["wire"] and "SMP" in sm["contacts"][1]["wire"]
    assert {h["article"] for v in sm["variants"] for h in v["housings"]} >= {"SMR-02V-B", "SMR-04V-B", "SMP-02V-BC"}
    assert [c["article"] for c in series["connfly-ds1071"]["contacts"]] == ["T-DS1071-SC600"]
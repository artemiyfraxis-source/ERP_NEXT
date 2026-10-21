"""Enrichment of an ETK config is safe to re-run (owner 19.10.2026: a reference change re-derives the cards by itself):
every enricher is idempotent and refreshing, hand-typed wire Ø survive, the references are read once per batch."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import event

from app.modules.references.cable_structure import HookupWire, WireGauge
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import load_mirror
from app.modules.references.technology import TerminalRecord
from app.workflows.techcard_build import (
    Refs, enrich_coax_depth, enrich_coax_diameters, enrich_coax_hex, enrich_linear_timing, enrich_marking,
    enrich_terminal_norms, enrich_wire_diameters, load_refs, refs_stamp, with_technology,
)
from tests.references.test_load_mirror import sessions  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 19, 12, 0)
WIRE = {"name": "UL1007 24AWG", "art": "UL1007 24AWG", "qty": 2, "length": 100}


def _config() -> dict:
    return {
        "wires": [dict(WIRE)],
        "ops": [
            {"type": "cutWireCrimp", "opNum": "CUT_WIRE+1crimp", "wires": [dict(WIRE)],
             "ep": {"termArt": "SXH-001T-P0.6N", "termName": "Контакт"}},
            {"type": "insMark", "opNum": "INS_MARK", "wires": [],
             "marks": [{"text": "W1", "target": "Провод W1"}]},
        ],
        "coax": {"cableArt": "RG-316", "sideA": {"article": "S-111F", "wire": "RG-316", "l1": "4"},
                 "sideB": {"article": "S-111F"}},
    }


def _record(height: str = "0,60 - 0,75") -> TerminalRecord:
    return TerminalRecord(article="SXH-001T-P0.6N", applicator="S5", crimp_height=height, awg_from="28", awg_to="22")


def _op(code: str = "CUT_WIRE+1crimp", roll: str = "3", t_op: str = "4,00") -> dict:
    return {"opNumber": code, "Номер": code, "Время Операции": t_op, "tOp": t_op, "tPrep": "5", "Тип операции": "Погонный",
            "Скорость проката, сек/м": roll, "Скорость работы инструмента": "0,2", "Кол-во операций инструмента": "5"}


def _gauge_and_hookup(outer: str = "1.4"):
    gauge = WireGauge.create(awg="24", section_mm2="0.2", conductor_d_mm="0.51")
    return [gauge], [HookupWire.create(mark="UL1007 24AWG", series="UL1007", gauge_id=gauge.id, outer_d_mm=outer)]


_GAUGE = WireGauge.create(awg="24", section_mm2="0.2", conductor_d_mm="0.51")
_HOOKUP_ID = _gauge_and_hookup()[1][0].id


def _coax_row(hex_: str = "5,41", depth: str = "0,5") -> dict:
    return {"coaxArticle": "S-111F", "coaxWire": "RG-316", "HEX обжима, мм": hex_, "Заглубление контакта, мм": depth}


def _dia(d1: str = "0,51") -> dict:
    return {"Кабель": "RG-316", "D1": d1, "D2": "1,52", "D3": "2,5"}


# name → (enricher with the reference value A, the same with B, a path to the value that must differ)
CASES = {
    "terminal": (lambda c, v: enrich_terminal_norms(c, [_record(v)], {}), ("0,60 - 0,75", "0,70 - 0,80"),
                 lambda c: c["ops"][0]["terminalNorm"]["crimpHeight"]),
    "marking": (lambda c, v: enrich_marking(c, [("Принтер трубок", {"Инв. номер": v})], [_op("PRINT_MARK", t_op=v)]),
                ("1", "2"), lambda c: c["ops"][1]["markKit"]["norms"]["tubePrint"]["operation_seconds"]),
    "timing": (lambda c, v: enrich_linear_timing(c, [_op(roll=v)]), ("3", "4"),
               lambda c: c["ops"][0]["linearTiming"]["rollSpeed"]),
    "wire diameters": (lambda c, v: enrich_wire_diameters(c, *reversed(_gauge_and_hookup(v))), ("1.4", "1.9"),
                       lambda c: c["wires"][0]["outerDiameter"]),
    "coax hex": (lambda c, v: enrich_coax_hex(c, [_coax_row(hex_=v)]), ("5,41", "6,5"),
                 lambda c: c["coax"]["sideA"]["crimpHex"]),
    "coax depth": (lambda c, v: enrich_coax_depth(c, [_coax_row(depth=v)]), ("0,5", "0,9"),
                   lambda c: c["coax"]["sideA"]["contactDepth"]),
    "coax diameters": (lambda c, v: enrich_coax_diameters(c, [_dia(v)]), ("0,51", "0,6"),
                       lambda c: c["coax"]["sideA"]["d1"]),
}


@pytest.mark.parametrize("name", CASES)
def test_every_enricher_is_idempotent(name):
    enrich, (value, _other), _read = CASES[name]
    once = enrich(_config(), value)
    assert enrich(once, value) == once
    assert once != _config()  # the enricher did something


@pytest.mark.parametrize("name", CASES)
def test_a_changed_reference_value_reaches_an_already_enriched_config(name):
    enrich, (value, other), read = CASES[name]
    first = enrich(_config(), value)
    refreshed = enrich(first, other)
    assert read(first) != read(refreshed)
    assert refreshed == enrich(_config(), other)  # the same as enriching the raw config from the new reference


@pytest.mark.parametrize("name", CASES)
def test_a_value_the_references_no_longer_give_disappears(name):
    enrich, (value, _other), read = CASES[name]
    first = enrich(_config(), value)
    gone = {
        "terminal": lambda c: enrich_terminal_norms(c, [], {}),
        "marking": lambda c: enrich_marking(c, [], []),
        "timing": lambda c: enrich_linear_timing(c, []),
        "wire diameters": lambda c: enrich_wire_diameters(c, [], []),
        "coax hex": lambda c: enrich_coax_hex(c, []),
        "coax depth": lambda c: enrich_coax_depth(c, []),
        "coax diameters": lambda c: enrich_coax_diameters(c, []),
    }[name](first)
    if name == "marking":
        assert gone["ops"][1]["markKit"]["norms"]["tubePrint"] == {"code": "PRINT_MARK"}  # no norm, the kit stays
        return
    if name == "terminal":  # the norm is still there, but says «не найдено», with no height from the old record
        assert gone["ops"][0]["terminalNorm"]["crimpHeight"] != first["ops"][0]["terminalNorm"]["crimpHeight"]
        return
    with pytest.raises((KeyError, IndexError)):
        read(gone)
    assert "refOwned" not in gone


def test_a_mark_operation_that_stopped_qualifying_drops_its_kit():
    enriched = enrich_marking(_config(), [], [])
    assert "markKit" in enriched["ops"][1]
    enriched["ops"][1]["marks"] = []
    assert "markKit" not in enrich_marking(enriched, [], [])["ops"][1]


def test_a_hand_typed_wire_diameter_survives_where_the_references_are_silent():
    config = _config()
    config["wires"][0]["outerDiameter"] = 2.2  # typed by hand: never set by the enricher
    config["wires"].append({"name": "МГТФ 0,12", "art": "МГТФ 0,12", "qty": 1, "length": 50})
    config["wires"][1]["conductorDiameter"] = 0.3
    gauges, hookups = _gauge_and_hookup()
    once = enrich_wire_diameters(config, hookups, gauges)
    assert once["wires"][1]["conductorDiameter"] == 0.3  # unknown to the references: stays
    assert once["wires"][0]["outerDiameter"] == 1.4  # known to the references: the reference answer (as before)
    again = enrich_wire_diameters(once, [], [])  # the references lost the wire: the enriched value goes, a typed one stays
    assert "outerDiameter" not in again["wires"][0] and again["wires"][1]["conductorDiameter"] == 0.3
    # A value changed after the enrichment is no longer the enricher's own.
    typed_over = deepcopy(once)
    typed_over["wires"][0]["outerDiameter"] = 3.0
    assert enrich_wire_diameters(typed_over, [], [])["wires"][0]["outerDiameter"] == 3.0


def test_keys_the_enrichers_do_not_own_are_left_alone():
    refs = _refs()
    built, _stamp = with_technology(_config(), refs=refs)
    assert built["coax"]["sideA"]["l1"] == "4" and built["wires"][0]["length"] == 100 and built["wires"][0]["qty"] == 2


def _refs(height: str = "0,60 - 0,75", outer: str = "1.4", hex_: str = "5,41") -> Refs:
    gauges, hookups = _gauge_and_hookup(outer)
    gauges = [_GAUGE]
    hookups = [HookupWire(**{**hookups[0].__dict__, "gauge_id": _GAUGE.id, "id": _HOOKUP_ID})]
    return Refs(
        snapshot={"schema_version": 1, "source_updated_at": None, "imported_at": NOW.isoformat()},
        terminal_rows=({"terArticle": "SXH-001T-P0.6N", "terApplicator": "S5", "terCrimpHeight": height,
                        "awgFrom": "28", "awgTo": "22"},),
        equipment=(("Принтер трубок", {"Инв. номер": "7"}),), operations=(_op("PRINT_MARK"), _op()),
        gauges=tuple(gauges), hookups=tuple(hookups), coax_rows=(_coax_row(hex_=hex_),), dia_rows=(_dia(),),
    )


def test_with_technology_over_the_snapshot_is_idempotent_and_refreshing():
    first, stamp = with_technology(_config(), refs=_refs())
    assert stamp["schema_version"] == 1
    assert with_technology(first, refs=_refs())[0] == first
    refreshed, _ = with_technology(first, refs=_refs(height="0,70 - 0,80", outer="1.9", hex_="6,5"))
    assert refreshed["ops"][0]["terminalNorm"]["crimpHeight"] != first["ops"][0]["terminalNorm"]["crimpHeight"]
    assert (refreshed["wires"][0]["outerDiameter"], refreshed["coax"]["sideA"]["crimpHex"]) == (1.9, "6,5")


def test_refs_stamp_follows_the_data_not_the_import_time():
    base = _refs()
    again = Refs(**{**base.__dict__, "snapshot": {"schema_version": 1, "source_updated_at": None, "imported_at": "other"}})
    assert refs_stamp(base) == refs_stamp(again) == refs_stamp(_refs())
    assert len(refs_stamp(base)) == 64
    for changed in (_refs(height="0,70 - 0,80"), _refs(outer="1.9"), _refs(hex_="6,5")):
        assert refs_stamp(changed) != refs_stamp(base)


def test_refs_are_an_immutable_snapshot(factory):
    refs = load_refs(factory[0])
    with pytest.raises(AttributeError):
        refs.operations = ()
    with pytest.raises(TypeError):
        refs.terminal_rows[0]["terCrimpHeight"] = "x"
    with pytest.raises(TypeError):
        refs.snapshot["schema_version"] = 0


@pytest.fixture()
def factory(sessions):  # noqa: F811
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)  # noqa: E731
    load_mirror(ROOT / "data" / "reference-mirror", factory(), dry_run=False, now=NOW)
    return factory, sessions.kw["bind"]


def _count(engine):
    seen = []
    event.listen(engine, "before_cursor_execute", lambda *args: seen.append(args[2]))
    return seen


def test_a_batch_reads_the_references_once(factory):
    make, engine = factory
    seen = _count(engine)
    refs = load_refs(make)
    loaded = len(seen)
    assert 0 < loaded <= 15
    configs = [_config() for _ in range(25)]
    results = [with_technology(config, refs=refs) for config in configs]
    assert len(seen) == loaded  # 25 cards, no further query
    assert all(stamp == results[0][1] for _config_out, stamp in results)
    # Without refs every card reads them again.
    with_technology(_config(), make)
    assert len(seen) >= 2 * loaded - 3
    assert refs_stamp(load_refs(make)) == refs_stamp(refs)


def test_the_single_card_path_equals_the_batch_path(factory):
    make, _engine = factory
    config = _config()
    assert with_technology(config, make) == with_technology(config, refs=load_refs(make))
    assert with_technology(config, None) == (config, None)


def test_the_713k4_config_is_stable_on_a_second_pass(factory):
    import json
    make, _engine = factory
    config = json.loads((ROOT / "tests" / "techcards" / "generator_713k4_config.json").read_text(encoding="utf-8"))
    refs = load_refs(make)
    once, _ = with_technology(config, refs=refs)
    assert with_technology(once, refs=refs)[0] == once

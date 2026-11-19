"""ОБ.019: лазерная маркировка из ЭТК → набор регистров по режиму (чистые правила, без БД)."""

from decimal import Decimal

from app.workflows.machine_writes_api import latin
from app.workflows.ob019_marking import group_laser_mark, marking_plan

LENGTH = Decimal(325)


def _plan(mark, length=LENGTH):
    return marking_plan(mark, length, latin)


def _values(plan):
    return {row["key"]: row["value"] for row in plan["rows"]}


def test_no_laser_mark_in_the_etk_means_no_plan():
    assert marking_plan(None, LENGTH, latin) is None
    assert group_laser_mark({"operations": [{"type": "cutWire", "fields": {}}]}) is None
    assert group_laser_mark({"operations": [{"type": "cutTut", "fields": {"laserMark": {"mode": "ends"}}}]}) is None
    found = group_laser_mark({"operations": [{"type": "cutWireStrip", "fields": {"laserMark": {"mode": "full"}}}]})
    assert found == {"mode": "full"}


def test_none_mode_switches_marking_off_and_nothing_else():
    plan = _plan({"mode": "none", "front": "A"})
    assert plan["mode"] == "none" and _values(plan) == {"marking": "Выкл"} and plan["blocking"] == []


def test_ends_mode_registers_with_defaults_and_cleared_middle():
    plan = _plan({"mode": "ends", "front": "A1", "back": "B1", "middle": "ignored"})
    assert _values(plan) == {"marking": "Вкл", "mark_by_step": "Выкл", "mark_count": "0", "mark_front": "A1",
                             "mark_back": "B1", "mark_middle": "", "mark_pos_front_mm": "20", "mark_pos_back_mm": "20"}
    assert plan["source"] == "ЭТК" and plan["warnings"] == [] and plan["blocking"] == []


def test_full_mode_registers_with_step_and_cleared_edges():
    plan = _plan({"mode": "full", "front": "A", "back": "B", "middle": "WIRE 1", "frontMm": "15", "backMm": "10",
                  "stepMm": "50,5"})
    assert _values(plan) == {"marking": "Вкл", "mark_by_step": "Вкл", "mark_step_mm": "50.5", "mark_middle": "WIRE 1",
                             "mark_front": "", "mark_back": "", "mark_count": "0", "mark_pos_front_mm": "15",
                             "mark_pos_back_mm": "10"}
    assert _values(_plan({"mode": "full", "middle": "X"}))["mark_step_mm"] == "100"


def test_cyrillic_is_transliterated_with_a_warning():
    plan = _plan({"mode": "ends", "front": "Провод", "back": "XS1"})
    assert _values(plan)["mark_front"] == "Provod"
    assert plan["warnings"] == ["Текст переведён в латиницу: «Провод» → «Provod»"]


def test_text_over_30_chars_blocks_even_when_the_cut_hides_it():
    plan = _plan({"mode": "ends", "front": "A" * 31})
    assert plan["blocking"] and "не длиннее 30" in plan["blocking"][0]
    assert not _plan({"mode": "ends", "front": "A" * 30})["blocking"]
    assert _plan({"mode": "ends", "front": "Ж" * 20})["blocking"]  # 20 Cyrillic → 40 Latin


def test_serial_token_blocks():
    plan = _plan({"mode": "full", "middle": "SN {№}"})
    assert plan["blocking"] == ["Серийные номера на лазер — не поддерживается"]


def test_short_wire_warns_but_does_not_block():
    assert "Провод короче меток" in _plan({"mode": "ends", "front": "A"}, Decimal(55))["warnings"]
    assert "Провод короче меток" not in _plan({"mode": "ends", "front": "A"}, Decimal(60))["warnings"]


def test_bad_numbers_and_unknown_mode_block():
    assert _plan({"mode": "ends", "frontMm": "abc"})["blocking"]
    assert _plan({"mode": "full", "stepMm": "0"})["blocking"]
    assert _plan({"mode": "spiral"})["blocking"]


def test_positions_and_step_outside_the_machine_limits_block_in_the_plan():
    """A value the machine can't hold shows in the plan, not as a 422 at «Передать на станок» (review 20.11)."""
    plan = marking_plan({"mode": "full", "middle": "W1", "frontMm": 20.005, "backMm": 20, "stepMm": 15000},
                                Decimal(500), lambda text: text)
    assert len(plan["blocking"]) == 2

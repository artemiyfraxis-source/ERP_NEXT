"""ОБ.019 speed (plan machine-speed-history S2), pure part: the cyclogram norm (etk_builder.linear_seconds / opFields
cycleTimes), running intervals and stops, a counter reset, a bridge restart, the running cap, the setup fact, and the
norm suggestion — a line through two lengths, the constant part only from one length, nothing on too few pieces or runs
or when the runs are not steadily off the norm."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.modules.production import machine_speed as speed
from app.shared.clock import MOSCOW

MACHINE = uuid4()
START = datetime(2026, 11, 18, 8, 0, tzinfo=MOSCOW)
KEY = "пвам 0,35|L325|S5/5|P0/0|M0"
LONG = "пвам 0,35|L1000|S5/5|P0/0|M0"
LINEAR = {
    "opNumber": "CUT_WIRE+2strip", "tOp": "6,50", "tPrep": "600", "Номер": "CUT_WIRE+2strip",
    "Время Операции": "6,50", "Время подготовки, сек": "600", "Время Чел, сек/оп; сек/м": "0,00",
    "Время машины, сек/оп; сек/м": "6,50", "Тип операции": "Погонный",
    "Время ручных работ для взятия полуфабриката": "", "Время ручных работ": "",
    "Время ручных работ для снятия полуфабриката": "", "Скорость проката, сек/м": "3",
    "Скорость работы инструмента": "0,5", "Кол-во операций инструмента": "7",
}


@dataclass
class Row:
    id: int
    recorded_at: datetime
    counter_total: int
    state_code: int = 103
    setup_key: str = KEY
    reason: str = "counter"
    boot: str = "boot-1"
    alarm_code: int | None = 0
    run_write_id: UUID | None = None
    run_kind: str | None = None
    equipment_id: UUID = MACHINE


def _rows(*items) -> list[Row]:
    """(seconds from START, counter, extra) → rows."""
    return [Row(index, START + timedelta(seconds=seconds), counter, **extra)
            for index, (seconds, counter, extra) in enumerate(items, start=1)]


def test_the_norm_is_the_cyclogram_at_the_setup_length():
    # 0,325 m / 3 m/s + 0,5 s × 7 = 3,6083…; the hands win when they are longer
    assert speed.piece_seconds(LINEAR, Decimal(325)).quantize(Decimal("0.0001")) == Decimal("3.6083")
    assert speed.piece_seconds({**LINEAR, "Время ручных работ": "5"}, Decimal(325)) == Decimal(5)
    assert speed.piece_seconds(LINEAR, None) is None
    assert speed.prep_seconds(LINEAR) == Decimal(600)
    static = {**LINEAR, "Тип операции": "Статичный", "Время ручных работ для взятия полуфабриката": "2",
              "Время ручных работ": "3,5", "Время ручных работ для снятия полуфабриката": "1"}
    assert speed.piece_seconds(static, Decimal(325)) == Decimal("6.5")  # max(2 + 3,5 + 1, 0,5 × 7)
    assert speed.timing_seconds({"rollSpeed": "3", "toolSeconds": "0,5", "toolOps": "7"}, Decimal(1000)) \
        == Decimal(1) / 3 + Decimal("3.5")


def test_with_cycle_times_recomputes_like_the_sheet_and_leaves_a_linear_row():
    assert speed.with_cycle_times(LINEAR) == LINEAR  # «Погонный» with прокат: the time depends on the length
    static = {**LINEAR, "Тип операции": "Статичный", "Время ручных работ для взятия полуфабриката": "2",
              "Время ручных работ": "3,5", "Скорость работы инструмента": "1,005", "Кол-во операций инструмента": "1"}
    result = speed.with_cycle_times(static)
    assert result["Время Чел, сек/оп; сек/м"] == "5,50"
    assert result["Время машины, сек/оп; сек/м"] == "1,01"  # ICU rounds the shortest decimal «1.005» half up
    assert result["Время Операции"] == "5,50"
    assert result["tOp"] == "6,50"  # schema keys are the references sync's business, not the sheet formula's
    assert speed.cycle_keys(LINEAR) == {
        "prep": "Время подготовки, сек", "take": "Время ручных работ для взятия полуфабриката",
        "manual": "Время ручных работ", "put": "Время ручных работ для снятия полуфабриката",
        "roll": "Скорость проката, сек/м", "tool": "Скорость работы инструмента",
        "toolOps": "Кол-во операций инструмента"}


def test_setup_key_parts_code_and_marking():
    assert speed.parse_setup(KEY) == {"wire": "пвам 0,35", "length_mm": "325", "strip_front_mm": "5",
                                      "strip_back_mm": "5", "pull": "0/0", "marking": "0"}
    assert speed.setup_code(KEY) == "CUT_WIRE+2strip"
    assert speed.setup_code("x|L100|S0/4|P0/0|M1/F-B/0") == "CUT_WIRE+1strip+2mark"
    assert speed.setup_code("x|L100|S0/0|P0/0|M0") == "CUT_WIRE_auto"
    assert speed.marking_text("0") == "Без маркировки"
    assert speed.marking_text("1/F-B/3") == "Маркировка: 2 текста"
    assert speed.marking_text("?") == "нет данных"


def test_fact_is_running_time_over_pieces_and_stops_go_by_reason():
    rows = _rows(
        (0, 100, {"state_code": 102, "reason": "setup"}),
        (90, 100, {"reason": "state"}),          # setup: 90 s until the first productive interval starts
        (120, 110, {}),                           # 30 s, 10 pcs
        (150, 120, {"state_code": 104, "reason": "state"}),  # 30 s, 10 pcs
        (450, 120, {"reason": "state"}),          # 300 s pause
        (480, 130, {"alarm_code": 1}),            # 30 s, 10 pcs
        (510, 130, {"state_code": 105}),          # 30 s with a fault alarm → «Авария»
        (530, 130, {"state_code": 107}),          # 20 s «Авария»
        (600, 130, {}),                           # 70 s «Готов/ждёт»
    )
    [segment] = speed.segments(rows, {})
    assert (segment.pieces, segment.running_seconds) == (30, 90.0)
    assert segment.fact == 3.0
    assert segment.setup_seconds == 90.0
    assert dict(segment.stops) == {"pause": 300.0, "alarm": 50.0, "idle": 160.0}
    [setup] = speed.by_setup([segment])
    assert setup.stops == {"pause": 300.0, "alarm": 50.0, "idle": 160.0}
    assert setup.setup_fact == 90.0 and len(setup.runs) == 1


def test_a_counter_reset_is_a_stop_and_counting_goes_on_from_the_new_value():
    rows = _rows((0, 500, {}), (30, 510, {}), (40, 0, {}), (70, 10, {}))
    [segment] = speed.segments(rows, {})
    assert (segment.pieces, segment.running_seconds, dict(segment.stops)) == (20, 60.0, {"reset": 10.0})


def test_a_bridge_restart_is_no_link_and_never_running():
    rows = _rows((0, 100, {}), (30, 110, {}), (3600, 150, {"boot": "boot-2", "reason": "start"}), (3630, 160, {"boot": "boot-2"}))
    [segment] = speed.segments(rows, {})
    assert (segment.pieces, segment.running_seconds) == (20, 60.0)
    assert dict(segment.stops) == {"offline": 3570.0}


def test_running_longer_than_the_cap_is_no_link():
    rows = _rows((0, 100, {}), (61, 101, {}), (100, 102, {}))
    [segment] = speed.segments(rows, {})
    assert (segment.pieces, dict(segment.stops)) == (1, {"offline": 61.0})
    # the cap is 5 × norm when that is longer than 60 s: a 20-s piece may take 61 s
    [segment] = speed.segments(rows, {(MACHINE, KEY): Decimal(20)})
    assert (segment.pieces, segment.running_seconds) == (2, 100.0)


def test_another_setup_or_run_starts_a_segment():
    run = uuid4()
    rows = _rows((0, 0, {}), (10, 5, {}), (20, 5, {"setup_key": LONG, "reason": "setup"}), (30, 8, {"setup_key": LONG}),
                 (40, 8, {"setup_key": LONG, "run_write_id": run, "run_kind": "cutting", "reason": "run"}),
                 (50, 12, {"setup_key": LONG, "run_write_id": run, "run_kind": "cutting"}))
    first, second, third = speed.segments(rows, {})
    assert (first.setup_key, first.pieces) == (KEY, 5)  # 10→20 changes setup: not counted
    assert (second.setup_key, second.pieces, second.setup_seconds) == (LONG, 3, 0.0)
    assert (third.run_kind, third.pieces, third.setup_seconds) == ("cutting", 4, 0.0)


def _point(length: int, pieces: int = 300) -> speed.Point:
    return speed.Point(Decimal(length), pieces, 2.0 + length / 1000 / 2.5)  # t = 2 s + L / 2,5 m/s


def test_two_lengths_give_the_feed_and_the_tool_time():
    points = [_point(325), _point(1000)]
    ratios = [0.59, 0.6, 0.62, 0.63, 0.65, 0.64]
    result = speed.suggest(LINEAR, points, ratios)
    assert result == {
        "cells": [{"field": "Скорость проката, сек/м", "label": "Скорость проката, м/с", "old": "3", "new": "2,5"},
                  {"field": "Скорость работы инструмента", "label": "Скорость работы инструмента, с",
                   "old": "0,5", "new": "0,3"}],  # 2 s / 7 ходов = 0,29 → 0,3
        "fit": "2 с + L / 2,5 м/с", "lengths": ["325", "1000"], "note": None,
    }


def test_one_length_suggests_only_the_constant_part():
    result = speed.suggest(LINEAR, [_point(325, 600)], [0.59] * 6)
    # a = 2,13 − 0,325 / 3 = 2,0217; 2,0217 / 7 = 0,289 → 0,3; прокат stays
    assert result["cells"] == [{"field": "Скорость работы инструмента", "label": "Скорость работы инструмента, с",
                                "old": "0,5", "new": "0,3"}]
    assert result["note"] == speed.ONE_LENGTH and result["fit"] == "2 с + L / 3 м/с"


@pytest.mark.parametrize(("points", "ratios"), [
    ([_point(325, 100), _point(1000, 99)], [0.6] * 6),   # 199 pieces
    ([_point(325), _point(1000)], [0.6] * 4),            # 4 runs
    ([_point(325), _point(1000)], [0.6, 0.6, 0.7, 1.1, 1.2, 1.3]),  # not steadily fast
    ([_point(325), _point(1000)], [1.0] * 6),            # on the norm
])
def test_no_suggestion_without_enough_steady_evidence(points, ratios):
    assert speed.suggest(LINEAR, points, ratios) is None


def test_no_suggestion_for_a_row_without_feed_or_when_nothing_changes():
    static = {**LINEAR, "Тип операции": "Статичный"}
    assert speed.suggest(static, [_point(325), _point(1000)], [0.6] * 6) is None
    same = {**LINEAR, "Скорость проката, сек/м": "2,5", "Скорость работы инструмента": "0,3"}
    assert speed.suggest(same, [_point(325), _point(1000)], [0.6] * 6) is None


def test_sheet_numbers_round_like_the_browser():
    # node: (v).toLocaleString("ru-RU", {minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: false})
    assert [speed.sheet_number(value) for value in (1.005, 0.125, 2.675, 1.0049999, 12345.5, 0.1 + 0.2)]         == ["1,01", "0,13", "2,68", "1,00", "12345,50", "0,30"]


def test_figures_are_dot_decimals():
    assert speed.seconds_text(6.266) == "6.27"
    assert speed.seconds_text(Decimal("539.6"), "1") == "540"
    assert speed.seconds_text(None) is None

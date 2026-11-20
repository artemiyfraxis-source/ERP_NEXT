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
    assert setup.setup_fact(None) == 90.0 and len(setup.runs) == 1


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


def _point(length: int, pieces: int = 100, constant: float = 2.0, feed: float = 2.5) -> speed.Point:
    return speed.Point(Decimal(length), pieces, constant + length / 1000 / feed)  # t = a + L / v


def _runs(*lengths: int, **line) -> list[speed.Point]:
    """Three runs of each length on the line t = a + L / v."""
    return [_point(length, **line) for length in lengths for _ in range(3)]


TOOL_CELL = {"field": "Скорость работы инструмента", "label": "Скорость работы инструмента, с", "old": "0,5"}
ROLL_CELL = {"field": "Скорость проката, сек/м", "label": "Скорость проката, м/с", "old": "3"}
FAST_RATIOS = [0.59, 0.6, 0.62, 0.63, 0.65, 0.64]


def test_lengths_far_apart_give_the_feed_and_the_tool_time():
    result = speed.suggest(LINEAR, _runs(325, 3000), FAST_RATIOS)
    # feed spread (3 − 0,325) m / 2,5 m/s = 1,07 s ≥ 0,5 s and ≥ 10 % of the piece; 2 s / 7 ходов = 0,2857 → 0,29
    assert result == {"cells": [{**ROLL_CELL, "new": "2,5"}, {**TOOL_CELL, "new": "0,29"}],
                      "fit": "2 с + L / 2,5 м/с", "lengths": ["325", "3000"], "note": None}


def test_close_lengths_suggest_no_feed_speed():
    # review 20.11: 4,30 s at 325 mm and 4,31 s at 1000 mm once gave прокат 3 → 67,5 m/s
    noisy = [speed.Point(Decimal(325), 100, 4.30)] * 3 + [speed.Point(Decimal(1000), 100, 4.31)] * 3
    result = speed.suggest({**LINEAR, "Скорость работы инструмента": "0,3"}, noisy, [1.9, 1.95, 1.9, 1.77, 1.8, 1.77])
    assert result["note"] == speed.CLOSE_LENGTHS
    # прокат stays 3: a = mean(t − L / 3) = 4,084; 4,084 / 7 = 0,583
    assert result["cells"] == [{**TOOL_CELL, "old": "0,3", "new": "0,58"}]
    assert result["fit"] == "4,1 с + L / 3 м/с"


def test_one_length_suggests_only_the_constant_part():
    result = speed.suggest(LINEAR, _runs(325), FAST_RATIOS)
    # a = 2,13 − 0,325 / 3 = 2,0217; 2,0217 / 7 = 0,2888 → 0,29; прокат stays
    assert result["cells"] == [{**TOOL_CELL, "new": "0,29"}]
    assert result["note"] == speed.ONE_LENGTH and result["fit"] == "2 с + L / 3 м/с"


def test_a_scattered_fit_or_a_falling_line_suggests_no_feed_speed():
    scattered = [speed.Point(Decimal(325), 100, fact) for fact in (1.5, 2.13, 2.76)] \
        + [speed.Point(Decimal(3000), 100, fact) for fact in (2.5, 3.2, 3.9)]
    result = speed.suggest(LINEAR, scattered, FAST_RATIOS)
    assert result["note"] == speed.UNCLEAR_FEED and [cell["field"] for cell in result["cells"]] == [TOOL_CELL["field"]]
    falling = [speed.Point(Decimal(325), 100, 2.9)] * 3 + [speed.Point(Decimal(3000), 100, 2.6)] * 3
    result = speed.suggest(LINEAR, falling, FAST_RATIOS)
    assert result["note"] == speed.UNCLEAR_FEED and [cell["field"] for cell in result["cells"]] == [TOOL_CELL["field"]]


def test_new_values_outside_half_to_double_the_current_are_not_suggested():
    # v = 1 m/s < 0,5 × 3: no прокат, only the tool part with прокат 3
    result = speed.suggest(LINEAR, _runs(325, 3000, feed=1), FAST_RATIOS)
    assert result["note"] == speed.UNCLEAR_FEED and [cell["field"] for cell in result["cells"]] == [TOOL_CELL["field"]]
    # a = 10 s → 1,43 s per stroke > 2 × 0,5: the tool cell is left out, прокат still suggested
    result = speed.suggest(LINEAR, _runs(325, 3000, constant=10), FAST_RATIOS)
    assert result["cells"] == [{**ROLL_CELL, "new": "2,5"}] and result["note"] is None


@pytest.mark.parametrize("fields", [
    {**LINEAR, "Кол-во операций инструмента": ""},
    {**LINEAR, "Кол-во операций инструмента": "0"},
    {key: value for key, value in LINEAR.items() if key != "Кол-во операций инструмента"},
])
def test_no_tool_strokes_fit_the_feed_through_the_origin(fields):
    result = speed.suggest(fields, _runs(325, 3000, constant=0), FAST_RATIOS)
    assert result == {"cells": [{**ROLL_CELL, "new": "2,5"}], "fit": "L / 2,5 м/с", "lengths": ["325", "3000"],
                      "note": None}
    # a big constant the formula has no place for: the origin fit gives 0,88 m/s < 0,5 × 3 — nothing suggested
    assert speed.suggest(fields, _runs(325, 3000), FAST_RATIOS) is None


@pytest.mark.parametrize(("points", "ratios"), [
    ([_point(325, 50)] * 2 + [_point(3000, 33)] * 3, FAST_RATIOS),  # 199 pieces
    (_runs(325, 3000), [0.6] * 4),                                   # 4 runs
    (_runs(325, 3000), [0.6, 0.6, 0.7, 1.1, 1.2, 1.3]),              # not steadily fast
    (_runs(325, 3000), [1.0] * 6),                                   # on the norm
])
def test_no_suggestion_without_enough_steady_evidence(points, ratios):
    assert speed.suggest(LINEAR, points, ratios) is None


def test_no_suggestion_for_a_row_without_feed_or_when_nothing_changes():
    static = {**LINEAR, "Тип операции": "Статичный"}
    assert speed.suggest(static, _runs(325, 3000), FAST_RATIOS) is None
    same = {**LINEAR, "Скорость проката, сек/м": "2,5", "Скорость работы инструмента": "0,29"}
    assert speed.suggest(same, _runs(325, 3000), FAST_RATIOS) is None


def test_setup_fact_leaves_out_pauses_and_an_overnight_gap():
    rows = _rows(
        (0, 100, {"state_code": 102, "reason": "setup"}),
        (30, 100, {"state_code": 104, "reason": "state"}),   # 300 s pause: not setup work
        (330, 100, {"state_code": 102, "reason": "state"}),
        (400, 100, {"reason": "state"}),
        (430, 110, {}),
    )
    [segment] = speed.segments(rows, {})
    assert segment.setup_seconds == 100.0
    overnight = _rows((0, 100, {"state_code": 102, "reason": "setup"}), (36000, 100, {"reason": "state"}),
                      (36030, 110, {}))
    [segment] = speed.segments(overnight, {})
    assert segment.setup_seconds == 36000.0
    assert segment.setup_fact(Decimal(600)) is None  # > 4 × «Время подготовки»
    assert segment.setup_fact(Decimal(10000)) == 36000.0 and segment.setup_fact(None) == 36000.0
    assert speed.by_setup([segment])[0].setup_fact(Decimal(600)) is None


def test_segments_of_one_setup_only_are_still_bounded_by_the_others():
    rows = _rows((0, 0, {}), (10, 5, {}), (20, 5, {"setup_key": LONG, "reason": "setup"}), (30, 8, {"setup_key": LONG}),
                 (40, 8, {"reason": "setup"}), (50, 12, {}))
    first, second = speed.segments(rows, {}, only=KEY)
    assert (first.pieces, second.pieces) == (5, 4)  # LONG's 3 pieces never join KEY


def test_sheet_numbers_round_like_the_browser():
    # node: (v).toLocaleString("ru-RU", {minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: false})
    assert [speed.sheet_number(value) for value in (1.005, 0.125, 2.675, 1.0049999, 12345.5, 0.1 + 0.2)]         == ["1,01", "0,13", "2,68", "1,00", "12345,50", "0,30"]


def test_figures_are_dot_decimals():
    assert speed.seconds_text(6.266) == "6.27"
    assert speed.seconds_text(Decimal("539.6"), "1") == "540"
    assert speed.seconds_text(None) is None

from decimal import Decimal

from app.modules.references.technology import (
    TerminalRecord,
    OperationWorkload,
    WireWorkload,
    calculate_operation_time,
    WireSection,
    crimp_entry,
    fold_wire_section,
    operation_minutes,
    parse_crimp_profiles,
    resolve_terminal,
    terminal_record_for_section,
    wire_section,
)


def test_wire_section_uses_explicit_awg_gost_map_and_owner_override():
    assert wire_section("UL1007 24 AWG").awg == 24
    assert wire_section("ПВАМ 0,35 жёлтый", {"0.35": 22}) == WireSection(22, Decimal("0.35"))
    assert wire_section("ПВ-3 1,0 мм²", {"1": 17}) == WireSection(18, Decimal("1.0"))
    assert wire_section("Неизвестный провод").awg == 0


def test_folded_wire_uses_doubled_cross_section_for_crimp_selection():
    folded = fold_wire_section(WireSection(26, Decimal("0.12")))
    assert folded == WireSection(24, Decimal("0.240"))
    assert folded.label == "0,24 мм² (24 AWG)"


def test_crimp_profile_prefers_exact_awg_over_range_and_keeps_pull_force():
    text = "28–22 AWG: 0,65 - 0,85 (4,9 Н); 24 AWG: 0,76 - 0,81 (29,3 Н)"
    profiles = parse_crimp_profiles(text)
    selected = crimp_entry(text, 24)

    assert len(profiles) == 2
    assert selected.height == "0,76 - 0,81"
    assert selected.pull_force == "29,3"
    assert selected.range is False


def test_terminal_row_prefers_exact_section_over_compatible_range():
    rows = (
        TerminalRecord(article="5034290000", awg_from="28", awg_to="22", crimp_height="0,7 - 0,9"),
        TerminalRecord(article="5034290000", awg_from="24", awg_to="24", crimp_height="0,76 - 0,81"),
    )
    assert terminal_record_for_section(rows, WireSection(24, Decimal("0.2"))) == rows[1]


def test_section_missing_from_terminal_rows_falls_back_to_first_row_plain_values():
    plain = TerminalRecord(
        article="T-1", applicator="AP-1", awg_from="26", awg_to="22",
        crimp_height="0,85", pull_force_min="50", pull_force_max="70",
    )
    result = resolve_terminal((plain,), "T-1", (WireSection(18, Decimal("0.75")),))
    assert (result.crimp_height, result.applicator, result.pull_force) == ("0,85", "AP-1", "50 - 70")
    assert result.by_section[0].found is False

    listed = TerminalRecord(
        article="T-2", applicator="AP-2", awg_from="26", awg_to="22",
        crimp_height="24 AWG: 0,76 - 0,81; 22 AWG: 0,84 - 0,89", pull_force_min="24 AWG: 29,3; 22 AWG: 44,1",
    )
    result = resolve_terminal((listed,), "T-2", (WireSection(18, Decimal("0.75")),))
    assert (result.crimp_height, result.applicator, result.pull_force) == ("", "AP-2", "")
    assert result.by_section[0].found is False


def test_terminal_resolution_matches_manufacturer_prefix_and_reports_each_section():
    rows = (
        TerminalRecord(
            article="5034290000",
            applicator="M49/A8",
            awg_from="28",
            awg_to="22",
            crimp_height="24 AWG: 0,76 - 0,81; 22 AWG: 0,84 - 0,89",
            pull_force_min="24 AWG: 29,3; 22 AWG: 44,1",
        ),
    )
    result = resolve_terminal(
        rows,
        "MOLEX 5034290000",
        (WireSection(24, Decimal("0.2")), WireSection(22, Decimal("0.33"))),
    )

    assert result.applicator == "M49/A8"
    assert result.crimp_height == "24 AWG: 0,76 - 0,81; 22 AWG: 0,84 - 0,89"
    assert result.pull_force == "24 AWG: 29,3; 22 AWG: 44,1"
    assert all(item.found for item in result.by_section)


def test_operation_time_is_per_unit_and_setup_is_once_per_batch():
    assert operation_minutes(
        operation_seconds="12",
        setup_seconds="60",
        units=4,
        batch_quantity=3,
    ) == Decimal("3.40")


def test_terminal_time_multiplies_multicore_wire_and_batch():
    lines = calculate_operation_time(OperationWorkload(
        operation_type="press",
        terminal_kind="ins",
        operation_seconds="12",
        setup_seconds="60",
        batch_quantity=4,
        wires=(WireWorkload("Кабель", quantity=2, cores=3),),
    ))
    assert [line.minutes for line in lines] == [Decimal("5.80")]


def test_cut_time_splits_setup_once_between_wire_rows():
    lines = calculate_operation_time(OperationWorkload(
        operation_type="cutWire",
        operation_seconds="30",
        setup_seconds="60",
        batch_quantity=2,
        wires=(
            WireWorkload("Красный", quantity=1),
            WireWorkload("Чёрный", quantity=2),
        ),
    ))
    assert [line.minutes for line in lines] == [Decimal("1.50"), Decimal("2.50")]


def test_splice_and_coax_time_multiply_operation_units():
    splice = calculate_operation_time(OperationWorkload(
        operation_type="splice",
        operation_seconds="20",
        setup_seconds="60",
        batch_quantity=2,
        units=3,
    ))
    coax = calculate_operation_time(OperationWorkload(
        operation_type="coaxCrimp",
        operation_seconds="30",
        setup_seconds="0",
        batch_quantity=2,
        units=2,
    ))
    assert splice[0].minutes == Decimal("3.00")
    assert coax[0].minutes == Decimal("2.00")

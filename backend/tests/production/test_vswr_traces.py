"""Atomic two-end acceptance and full-resolution Touchstone evidence."""

from dataclasses import replace
from decimal import Decimal
import math
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.modules.production.domain import ProductionValidationError
from app.modules.production.infrastructure import ProductionVswrMeasurementRow, ProductionVswrTraceRow
from app.modules.production.vna_files import VnaFileError, parse_vna_traces
from app.modules.production.vswr import RecordVswrCommand, VswrFileInput, VswrVerdict, latest_vswr, record_vswr, vswr_file
from app.workflows.coax_vswr_api import _measurement_result, task_norm, CoaxRefs
from tests.production.test_vswr import setup  # noqa: F401

NORM = {"status": "ok", "vswrMax": 1.35, "bandMhz": [1000, 3000], "s21Db": -0.01,
        "ends": [{"side": "A", "article": "SMA-1"}, {"side": "B", "article": "SMA-2"}]}


def s2p(s11=0.05, s22=0.1, *, frequencies=(1000, 2000, 3000), transmission=0.001):
    return ("# MHz S RI R 50\n" + "\n".join(
        f"{f} {s11} 0 {transmission} 0 {transmission} 0 {s22} 0" for f in frequencies
    )).encode()


def record(setup, content=None, **changes):
    factory, actor, _, task, instrument, now = setup
    values = dict(task_id=task.id, position=1, instrument_id=instrument.id, file_name="both.s2p",
                  content=content if content is not None else (b"" if changes.get("files") else s2p()),
                  norm=NORM, actor=actor, now=now)
    values.update(changes)
    return record_vswr(RecordVswrCommand(**values), factory())


@pytest.mark.parametrize("unit,factor", [("Hz", 1), ("kHz", 1e3), ("MHz", 1e6), ("GHz", 1e9)])
@pytest.mark.parametrize("fmt", ["RI", "MA", "DB"])
def test_all_parameters_formats_units_and_phase(unit, factor, fmt):
    magnitudes = (0.1, 0.5, 0.6, 0.2)
    pairs = [(0, -m) if fmt == "RI" else (20 * math.log10(m) if fmt == "DB" else m, -90)
             for m in magnitudes]
    data = " ".join(f"{a} {b}" for a, b in pairs)
    content = f"! header\n# {unit} S {fmt} R 50 ! options\n{1e9/factor} {data} ! point\n{3e9/factor} {data}\n".encode()
    traces = parse_vna_traces(content, "test.s2p")
    assert [t.name for t in traces] == ["S11", "S21", "S12", "S22"]
    for trace, magnitude in zip(traces, magnitudes):
        assert trace.complex_points[0] == pytest.approx((1e9, 0, -magnitude))
        assert trace.complex_points[1][0] == pytest.approx(3e9)
        expected = (1 + magnitude) / (1 - magnitude) if trace.name in ("S11", "S22") else magnitude
        assert trace.points[0][1] == pytest.approx(expected)


def test_touchstone_continuations_and_default_options():
    traces = parse_vna_traces(b"! defaults GHz MA\n1 .1 0 .8 -20\n! split\n .7 20 .2 0\n3 .1 0\n .8 -20 .7 20 .2 0", "split.s2p")
    assert len(traces) == 4
    assert traces[-1].points[-1] == pytest.approx((3e9, 1.5))


@pytest.mark.parametrize("content", [
    b"# MHz S RI R 50\n1000 .1 0\n3000 .1 0",  # .s2p cannot silently become .s1p
    b"# MHz S RI R 50\n1000 .05 0\n1500 .05 0\n2000 .05 0\n3000 .05 0\n3500 .05 0\n4000 .05 0",
    b"# MHz S MA R 50\n1000 .1 0 1 0 1 0 -.2 0\n3000 .1 0 1 0 1 0 .2 0",
    b"# MHz S DB R 50\n1000 9000 0 0 0 0 0 -20 0\n3000 -20 0 0 0 0 0 -20 0",
])
def test_malformed_two_port_evidence_is_rejected(content):
    with pytest.raises(VnaFileError):
        parse_vna_traces(content, "bad.s2p")


def test_s11_pass_s22_fail_is_one_failed_attempt(setup):
    measured = record(setup, s2p(s22=0.3))
    assert measured.verdict is VswrVerdict.FAIL and measured.complete
    assert [(r["end"], r["verdict"]) for r in measured.end_results] == [("A", "pass"), ("B", "fail")]
    assert measured.max_vswr == Decimal("1.8571")
    assert measured.file_meta["summary_trace"] == "S22"
    assert max(v for _, v in measured.trace) == float(measured.max_vswr)
    with setup[0]() as uow:
        assert uow.session.scalar(select(func.count()).select_from(ProductionVswrMeasurementRow)) == 1
        assert uow.session.scalar(select(func.count()).select_from(ProductionVswrTraceRow)) == 4
    stored = latest_vswr(measured.serial_id, setup[0]())
    assert _measurement_result(stored, trace=True) == _measurement_result(measured, trace=True)


def test_reversed_port_mapping_changes_end_results(setup):
    measured = record(setup, s2p(s22=0.3), port_mapping={"1": "B", "2": "A"})
    assert [(r["end"], r["trace_name"], r["verdict"]) for r in measured.end_results] == [
        ("A", "S22", "fail"), ("B", "S11", "pass"),
    ]


def test_missing_end_cannot_pass_and_single_port_has_only_one_trace(setup):
    measured = record(setup, b"# MHz S RI R 50\n1000 .05 0\n3000 .05 0", file_name="one.s1p")
    assert not measured.complete and measured.verdict is VswrVerdict.FAIL
    assert measured.end_results[1]["verdict"] == "incomplete"
    assert "Не измерен конец Б" in measured.verdict_reason
    assert [t.name for t in measured.traces] == ["S11"]


@pytest.mark.parametrize("verified", [False, True])
def test_partial_band_never_passes_even_verified_capture_or_client_override(setup, verified):
    measured = record(setup, s2p(frequencies=(1500, 2500)), capture_verified=verified,
                      band_from_mhz=Decimal("1500"), band_to_mhz=Decimal("2500"))
    assert not measured.complete and measured.verdict is VswrVerdict.FAIL
    assert (measured.band_from_mhz, measured.band_to_mhz) == (Decimal("1000"), Decimal("3000"))
    assert all(r["verdict"] == "incomplete" for r in measured.end_results)


def test_explicit_one_end_ignores_nonrequired_reflection_and_transmission(setup):
    measured = record(setup, s2p(s22=0.9, transmission=4), norm={**NORM, "requiredEnds": ["A"]})
    assert measured.complete and measured.verdict is VswrVerdict.PASS
    assert len(measured.end_results) == 1
    assert {t.name for t in measured.traces} == {"S11", "S22", "S21", "S12"}


def test_single_connector_topology_and_verified_s22_capture(setup):
    norm = {**NORM, "ends": [{"side": "B", "article": "SMA-2"}]}
    measured = record(setup, b"# MHz S RI R 50\n1000 .05 0\n3000 .05 0", file_name="one.s1p", norm=norm,
                      capture_verified=True, capture_settings={"s_parameter": "S22"})
    assert measured.required_ends == ("B",) and measured.verdict is VswrVerdict.PASS
    assert measured.traces[0].name == "S22"


def test_untrusted_capture_metadata_cannot_relabel_evidence(setup):
    measured = record(setup, b"# MHz S RI R 50\n1000 .05 0\n3000 .05 0", file_name="one.s1p",
                      norm={**NORM, "requiredEnds": ["B"]}, capture_settings={"s_parameter": "S22"})
    assert measured.verdict is VswrVerdict.FAIL and measured.traces[0].name == "S11"


def test_identical_file_and_session_retries_reuse_the_original_snapshot(setup):
    first = record(setup)
    again = record(setup, file_name="renamed.s2p", norm={**NORM, "vswrMax": 1.01})
    assert again.id == first.id and again.attempt == 1 and again.norm == NORM
    assert record(setup, s2p(s22=0.11)).attempt == 2
    remapped = record(setup, port_mapping={"1": "B", "2": "A"})
    assert remapped.id != first.id and remapped.attempt == 3


def test_session_identity_distinguishes_identical_s11_and_s22_bytes_and_rejects_changed_evidence(setup):
    content = b"# MHz S RI R 50\n1000 .05 0\n3000 .05 0"
    first_id, second_id = uuid4(), uuid4()
    common = dict(file_name="one.s1p", norm={**NORM, "requiredEnds": ["A"]}, capture_verified=True)
    first = record(setup, content, evidence_id=first_id, capture_settings={"s_parameter": "S11"}, **common)
    second = record(setup, content, evidence_id=second_id, capture_settings={"s_parameter": "S22"}, **common)
    assert first.verdict is VswrVerdict.PASS and second.verdict is VswrVerdict.FAIL
    assert (first.attempt, second.attempt) == (1, 2)
    again = record(setup, content, evidence_id=second_id, capture_settings={"s_parameter": "S22"}, **common)
    assert again.id == second.id
    for changed in ({"content": content.replace(b".05", b".06"), "capture_settings": {"s_parameter": "S22"}},
                    {"content": content, "capture_settings": {"s_parameter": "S11"}}):
        with pytest.raises(ProductionValidationError, match="другие данные"):
            record(setup, evidence_id=second_id, **common, **changed)


@pytest.mark.parametrize("verified", [False, True])
def test_s2vna_s22_header_retains_trace_identity_without_optional_settings(setup, verified):
    content = b"! S2VNA PLANAR,MODEL,SERIAL,1 2026-11-16T10:00:00 S22\n# MHz S RI R 50\n1000 .05 0\n3000 .05 0"
    values = dict(file_name="one.s1p", norm={**NORM, "requiredEnds": ["A"]},
                  evidence_id=uuid4() if verified else None, capture_verified=verified)
    measured = record(setup, content, **values)
    assert measured.verdict is VswrVerdict.FAIL and measured.traces[0].name == "S22"
    assert record(setup, content, **values).id == measured.id


def test_full_precision_peak_cannot_round_into_a_pass(setup):
    vswr = 1.35004
    measured = record(setup, s2p(s22=(vswr-1)/(vswr+1)))
    assert measured.verdict is VswrVerdict.FAIL
    assert measured.end_results[1]["verdict"] == "fail"
    assert measured.end_results[1]["max_vswr_raw"] == pytest.approx(vswr)
    assert measured.max_vswr == Decimal("1.3501")


def test_full_resolution_and_complex_evidence_survive_display_reduction(setup):
    content = s2p(frequencies=range(1000, 3001))
    measured = record(setup, content)
    with setup[0]() as uow:
        rows = list(uow.session.scalars(select(ProductionVswrTraceRow)))
        assert len(rows) == 4
        for row in rows:
            assert row.points == len(row.samples) == len(row.complex_samples) == 2001
            assert len(row.trace) <= 500
            assert row.samples[0][0] == row.complex_samples[0][0] == 1e9
        assert uow.vswr.file(measured.id)[1] == content


def test_historical_attempt_response_is_not_reinterpreted(setup):
    factory = setup[0]
    current = record(setup)
    legacy = replace(current, id=uuid4(), attempt=2, evidence_key=None, port_mapping=None, required_ends=(),
                     end_results=(), complete=None, traces=(), files=())
    before = _measurement_result(legacy, trace=True)
    with factory() as uow:
        uow.vswr.add(legacy, s2p())
        uow.commit()
    after = _measurement_result(latest_vswr(legacy.serial_id, factory()), trace=True)
    assert after == before
    assert "traces" not in after and "complete" not in after


def test_task_norm_recovers_one_end_from_pinned_assembly(setup):
    document = {"coaxVswrNorm": {"status": "undefined"}, "build_config": {"coax": {"sideB": {"article": "SMA"}}}}
    assert task_norm(document, None, None, CoaxRefs(None), setup[-1])["requiredEnds"] == ["B"]


def test_pinned_two_connector_topology_cannot_be_shortened_by_incomplete_norm_metadata(setup):
    document = {"coaxVswrNorm": {**NORM, "ends": NORM["ends"][:1]}, "build_config": {"coax": {
        "sideA": {"article": "SMA1"}, "sideB": {"article": "SMA2"},
    }}}
    assert task_norm(document, None, None, CoaxRefs(None), setup[-1])["requiredEnds"] == ["A", "B"]


@pytest.mark.parametrize("mapping", [{"1": "A"}, {"1": "A", "2": "A"}, {"1": "A", "2": "C"}])
def test_ambiguous_port_mapping_is_rejected_without_an_attempt(setup, mapping):
    with pytest.raises(ProductionValidationError, match="разные концы"):
        record(setup, port_mapping=mapping)
    with setup[0]() as uow:
        assert uow.session.scalar(select(func.count()).select_from(ProductionVswrMeasurementRow)) == 0


def one_port(end, gamma=.05, *, frequencies=(1000, 2000, 3000), **extra):
    content = ("# MHz S RI R 50\n" + "\n".join(f"{f} {gamma} 0" for f in frequencies)).encode()
    return VswrFileInput(end=end, file_name=f"{end}.s1p", content=content, **extra)


@pytest.mark.parametrize("b_gamma,verdict", [(.05, VswrVerdict.PASS), (.3, VswrVerdict.FAIL)])
def test_two_one_port_files_are_one_atomic_attempt_with_original_port_names(setup, b_gamma, verdict):
    a, b = one_port("A"), one_port("B", b_gamma)
    measured = record(setup, file_name="", files=(a, b))
    assert measured.verdict is verdict and measured.complete
    assert [(r["end"], r["trace_name"], r["verdict"]) for r in measured.end_results] == [
        ("A", "A:S11", "pass"), ("B", "B:S11", verdict.value),
    ]
    assert [trace.name for trace in measured.traces] == ["A:S11", "B:S11"]
    assert vswr_file(measured.id, setup[0](), "A") == (a.file_name, a.content)
    assert vswr_file(measured.id, setup[0](), "B") == (b.file_name, b.content)
    assert latest_vswr(measured.serial_id, setup[0]()) == measured
    with setup[0]() as uow:
        assert uow.session.scalar(select(func.count()).select_from(ProductionVswrMeasurementRow)) == 1


def test_pair_retries_ignore_file_order_and_names_but_include_assignment_and_mapping(setup):
    a, b = one_port("A"), one_port("B", .1)
    measured = record(setup, file_name="", files=(a, b))
    replay = record(setup, file_name="", files=(replace(b, file_name="renamed.s1p"), a))
    assert replay.id == measured.id and replay.attempt == 1
    swapped = record(setup, file_name="", files=(replace(a, end="B"), replace(b, end="A")))
    assert swapped.id != measured.id and swapped.attempt == 2
    remapped = record(setup, file_name="", files=(a, b), port_mapping={"1": "B", "2": "A"})
    assert remapped.id != measured.id and remapped.attempt == 3


@pytest.mark.parametrize("files,message", [
    ((one_port("A"), one_port("A", .1)), "два файла"),
    ((VswrFileInput(file_name="both.s2p", content=s2p()), one_port("B")), "нельзя объединять"),
    ((one_port(None),), "укажите конец"),
    ((one_port("A"), one_port("B"), one_port("A")), "не больше двух"),
])
def test_invalid_file_sets_never_create_partial_attempts(setup, files, message):
    with pytest.raises(ProductionValidationError, match=message):
        record(setup, file_name="", files=files)
    with setup[0]() as uow:
        assert uow.session.scalar(select(func.count()).select_from(ProductionVswrMeasurementRow)) == 0


def test_explicit_one_port_end_and_partial_pair_cannot_hide_missing_coverage(setup):
    b = one_port("B")
    single = record(setup, file_name="", files=(b,))
    assert single.verdict is VswrVerdict.FAIL and not single.complete
    assert "Не измерен конец А" in single.verdict_reason
    partial = record(setup, file_name="", files=(one_port("A", frequencies=(1500, 2500)), b))
    assert partial.verdict is VswrVerdict.FAIL and not partial.complete
    assert partial.end_results[0]["verdict"] == "incomplete"
    assert partial.end_results[1]["verdict"] == "pass"


def test_two_port_file_alone_in_list_keeps_the_port_mapping(setup):
    source = VswrFileInput(file_name="both.s2p", content=s2p(s22=.3))
    measured = record(setup, file_name="", files=(source,), port_mapping={"1": "B", "2": "A"})
    assert measured.verdict is VswrVerdict.FAIL
    assert [(r["end"], r["trace_name"]) for r in measured.end_results] == [("A", "S22"), ("B", "S11")]
    assert vswr_file(measured.id, setup[0](), "A") == vswr_file(measured.id, setup[0](), "B") == (source.file_name, source.content)


def test_pair_keeps_each_server_capture_identity_and_rejects_changed_source_bytes(setup):
    a, b = one_port("A", evidence_id=uuid4()), one_port("B", .1, evidence_id=uuid4())
    measured = record(setup, file_name="", files=(a, b))
    assert record(setup, file_name="", files=(b, a)).id == measured.id
    assert [file.file_meta["evidence_id"] for file in measured.files] == [str(a.evidence_id), str(b.evidence_id)]
    with pytest.raises(ProductionValidationError, match="другие данные"):
        record(setup, file_name="", files=(a, replace(b, content=one_port("B", .2).content)))


def test_one_server_acquisition_cannot_be_counted_as_both_ends(setup):
    source_id = uuid4()
    with pytest.raises(ProductionValidationError, match="Одно задание ВАЦ"):
        record(setup, file_name="", files=(one_port("A", evidence_id=source_id), one_port("B", evidence_id=source_id)))

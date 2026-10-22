from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.production.domain import ProductionValidationError
from app.modules.production.quality import InspectBoxCommand, RegisterInstrumentCommand, inspect_box, register_instrument
from app.modules.production.traceability import ProductionBox, SerialStatus, format_box_number
from app.modules.production.vswr import (
    TRACE_LIMIT,
    RecordVswrCommand,
    VswrVerdict,
    downsample,
    latest_vswr,
    list_vswr_for_serial,
    list_vswr_for_task,
    record_vswr,
    vswr_file,
)
from tests.production.test_materials_finished_goods import stage_three_setup

NORM = {"status": "ok", "reason": "", "vswrMax": 1.35, "bandMhz": [1000, 3000], "source": "ЭТК 591K.3.КБ1"}
UNDEFINED = {"status": "undefined", "reason": "Паспортной нормы для этого класса нет", "vswrMax": None}


def _csv(points: list[tuple[float, float]]) -> bytes:
    """Выгрузка ВАЦ: частота, МГц; КСВ с десятичной запятой."""
    lines = ["Частота, МГц;КСВН"] + [f"{mhz:g};{vswr:.4f}".replace(".", ",") for mhz, vswr in points]
    return "\n".join(lines).encode("utf-8")


@pytest.fixture
def setup():
    engine, _sessions, factory, master, operator, task, _line_id, now = stage_three_setup(fulfillment=False)
    instrument = register_instrument(RegisterInstrumentCommand(
        inventory_number="ВАЦ-01", name="Arinst VNA-DL", instrument_type="ВАЦ",
        verification_number="ПВ-2026-17", verified_at=now - timedelta(days=30),
        verification_due=now + timedelta(days=335), actor=master, now=now,
    ), factory())
    yield factory, master, operator, task, instrument, now
    engine.dispose()


def _record(setup, position: int, points, *, norm=NORM, **extra):
    factory, master, _operator, task, instrument, now = setup
    values = dict(
        task_id=task.id, position=position, instrument_id=instrument.id, file_name=f"sn{position}.csv",
        content=_csv(points), norm=norm, actor=master, now=now,
    )
    values.update(extra)
    return record_vswr(RecordVswrCommand(**values), factory())


def test_verdict_is_automatic_against_the_etk_norm_within_its_band(setup):
    factory, master, _operator, task, instrument, now = setup
    good = _record(setup, 1, [(900, 2.5), (1000, 1.10), (2400, 1.30), (3000, 1.20), (4000, 3.0)])
    bad = _record(setup, 2, [(1000, 1.10), (1800, 1.42), (3000, 1.20)])

    assert good.verdict is VswrVerdict.PASS and not good.verdict_manual
    assert (good.band_from_mhz, good.band_to_mhz) == (Decimal("1000.000"), Decimal("3000.000"))
    # Пики 2,5 и 3,0 вне полосы 1000–3000 МГц не считаются.
    assert good.max_vswr == Decimal("1.3000") and good.max_vswr_mhz == Decimal("2400.000")
    assert good.norm_vswr_max == Decimal("1.3500") and good.norm == NORM
    assert good.serial_number == f"{task.number}-0001" and good.attempt == 1
    assert good.points == 5 and good.instrument_id == instrument.id
    assert bad.verdict is VswrVerdict.FAIL and bad.max_vswr == Decimal("1.4200")

    assert latest_vswr(good.serial_id, factory()) == good
    assert [item.serial_number for item in list_vswr_for_task(task.id, factory())] == [
        f"{task.number}-0001", f"{task.number}-0002",
    ]
    assert vswr_file(good.id, factory()) == ("sn1.csv", _csv([(900, 2.5), (1000, 1.10), (2400, 1.30), (3000, 1.20), (4000, 3.0)]))


def test_explicit_band_overrides_the_norm_band_and_empty_band_is_refused(setup):
    measured = _record(setup, 1, [(1000, 1.1), (5000, 1.6), (6000, 1.2)], band_from_mhz=Decimal("4000"), band_to_mhz=Decimal("6000"))
    assert measured.max_vswr_mhz == Decimal("5000.000") and measured.verdict is VswrVerdict.FAIL
    with pytest.raises(ProductionValidationError, match="нет точек в полосе 7000–8000 МГц"):
        _record(setup, 1, [(1000, 1.1), (5000, 1.6)], band_from_mhz=Decimal("7000"), band_to_mhz=Decimal("8000"))
    with pytest.raises(ProductionValidationError, match="меньше конца"):
        _record(setup, 1, [(1000, 1.1), (5000, 1.6)], band_from_mhz=Decimal("3000"), band_to_mhz=Decimal("1000"))
    with pytest.raises(ProductionValidationError, match="полосу"):
        _record(setup, 1, [(1000, 1.1), (5000, 1.6)], norm={"status": "ok", "vswrMax": 1.3})


def test_undefined_norm_needs_a_manual_verdict_with_reason(setup):
    points = [(1000, 1.1), (2000, 1.5)]
    band = dict(band_from_mhz=Decimal("1000"), band_to_mhz=Decimal("2000"))
    with pytest.raises(ProductionValidationError, match="вручную"):
        _record(setup, 1, points, norm=UNDEFINED, **band)
    with pytest.raises(ProductionValidationError, match="причин"):
        _record(setup, 1, points, norm=UNDEFINED, verdict=VswrVerdict.PASS, reason="  ", **band)

    manual = _record(setup, 1, points, norm=UNDEFINED, verdict=VswrVerdict.PASS, reason="Согласовано с заказчиком: ≤1,6", **band)
    assert manual.verdict is VswrVerdict.PASS and manual.verdict_manual
    assert manual.verdict_reason == "Согласовано с заказчиком: ≤1,6" and manual.norm_vswr_max is None
    assert manual.norm["reason"] == UNDEFINED["reason"]


def test_defined_norm_refuses_a_contradicting_manual_verdict(setup):
    with pytest.raises(ProductionValidationError, match="ставится по замеру"):
        _record(setup, 1, [(1000, 1.1), (2000, 1.5)], verdict=VswrVerdict.PASS, reason="хочу")
    same = _record(setup, 1, [(1000, 1.1), (2000, 1.5)], verdict=VswrVerdict.FAIL)
    assert same.verdict is VswrVerdict.FAIL and not same.verdict_manual


def test_expired_instrument_is_refused(setup):
    factory, master, _operator, task, _instrument, now = setup
    old = register_instrument(RegisterInstrumentCommand(
        inventory_number="ВАЦ-OLD", name="ВАЦ", instrument_type="ВАЦ", verification_number="ПВ-1",
        verified_at=now - timedelta(days=400), verification_due=now - timedelta(days=1), actor=master, now=now,
    ), factory())
    with pytest.raises(ProductionValidationError, match="истекла"):
        _record(setup, 1, [(1000, 1.1), (2000, 1.2)], instrument_id=old.id)
    assert list_vswr_for_task(task.id, factory()) == []


def test_bad_file_size_position_and_garbage_are_refused(setup):
    with pytest.raises(ProductionValidationError, match="2 МБ"):
        _record(setup, 1, [(1000, 1.1)], content=b"1;1\n" * 600_000)
    with pytest.raises(ProductionValidationError, match="с 1 по 10"):
        _record(setup, 11, [(1000, 1.1), (2000, 1.2)])
    with pytest.raises(ProductionValidationError, match="не похож на выгрузку ВАЦ"):
        _record(setup, 1, [], content="Просто текст".encode())


def test_every_measurement_is_kept_and_the_latest_counts(setup):
    factory, *_rest = setup
    first = _record(setup, 3, [(1000, 1.5), (2000, 1.2)])
    second = _record(setup, 3, [(1000, 1.2), (2000, 1.25)])
    assert (first.attempt, second.attempt) == (1, 2)
    assert [item.id for item in list_vswr_for_serial(first.serial_id, factory())] == [first.id, second.id]
    assert latest_vswr(first.serial_id, factory()).verdict is VswrVerdict.PASS


def _qc_box(setup, quantity: int, accepted: int, rejected: int, sequence: int = 1):
    factory, master, operator, task, _instrument, now = setup
    box = ProductionBox.create(
        task_id=task.id, operation_id=task.operations[0].id, item=task.item,
        operation_position=1, operator_id=operator.id, created_at=now,
    ).add_output(Decimal(quantity))
    box = box.close(format_box_number(sequence), operator.name, now).confirm(master.name, now)
    with factory() as uow:
        uow.boxes.save(box)
        uow.commit()
    return inspect_box(InspectBoxCommand(
        box_id=box.id, expected_version=box.version, accepted=accepted, rejected=rejected,
        defect_character="КСВН" if rejected else "", defect_description="КСВН выше нормы" if rejected else "",
        instrument_ids=(), actor=master, now=now,
    ), factory())


def test_serial_failed_on_vswr_lands_among_the_rejected(setup):
    factory, _master, _operator, task, _instrument, _now = setup
    _record(setup, 1, [(1000, 1.1), (2000, 1.2)])
    _record(setup, 7, [(1000, 1.1), (2000, 1.8)])  # не годно
    _record(setup, 8, [(1000, 1.1), (2000, 1.9)])  # не годно, потом перемерено — годно
    _record(setup, 8, [(1000, 1.1), (2000, 1.2)])

    _box, _inspection, serials = _qc_box(setup, quantity=5, accepted=4, rejected=1)

    # Брак — сначала «не годно» по КСВН (№7); годные — сначала прошедшие замер (№1, №8), затем не мерянные по порядку.
    by_status = {item.serial_number[-4:]: item.status for item in serials}
    assert by_status == {
        "0001": SerialStatus.QC_ACCEPTED, "0002": SerialStatus.QC_ACCEPTED, "0003": SerialStatus.QC_ACCEPTED,
        "0008": SerialStatus.QC_ACCEPTED, "0007": SerialStatus.DEFECT,
    }
    with factory() as uow:
        free = [item.position for item in uow.serials.list_for_task(task.id) if item.current_box_id is None]
    assert free == [4, 5, 6, 9, 10]
    with pytest.raises(ProductionValidationError, match="уже прошло ОТК"):
        _record(setup, 7, [(1000, 1.1), (2000, 1.2)])


def test_without_measurements_qc_keeps_the_old_serial_order(setup):
    _box, _inspection, serials = _qc_box(setup, quantity=3, accepted=2, rejected=1)
    assert [(item.position, item.status) for item in serials] == [
        (1, SerialStatus.QC_ACCEPTED), (2, SerialStatus.QC_ACCEPTED), (3, SerialStatus.DEFECT),
    ]


def test_failed_serials_are_never_accepted_as_good(setup):
    for position in range(1, 11):
        _record(setup, position, [(1000, 1.1), (2000, 1.2 if position <= 8 else 1.9)])
    with pytest.raises(ProductionValidationError, match="только в брак"):
        _qc_box(setup, quantity=10, accepted=9, rejected=1)
    _box, _inspection, serials = _qc_box(setup, quantity=10, accepted=8, rejected=2, sequence=2)
    assert [item.position for item in serials if item.status is SerialStatus.DEFECT] == [9, 10]


def test_downsample_keeps_the_maximum_and_the_limit():
    points = [(1e6 * index, 1.1 + (index % 7) / 1000) for index in range(5000)]
    points[3333] = (points[3333][0], 4.2)
    thinned = downsample(points)
    assert len(thinned) <= TRACE_LIMIT
    assert (points[3333][0], 4.2) in thinned
    assert thinned == sorted(thinned)
    assert downsample(points[:10]) == points[:10]
    # Обязательная точка (максимум в полосе) остаётся, даже если в её отрезке есть выше.
    kept = downsample(points, keep=(3334,))
    assert points[3334] in kept and len(kept) <= TRACE_LIMIT


def test_real_arinst_export_is_recorded_with_sweep_parameters(setup):
    content = (Path(__file__).parent / "fixtures" / "arinst_vna_dl.s1p").read_bytes()
    measured = _record(
        setup, 1, [], content=content, file_name="arinst_1.s1p",
        norm={"status": "ok", "vswrMax": 1.8, "bandMhz": [1, 3000]},
    )
    assert measured.points == 1000 and measured.verdict is VswrVerdict.PASS
    assert measured.max_vswr == Decimal("1.7466") and abs(measured.max_vswr_mhz - Decimal("2274")) < 4
    assert measured.file_meta == {"start_hz": 1e6, "stop_hz": 3e9, "points": 1000, "if_bandwidth_hz": 1000}
    in_band = _record(setup, 2, [], content=content, file_name="arinst_1.s1p", norm=NORM)  # 1000–3000 МГц, норма 1,35
    assert in_band.verdict is VswrVerdict.FAIL
    factory, *_rest = setup
    assert latest_vswr(measured.serial_id, factory()).file_meta == measured.file_meta


def test_long_sweep_trace_is_thinned_but_keeps_the_in_band_peak(setup):
    sweep = [(1 + index * 0.5, 1.05 + (index % 5) / 100) for index in range(4000)]  # 1…2000,5 МГц
    sweep[1500] = (sweep[1500][0], 1.33)  # 751 МГц — пик в полосе 500–1000
    sweep[3000] = (sweep[3000][0], 2.0)  # 1501 МГц — вне полосы
    measured = _record(setup, 1, sweep, band_from_mhz=Decimal("500"), band_to_mhz=Decimal("1000"))
    assert measured.points == 4000 and len(measured.trace) <= TRACE_LIMIT
    assert measured.max_vswr == Decimal("1.3300") and measured.max_vswr_mhz == Decimal("751.000")
    assert (751.0, 1.33) in measured.trace and (1501.0, 2.0) in measured.trace
    factory, *_rest = setup
    assert latest_vswr(measured.serial_id, factory()).trace == measured.trace

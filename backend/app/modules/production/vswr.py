"""Замеры КСВН коаксиальных сборок по серийному номеру изделия (ОТК).

ОТК меряет каждую сборку на ВАЦ и прикладывает выгрузку прибора. ERP хранит каждый замер: какое изделие (серийный
номер задания), каким СИ (поверка не истекла), в какой полосе, наибольший КСВН в полосе и его частоту, норму из ЭТК
снимком на момент замера, вердикт, кто мерил и когда, сам файл (до 2 МБ) и трассу, прореженную до 500 точек так,
что максимум остаётся. Норма задана — вердикт ставится сам (КСВН ≤ нормы — годно); нормы нет — вердикт ставит
контролёр и пишет причину. Изделие можно перемерить: замеры не переписываются, действует последний.
Изделие с последним замером «не годно» при приёмке коробки ОТК уходит в брак первым (SqlAlchemySerialRepository.assign_qc).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
import json
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .domain import ProductionValidationError
from .traceability import SerialStatus
from .vna_files import VswrPoint, parse_vna_file, vna_header

if TYPE_CHECKING:
    from .quality import QualityUnitOfWork

VSWR_FILE_LIMIT = 2 * 1024 * 1024
TRACE_LIMIT = 500
NORM_LIMIT = 20_000  # символов JSON снимка нормы
_MHZ = Decimal("0.001")
_VSWR = Decimal("0.0001")


class VswrVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"


VERDICT_NAMES = {VswrVerdict.PASS: "Годно", VswrVerdict.FAIL: "Не годно"}


@dataclass(frozen=True)
class VswrMeasurement:
    id: UUID
    serial_id: UUID
    attempt: int
    instrument_id: UUID
    band_from_mhz: Decimal
    band_to_mhz: Decimal
    max_vswr: Decimal
    max_vswr_mhz: Decimal
    norm: dict
    norm_vswr_max: Decimal | None
    verdict: VswrVerdict
    verdict_manual: bool
    verdict_reason: str
    inspector_id: UUID
    inspector_name: str
    measured_at: datetime
    file_name: str
    points: int
    file_meta: dict = field(default_factory=dict)  # шапка выгрузки: start_hz, stop_hz, points, if_bandwidth_hz
    trace: tuple[tuple[float, float], ...] = field(default=(), repr=False)  # (МГц, КСВН)
    serial_number: str = ""


class VswrRepository(Protocol):
    def next_attempt(self, serial_id: UUID) -> int: ...
    def add(self, measurement: VswrMeasurement, content: bytes) -> None: ...
    def list_for_task(self, task_id: UUID) -> Sequence[VswrMeasurement]: ...
    def list_for_serial(self, serial_id: UUID) -> Sequence[VswrMeasurement]: ...
    def latest_for_serial(self, serial_id: UUID) -> VswrMeasurement | None: ...
    def file(self, measurement_id: UUID) -> tuple[str, bytes] | None: ...


# --- Чистые правила ------------------------------------------------------------------------------------------------


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def norm_limit(norm: Mapping[str, Any]) -> Decimal | None:
    """Наибольший допустимый КСВН из снимка нормы ЭТК; None — норма не задана (статус не «ok» или числа нет)."""
    status = str(norm.get("status") or "ok").strip().lower()
    if status != "ok":
        return None
    limit = _decimal(norm.get("vswrMax"))
    if limit is None or limit < 1:
        return None
    return limit.quantize(_VSWR, rounding=ROUND_HALF_UP)


def norm_band(norm: Mapping[str, Any]) -> tuple[Decimal, Decimal] | None:
    """Полоса нормы, МГц: `bandMhz` — [от, до] или {"from", "to"}."""
    band = norm.get("bandMhz")
    if isinstance(band, Mapping):
        low, high = _decimal(band.get("from")), _decimal(band.get("to"))
    elif isinstance(band, (list, tuple)) and len(band) == 2:
        low, high = _decimal(band[0]), _decimal(band[1])
    else:
        return None
    if low is None or high is None:
        return None
    return low, high


def validate_band(low: Decimal, high: Decimal) -> tuple[Decimal, Decimal]:
    low, high = Decimal(low).quantize(_MHZ), Decimal(high).quantize(_MHZ)
    if not low.is_finite() or not high.is_finite() or low <= 0 or high > Decimal("1000000"):
        raise ProductionValidationError("Полоса замера КСВН — от 0 до 1 000 000 МГц")
    if low >= high:
        raise ProductionValidationError("Начало полосы замера должно быть меньше конца")
    return low, high


def peak_index(points: Sequence[VswrPoint], low_mhz: Decimal, high_mhz: Decimal) -> int:
    """Индекс наибольшего КСВН среди точек полосы (границы включены; при равенстве — меньшая частота)."""
    low_hz, high_hz = float(low_mhz) * 1e6, float(high_mhz) * 1e6
    best: int | None = None
    for index, (frequency, vswr) in enumerate(points):
        if low_hz - 0.5 <= frequency <= high_hz + 0.5 and (best is None or vswr > points[best][1]):
            best = index
    if best is None:
        raise ProductionValidationError(
            f"В файле нет точек в полосе {_mhz_text(low_mhz)}–{_mhz_text(high_mhz)} МГц — проверьте полосу или файл",
        )
    return best


def downsample(points: Sequence[VswrPoint], limit: int = TRACE_LIMIT, keep: Sequence[int] = ()) -> list[VswrPoint]:
    """Не больше `limit` точек: из каждого отрезка — точка с наибольшим КСВН (огибающая пиков) и точки `keep`."""
    if len(points) <= limit:
        return list(points)
    chosen = {index for index in keep if 0 <= index < len(points)}
    buckets = limit - len(chosen)
    total = len(points)
    for bucket in range(buckets):
        start, end = bucket * total // buckets, (bucket + 1) * total // buckets
        if start < end:
            chosen.add(max(range(start, end), key=lambda index: points[index][1]))
    return [points[index] for index in sorted(chosen)]


def decide_verdict(
    max_vswr: Decimal, limit: Decimal | None, manual: VswrVerdict | None, reason: str,
) -> tuple[VswrVerdict, bool, str]:
    """(вердикт, поставлен вручную, причина): по норме — сам; без нормы — только рукой контролёра с причиной."""
    reason = str(reason or "").strip()
    if limit is not None:
        automatic = VswrVerdict.PASS if max_vswr <= limit else VswrVerdict.FAIL
        if manual is not None and manual is not automatic:
            raise ProductionValidationError(
                f"Норма КСВН {_vswr_text(limit)} задана — вердикт «{VERDICT_NAMES[automatic]}» ставится по замеру",
            )
        return automatic, False, reason
    if manual is None:
        raise ProductionValidationError("Норма КСВН в ЭТК не задана — поставьте вердикт вручную и укажите причину")
    if not reason:
        raise ProductionValidationError("Вердикт без нормы КСВН ставится с причиной — напишите, по чему решено")
    return manual, True, reason


def _mhz_text(value: Decimal) -> str:
    return format(value.normalize(), "f").replace(".", ",")


def _vswr_text(value: Decimal) -> str:
    return format(value.normalize(), "f").replace(".", ",")


# --- Сценарии ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordVswrCommand:
    task_id: UUID
    position: int  # порядковый номер изделия в задании: серийный номер «<ПЗ>-0001» — позиция 1
    instrument_id: UUID
    file_name: str
    content: bytes
    norm: Mapping[str, Any]
    actor: Actor
    now: datetime
    band_from_mhz: Decimal | None = None  # пусто — полоса из нормы (bandMhz)
    band_to_mhz: Decimal | None = None
    verdict: VswrVerdict | None = None  # ручной вердикт — только когда нормы нет
    reason: str = ""


def _norm_snapshot(norm: Mapping[str, Any]) -> dict:
    if not isinstance(norm, Mapping):
        raise ProductionValidationError("Норма КСВН передана не объектом")
    try:
        text = json.dumps(dict(norm), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise ProductionValidationError("Норму КСВН не сохранить: в ней не JSON-значения") from error
    if len(text) > NORM_LIMIT:
        raise ProductionValidationError("Снимок нормы КСВН слишком большой")
    return json.loads(text)


def record_vswr(command: RecordVswrCommand, uow: QualityUnitOfWork) -> VswrMeasurement:
    content = bytes(command.content or b"")
    if not content:
        raise ProductionValidationError("Приложите файл замера ВАЦ")
    if len(content) > VSWR_FILE_LIMIT:
        raise ProductionValidationError("Файл замера больше 2 МБ")
    file_name = str(command.file_name or "").strip()[:255]
    if not file_name:
        raise ProductionValidationError("У файла замера нет имени")
    norm = _norm_snapshot(command.norm)
    if command.band_from_mhz is None and command.band_to_mhz is None:
        band = norm_band(norm)
        if band is None:
            raise ProductionValidationError("Укажите полосу замера: в норме ЭТК её нет")
    elif command.band_from_mhz is None or command.band_to_mhz is None:
        raise ProductionValidationError("Укажите оба края полосы замера")
    else:
        band = (command.band_from_mhz, command.band_to_mhz)
    low, high = validate_band(*band)
    points = parse_vna_file(content, file_name)
    peak = peak_index(points, low, high)
    peak_hz, peak_vswr = points[peak]
    max_vswr = Decimal(str(peak_vswr)).quantize(_VSWR, rounding=ROUND_HALF_UP)
    # Частота пика — внутри полосы и после округления до кГц (CHECK в базе).
    max_mhz = min(max(Decimal(str(peak_hz / 1e6)).quantize(_MHZ, rounding=ROUND_HALF_UP), low), high)
    limit = norm_limit(norm)
    verdict, manual, reason = decide_verdict(max_vswr, limit, command.verdict, command.reason)
    global_peak = max(range(len(points)), key=lambda index: points[index][1])
    trace = tuple(
        (round(frequency / 1e6, 6), round(vswr, 4))
        for frequency, vswr in downsample(points, TRACE_LIMIT, keep=(peak, global_peak))
    )
    with uow:
        task = uow.production.get(command.task_id)
        if task is None:
            raise KeyError(f"Production task not found: {command.task_id}")
        if not 1 <= command.position <= task.quantity:
            raise ProductionValidationError(f"В задании {task.number} изделия с 1 по {task.quantity}")
        uow.serials.ensure_task(task.id, task.number, task.quantity)
        serial = uow.serials.by_position(task.id, command.position)
        if serial is None:
            raise KeyError(f"Serial not found: {task.number} #{command.position}")
        if serial.status is not SerialStatus.IN_PROGRESS:
            raise ProductionValidationError(
                f"Изделие {serial.serial_number} уже прошло ОТК («{serial.status.value}») — КСВН меряют до приёмки",
            )
        instrument = uow.instruments.get(command.instrument_id)
        if instrument is None:
            raise ProductionValidationError("Средство измерения не найдено")
        instrument.validate_for(command.now)
        measurement = VswrMeasurement(
            id=uuid4(), serial_id=serial.id, attempt=uow.vswr.next_attempt(serial.id),
            instrument_id=instrument.id, band_from_mhz=low, band_to_mhz=high,
            max_vswr=max_vswr, max_vswr_mhz=max_mhz, norm=norm, norm_vswr_max=limit,
            verdict=verdict, verdict_manual=manual, verdict_reason=reason,
            inspector_id=command.actor.id, inspector_name=command.actor.name,
            measured_at=command.now, file_name=file_name, points=len(points),
            file_meta=vna_header(content), trace=trace,
            serial_number=serial.serial_number,
        )
        uow.vswr.add(measurement, content)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=serial.id, event_type="production.vswr_recorded",
            actor=command.actor, occurred_at=command.now, details={
                "serial_number": serial.serial_number, "attempt": measurement.attempt,
                "instrument": instrument.inventory_number,
                "band_mhz": [str(low), str(high)], "max_vswr": str(max_vswr), "max_vswr_mhz": str(max_mhz),
                "norm_vswr_max": str(limit) if limit is not None else None,
                "verdict": verdict.value, "manual": manual, "file_name": file_name,
            },
        ))
        uow.commit()
        return measurement


def list_vswr_for_task(task_id: UUID, uow: QualityUnitOfWork) -> Sequence[VswrMeasurement]:
    """Все замеры задания: по позиции изделия, затем по попытке."""
    with uow:
        return uow.vswr.list_for_task(task_id)


def list_vswr_for_serial(serial_id: UUID, uow: QualityUnitOfWork) -> Sequence[VswrMeasurement]:
    """Все замеры изделия, первая попытка первой."""
    with uow:
        return uow.vswr.list_for_serial(serial_id)


def latest_vswr(serial_id: UUID, uow: QualityUnitOfWork) -> VswrMeasurement | None:
    """Действующий замер изделия — последняя попытка."""
    with uow:
        return uow.vswr.latest_for_serial(serial_id)


def vswr_file(measurement_id: UUID, uow: QualityUnitOfWork) -> tuple[str, bytes]:
    """Имя и байты приложенной выгрузки ВАЦ."""
    with uow:
        stored = uow.vswr.file(measurement_id)
    if stored is None:
        raise KeyError(f"VSWR measurement not found: {measurement_id}")
    return stored

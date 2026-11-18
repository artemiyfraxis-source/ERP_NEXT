"""Замеры КСВН коаксиальных сборок по серийному номеру изделия (ОТК).

ОТК меряет каждую сборку на ВАЦ и прикладывает выгрузку прибора. ERP хранит каждый замер: какое изделие (серийный
номер задания), каким СИ (поверка не истекла), в какой полосе, наибольший КСВН в полосе и его частоту, норму из ЭТК
снимком на момент замера, вердикт, кто мерил и когда, сам файл (до 2 МБ) и полные именованные трассы.
Для графика трассы прореживаются до 500 точек с сохранением пиков. Годно — когда все требуемые концы
измерены во всей полосе и укладываются в норму; S21/S12 сохраняются как справочные данные. Без нормы
полный замер оценивает контролёр с указанием причины. Изделие можно перемерить: действует последняя попытка;
повторная запись того же файла или серверного источника возвращает прежнюю попытку.
Изделие с последним замером «не годно» при приёмке коробки ОТК уходит в брак первым (SqlAlchemySerialRepository.assign_qc).
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .domain import ProductionValidationError
from .traceability import SerialStatus
from .vna_files import VnaTrace, VswrPoint, parse_vna_traces, vna_header

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

# СТО ФРАК-002-2026 п. 11.1, 11.5 (РЭ ч. 1 п. 3.3.3; РЭ ч. 2, табл. 73): калибровка, пересчитанная на другую сетку
# («К?» — интерполяция, «К!» — экстраполяция), и сглаживание трассы искажают КСВН — такой замер не принимается.
INTERPOLATED_CALIBRATION = {"C?": "К?", "RC?": "К?", "C!": "К!", "RC!": "К!"}


def capture_refusal(settings: Mapping[str, Any] | None) -> str:
    """Почему замер с этими настройками ВАЦ не принимается; "" — принимается (или настройки не прочитаны)."""
    if not isinstance(settings, Mapping):
        return ""
    status = INTERPOLATED_CALIBRATION.get(str(settings.get("correction_status") or ""))
    if status == "К?":
        return ("калибровка интерполирована («К?»): сетка замера не совпадает с сеткой калибровки — "
                "откалибруйте ВАЦ на полосе и точках замера и повторите")
    if status == "К!":
        return ("калибровка экстраполирована («К!»): полоса замера шире калиброванной — "
                "откалибруйте ВАЦ на полосе замера и повторите")
    if settings.get("smoothing_enabled") is True:
        return "включено сглаживание трассы: оно занижает пики КСВН — выключите сглаживание и повторите замер"
    return ""


@dataclass(frozen=True)
class VswrTrace:
    name: str
    quantity: str
    points: int
    trace: tuple[tuple[float, float], ...]
    # Full-resolution evidence is stored separately and is not loaded for lists/passports.
    samples: tuple[VswrPoint, ...] = field(default=(), repr=False, compare=False)
    complex_samples: tuple[tuple[float, float, float], ...] = field(default=(), repr=False, compare=False)


@dataclass(frozen=True)
class VswrFile:
    end: str
    file_name: str
    file_meta: dict = field(default_factory=dict)
    content: bytes = field(default=b"", repr=False, compare=False)


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
    evidence_key: str | None = None
    port_mapping: dict[str, str] | None = None
    required_ends: tuple[str, ...] = ()
    end_results: tuple[dict, ...] = ()
    complete: bool | None = None  # None identifies historical attempts, never re-evaluated.
    traces: tuple[VswrTrace, ...] = ()
    files: tuple[VswrFile, ...] = ()


class VswrRepository(Protocol):
    def lock_task(self, task_id: UUID) -> None: ...
    def by_evidence(self, serial_id: UUID, evidence_key: str) -> VswrMeasurement | None: ...
    def next_attempt(self, serial_id: UUID) -> int: ...
    def add(self, measurement: VswrMeasurement, content: bytes) -> None: ...
    def list_for_task(self, task_id: UUID) -> Sequence[VswrMeasurement]: ...
    def list_for_serial(self, serial_id: UUID) -> Sequence[VswrMeasurement]: ...
    def latest_for_serial(self, serial_id: UUID) -> VswrMeasurement | None: ...
    def file(self, measurement_id: UUID, end: str | None = None) -> tuple[str, bytes] | None: ...


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


def _peak_value(value: float, limit: Decimal | None) -> Decimal:
    precise = Decimal(str(value))
    rounded = precise.quantize(_VSWR, rounding=ROUND_HALF_UP)
    # Keep the existing 4dp summary schema without rounding a real exceedance into a pass.
    # A 1e-12 tolerance only removes floating-point noise in the gamma conversion.
    if limit is not None and precise > limit + Decimal("1e-12") and rounded <= limit:
        return limit + _VSWR
    return rounded


CUSTOMER_SOURCE = "customer"
_EDGE_HZ = 0.5  # Only tolerate sub-Hz export precision, not a shortened measurement band.


def customer_band(norm: Mapping[str, Any]) -> tuple[Decimal, Decimal] | None:
    """Полоса нормы заказчика (технолог задаёт её в ЭТК, владелец 23.10); норма по комплектующим — None."""
    return norm_band(norm) if norm.get("source") == CUSTOMER_SOURCE else None


def require_coverage(points: Sequence[VswrPoint], low: Decimal, high: Decimal) -> None:
    """Проверка всей требуемой полосы, независимо от источника нормы или измерения."""
    frequencies = [frequency for frequency, _vswr in points]
    if not frequencies or min(frequencies) > float(low) * 1e6 + _EDGE_HZ or max(frequencies) < float(high) * 1e6 - _EDGE_HZ:
        raise ProductionValidationError(
            f"Файл замера не покрывает полосу нормы {_mhz_text(low)}…{_mhz_text(high)} МГц",
        )


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
class VswrFileInput:
    file_name: str
    content: bytes
    end: str | None = None
    capture_settings: dict | None = None
    capture_verified: bool = False
    evidence_id: UUID | None = None


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
    capture_settings: dict | None = None
    capture_verified: bool = False
    port_mapping: Mapping[str, str] | None = None
    # Set only by a workflow after resolving owned, completed server evidence (capture/session).
    evidence_id: UUID | None = None
    files: tuple[VswrFileInput, ...] = ()


def required_ends(norm: Mapping[str, Any]) -> tuple[str, ...]:
    explicit = norm.get("requiredEnds")
    if explicit is not None:
        if (not isinstance(explicit, (list, tuple)) or not explicit
                or any(end not in ("A", "B") for end in explicit) or len(set(explicit)) != len(explicit)):
            raise ProductionValidationError("В норме неверно указаны требуемые концы сборки")
        return tuple(explicit)
    ends = norm.get("ends") or ()
    found = tuple(end for end in ("A", "B") if any(
        isinstance(item, Mapping) and item.get("side") == end and item.get("article") for item in ends
    ))
    # Absence of topology is not evidence that only one connector needs measuring.
    return found or ("A", "B")


def _port_mapping(value: Mapping[str, str] | None) -> dict[str, str]:
    mapping = dict(value) if value is not None else {"1": "A", "2": "B"}
    if set(mapping) != {"1", "2"} or set(mapping.values()) != {"A", "B"}:
        raise ProductionValidationError("Укажите разные концы А и Б для портов 1 и 2")
    return mapping


def _evaluate(
    parsed: tuple[VnaTrace, ...], norm: dict, mapping: dict[str, str], low: Decimal, high: Decimal,
    command: RecordVswrCommand, end_traces: Mapping[str, str] | None = None,
) -> tuple[tuple[dict, ...], bool, VswrVerdict, bool, str, Decimal, Decimal, tuple[VswrTrace, ...]]:
    limit = norm_limit(norm)
    results = []
    peaks: dict[str, int] = {}
    for end in required_ends(norm):
        name = ((end_traces or {}).get(end) or ("S11" if mapping["1"] == end else "S22"))
        source = next((item for item in parsed if item.name == name), None)
        result = {"end": end, "trace_name": name, "verdict": "incomplete", "complete": False,
                  "max_vswr": None, "max_vswr_mhz": None, "reason": ""}
        label = "А" if end == "A" else "Б"
        if source is None:
            result["reason"] = f"Не измерен конец {label}"
            results.append(result)
            continue
        try:
            require_coverage(source.points, low, high)
            result["complete"] = True
        except ProductionValidationError:
            result["reason"] = f"Конец {label}: файл не покрывает полосу нормы целиком"
        try:
            peak = peak_index(source.points, low, high)
        except ProductionValidationError:
            result["complete"] = False
            result["reason"] = f"Конец {label}: нет точек в требуемой полосе"
            results.append(result)
            continue
        peaks[name] = peak
        frequency, value = source.points[peak]
        maximum = _peak_value(value, limit)
        mhz = min(max(Decimal(str(frequency / 1e6)).quantize(_MHZ, rounding=ROUND_HALF_UP), low), high)
        result.update(max_vswr=float(maximum), max_vswr_raw=value, max_vswr_mhz=float(mhz))
        if limit is not None and maximum > limit:
            result.update(verdict="fail", reason=f"Конец {label}: КСВН выше нормы")
        elif result["complete"]:
            if limit is not None:
                result["verdict"] = "pass"
            elif command.verdict is not None:
                result["verdict"] = command.verdict.value
        results.append(result)
    complete = all(item["complete"] for item in results)
    measured = [item for item in results if item["max_vswr"] is not None]
    if measured:
        worst = max(measured, key=lambda item: item["max_vswr"])
        maximum, mhz = Decimal(str(worst["max_vswr"])), Decimal(str(worst["max_vswr_mhz"]))
    else:
        # A measured, non-required end is still evidence, but cannot make the attempt pass.
        primary = next(item for item in parsed if item.name.endswith(("S11", "S22")))
        peak = peak_index(primary.points, low, high)
        peaks[primary.name] = peak
        frequency, value = primary.points[peak]
        maximum = Decimal(str(value)).quantize(_VSWR, rounding=ROUND_HALF_UP)
        mhz = min(max(Decimal(str(frequency / 1e6)).quantize(_MHZ), low), high)
    if complete:
        verdict, manual, reason = decide_verdict(maximum, limit, command.verdict, command.reason)
    else:
        verdict, manual = VswrVerdict.FAIL, False
        reason = "; ".join(item["reason"] for item in results if item["reason"])
        if command.verdict is VswrVerdict.PASS:
            raise ProductionValidationError("Неполный замер нельзя принять: " + reason)
    traces = tuple(VswrTrace(
        name=item.name, quantity="vswr" if item.name.endswith(("S11", "S22")) else "magnitude",
        points=len(item.points),
        trace=tuple((round(f / 1e6, 6), round(v, 4)) for f, v in downsample(
            item.points, keep=((peaks[item.name],) if item.name in peaks else ()),
        )), samples=item.points, complex_samples=item.complex_points,
    ) for item in sorted(parsed, key=lambda item: item.name))
    return tuple(results), complete, verdict, manual, reason, maximum, mhz, traces


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


def _prepare_files(
    command: RecordVswrCommand, mapping: dict[str, str],
) -> tuple[tuple[VnaTrace, ...], tuple[VswrFile, ...], list[dict], dict[str, str]]:
    if command.files and (command.content or command.file_name or command.capture_settings is not None
                          or command.capture_verified or command.evidence_id is not None):
        raise ProductionValidationError("Передайте либо один файл замера, либо список файлов по концам")
    inputs = command.files or (VswrFileInput(
        file_name=command.file_name, content=command.content, capture_settings=command.capture_settings,
        capture_verified=command.capture_verified, evidence_id=command.evidence_id,
    ),)
    if not 1 <= len(inputs) <= 2:
        raise ProductionValidationError("В одной попытке допускается не больше двух файлов")
    source_ids = [item.evidence_id for item in inputs if item.evidence_id is not None]
    if len(source_ids) != len(set(source_ids)):
        raise ProductionValidationError("Одно задание ВАЦ нельзя использовать как два замера разных концов")
    parsed, files, signature, end_traces = [], [], [], {}
    for item in inputs:
        content = bytes(item.content or b"")
        if not content:
            raise ProductionValidationError("Приложите файл замера ВАЦ")
        if len(content) > VSWR_FILE_LIMIT:
            raise ProductionValidationError("Файл замера больше 2 МБ")
        file_name = str(item.file_name or "").strip()[:255]
        if not file_name:
            raise ProductionValidationError("У файла замера нет имени")
        if item.end not in (None, "A", "B"):
            raise ProductionValidationError("Для файла укажите конец А или Б")
        traces = parse_vna_traces(content, file_name)
        if len(traces) > 1:
            if len(inputs) != 1:
                raise ProductionValidationError("Файл .s2p нельзя объединять с другим файлом в одной попытке")
            if item.end is not None:
                raise ProductionValidationError("Для .s2p задайте привязку портов, а не один конец")
            ends = ("A", "B")
        else:
            source = traces[0]
            if item.capture_verified and item.capture_settings and item.capture_settings.get("s_parameter") == "S22":
                source = VnaTrace("S22", source.points, source.complex_points)
            if command.files and item.end is None:
                raise ProductionValidationError("Для каждого однопортового файла укажите конец А или Б")
            end = item.end or mapping["1" if source.name == "S11" else "2"]
            name = f"{end}:{source.name}" if command.files else source.name
            if end in end_traces:
                raise ProductionValidationError(f"Для конца {'А' if end == 'A' else 'Б'} передано два файла")
            end_traces[end] = name
            traces = (VnaTrace(name, source.points, source.complex_points),)
            ends = (end,)
        if refusal := capture_refusal(item.capture_settings):
            raise ProductionValidationError("Замер не принят: " + refusal)
        file_meta = vna_header(content)
        if item.capture_settings is not None:
            file_meta.update(capture_settings=item.capture_settings, capture_verified=item.capture_verified)
        content_hash = sha256(content).hexdigest()
        if item.evidence_id is not None:
            file_meta.update(evidence_id=str(item.evidence_id), content_sha256=content_hash,
                             capture_verified=item.capture_verified)
        file_meta["trace_names"] = [trace.name for trace in traces]
        files.extend(VswrFile(end, file_name, dict(file_meta), content) for end in ends)
        signature.append({
            "assigned_end": item.end, "ends": list(ends), "trace_names": file_meta["trace_names"], "content_sha256": content_hash,
            "evidence_id": str(item.evidence_id) if item.evidence_id is not None else None,
            "capture_verified": item.capture_verified,
            "capture_settings": item.capture_settings if item.evidence_id is not None else None,
        })
        parsed.extend(traces)
    return tuple(parsed), tuple(sorted(files, key=lambda file: file.end)), sorted(signature, key=lambda entry: entry["ends"]), end_traces


def record_vswr(
    command: RecordVswrCommand, uow: QualityUnitOfWork, *, manage_transaction: bool = True,
) -> VswrMeasurement:
    """Record an attempt; workflows may supply an entered UOW and own the single commit."""
    mapping = _port_mapping(command.port_mapping)
    parsed, files, signature, end_traces = _prepare_files(command, mapping)
    # Order and filenames do not change evidence identity; assignment to ends/ports does.
    identities = [{"end": entry["assigned_end"], "source": entry["evidence_id"] or entry["content_sha256"]} for entry in signature]
    evidence_key = sha256(json.dumps({"files": identities, "mapping": mapping}, sort_keys=True).encode()).hexdigest()
    with uow if manage_transaction else nullcontext(uow):
        # Serialize serial creation, duplicate lookup and attempt numbering on PostgreSQL.
        uow.vswr.lock_task(command.task_id)
        task = uow.production.get(command.task_id)
        if task is None:
            raise KeyError(f"Production task not found: {command.task_id}")
        if not 1 <= command.position <= task.quantity:
            raise ProductionValidationError(f"В задании {task.number} изделия с 1 по {task.quantity}")
        uow.serials.ensure_task(task.id, task.number, task.quantity)
        serial = uow.serials.by_position(task.id, command.position)
        if serial is None:
            raise KeyError(f"Serial not found: {task.number} #{command.position}")
        existing = uow.vswr.by_evidence(serial.id, evidence_key)
        if existing is not None:
            if existing.port_mapping != mapping or existing.instrument_id != command.instrument_id:
                raise ProductionValidationError("Этот файл уже сохранён с другой привязкой портов или СИ")
            if existing.file_meta.get("evidence_signature") != signature:
                raise ProductionValidationError("Сохранённое измерение этого источника содержит другие данные")
            return existing
        if serial.status is not SerialStatus.IN_PROGRESS:
            raise ProductionValidationError(
                f"Изделие {serial.serial_number} уже прошло ОТК («{serial.status.value}») — КСВН меряют до приёмки",
            )
        instrument = uow.instruments.get(command.instrument_id)
        if instrument is None:
            raise ProductionValidationError("Средство измерения не найдено")
        instrument.validate_for(command.now)
        norm = _norm_snapshot(command.norm)
        # A specified norm band always wins; callers cannot shorten the acceptance band.
        band = norm_band(norm)
        if band is None:
            if command.band_from_mhz is None or command.band_to_mhz is None:
                raise ProductionValidationError("Укажите полосу замера: в норме ЭТК её нет")
            band = (command.band_from_mhz, command.band_to_mhz)
        low, high = validate_band(*band)
        results, complete, verdict, manual, reason, max_vswr, max_mhz, traces = _evaluate(
            parsed, norm, mapping, low, high, command, end_traces,
        )
        limit = norm_limit(norm)
        primary_name = next((item["trace_name"] for item in results if item["max_vswr"] == float(max_vswr)), None)
        primary = next(item for item in traces if item.name == primary_name) if primary_name else next(
            item for item in traces if item.name.endswith(("S11", "S22")))
        primary_file = next(file for file in files if primary.name in file.file_meta["trace_names"])
        file_name, content = primary_file.file_name, primary_file.content
        file_meta = {key: value for key, value in primary_file.file_meta.items() if key != "trace_names"}
        file_meta["evidence_signature"] = signature
        if len(traces) > 1 or primary.name != "S11":
            file_meta["summary_trace"] = primary.name
        if any(entry["capture_verified"] for entry in signature) and not complete:
            file_meta["capture_warnings"] = [item["reason"] for item in results if not item["complete"]]
        measurement = VswrMeasurement(
            id=uuid4(), serial_id=serial.id, attempt=uow.vswr.next_attempt(serial.id),
            instrument_id=instrument.id, band_from_mhz=low, band_to_mhz=high,
            max_vswr=max_vswr, max_vswr_mhz=max_mhz, norm=norm, norm_vswr_max=limit,
            verdict=verdict, verdict_manual=manual, verdict_reason=reason,
            inspector_id=command.actor.id, inspector_name=command.actor.name,
            measured_at=command.now, file_name=file_name, points=primary.points,
            file_meta=file_meta, trace=primary.trace, serial_number=serial.serial_number,
            evidence_key=evidence_key, port_mapping=mapping, required_ends=required_ends(norm),
            end_results=results, complete=complete, traces=traces, files=files,
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
        if manage_transaction:
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


def vswr_file(measurement_id: UUID, uow: QualityUnitOfWork, end: str | None = None) -> tuple[str, bytes]:
    """Имя и байты приложенной выгрузки ВАЦ."""
    with uow:
        stored = uow.vswr.file(measurement_id, end)
    if stored is None:
        raise KeyError(f"VSWR measurement not found: {measurement_id}")
    return stored

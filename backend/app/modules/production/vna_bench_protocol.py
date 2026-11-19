"""VNA bench protocol v2 contract: typed screen, status, confidence, memory and release ops.

Protocol v1 (`vna_sessions.py`) is unchanged; v2 adds ops and extends the capture receipt. No instrument IO, SCPI
text, COM member names or file paths live here — the agent maps each typed op to its own fixed calls.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .vna_sessions import (ARGS, OPS, RESULTS, CaptureArgs, Captured, Digest, EmptyArgs, Profile, Result,
                           StrictModel, Trace, fingerprint, validate_result)

PROTOCOL_VERSIONS = (1, 2)
V2_OPS = ("device_status", "apply_screen", "acm_confidence", "store_memory", "release")
OPS_V2 = OPS + V2_OPS
SCREEN_TRACES_MAX = 16
MARKERS_MAX = 15
LIMIT_SEGMENTS_MAX = 20
CONFIDENCE_POINTS_MAX = 4001  # keeps Data+Memory arrays of four traces well inside the 3 MiB result request

Hz = Annotated[float, Field(gt=0, le=1e12)]
Level = Annotated[float, Field(ge=-1e6, le=1e6)]
Celsius = Annotated[float, Field(ge=-50, le=150)]
Serial = Annotated[str, Field(min_length=1, max_length=300, pattern=r"\S")]
Format = Literal["SWR", "MLOG", "PHAS", "GDEL", "SMIT"]
Complex = tuple[float, float]
Samples = Annotated[list[Complex], Field(min_length=2, max_length=CONFIDENCE_POINTS_MAX)]
Position = Annotated[int, Field(ge=1, le=SCREEN_TRACES_MAX, strict=True)]


class Scale(StrictModel):
    """Y scale of one graph: value per division, reference level and reference line (0..10 divisions)."""

    pdiv: float = Field(gt=0, le=1e6)
    rlev: Level | None = None
    rpos: int | None = Field(default=None, ge=0, le=10, strict=True)


class LimitSegment(StrictModel):
    kind: Literal["upper", "lower"]
    start_hz: Hz
    stop_hz: Hz
    start_value: Level
    stop_value: Level

    @model_validator(mode="after")
    def ordered(self):
        if self.start_hz >= self.stop_hz:
            raise ValueError("Отрезок линии допуска: начало должно быть меньше конца")
        return self


class ScreenTrace(StrictModel):
    param: Trace
    format: Format
    scale: Scale | Literal["auto"]
    hidden: bool = False
    memory: bool = False
    limits: list[LimitSegment] = Field(default_factory=list, max_length=LIMIT_SEGMENTS_MAX)

    @model_validator(mode="after")
    def scale_fits_format(self):
        if isinstance(self.scale, Scale):
            smith = self.format == "SMIT"
            if smith != (self.scale.rlev is None) or smith != (self.scale.rpos is None):
                raise ValueError("Шкала Вольперта-Смита задаётся только pdiv; остальные форматы — pdiv, rlev и rpos")
        if self.format == "SMIT" and self.limits:
            raise ValueError("Линии допуска на диаграмме Вольперта-Смита не задаются")
        return self


class Screen(StrictModel):
    split: int = Field(ge=1, le=SCREEN_TRACES_MAX, strict=True)
    traces: list[ScreenTrace] = Field(min_length=1, max_length=SCREEN_TRACES_MAX)
    markers_hz: list[Hz] = Field(default_factory=list, max_length=MARKERS_MAX)
    title: str = Field(default="", max_length=256, pattern=r"^[^\x00-\x1f\x7f]*$")

    @model_validator(mode="after")
    def ascending_markers(self):
        if any(a >= b for a, b in zip(self.markers_hz, self.markers_hz[1:])):
            raise ValueError("Маркеры задаются по возрастанию частоты без повторов")
        return self


def screen_hash(screen: Screen) -> str:
    """ERP identity of a requested screen; the agent stores the spec under it and only echoes it."""
    return fingerprint(screen.model_dump(mode="json"))


class ApplyScreenArgs(StrictModel):
    screen: Screen
    screen_hash: Digest

    @model_validator(mode="after")
    def hash_matches(self):
        if self.screen_hash != screen_hash(self.screen):
            raise ValueError("Отпечаток экрана не совпадает с его описанием")
        return self


class AcmConfidenceArgs(StrictModel):
    profile: Profile
    module_serial: Serial

    @model_validator(mode="after")
    def bounded_grid(self):
        if self.profile.points > CONFIDENCE_POINTS_MAX:
            raise ValueError(f"Доверительный тест АКМ — не более {CONFIDENCE_POINTS_MAX} точек")
        return self


class StoreMemoryArgs(StrictModel):
    screen_hash: Digest
    traces: list[Position] = Field(min_length=1, max_length=SCREEN_TRACES_MAX)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.traces)) != len(self.traces):
            raise ValueError("Графики повторяются")
        return self


class ReleaseArgs(StrictModel):
    screen_hash: Digest | None


class CaptureArgsV2(CaptureArgs):
    screen_hash: Digest


class Temperatures(StrictModel):
    """Unknown sensor readings stay null — never a fabricated 0 °C."""

    vna_c: list[Celsius | None] = Field(min_length=1, max_length=4)
    acm_c: Celsius | None = None


class AcmStatus(StrictModel):
    model: str = Field(min_length=1, max_length=100, pattern=r"\S")
    serial: Serial
    min_hz: float = Field(ge=0, le=1e12)
    max_hz: Hz

    @model_validator(mode="after")
    def band(self):
        if self.min_hz >= self.max_hz:
            raise ValueError("Некорректная полоса АКМ")
        return self


class DeviceStatus(Result):
    name: str = Field(min_length=1, max_length=500)
    ready: bool
    warmup_min: float | None = Field(ge=0, le=1e6)
    temperatures: Temperatures
    acm: AcmStatus | None
    correction: dict[Trace, Annotated[str, Field(pattern=r"^[A-Z?!]{1,8}$")]] = Field(max_length=4)
    screen_hash: Digest | None


class ScreenApplied(Result):
    screen: Screen
    screen_hash: Digest


class AcmConfidence(Result):
    module_serial: Serial
    frequencies_hz: list[Hz] = Field(min_length=2, max_length=CONFIDENCE_POINTS_MAX)
    data: dict[Trace, Samples] = Field(min_length=1, max_length=4)
    memory: dict[Trace, Samples] = Field(min_length=1, max_length=4)
    temperatures: Temperatures

    @model_validator(mode="after")
    def aligned(self):
        f = self.frequencies_hz
        if any(a >= b for a, b in zip(f, f[1:])):
            raise ValueError("Частоты доверительного теста не возрастают")
        if set(self.data) != set(self.memory):
            raise ValueError("Измеренные и сохранённые в АКМ трассы различаются")
        if any(len(s) != len(f) for s in (*self.data.values(), *self.memory.values())):
            raise ValueError("Длина трассы не совпадает с сеткой частот")
        return self


class MemoryStored(Result):
    screen_hash: Digest
    memorized: list[Position] = Field(min_length=1, max_length=SCREEN_TRACES_MAX)


class Released(Result):
    released: Literal[True]
    continuous: Literal[True]
    screen_hash: Digest | None


class CapturedV2(Captured):
    temperatures: Temperatures
    screen_hash: Digest


ARGS_V2 = {**ARGS, "capture": CaptureArgsV2, "device_status": EmptyArgs, "apply_screen": ApplyScreenArgs,
           "acm_confidence": AcmConfidenceArgs, "store_memory": StoreMemoryArgs, "release": ReleaseArgs}
RESULTS_V2 = {**RESULTS, "capture": CapturedV2, "device_status": DeviceStatus, "apply_screen": ScreenApplied,
              "acm_confidence": AcmConfidence, "store_memory": MemoryStored, "release": Released}


def command_args(protocol: int, op: str, raw: dict) -> dict:
    """Validated, canonical args of one command; unknown protocols and ops are refused."""
    table = {1: ARGS, 2: ARGS_V2}.get(protocol)
    if table is None or op not in table:
        raise ValueError(f"Операция {op!r} не входит в протокол vna_control версии {protocol}")
    return table[op].model_validate(raw).model_dump(mode="json")


def _near(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-3)


def validate_result_v2(op: str, args: dict, serial: str, raw: dict):
    """Protocol v2 receipt check; v1 ops other than capture keep their v1 rules unchanged."""
    if op not in RESULTS_V2:
        raise ValueError(f"Операция {op!r} не входит в протокол vna_control версии 2")
    if op in OPS and op != "capture":
        return validate_result(op, args, serial, raw)
    result = RESULTS_V2[op].model_validate(raw)
    if result.serial != serial:
        raise ValueError("Серийный номер прибора не совпадает с сессией")
    if op == "capture":
        if result.screen_hash != args["screen_hash"]:
            raise ValueError("Экран ВАЦ изменён после настройки из ERP")
        base_args = {k: v for k, v in args.items() if k != "screen_hash"}
        base_raw = {k: v for k, v in raw.items() if k not in ("temperatures", "screen_hash")}
        saved, artifacts = validate_result("capture", base_args, serial, base_raw)
        saved.update(temperatures=result.temperatures.model_dump(mode="json"), screen_hash=result.screen_hash)
        return saved, artifacts
    if op == "apply_screen":
        requested = ApplyScreenArgs.model_validate(args)
        if result.screen != requested.screen or result.screen_hash != requested.screen_hash:
            raise ValueError("Прочитанный экран ВАЦ не совпадает с заданным")
    elif op == "acm_confidence":
        profile = Profile.model_validate(args["profile"])
        if result.module_serial != args["module_serial"]:
            raise ValueError("Серийный номер АКМ не совпадает с сессией")
        f = result.frequencies_hz
        if (set(result.data) != set(profile.traces) or len(f) != profile.points
                or not _near(f[0], profile.start_hz) or not _near(f[-1], profile.stop_hz)):
            raise ValueError("Доверительный тест выполнен не на сетке калибровки")
    elif op == "store_memory":
        if result.screen_hash != args["screen_hash"] or sorted(result.memorized) != sorted(args["traces"]):
            raise ValueError("В память записаны не те графики")
    elif op == "release" and result.screen_hash != args["screen_hash"]:
        raise ValueError("После освобождения на ВАЦ не экран ERP")
    return result.model_dump(mode="json"), []

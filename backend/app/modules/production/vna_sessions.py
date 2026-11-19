"""ERP-owned VNA steps and bounded agent protocol. No instrument IO lives here."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

STATES = ("draft", "planned", "preparing", "awaiting_standard", "acquiring_standard",
          "saving_calibration", "awaiting_dut", "acquiring_dut", "review", "recorded",
          "failed", "aborting", "aborted", "recovery_required",
          "ready", "releasing")  # the last two: bench sessions only (vna_bench.py, 0205_vna_bench_sessions)
TERMINAL = ("recorded", "failed", "aborted")
OPS = ("prepare", "apply_profile", "begin_calibration", "measure_standard", "acm_calibrate",
       "save_calibration", "capture", "abort", "restore")
ARTIFACT_LIMIT = 2 * 1024 * 1024
Port = Literal[1, 2]
Trace = Literal["S11", "S22", "S21", "S12"]
Method = Literal["acm", "solt2", "solt1"]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SessionConflict(ValueError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Profile(StrictModel):
    start_hz: float = Field(gt=0, le=1e12)
    stop_hz: float = Field(gt=0, le=1e12)
    points: int = Field(ge=2, le=200001, strict=True)
    if_bandwidth_hz: float = Field(ge=1, le=1000)
    power_dbm: float = Field(ge=-30, le=0)
    impedance_ohm: Literal[50] = 50
    sweep: Literal["LIN"] = "LIN"
    smoothing: Literal[False] = False
    averaging: Literal[False] = False
    processing: Literal["off"] = "off"
    traces: list[Trace] = Field(min_length=1, max_length=4)
    vswr_limit: float = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def valid_band(self):
        if self.start_hz >= self.stop_hz or len(set(self.traces)) != len(self.traces):
            raise ValueError("Некорректная полоса или набор трасс")
        # Conservative full-resolution ASCII export budget (four complex traces).
        if self.points * (32 + 48 * len(self.traces)) + 4096 > ARTIFACT_LIMIT:
            raise ValueError("Полная трасса превышает 2 МиБ; число точек не уменьшено")
        return self


class Override(StrictModel):
    field: Literal["points", "if_bandwidth_hz", "power_dbm"]
    value: float
    reason: str = Field(min_length=3, max_length=500)


class EmptyArgs(StrictModel):
    pass


class ApplyArgs(StrictModel):
    profile: Profile


class Standard(StrictModel):
    kind: Literal["OPEN", "SHORT", "LOAD", "THRU", "ACM"]
    ports: list[Port] = Field(min_length=1, max_length=2)
    label: str = Field(min_length=1, max_length=300)
    kit_ports: list[Port] = Field(default_factory=list, max_length=2)


class CalibrationArgs(StrictModel):
    method: Method
    kit_id: UUID
    kit_fingerprint: Digest
    standards: list[Standard] = Field(min_length=1, max_length=8)
    orientation: Literal["both", "А", "Б"]


class MeasureArgs(StrictModel):
    standard: Standard


class AcmArgs(StrictModel):
    ports: tuple[Literal[1], Literal[2]]
    kit_ports: tuple[Port, Port]
    module_serial: str = Field(min_length=1, max_length=300, pattern=r"\S")
    start_hz: float = Field(gt=0, le=1e12)
    stop_hz: float = Field(gt=0, le=1e12)

    @model_validator(mode="after")
    def commissioned_two_port(self):
        if set(self.kit_ports) != {1, 2} or self.start_hz >= self.stop_hz:
            raise ValueError("Нужны оба порта АКМ и полная полоса измерения")
        return self


class SaveArgs(StrictModel):
    method: Method
    traces: list[Trace] = Field(min_length=1, max_length=4)


class CaptureArgs(StrictModel):
    profile: Profile
    orientation: Literal["both", "А", "Б"]
    port_to_end: dict[Literal["1", "2"], Literal["А", "Б"]] = Field(min_length=1, max_length=2)


ARGS = dict(prepare=EmptyArgs, apply_profile=ApplyArgs, begin_calibration=CalibrationArgs,
            measure_standard=MeasureArgs, acm_calibrate=AcmArgs, save_calibration=SaveArgs,
            capture=CaptureArgs, abort=EmptyArgs, restore=EmptyArgs)


class Result(StrictModel):
    serial: str = Field(min_length=1, max_length=300)


class Prepared(Result):
    ready: Literal[True]
    backup_id: UUID
    state_saved: Literal[True]
    kit_assignments_saved: Literal[True]


class Applied(Result):
    profile: Profile


class CalibrationBegun(Result):
    kit_fingerprint: Digest
    definition_hash: Digest


class Acquired(Result):
    acquired: Literal[True]


class AcmAcquired(Acquired):
    module_serial: str = Field(min_length=1, max_length=300, pattern=r"\S")
    module_min_hz: float = Field(ge=0, le=1e12)
    module_max_hz: float = Field(gt=0, le=1e12)


class CalibrationSaved(Result):
    method: Method
    correction: dict[Trace, Literal["COR"]] = Field(min_length=1, max_length=4)


class Artifact(StrictModel):
    file_name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}\.s[12]p$")
    content_base64: str = Field(min_length=4, max_length=2796204)
    traces: list[Trace] = Field(min_length=1, max_length=4)

    def content(self) -> bytes:
        try:
            content = base64.b64decode(self.content_base64, validate=True)
        except ValueError:
            raise ValueError("Некорректные байты Touchstone") from None
        if not content or len(content) > ARTIFACT_LIMIT:
            raise ValueError("Файл ВАЦ пуст или больше 2 МиБ")
        if len(set(self.traces)) != len(self.traces):
            raise ValueError("Трассы повторяются")
        if self.file_name.endswith(".s1p") and len(self.traces) != 1:
            raise ValueError("Файл .s1p содержит одну трассу")
        return content


class Captured(Applied):
    correction: dict[Trace, Literal["COR"]] = Field(min_length=1, max_length=4)
    artifacts: list[Artifact] = Field(min_length=1, max_length=4)


class Aborted(Result):
    stopped: Literal[True]
    calibration_cleared: Literal[True]


class Restored(Result):
    restored: Literal[True]


RESULTS = dict(prepare=Prepared, apply_profile=Applied, begin_calibration=CalibrationBegun,
               measure_standard=Acquired, acm_calibrate=AcmAcquired, save_calibration=CalibrationSaved,
               capture=Captured, abort=Aborted, restore=Restored)


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def resolved_profile(plan: dict, overrides: list[Override]) -> Profile:
    source = plan["profile"]
    sampling_problems = {"Не задана длина кабеля; требуется согласованное число точек",
                         "Не задан коэффициент укорочения кабеля; требуется согласованное число точек"}
    manual_points = any(o.field == "points" for o in overrides)
    if source["status"] != "ready" and not (manual_points and source["problems"]
                                              and set(source["problems"]).issubset(sampling_problems)):
        raise ValueError("Профиль P1 не готов: " + "; ".join(source["problems"]))
    profile = dict(start_hz=source["band_mhz"][0] * 1e6, stop_hz=source["band_mhz"][1] * 1e6,
                   points=source["points"], if_bandwidth_hz=source["if_bandwidth_hz"],
                   power_dbm=source["power_dbm"], vswr_limit=source["limit_line"]["vswr_max"],
                   traces=["S11"] if plan["chains"].get("mode") == "one_port_reversal" else ["S11", "S22", "S21", "S12"])
    seen = set()
    for override in overrides:
        if override.field in seen or not override.reason.strip():
            raise ValueError("Для каждой правки нужна одна непустая причина")
        seen.add(override.field)
        if override.field == "points":
            if not override.value.is_integer() or override.value < (source["points"] or 401):
                raise ValueError("Число точек нельзя уменьшать относительно плана")
            profile[override.field] = int(override.value)
        else:
            profile[override.field] = override.value
    return Profile.model_validate(profile)


def ordered_steps(plan: dict, profile: Profile, kit_definitions: dict[str, dict]) -> list[dict]:
    """Expand the P1 physical plan, including both through directions and one-port reversal."""
    if plan["chains"]["status"] == "blocked":
        raise ValueError("План подключения заблокирован")
    chains = plan["chains"]
    steps = []

    def add(op, args=None, *, human=False, label=""):
        steps.append(dict(op=op, args=ARGS[op].model_validate(args or {}).model_dump(mode="json"),
                          human=human, label=label))

    add("prepare")
    add("apply_profile", dict(profile=profile))
    if chains.get("mode") == "one_port_reversal":
        orientations = chains["orientations"]
        if [o["side"] for o in orientations] != ["А", "Б"] or any(not o.get("termination", {}).get("load_id") for o in orientations):
            raise ValueError("Нужны обе ориентации и явная согласованная нагрузка дальнего конца")
        calibrations = [(o["side"], o["calibration"]) for o in orientations]
    else:
        calibrations = [("both", chains["calibration_connections"])]
    for orientation, calibration in calibrations:
        raw = calibration["standards"]
        automatic = bool(raw) and all(s["kind"] == "АКМ" for s in raw)
        method = "acm" if automatic else "solt2" if orientation == "both" else "solt1"
        standards = []
        if automatic:
            if orientation != "both":
                raise ValueError("Однопортовая калибровка АКМ пока не проверена; нужен план SOLT1")
            # P1 names module ports explicitly; retain the instrument-to-module permutation.
            standards = [Standard(kind="ACM", ports=[s["port"] for s in raw], label="Подключите АКМ",
                                  kit_ports=[int(s["standard"].split()[1]) for s in raw])]
        else:
            for s in raw:
                for ports in ([s["ports"], list(reversed(s["ports"]))] if s["kind"] == "THRU" else [[s["port"]]]):
                    standards.append(Standard(kind=s["kind"], ports=ports, label=s["standard"]))
            expected = [(kind, [p]) for p in ([1, 2] if method == "solt2" else [1]) for kind in ("OPEN", "SHORT", "LOAD")]
            if method == "solt2":
                expected += [("THRU", [1, 2]), ("THRU", [2, 1])]
            if [(s.kind, s.ports) for s in standards] != expected:
                raise ValueError("План не содержит полного набора калибровочных шагов")
        kit_id = calibration["kit_id"]
        definition = kit_definitions[kit_id]
        add("begin_calibration", dict(method=method, kit_id=kit_id, kit_fingerprint=fingerprint(definition),
                                      standards=standards, orientation=orientation))
        for standard in standards:
            if automatic:
                module_serial = definition.get("serial_number", "").strip()
                if not module_serial:
                    raise ValueError("Укажите серийный номер АКМ в реестре")
                add("acm_calibrate", dict(ports=standard.ports, kit_ports=standard.kit_ports,
                    module_serial=module_serial, start_hz=profile.start_hz, stop_hz=profile.stop_hz),
                    human=True, label=standard.label)
            else:
                add("measure_standard", dict(standard=standard), human=True, label=standard.label)
        add("save_calibration", dict(method=method, traces=profile.traces))
        add("capture", dict(profile=profile, orientation=orientation,
                            port_to_end={"1": "А", "2": "Б"} if orientation == "both" else {"1": orientation}), human=True,
            label="Подключите изделие" if orientation == "both" else f"Измерьте сторону {orientation}; дальний конец — нагрузка 50 Ом")
    add("restore")
    return steps


def waiting_state(step: dict) -> str:
    return "awaiting_dut" if step["op"] == "capture" else "awaiting_standard"


def executing_state(op: str) -> str:
    if op in ("measure_standard", "acm_calibrate"):
        return "acquiring_standard"
    return {"save_calibration": "saving_calibration", "capture": "acquiring_dut",
            "restore": "acquiring_dut"}.get(op, "preparing")


def validate_result(op: str, args: dict, serial: str, raw: dict) -> tuple[dict, list[tuple[Artifact, bytes]]]:
    result = RESULTS[op].model_validate(raw)
    if result.serial != serial:
        raise ValueError("Серийный номер прибора не совпадает с сессией")
    if op in ("apply_profile", "capture") and result.profile != Profile.model_validate(args["profile"]):
        raise ValueError("Прочитанные настройки ВАЦ не совпадают с профилем")
    if op == "begin_calibration" and result.kit_fingerprint != args["kit_fingerprint"]:
        raise ValueError("Определение калибровочного набора изменилось")
    if op == "acm_calibrate":
        if result.module_serial != args["module_serial"]:
            raise ValueError("Серийный номер АКМ не совпадает с сессией")
        if not result.module_min_hz <= args["start_hz"] < args["stop_hz"] <= result.module_max_hz:
            raise ValueError("АКМ не покрывает полную полосу измерения")
    if op == "save_calibration" and (result.method != args["method"] or set(result.correction) != set(args["traces"])):
        raise ValueError("Не подтверждена полная коррекция всех трасс")
    artifacts = []
    if op == "capture":
        traces = [t for a in result.artifacts for t in a.traces]
        if set(result.correction) != set(args["profile"]["traces"]) or sorted(traces) != sorted(args["profile"]["traces"]):
            raise ValueError("Получены не все требуемые трассы")
        if len({a.file_name for a in result.artifacts}) != len(result.artifacts):
            raise ValueError("Имена файлов трасс повторяются")
        artifacts = [(a, a.content()) for a in result.artifacts]
        if sum(len(data) for _, data in artifacts) > ARTIFACT_LIMIT:
            raise ValueError("Суммарный объём трасс превышает 2 МиБ")
    saved = result.model_dump(mode="json")
    if artifacts:
        saved["artifacts"] = [dict(file_name=a.file_name, traces=a.traces,
                                    sha256=hashlib.sha256(data).hexdigest()) for a, data in artifacts]
    return saved, artifacts

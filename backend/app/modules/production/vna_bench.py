"""The VNA workplace session (purpose='bench', protocol v2): one ACM calibration, then many items.

СТО ФРАК-002-2026: калибровка АКМ (11.2) и её проверка (11.4: доверительный тест, LOAD на каждом порту), условия
действия калибровки (11.5: 4 ч, ±1 °C, те же приборы, тот же профиль, «Кор»), замер перехода (12.2, 13.1, 13.3 с
повтором 12.4 — три подключения с поворотом 120°), сборка (12.3, 13.2), проверка на изгиб (10.6) и контроль точности
(разд. 14: еженедельно СИ.009 по эталонной кривой, ежемесячно меры 6650F27).

Pure: the profile, ordered steps, item metadata and judgements. The workflow (`workflows/vna_bench_api.py`) owns the
cards, journal rows, matings and VSWR records; the repository owns the state machine.
Arinst helpers keep its diagnostic SOL/THRU acquisitions separate from Planar's ACM validity policy.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from app.shared.clock import as_utc
from .domain import ProductionValidationError
from .instrument_characterization import Application, Characterization, characterization
from .instrument_norms import Norm
from .traceability import InstrumentFile, InstrumentFileKind
from .vna_bench_protocol import ArinstProfile, Screen, command_args, screen_hash
from . import arinst_calibration as arinst_math
from .vna_checks import KitStandard, LOAD_LIMIT
from .vna_dsp import TwoPort, remove_fixtures, select_band, write_touchstone
from .vna_sessions import Profile, Standard, fingerprint
from .vna_screens import FULL_BAND_HZ, ScreenDef, screen as bench_screen

ITEM_KINDS = ("instrument", "assembly", "load", "flex", "control_standard", "kit_standards")
TARGETS = ("dut", "port1", "port2", "thru", "thru_bent")
VERDICTS = ("pass", "border", "fail", "not_judged")
ALL_TRACES = ("S11", "S21", "S12", "S22")
CALIBRATION_VALID = timedelta(hours=4)       # 11.5
TEMPERATURE_DRIFT_C = 1.0                    # 11.5, 4.2
REPEATS = 3                                  # 12.4: three connections rotated by 120°
KIT_LABEL = "6650F27"
VNA_MODEL = "S50180"
# Ops every bench session sends besides the v1 ones; the monthly kit check additionally needs `read_kit`.
SESSION_OPS = frozenset({"device_status", "apply_screen", "acm_confidence", "store_memory", "release"})
UNCALIBRATED = dict(calibrated_at=None, calibration_temps=None, profile_hash=None, calibration_check_id=None)
PORT_TO_END = {"1": "А", "2": "Б"}
ARINST_COMMISSIONING = ("Сохранение Arinst заблокировано: требуется стендовое сравнение "
                        "с подтверждёнными характеристиками калибровочных мер")
ARINST_RAW_EVIDENCE = "arinst-native-bench-2026-11-23"
CONNECT_ACM = "Подключите АКМ к концам обоих кабелей (порт 1 АКМ — к кабелю порта 1), не сдвигая кабели"
KIT_ORDER = tuple((kind, port) for port in (1, 2) for kind in ("OPEN", "SHORT", "LOAD")) + (("THRU", 0),)


def arinst_step(op, args=None, *, human=False, label="", item=None):
    return dict(op=op, args=command_args(3, op, args or {}), human=human, label=label, item=item)


def arinst_invalidated(snapshot, reason):
    """Keep immutable artifacts/history, remove every reusable calibration reference."""
    return {**snapshot, "calibration": dict(valid=False, status="required", reason=reason,
                                            coefficient_artifact_id=None)}


def arinst_license(state):
    """The native ArinstVL protocol needs no licence; anything else is simply not ready."""
    if state == "not_required":
        return dict(state="not_required", message="Связь: протокол ArinstVL")
    return dict(state="UNKNOWN", message="Связь с Arinst не подтверждена агентом ERP")


def arinst_calibration_steps(snapshot, *, orientation, transmission, run_id):
    profile = ArinstProfile.model_validate(snapshot["profile"]).model_dump(mode="json")
    standards = ("OPEN", "SHORT", "LOAD", "THRU") if transmission else ("OPEN", "SHORT", "LOAD")
    return [arinst_step("measure_standard", dict(profile=profile, orientation=orientation, standard=standard),
                       human=True, label=f"Подключите {standard} в плоскости измерения и подтвердите",
                       item=dict(kind="arinst_calibration", run_id=run_id)) for standard in standards]


def arinst_acquisition(receipt, evidence_id, context):
    """Use the validated native grid and bench-confirmed raw semantics in T3."""
    data = receipt["acquisition"]
    if (data.get("grid_confirmed") is not True or not data.get("frequencies_hz")
            or data.get("correction_origin") != "raw" or receipt.get("correction_origin") != "raw"):
        raise ValueError("Для калибровки Arinst нужны исходные данные с подтверждённой сеткой")
    parsed_profile = ArinstProfile.model_validate(receipt["profile"])
    profile = parsed_profile.model_dump(mode="json")
    if (context.instrument_uid != receipt["hardware_uid"] or context.firmware != receipt["firmware"]
            or context.profile_fingerprint != fingerprint(profile)
            or context.if_bandwidth_hz != profile["if_bandwidth_hz"]
            or context.orientation != data["orientation"]
            or data["frequencies_hz"] != parsed_profile.frequencies()):
        raise ValueError("Настройка Arinst изменилась после калибровки")
    return arinst_math.Acquisition(context, tuple(data["frequencies_hz"]),
        tuple(complex(*p) for p in data["data"]["S11"]), evidence_id,
        tuple(complex(*p) for p in data["data"]["S21"]) if "S21" in data["data"] else None,
        correction_origin="raw", raw_semantics_evidence=ARINST_RAW_EVIDENCE, grid_confirmed=True)


def arinst_json(value):
    """Lossless JSON representation of T3 dataclasses, including complex coefficients."""
    if isinstance(value, complex):
        return [value.real, value.imag]
    if isinstance(value, dict):
        return {k: arinst_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [arinst_json(v) for v in value]
    return value


def arinst_solve(snapshot, standards):
    """Map immutable acquisitions and server-owned characterized definitions to SOL/THRU.

    Browser input cannot set characterized standard coefficients. A numerical solution also needs
    a separate physical verification before it may authorize any quality result.
    """
    if not snapshot.get("calibration_context") or not snapshot.get("characterized_standards"):
        raise ValueError("Для калибровки Arinst нужны подтверждённые характеристики мер и плоскость измерения")
    context = arinst_math.CalibrationContext(**snapshot["calibration_context"])
    measured = []
    for receipt, artifact_id, artifact_hash in standards:
        if receipt["standard"] not in snapshot["characterized_standards"]:
            raise ValueError("Не подтверждены характеристики калибровочной меры Arinst")
        definition = dict(snapshot["characterized_standards"][receipt["standard"]])
        definition["response"] = tuple(complex(*v) for v in definition["response"])
        standard = arinst_math.CalibrationStandard(**definition)
        measured.append(arinst_math.StandardMeasurement(standard,
            arinst_acquisition(receipt, artifact_id, context)))
    thru = next((m for m in measured if m.standard.kind == "THRU"), None)
    solved = arinst_math.solve_sol([m for m in measured if m.standard.kind != "THRU"], thru=thru)
    return dict(schema="arinst.calibration.v1", coefficients=arinst_json(asdict(solved)),
                calibration_id=solved.calibration_id, method=solved.method,
                raw_artifacts=[dict(id=i, sha256=h) for _, i, h in standards],
                characterized_kit_sha256=fingerprint(snapshot["characterized_standards"]),
                operator=snapshot["operator"], verification=dict(status="required", passed=False),
                acceptance_eligible=False)


def adapter_profile() -> Profile:
    """Приложение А.1: the calibration grid of the «ОТК — переход» screen — 1601 points, 1 kHz IF, 0 dBm, four traces."""
    return Profile(start_hz=FULL_BAND_HZ[0], stop_hz=FULL_BAND_HZ[1], points=1601, if_bandwidth_hz=1000, power_dbm=0,
                   traces=list(ALL_TRACES), vswr_limit=LOAD_LIMIT)


def profile_hash(profile: Profile | dict) -> str:
    return fingerprint(Profile.model_validate(profile).model_dump(mode="json"))


def executing_state(op: str) -> str:
    return {"acm_calibrate": "acquiring_standard", "acm_confidence": "acquiring_standard",
            "save_calibration": "saving_calibration", "release": "releasing",
            "prepare": "preparing", "apply_profile": "preparing", "begin_calibration": "preparing",
            "abort": "aborting", "restore": "aborting"}.get(op, "acquiring_dut")


def step(op: str, args: dict | None = None, *, human: bool = False, label: str = "", item: dict | None = None) -> dict:
    return dict(op=op, args=command_args(2, op, args or {}), human=human, label=label, item=item)


def run(kind: str, **extra) -> dict:
    """A run: one item of the operator (a СИ, a КБ position, a check) or the calibration."""
    return dict(run_id=str(uuid4()), kind=kind, **extra)


def connection(run_item: dict, target: str, number: int = 1, group_id: str | None = None, **extra) -> dict:
    return {**run_item, "target": target, "connection_no": number, "group_id": group_id or str(uuid4()), **extra}


def fit_screen(screen: Screen, profile: Profile) -> Screen:
    """The screen the agent can put on this calibration: limit lines clipped to the swept band, markers inside it,
    and every captured trace present (hidden when the screen does not show it) — the agent refuses a capture
    whose profile traces are not on the ERP screen."""
    start, stop = profile.start_hz, profile.stop_hz
    traces = []
    for trace in screen.model_dump(mode="json")["traces"]:
        limits = []
        for segment in trace["limits"]:
            low, high = max(segment["start_hz"], start), min(segment["stop_hz"], stop)
            if high <= low:
                continue
            span = segment["stop_hz"] - segment["start_hz"]

            def at(hz, s=segment, span=span):
                return s["start_value"] + (s["stop_value"] - s["start_value"]) * (hz - s["start_hz"]) / span
            limits.append({**segment, "start_hz": low, "stop_hz": high, "start_value": at(low), "stop_value": at(high)})
        traces.append({**trace, "limits": limits})
    shown = {trace["param"] for trace in traces}
    traces += [dict(param=name, format="MLOG", scale="auto", hidden=True, memory=False, limits=[])
               for name in profile.traces if name not in shown]
    markers = [hz for hz in screen.markers_hz if start <= hz <= stop]
    return Screen.model_validate(dict(split=screen.split, traces=traces, markers_hz=markers, title=screen.title))


def screen_step(screen: Screen, item: dict | None = None) -> dict:
    return step("apply_screen", dict(screen=screen, screen_hash=screen_hash(screen)), item=item)


def capture_step(profile: Profile, hash_: str, label: str, item: dict, *, human: bool = True) -> dict:
    """A sweep. `human=False` when the operator's own click («Измерить», «Дальше») already confirmed the connection."""
    return step("capture", dict(profile=profile, orientation="both", port_to_end=PORT_TO_END, screen_hash=hash_),
                human=human, label=label, item=item)


def last_screen_hash(steps: list[dict]) -> str | None:
    return next((s["args"]["screen_hash"] for s in reversed(steps) if s["op"] == "apply_screen"), None)


def release_step(hash_: str | None) -> dict:
    return step("release", dict(screen_hash=hash_), item=dict(kind="close"))


def calibration_steps(snapshot: dict, *, acm_connected: bool = False, load: ScreenDef | None = None) -> list[dict]:
    """11.2 + 11.4: ACM calibration, its confidence test and LOAD on each port, on the session profile.
    `acm_connected` — the operator's click already says the ACM is on the cables (no separate confirmation);
    `load` — the stored «нагрузка» screen (production_vna_screens), the code preset without it."""
    profile, acm = Profile.model_validate(snapshot["profile"]), snapshot["acm"]
    calibration = run("calibration")
    load_screen = fit_screen(bench_screen(load or "load_check"), profile)
    hash_ = screen_hash(load_screen)
    loads = run("load", calibration_run=calibration["run_id"])
    standard = Standard(kind="ACM", ports=[1, 2], label="Подключите АКМ", kit_ports=[1, 2])
    return [
        step("begin_calibration", dict(method="acm", kit_id=acm["id"], kit_fingerprint=fingerprint(acm["definition"]),
                                       standards=[standard], orientation="both"), item=calibration),
        step("acm_calibrate", dict(ports=[1, 2], kit_ports=[1, 2], module_serial=acm["serial"], start_hz=profile.start_hz,
                                   stop_hz=profile.stop_hz), human=not acm_connected, label=CONNECT_ACM, item=calibration),
        step("save_calibration", dict(method="acm", traces=profile.traces), item=calibration),
        step("acm_confidence", dict(profile=profile, module_serial=acm["serial"]), item=calibration),
        screen_step(load_screen, calibration),
        capture_step(profile, hash_, "Отключите АКМ, не сдвигая кабели. Поставьте LOAD на конец кабеля порта 1",
                     connection(loads, "port1")),
        capture_step(profile, hash_, "Перенесите LOAD на конец кабеля порта 2", connection(loads, "port2")),
    ]


def session_steps(snapshot: dict, *, load: ScreenDef | None = None) -> list[dict]:
    profile = Profile.model_validate(snapshot["profile"])
    return [step("prepare"), step("apply_profile", dict(profile=profile)), *calibration_steps(snapshot, load=load)]


def item_steps(profile: Profile, screen: Screen, capture_label: str | None, item: dict, target: str = "dut",
               **capture_extra) -> list[dict]:
    """A new item: read the device (temperatures, ACM, «Кор»), put its screen, then — when `capture_label` is given —
    the first sweep at once (the operator clicked «Измерить» with the item connected)."""
    screen = fit_screen(screen, profile)
    steps = [step("device_status", item=item), screen_step(screen, item)]
    if capture_label is not None:
        steps.append(capture_step(profile, screen_hash(screen), capture_label, connection(item, target, **capture_extra),
                                  human=False))
    return steps


def run_meta(item: dict) -> dict:
    """The run part of a step's item (without the connection keys)."""
    return {k: v for k, v in item.items() if k not in ("target", "connection_no", "group_id", "standard", "port", "definition")}


def repeat_label(number: int, what: str) -> str:
    return (f"Отключите {what}, поверните разъём примерно на 120° и подключите снова — подключение {number} из {REPEATS}. "
            "Кабели не двигайте")


# ---- gates (СТО 11.5) ------------------------------------------------------------------------------------------------

# Operator-facing reasons and the stable codes the screen translates (frontend/src/vnaBenchApi.ts REFUSALS).
REFUSAL_CODES = (
    ("Нет действующей калибровки", "calibration_expired"),
    ("Калибровка старше 4 часов", "calibration_expired"),
    ("Температура ВАЦ ушла", "temperature_drift"),
    ("Температура ВАЦ или АКМ не прочитана", "temperature_unknown"),
    ("Температура не прочитана", "temperature_unknown"),
    ("ВАЦ не готов", "not_ready"),
    ("АКМ заменён или не подключён", "acm_missing"),
    ("Статус коррекции не «Кор»", "correction_lost"),
    ("Профиль сессии изменён", "profile_changed"),
    ("Доверительный тест АКМ возможен до", "points_over_limit"),
)


def refusal_code(message: str, default: str = "refused") -> str:
    return next((code for prefix, code in REFUSAL_CODES if message.startswith(prefix)), default)

def aware(moment: datetime, now: datetime) -> datetime:
    """SQLite drops the zone of a stored moment: it was written in the clock's zone."""
    return moment.replace(tzinfo=now.tzinfo) if moment.tzinfo is None and now.tzinfo is not None else moment


def calibration_refusal(calibrated_at: datetime | None, now: datetime) -> str:
    if calibrated_at is None:
        return "Нет действующей калибровки — выполните калибровку АКМ и её проверку"
    age = as_utc(now) - as_utc(aware(calibrated_at, now))
    if age < timedelta(0) or age > CALIBRATION_VALID:
        return "Калибровка старше 4 часов или часы изменены — откалибруйте заново (СТО ФРАК-002-2026 п. 11.5)"
    return ""


def temperature_refusal(calibration: dict | None, temperatures: dict | None) -> str:
    """Every VNA sensor within ±1 °C of the calibration; an unknown reading (null) refuses, never passes."""
    if not calibration or not temperatures:
        return "Температура не прочитана — откалибруйте заново"
    before, now = calibration.get("vna_c") or [], temperatures.get("vna_c") or []
    if (not before or len(before) != len(now) or any(v is None for v in (*before, *now))
            or calibration.get("acm_c") is None or temperatures.get("acm_c") is None):
        return "Температура ВАЦ или АКМ не прочитана — замер не принимается, откалибруйте заново"
    if any(abs(a - b) > TEMPERATURE_DRIFT_C + 1e-9 for a, b in zip(before, now)):
        return "Температура ВАЦ ушла от калибровочной больше чем на 1 °C — откалибруйте заново (СТО п. 11.5)"
    return ""


def device_refusal(status: dict, acm_serial: str, traces) -> str:
    """The pre-item reading of the device (protocol v2 `device_status`)."""
    if not status.get("ready"):
        return "ВАЦ не готов (строка состояния S2VNA не «Готов»)"
    acm = status.get("acm") or {}
    if acm.get("serial") != acm_serial:
        return "АКМ заменён или не подключён по USB — откалибруйте заново"
    correction = status.get("correction") or {}
    bad = [name for name in traces if correction.get(name) != "COR"]
    if bad:
        return f"Статус коррекции не «Кор» на {', '.join(bad)} — работа при «К?»/«К!» запрещена, откалибруйте заново"
    return ""


# ---- СИ (переход) --------------------------------------------------------------------------------------------------

def series_fmax_hz(name: str) -> float:
    """12.2.3: SMA, 3,5 мм and N — 18 GHz; TNC — 11 GHz; BNC — 4 GHz (the bench program's name rule)."""
    if re.search(r"(?i)\bBNC\b|\b(?:SB|TB|B)-\d", name or ""):
        return 4e9
    if re.search(r"(?i)\bTNC\b|\b(?:ST|TN|T)-\d", name or ""):
        return 11e9
    return 18e9


def fmax_hz(name: str, series_cap_ghz: float | None, norm: Norm | None, profile: Profile,
            requested_ghz: float | None = None) -> float:
    """The upper frequency of an item: the series, the norm band and the swept band; the operator may only lower it."""
    top = min(series_fmax_hz(name), profile.stop_hz)
    if series_cap_ghz:
        top = min(top, series_cap_ghz * 1e9)
    if norm is not None:
        top = min(top, norm.to_ghz * 1e9)
    if requested_ghz is not None:
        if not math.isfinite(requested_ghz) or requested_ghz * 1e9 > top + 1 or requested_ghz <= 0.01:
            raise ProductionValidationError(f"Верхняя частота должна быть больше 0,01 ГГц и не выше {top / 1e9:g} ГГц")
        top = requested_ghz * 1e9
    if top <= profile.start_hz:
        raise ProductionValidationError("Верхняя частота ниже начала полосы калибровки")
    return top


def reversed_two_port(data: TwoPort) -> TwoPort:
    """A fixture standing reversed: its side 2 faces VNA port 1."""
    return TwoPort(data.frequencies, data.s22, data.s12, data.s21, data.s11)


def adapter_data(raw: TwoPort, top_hz: float, left: TwoPort | None, right: TwoPort | None) -> TwoPort:
    """The item itself: the sweep cut at its upper frequency, fixtures removed on the very same grid (T-matrices)."""
    def cut(data):
        return select_band(data, top_hz) if data is not None else None
    return remove_fixtures(cut(raw), cut(left), cut(right))


def characterize(file_name: str, content: bytes, instrument_id, operator: dict, now: datetime) -> Characterization:
    file = InstrumentFile(id=uuid4(), instrument_id=instrument_id, kind=InstrumentFileKind.OTHER, file_name=file_name,
                          size=len(content), uploaded_at=as_utc(now), uploaded_by=UUID(str(operator["id"])),
                          uploaded_by_name=operator["name"], content=content)
    measured = characterization(file)
    if measured is None:
        raise ProductionValidationError("Файл замера не читается как двухпортовый замер ВАЦ")
    return measured


def verdict_of(application: Application) -> str:
    """13.3 over VSWR and loss: any clear excess fails, a point within ±U is «на границе» (repeat), else pass;
    without a norm or below the VNA accuracy there is no conclusion."""
    statuses = {a.status for a in (application.check, application.loss_check) if a is not None}
    for status in ("fail", "border", "pass"):
        if status in statuses:
            return status
    return "not_judged"


def application_view(application: Application) -> dict:
    check, loss = application.check, application.loss_check
    return dict(fit_ghz=application.fit_ghz, limit_ghz=application.limit_ghz, range_line=application.range_line,
                vswr_status=check.status if check else None, vswr_line=check.brief if check else "",
                vswr_uncertainty=check.uncertainty if check else None,
                loss_status=loss.status if loss else None, loss_line=loss.brief if loss else "",
                bands=[dict(stop_ghz=s.stop_ghz, vswr=_finite(s.vswr), loss_db=_finite(s.loss_db)) for s in application.bands])


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


# ---- 6650F27 (разд. 14, ежемесячно) ---------------------------------------------------------------------------------

def kit_standard(definition: dict) -> KitStandard:
    return KitStandard(definition["type"], definition["delay"], definition["loss"], definition["z0"],
                       tuple(definition["c"]), tuple(definition["l"]), definition.get("label", ""))


def kit_choice(standards: list[dict], selection: dict[str, str]) -> list[tuple[str, int, dict]]:
    """Which standard is connected where: OPEN, SHORT, LOAD on ports 1 and 2, then a thru. One candidate of a type is
    taken as is; several (both genders, MA6666A/B/C) need the operator's choice by the gender of the cable ends."""
    plan = []
    for kind, port in KIT_ORDER:
        candidates = [s for s in standards if s["type"] == kind]
        if not candidates:
            raise ProductionValidationError(f"В описании набора нет меры {kind}")
        key = kind if kind == "THRU" else f"{kind}_{port}"
        chosen = selection.get(key)
        if chosen is not None:
            candidates = [s for s in candidates if s.get("label") == chosen]
            if len(candidates) != 1:
                raise ProductionValidationError(f"Мера «{chosen}» для {key} не найдена в наборе (или не единственная)")
        elif len(candidates) > 1:
            labels = ", ".join(s.get("label") or "без названия" for s in candidates)
            raise ProductionValidationError(f"Выберите меру {key} по полу разъёма на конце кабеля: {labels}")
        plan.append((kind, port, candidates[0]))
    return plan


def kit_label(kind: str, port: int, standard: dict, number: int) -> str:
    where = f"к порту {port}" if port else "между концами кабелей"
    return (f"Подключите {standard.get('label') or kind} ({kind}) {where} — подключение {number} из {REPEATS}, "
            "каждый раз с поворотом на 120°")

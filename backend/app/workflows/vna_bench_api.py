"""«ВАЦ — рабочее место» from ERP: a bench session (purpose='bench', protocol v2), one ACM calibration, many items.

S3: open a session (the station: S2VNA card, its PC, its АКМ and two test cables) → profile → «подключите АКМ» →
acm_calibrate → доверительный тест (judged here, СТО ФРАК-002-2026 п. 11.4) → LOAD on port 1 and 2 (judged here) →
journal row (form В.1) → calibrated. Before every item the calibration must still hold (п. 11.5): < 4 h, ±1 °C, the
same ВАЦ/АКМ, the same profile, «Кор»; a failed weekly/monthly control stops measurements (разд. 14).
S4: items — a СИ (переход, 12.2/13.1/13.3, «на границе» → 12.4 three connections, «Записать в карточку»), a КБ
position (12.3 through the ОТК VSWR record), and checks — bend (10.6), weekly control standard, monthly 6650F27.

The screen's contract is `frontend/src/vnaBenchApi.ts` (S5): every POST queues agent work and returns the state at once,
the screen polls `GET state`; refusals are `detail: {code, message}`. Each accepted capture is judged in the agent's
result request in the same unit of work as its receipt (journal row, matings, VSWR attempt, the session's next step).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import re
from typing import Annotated, Callable, Literal
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.modules.production import vna_bench as bench
from app.modules.production.api import techcards_uow_factory
from app.modules.production.autoplan_api import references_uow_factory
from app.modules.production.domain import ProductionValidationError
from app.modules.production.infrastructure import (ProductionInstrumentFileRow, ProductionInstrumentRow,
                                                   ProductionSerialRow, ProductionTaskRow)
from app.modules.production.instrument_characterization import application, series_cap_ghz
from app.modules.production.instrument_norms import loss_uncertainty_for, norm_for, uncertainty_for, vna_model
from app.modules.production.mating_api import record_batch
from app.modules.production.matings import end_names
from app.modules.production.rf_infrastructure import RfRepository
from app.modules.production.traceability import InstrumentFile, InstrumentFileKind
from app.modules.production.vna_bench_protocol import Screen, screen_hash
from app.modules.production.vna_check_infrastructure import VALUE_FIELDS, VnaCheckRepository, VnaCheckRow
from app.modules.production.vna_checks import (LOAD_LIMIT, KitTolerance, assert_confidence_points, confidence_from_receipt,
                                               control_standard, expected_verdict, flex, journal_number,
                                               kit_standard as judge_kit_standard, load_vswr, same_grid)
from app.modules.production.vna_dsp import TRACES, TwoPort, VnaDataError, read_touchstone, select_band, summary, vswr, write_touchstone
from app.modules.production.vna_screens import item_limits, number_text, screen as bench_screen
from app.modules.production.vna_session_infrastructure import VnaCommandRow, VnaSessionRepository, VnaSessionRow
from app.modules.production.vna_sessions import TERMINAL, Profile, SessionConflict, StrictModel, resolved_profile
from app.modules.production.vswr import RecordVswrCommand, VswrFileInput, record_vswr
from app.modules.warehouse import vna_jobs
from app.modules.warehouse.devices import DeviceRow, VnaBindingRow, VnaControlRow
from app.modules.warehouse.scan_agents import LIVE_FOR, ScanAgentRow
from app.modules.warehouse.scan_jobs import ScanJobClosed
from app.shared.clock import as_utc
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from .coax_vswr_api import CoaxRefs, _task_etk, task_norm
from .vna_capture_instrument import capture_instrument
from .vna_measurement_api import Factory, Now, Person, _expire
from .vna_plan_api import vna_plan

router = APIRouter(tags=["production"])
PREFIX = "/api/production/vna-bench"
Techcards = Annotated[Callable | None, Depends(techcards_uow_factory)]
Refs = Annotated[Callable | None, Depends(references_uow_factory)]
MEASURING = ("instrument", "assembly")       # stopped by a failed weekly/monthly control (разд. 14)
ITEMS = ("instrument", "assembly")
CHECKS = {"flex": "flex", "weekly": "control_standard", "monthly": "kit_standards"}
CHECK_NAMES = {v: k for k, v in CHECKS.items()}
JUDGE_ERRORS = (ProductionValidationError, VnaDataError, ValueError, KeyError)
VERDICT_CODES = {"pass": "pass", "border": "border", "fail": "fail", "not_judged": "not_assessed"}
OP_LABELS = {"prepare": "сохранение настроек оператора", "apply_profile": "профиль стимула",
             "begin_calibration": "подготовка калибровки", "acm_calibrate": "калибровка АКМ",
             "save_calibration": "проверка коррекции «Кор»", "acm_confidence": "доверительный тест АКМ",
             "apply_screen": "экран ERP на приборе", "device_status": "чтение прибора", "capture": "развёртка",
             "store_memory": "запоминание графиков", "read_kit": "чтение описания мер", "release": "закрытие сеанса",
             "abort": "остановка", "restore": "возврат настроек оператора"}
FLEX_PHASES = (("load_p1", "LOAD на кабеле порта 1, кабель изгибается", "port1"),
               ("load_p2", "LOAD на кабеле порта 2, кабель изгибается", "port2"),
               ("thru_memory", "Перемычка, кабели в рабочем положении — графики запоминаются", "thru"),
               ("thru_flex", "Перемычка, кабели изогнуты", "thru_bent"))


class Refusal(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _agent_code(message: str) -> str:
    if "версии 1" in message or "не поддерживает рабочее место" in message:
        return "agent_old"
    if "серийный" in message.lower():
        return "serial_changed"
    if "занят" in message or "Сначала завершите" in message:
        return "device_busy"
    return "agent_offline"


@contextmanager
def _refusals():
    try:
        yield
    except Refusal as error:
        raise HTTPException(error.status, {"code": error.code, "message": error.message}) from error
    except HTTPException:
        raise
    except KeyError as error:
        raise HTTPException(404, {"code": "not_found", "message": str(error.args[0]) if error.args else "Не найдено"}) from error
    except ScanJobClosed as error:
        raise HTTPException(409, {"code": _agent_code(str(error)), "message": str(error)}) from error
    except SessionConflict as error:
        text = str(error)
        code = "stale" if "изменилась" in text else bench.refusal_code(text, "conflict")
        raise HTTPException(409, {"code": code, "message": text}) from error
    except (ValueError, ProductionValidationError) as error:
        raise HTTPException(422, {"code": bench.refusal_code(str(error), "invalid"), "message": str(error)}) from error


# ---- inputs (frontend/src/vnaBenchApi.ts) ----------------------------------------------------------------------------

class Cable(StrictModel):
    instrument_id: UUID
    end: Literal["A", "B"] = "B"   # the side of the cable toward the item (RF interface order in its card)


class AssemblyProfile(StrictModel):
    task_id: UUID
    operation_id: UUID | None = None


class SessionInput(StrictModel):
    """Everything optional: by default the station is the S2VNA card with its accessories (АКМ, two test cables)."""
    agent_id: str | None = Field(default=None, min_length=1, max_length=80)
    instrument_id: UUID | None = None
    acm_id: UUID | None = None
    cables: list[Cable] | None = Field(default=None, min_length=2, max_length=2)
    # Calibrate on the ETK band of this КБ (СТО прил. А.2); without it — the «ОТК — переход» grid (СИ and checks).
    assembly: AssemblyProfile | None = None


class Version(StrictModel):
    version: int = Field(ge=1, strict=True)


class Fixture(StrictModel):
    instrument_id: UUID
    reversed: bool = False   # its side 2 faces VNA port 1


class Target(StrictModel):
    kind: Literal["instrument", "assembly"]
    instrument_id: UUID | None = None
    number: str = ""
    name: str = ""
    serial_number: str | None = None
    task_id: UUID | None = None
    task_number: str = ""
    position: int | None = Field(default=None, ge=1, le=1_000_000)
    serial: str | int | None = None
    item: str = ""
    operation_id: UUID | None = None
    # СИ only (12.2): the fixture chain and a lower upper frequency than the series/norm allow.
    left: Fixture | None = None
    right: Fixture | None = None
    fmax_ghz: float | None = Field(default=None, gt=0, le=1000)


class ItemInput(Version):
    target: Target


class PlanInput(StrictModel):
    target: Target


class ResolveInput(StrictModel):
    query: str = Field(min_length=1, max_length=200)


class CheckInput(Version):
    kind: Literal["flex", "weekly", "monthly"]
    instrument_id: UUID | None = None                                # СИ.009 / the 6650F27 card; default — the only one
    label: str = Field(default=bench.KIT_LABEL, min_length=1, max_length=64)
    selection: dict[str, str] = Field(default_factory=dict, max_length=7)   # OPEN_1 … THRU → label of the standard


# ---- station and state ---------------------------------------------------------------------------------------------

def _station(uow, instrument_id=None):
    """The bench ВАЦ: the given card or the S2VNA card with a serial bound to a PC (one bench in the shop)."""
    query = select(ProductionInstrumentRow).where(ProductionInstrumentRow.vna_app == "S2VNA")
    if instrument_id is not None:
        query = query.where(ProductionInstrumentRow.id == instrument_id)
    cards = [c for c in uow.session.scalars(query.order_by(ProductionInstrumentRow.inventory_number))
             if (c.serial_number or "").strip()]
    bound = [c for c in cards if uow.session.get(VnaBindingRow, c.serial_number.strip()) is not None]
    return (bound or cards or [None])[0]


def _accessories(uow, vna_id, role):
    rf = RfRepository(uow.session)
    rows = uow.session.scalars(select(ProductionInstrumentRow).where(ProductionInstrumentRow.parent_id == vna_id)
                               .order_by(ProductionInstrumentRow.inventory_number))
    return [r for r in rows if rf.get(r.id).role == role]


def _session_row(uow, vna_id):
    return uow.session.scalar(select(VnaSessionRow).where(
        VnaSessionRow.instrument_id == vna_id, VnaSessionRow.purpose == "bench",
        VnaSessionRow.state.not_in(TERMINAL)).limit(1)) if vna_id else None


def _owned(uow, session_id, principal):
    row = VnaSessionRepository(uow.session).get(session_id)
    if row.purpose != "bench" or row.operator_id != principal.actor.id:
        raise KeyError("Сеанс рабочего места ВАЦ не найден")
    return row


def _actor(snapshot) -> Actor:
    operator = snapshot["operator"]
    return Actor(id=UUID(operator["id"]), name=operator["name"], department=operator["department"])


def _profile(row) -> Profile:
    return Profile.model_validate(row.snapshot["profile"])


def _commands(uow, row):
    return list(uow.session.scalars(select(VnaCommandRow).where(VnaCommandRow.session_id == row.id)
                                    .order_by(VnaCommandRow.created_at, VnaCommandRow.expected_version)))


def _latest_status(commands):
    done = [c for c in commands if c.op == "device_status" and c.status == "done"]
    return done[-1] if done else None


def _device(uow, vna, row, commands, now):
    serial = (vna.serial_number or "").strip() if vna else ""
    binding = uow.session.get(VnaBindingRow, serial) if serial else None
    agent = uow.session.get(ScanAgentRow, binding.machine_id) if binding else None
    control = uow.session.get(VnaControlRow, binding.machine_id) if binding else None
    live = lambda moment: moment is not None and as_utc(moment) >= as_utc(now) - LIVE_FOR  # noqa: E731
    device = uow.session.scalar(select(DeviceRow).where(
        DeviceRow.machine_id == binding.machine_id, DeviceRow.kind == "s2vna", DeviceRow.device_id == serial,
        DeviceRow.present.is_(True))) if binding else None
    status = _latest_status(commands)
    reading = status.result if status else {}
    temperatures = reading.get("temperatures")
    if temperatures is None and row is not None and row.calibration_temps:
        temperatures = row.calibration_temps
    refusal = None
    if vna is None:
        refusal = dict(code="station_setup", message="В реестре нет карточки ВАЦ S2VNA с заводским номером")
    else:
        savepoint = uow.session.begin_nested()   # the station check locks the binding row: read-only here
        try:
            vna_jobs.control_station(uow.session, serial, now, methods={"acm"}, ops=set(bench.SESSION_OPS))
        except ScanJobClosed as error:
            refusal = dict(code=_agent_code(str(error)), message=str(error))
        finally:
            savepoint.rollback()
    model = row.snapshot.get("vna_model") if row else (vna_model(f"{vna.name} {vna.doc_designation or ''}") if vna else "")
    acm = reading.get("acm")
    return dict(pc_name=(agent.pc_name or agent.machine_id) if agent else None,
                agent_online=bool(agent and live(agent.last_seen) and control and live(control.reported_at)),
                agent_version=agent.version if agent else None,
                instrument_number=vna.inventory_number if vna else "", instrument_id=str(vna.id) if vna else None,
                model=f"Planar {model}" if model else (vna.name if vna else ""),
                serial=serial if device is not None and live(device.last_seen) else None,
                s2vna_ready=reading.get("ready") if status else (True if device is not None and live(device.last_seen) else None),
                warmup_min=reading.get("warmup_min"), temperatures=temperatures,
                acm=dict(model=acm["model"], serial=acm["serial"]) if acm else None, refusal=refusal,
                read_at=status.finished_at if status else None)


def _calibration(row, now):
    if row is None:
        return dict(valid=False, calibrated_at=None, valid_until=None, reason="Сеанс не открыт", temperatures=None)
    reason = bench.calibration_refusal(row.calibrated_at, now)
    return dict(valid=not reason, calibrated_at=row.calibrated_at,
                valid_until=(as_utc(bench.aware(row.calibrated_at, now)) + bench.CALIBRATION_VALID
                             if row.calibrated_at else None),
                reason=reason or None, temperatures=row.calibration_temps)


def _due(uow, vna, row, now):
    if vna is None:
        return []
    statuses = {"ok": "ok", "overdue": "overdue", "never": "due", "failed": "failed"}
    due = [dict(kind=CHECK_NAMES[d["kind"]], due_at=d["due_at"], last_at=d["last_at"], status=statuses[d["status"]])
           for d in VnaCheckRepository(uow.session).due(vna.id, now)]
    # 14: the bend check belongs to every calibration.
    last = uow.session.scalar(select(VnaCheckRow).where(VnaCheckRow.instrument_id == vna.id, VnaCheckRow.kind == "flex")
                              .order_by(VnaCheckRow.performed_at.desc()).limit(1))
    fresh = bool(last and row is not None and row.calibrated_at
                 and as_utc(bench.aware(last.performed_at, now)) >= as_utc(bench.aware(row.calibrated_at, now)))
    due.insert(0, dict(kind="flex", due_at=None, last_at=last.performed_at if last else None,
                       status=("ok" if last.passed else "failed") if fresh else "due"))
    return due


def _runs(row) -> list[dict]:
    seen, runs = set(), []
    for index, step in enumerate(row.steps):
        item = step.get("item") or {}
        if item.get("run_id") and item["run_id"] not in seen:
            seen.add(item["run_id"])
            runs.append({**bench.run_meta(item), "first_step": index})
    return runs


def _run_steps(row, run_id):
    return [(i, s) for i, s in enumerate(row.steps) if (s.get("item") or {}).get("run_id") == run_id]


def _latest_run(row, kinds):
    runs = [r for r in _runs(row) if r["kind"] in kinds]
    return runs[-1] if runs else None


def _command_for(commands, index):
    found = [c for c in commands if c.expected_step == index]
    return found[-1] if found else None


def _status(command) -> str:
    if command is None:
        return "pending"
    return {"done": "passed", "failed": "failed", "uncertain": "refused", "cancelled": "refused"}.get(command.status, "running")


def _refusal(row, run, all_runs):
    """An unfinished run refused: the latest one shows the session's reason, an older one was left."""
    if all_runs and all_runs[-1]["run_id"] == run["run_id"]:
        return dict(code=bench.refusal_code(row.error), message=row.error) if row.error else None
    return dict(code="stopped", message="Прервано — начат следующий объект или проверка")


def _steps_view(uow, row, commands, items):
    calibration = _latest_run(row, ("calibration",))
    if calibration is None:
        return []
    indexes = {s["op"]: i for i, s in _run_steps(row, calibration["run_id"])}
    loads = [(i, s) for i, s in enumerate(row.steps) if (s.get("item") or {}).get("calibration_run") == calibration["run_id"]]
    view = []
    for key, index in (("calibration", indexes.get("acm_calibrate")), ("confidence", indexes.get("acm_confidence")),
                       ("load_port1", next((i for i, s in loads if s["item"]["target"] == "port1"), None)),
                       ("load_port2", next((i for i, s in loads if s["item"]["target"] == "port2"), None))):
        command = _command_for(commands, index) if index is not None else None
        status, text = _status(command), None
        if index is None or index >= len(row.steps):
            status = "refused" if row.error else "pending"
        if key == "confidence" and command is not None and command.status == "done":
            check = confidence_from_receipt(command.result)
            status = "passed" if check.passed else "failed"
            text = (f"передача {number_text(check.tx_dev_db, 3)} дБ ≤ 0,1"
                    + (f"; отражение {number_text(check.rx_dev_db, 3)} дБ ≤ 0,4/0,5" if check.rx_dev_db is not None else ""))
        if key.startswith("load") and command is not None and command.status == "done":
            found = next((it for it in items if it.command_id == command.id), None)
            if found is not None and found.verdict is not None:
                status = "passed" if found.verdict == "pass" else "failed"
                text = f"КСВН {number_text(found.result['vswr'], 4)} ≤ 1,06"
        refusal = None
        if status in ("failed", "refused") and row.error:
            refusal = dict(code=bench.refusal_code(row.error), message=row.error)
        if command is not None and command.status == "failed":
            refusal = dict(code=bench.refusal_code(command.result.get("error", "")), message=command.result.get("error", ""))
        view.append(dict(key=key, status=status, result_text=text, refusal=refusal,
                         finished_at=command.finished_at if command else None))
    return view


def _command_view(row, commands):
    active = [c for c in commands if c.status in ("pending", "claimed")]
    if not active:
        return None
    command = active[-1]
    step = row.steps[command.expected_step] if command.expected_step < len(row.steps) else {}
    label = OP_LABELS.get(command.op, command.op)
    if command.op == "capture" and step.get("label"):
        label = "развёртка: " + step["label"]
    return dict(label=label, state="queued" if command.status == "pending" else "running",
                since=command.claimed_at or command.created_at)


def _trace(data: TwoPort):
    return [dict(mhz=f / 1e6, vswr=min(max(vswr(a), vswr(b)), 1000.0)) for f, a, b in zip(data.frequencies, data.s11, data.s22)]


def _item_view(uow, repository, row, run, all_runs, items, commands):
    run_rows = [it for it in items if str(it.run_id) == run["run_id"]]
    # СИ: the latest series (a recalibration starts a new one); КБ: every attempt, linked by the VSWR rule.
    rows = _last_group(run_rows) if run["kind"] == "instrument" else run_rows
    busy = any(c.status in ("pending", "claimed") for c in commands
               if (row.steps[c.expected_step].get("item") or {}).get("run_id") == run["run_id"]
               if c.expected_step < len(row.steps))
    last = rows[-1] if rows else None
    if run["kind"] == "instrument":
        target = dict(kind="instrument", instrument_id=run["instrument_id"], number=run["number"], name=run["name"],
                      serial_number=run["serial"] or None)
        border = bool(rows) and rows[0].verdict == "border"
        total = bench.REPEATS if border else 1
    else:
        target = dict(kind="assembly", task_id=run["task_id"], task_number=run["task_number"],
                      position=run["product_position"], serial=None, name=run.get("task_name", ""), item=run.get("task_item", ""))
        # A «на границе» attempt asks for the next connection; record_vswr decides when the repeat series is done.
        total = max(1, len(rows) + (1 if rows and rows[-1].verdict == "border" else 0))
    connections = [dict(no=n, status="failed" if it.verdict == "fail" else "passed",
                        max_vswr=it.result.get("worst_vswr", it.result.get("max_vswr")),
                        max_mhz=it.result.get("worst_mhz", it.result.get("max_vswr_mhz")), measured_at=it.created_at)
                   for n, it in enumerate(rows, start=1)]
    for n in range(len(rows) + 1, total + 1):
        connections.append(dict(no=n, status="running" if busy and n == len(rows) + 1 else "pending", max_vswr=None,
                                max_mhz=None, measured_at=None))
    if busy and not rows:
        connections = [dict(no=1, status="running", max_vswr=None, max_mhz=None, measured_at=None)]
    verdict, trace = None, []
    judged = [it for it in rows if it.verdict is not None]
    if judged:
        worst = max(judged, key=lambda it: it.result.get("worst_vswr") or it.result.get("max_vswr") or 0)
        decided = judged[-1]
        final = (len(judged) >= total) if run["kind"] == "instrument" else decided.verdict != "border"
        plan = run.get("plan") or {}
        verdict = dict(code=VERDICT_CODES[decided.verdict], worst_vswr=worst.result.get("worst_vswr", worst.result.get("max_vswr")),
                       worst_mhz=worst.result.get("worst_mhz", worst.result.get("max_vswr_mhz")),
                       norm_vswr=plan.get("norm_vswr"),
                       uncertainty=decided.result.get("vswr_uncertainty", decided.result.get("uncertainty")),
                       reason=decided.result.get("range_line") or decided.result.get("reason") or None, final=final)
        try:
            artifact = repository.artifact(worst.command_id)
            data = read_touchstone(bytes(artifact.content), artifact.file_name)
            data = (_instrument_data(uow, run, data)[0] if run["kind"] == "instrument"
                    else select_band(data, _profile(row).stop_hz))
            trace = _trace(data)
        except JUDGE_ERRORS:
            trace = []
    recorded = [it.recorded_at for it in run_rows if it.recorded_at is not None]
    unfinished = not verdict or not verdict["final"]
    refusal = _refusal(row, run, all_runs) if unfinished and not busy else None
    return dict(id=run["run_id"], version=row.version, target=target, plan=run.get("plan"), connections_total=total,
                connections=connections, trace=trace, verdict=verdict, refusal=refusal,
                recorded_at=max(recorded) if recorded else None,
                recorded_to=(f"карточка {run['number']}" if run["kind"] == "instrument"
                             else f"{run['task_number']}, изделие № {run['product_position']}") if recorded else None,
                # «Печать этикетки» after recording: POST /api/production/instruments/{id}/label {copies}.
                label_instrument_id=run["instrument_id"] if recorded and run["kind"] == "instrument" else None)


def _phases(uow, row, run, commands):
    kind = run["kind"]
    if kind == "flex":
        return [dict(key=k, label=label, target=t, standard=None, port=None, connection_no=None, connections_total=None)
                for k, label, t in FLEX_PHASES]
    if kind == "control_standard":
        return [dict(key="si009", label=f"Контрольная мера {run['number']}", target="dut", standard=None, port=None,
                     connection_no=None, connections_total=None)]
    read = next((c for i, s in _run_steps(row, run["run_id"]) if s["op"] == "read_kit"
                 for c in [_command_for(commands, i)] if c is not None and c.status == "done"), None)
    if read is None:
        return []
    phases = []
    for standard, port, definition in bench.kit_choice(read.result["standards"], run.get("selection") or {}):
        for number in range(1, bench.REPEATS + 1):
            phases.append(dict(key=f"{standard.lower()}_p{port}" if port else "thru",
                               label=f"{definition.get('label') or standard} ({standard}), "
                                     + (f"порт {port}" if port else "перемычка"),
                               target=f"port{port}" if port else "thru", standard=standard, port=port, definition=definition,
                               connection_no=number, connections_total=bench.REPEATS))
    return phases


def _series_rows(row, run, rows, phases):
    """The rows that count: a whole check (bend, monthly) is made under one calibration — an unfinished check whose
    calibration was replaced starts again from its first phase, so its journal row covers every standard."""
    return _open_series(rows, row.calibration_check_id, any(r.result.get("check_id") for r in rows))


def _phase_done(rows, phase):
    for it in rows:
        if it.target == phase["target"] and (phase["standard"] is None or it.standard == phase["standard"]) and (
                phase["connection_no"] is None or it.connection_no == phase["connection_no"]):
            return it
    return None


def _check_view(uow, row, run, all_runs, items, commands):
    rows = [it for it in items if str(it.run_id) == run["run_id"]]
    busy = any(c.status in ("pending", "claimed") for c in commands if c.expected_step < len(row.steps)
               and (row.steps[c.expected_step].get("item") or {}).get("run_id") == run["run_id"])
    try:
        phases = _phases(uow, row, run, commands)
    except ProductionValidationError:
        phases = []
    finals = [it for it in rows if it.result.get("check_id")]
    rows = rows if finals else _series_rows(row, run, rows, phases)
    view, current = [], True
    for phase in phases:
        done = _phase_done(rows, phase)
        if done is not None:
            status = "failed" if done.verdict == "fail" else "passed"
            text = done.result.get("result_text")
        else:
            status, text = ("running" if busy else "pending") if current else "pending", None
            current = False
        view.append(dict(key=phase["key"], label=phase["label"], status=status, connection_no=phase["connection_no"],
                         connections_total=phase["connections_total"], result_text=text))
    finished = bool(finals)
    # The check's verdict is its journal row's (every standard / phase), never the last phase's own.
    status = (("passed" if finals[-1].result["check_passed"] else "failed") if finished
              else ("running" if busy else "pending"))
    refusal = None
    if not finished and not busy:
        refusal = _refusal(row, run, all_runs)
        if refusal:
            status = "refused"
    started = next((c.created_at for c in commands if c.expected_step >= run["first_step"]), row.updated_at)
    return dict(id=run["run_id"], version=row.version, kind=CHECK_NAMES[run["kind"]], status=status, phases=view,
                result_text=finals[-1].result.get("result_text") if finished else None, refusal=refusal,
                started_at=started, finished_at=finals[-1].created_at if finished else None)


def _state(uow, principal, now, vna=None, row=None):
    if row is None:
        vna = vna or _station(uow)
        row = _session_row(uow, vna.id if vna else None)
    else:
        vna = uow.session.get(ProductionInstrumentRow, row.instrument_id)
    commands = _commands(uow, row) if row else []
    session = None
    if row is not None:
        repository = VnaSessionRepository(uow.session)
        items = repository.items(row.id)
        runs = _runs(row)
        status = "open" if row.state not in TERMINAL else "closed" if row.state == "recorded" else "aborted"
        session = dict(id=str(row.id), version=row.version, status=status, state=row.state, started_at=row.created_at,
                       operator=row.snapshot["operator"]["name"], owned=row.operator_id == principal.actor.id,
                       error=row.error or None, steps=_steps_view(uow, row, commands, items),
                       command=_command_view(row, commands),
                       items=[_item_view(uow, repository, row, r, runs, items, commands) for r in runs if r["kind"] in ITEMS],
                       checks=[_check_view(uow, row, r, runs, items, commands) for r in runs if r["kind"] in CHECK_NAMES])
    return dict(device=_device(uow, vna, row, commands, now), calibration=_calibration(row, now),
                due=_due(uow, vna, row, now), session=session)


@router.get(PREFIX + "/state")
def bench_state(principal: Person, now: Now, factory: Factory):
    with _refusals():
        with factory() as uow:
            vna = _station(uow)
            row = _session_row(uow, vna.id if vna else None)
            session_id = row.id if row else None
        if session_id:
            _expire(factory, now, session_id=session_id)
        with factory() as uow:
            return _state(uow, principal, now)


def _version(row, version):
    if row.version != version:
        raise Refusal("stale", "Состояние уже изменилось — экран обновлён, повторите действие")


def _act(factory, session_id, principal, now, action):
    """Run one session change under the session lock and answer the full state."""
    with _refusals():
        with factory() as uow:
            _owned(uow, session_id, principal)
        _expire(factory, now, session_id=session_id)
        with factory() as uow:
            row = _owned(uow, session_id, principal)
            repository = VnaSessionRepository(uow.session)
            repository.lock(row)
            action(uow, repository, row)
            state = _state(uow, principal, now, row=row)
            uow.commit()
            return state


# ---- session ---------------------------------------------------------------------------------------------------------

def _vswr_operation(task):
    for operation in task.operations:
        text = f"{operation.code} {operation.description}".upper()
        if "VSWR" in text or "КСВН" in text:
            return operation.id
    return None


@router.post(PREFIX + "/sessions")
def open_session(principal: Person, now: Now, factory: Factory, techcards_factory: Techcards, refs_factory: Refs,
                 payload: SessionInput | None = None):
    payload = payload or SessionInput()
    with _refusals():
        with factory() as uow:
            vna = _station(uow, payload.instrument_id)
            stale = _session_row(uow, vna.id if vna else None)
            stale_id = stale.id if stale else None
        if stale_id:
            _expire(factory, now, session_id=stale_id)   # a session idle 12 h must not block a new one
        with factory() as uow:
            vna = _station(uow, payload.instrument_id)
            if vna is None:
                raise Refusal("station_setup", "В реестре нет карточки ВАЦ S2VNA с заводским номером")
            vna_id = vna.id
            acms = [uow.session.get(ProductionInstrumentRow, payload.acm_id)] if payload.acm_id else _accessories(uow, vna_id, "auto_calibration")
            cables = ([uow.session.get(ProductionInstrumentRow, c.instrument_id) for c in payload.cables] if payload.cables
                      else _accessories(uow, vna_id, "test_cable"))
            ends = [c.end for c in payload.cables] if payload.cables else ["B", "B"]
            if len(acms) != 1 or acms[0] is None:
                raise Refusal("station_setup", "Укажите АКМ рабочего места: в карточке ВАЦ должна быть ровно одна "
                                               "принадлежность с ролью «АКМ»")
            if len(cables) != 2 or any(c is None for c in cables):
                raise Refusal("station_setup", "Укажите кабели портов 1 и 2: в карточке ВАЦ должно быть ровно два "
                                               "тестовых кабеля (порт 1 — меньший инвентарный номер)")
            acm_id, cable_ids = acms[0].id, [c.id for c in cables]
            task = uow.production.get(payload.assembly.task_id) if payload.assembly else None
            operation_id = None
            if payload.assembly:
                if task is None:
                    raise KeyError("Задание не найдено")
                operation_id = payload.assembly.operation_id or _vswr_operation(task)
                if operation_id is None:
                    raise Refusal("no_band", "В задании нет операции «Контроль КСВН»")
        instrument = capture_instrument(factory(), vna_id)
        if instrument["source"] != "s2vna" or not instrument["serial"]:
            raise Refusal("station_setup", "Рабочее место ВАЦ — только для S2VNA с серийным номером")
        assembly = None
        if payload.assembly is None:
            profile = bench.adapter_profile()
        else:
            plan = vna_plan(payload.assembly.task_id, operation_id, vna_id, principal, now, factory, techcards_factory,
                            refs_factory)
            chains = plan["chains"]
            if plan["profile"]["status"] != "ready":
                raise Refusal("no_band", "План замера КБ не готов: " + "; ".join(plan["profile"]["problems"]))
            if plan["status"] != "ready" or chains.get("status") != "ready" or not chains.get("automatic_acceptance"):
                raise Refusal("other_calibration", "План замера КБ требует согласования метода — используйте сессию ОТК")
            calibration = chains["calibration_connections"]
            if (chains.get("mode") == "one_port_reversal" or isinstance(calibration, list)
                    or not all(s["kind"] == "АКМ" for s in calibration["standards"])):
                raise Refusal("other_calibration", "На рабочем месте — двухпортовый замер КБ с калибровкой АКМ")
            profile = resolved_profile(plan, [])
            assembly = dict(task_id=str(payload.assembly.task_id), operation_id=str(operation_id),
                            task_number=task.number, norm=plan["profile"]["norm"], band_mhz=plan["profile"]["band_mhz"],
                            vswr_max=plan["profile"]["limit_line"]["vswr_max"],
                            norm_source=(plan["profile"]["norm"].get("source") or plan["profile"]["norm"].get("label")
                                         or "норма ЭТК"))
        try:
            assert_confidence_points(profile.points)   # never calibrate a grid the confidence test cannot cover
        except ProductionValidationError as error:
            raise Refusal("points_over_limit", str(error)) from error
        with factory() as uow:
            rf = RfRepository(uow.session)
            vna = uow.instruments.get(vna_id)
            if profile.points > (rf.get(vna_id).max_points or 0):
                raise Refusal("points_over_limit", "Число точек превышает подтверждённую ёмкость ВАЦ")
            acm = uow.instruments.get(acm_id)
            if acm is None or rf.get(acm_id).role != "auto_calibration" or not (acm.serial_number or "").strip():
                raise Refusal("station_setup", "АКМ не найден в реестре или без серийного номера (роль «АКМ» в карточке)")
            if len(set(cable_ids)) != 2 or acm_id in cable_ids:
                raise Refusal("station_setup", "Кабели портов 1 и 2 и АКМ — разные экземпляры")
            cable_view = []
            for cable_id, end in zip(cable_ids, ends):
                card = uow.instruments.get(cable_id)
                if card is None or rf.get(cable_id).role != "test_cable":
                    raise Refusal("station_setup", "Кабель порта не найден в реестре с ролью «Тестовый кабель»")
                cable_view.append(dict(instrument_id=str(card.id), inventory_number=card.inventory_number, end=end))
            definition = {**asdict(rf.get(acm_id)), "serial_number": acm.serial_number.strip()}
            snapshot = dict(profile=profile.model_dump(mode="json"), profile_kind="assembly" if assembly else "adapter",
                            assembly=assembly, vna_model=vna_model(f"{vna.name} {vna.doc_designation or ''}") or bench.VNA_MODEL,
                            cables=cable_view,
                            acm=dict(id=str(acm.id), serial=acm.serial_number.strip(), inventory_number=acm.inventory_number,
                                     definition=definition),
                            kit_definitions={str(acm.id): definition},
                            operator=dict(id=str(principal.actor.id), name=principal.actor.name,
                                          department=principal.actor.department))
            if payload.agent_id is not None and uow.session.get(ScanAgentRow, payload.agent_id) is None:
                raise Refusal("agent_offline", "ПК агента не найден")
            owner = vna_jobs.control_station(uow.session, instrument["serial"], now, machine_id=payload.agent_id,
                                            methods={"acm"}, ops=set(bench.SESSION_OPS))
            vna_jobs.refuse_open_capture(uow.session, vna_id, instrument["serial"])
            repository = VnaSessionRepository(uow.session)
            if repository.active(vna_id, instrument["serial"]):
                raise Refusal("device_busy", "Прибор занят активной сессией ВАЦ — закройте её или прервите")
            try:
                with uow.session.begin_nested():
                    row = repository.create(now=now, operator_id=principal.actor.id, machine_id=owner, purpose="bench",
                                            instrument_id=vna_id, instrument_serial=instrument["serial"], task_id=None,
                                            operation_id=None, product_position=None, snapshot=snapshot,
                                            steps=bench.session_steps(snapshot))
            except IntegrityError:
                if repository.active(vna_id, instrument["serial"]):
                    raise Refusal("device_busy", "Прибор занят активной сессией ВАЦ") from None
                raise
            # The operator opened the session to work: prepare, profile and calibration set-up run at once.
            repository.act(row, "plan", row.version, row.step, now)
            repository.act(row, "start", row.version, row.step, now)
            state = _state(uow, principal, now, row=row)
            uow.commit()
            return state


@router.post(PREFIX + "/sessions/{session_id}/steps/{key}")
def run_step(session_id: UUID, key: Literal["calibration", "confidence", "load_port1", "load_port2"], payload: Version,
             principal: Person, now: Now, factory: Factory):
    """The operator's click after connecting what the step says. Calibration and the LOAD steps confirm the waiting
    connection; the confidence test runs by itself right after the calibration (the ACM still on the cables)."""
    def action(uow, repository, row):
        _version(row, payload.version)
        current = row.steps[row.step] if row.step < len(row.steps) else {}
        item = current.get("item") or {}
        waiting = row.state in ("awaiting_standard", "awaiting_dut")
        if key == "calibration":
            if waiting and current.get("op") == "acm_calibrate":
                repository.act(row, "confirm", row.version, row.step, now)
            elif row.state == "ready":
                # 11.5: redo the calibration in the same session; the operator has connected the ACM.
                repository.append(row, bench.calibration_steps(row.snapshot, acm_connected=True), now,
                                  error="", **bench.UNCALIBRATED)
            else:
                raise Refusal("device_busy", "Калибровку сейчас начать нельзя — дождитесь окончания команды")
        elif key == "confidence":
            if row.state == "ready" and row.calibrated_at is None:
                raise Refusal("calibration_required", "Доверительный тест выполняется сразу после калибровки АКМ — "
                                                      "повторите калибровку (СТО п. 11.4)")
        else:
            target = "port1" if key == "load_port1" else "port2"
            if waiting and current.get("op") == "capture" and item.get("kind") == "load" and item.get("target") == target:
                repository.act(row, "confirm", row.version, row.step, now)
            elif row.state == "ready" and row.calibrated_at is None:
                raise Refusal("calibration_required", "LOAD проверяется после калибровки — повторите калибровку АКМ "
                                                      "(СТО п. 11.4)")
            else:
                raise Refusal("stale", "Сейчас ожидается другой шаг — экран обновлён")
    return _act(factory, session_id, principal, now, action)


@router.post(PREFIX + "/sessions/{session_id}/abort")
def abort(session_id: UUID, payload: Version, principal: Person, now: Now, factory: Factory):
    def action(uow, repository, row):
        _version(row, payload.version)
        if row.state in ("recovery_required",):
            repository.act(row, "recover", row.version, row.step, now)
        else:
            repository.act(row, "abort", row.version, row.step, now)
    return _act(factory, session_id, principal, now, action)


@router.post(PREFIX + "/sessions/{session_id}/close")
def close(session_id: UUID, payload: Version, principal: Person, now: Now, factory: Factory):
    def action(uow, repository, row):
        _version(row, payload.version)
        if row.state != "ready":
            raise Refusal("device_busy", "Закрыть сеанс можно, когда прибор не выполняет команду")
        repository.act(row, "close", row.version, row.step, now)
    return _act(factory, session_id, principal, now, action)


# ---- objects ---------------------------------------------------------------------------------------------------------

@router.post(PREFIX + "/resolve")
def resolve(payload: ResolveInput, principal: Person, factory: Factory):
    """A scan or a number: «СИ.035» (or 35), a task «ПЗ-0001/3», a product serial number."""
    query = payload.query.strip()
    with _refusals(), factory() as uow:
        candidates = []
        number = f"СИ.{int(query):03d}" if query.isdigit() else query.upper().replace("CИ", "СИ")
        card = uow.instruments.by_inventory_number(number)
        if card is not None:
            candidates.append(dict(kind="instrument", instrument_id=str(card.id), number=card.inventory_number,
                                   name=card.name, serial_number=card.serial_number or None))
        task_number, position = query, None
        if match := re.fullmatch(r"(.+?)\s*/\s*(\d+)", query):
            task_number, position = match[1], int(match[2])
        serial = uow.session.scalar(select(ProductionSerialRow).where(ProductionSerialRow.serial_number == query))
        if serial is not None:
            task_number, position = None, serial.position
            task_rows = [uow.session.get(ProductionTaskRow, serial.task_id)]
        else:
            task_rows = list(uow.session.scalars(select(ProductionTaskRow).where(ProductionTaskRow.number == task_number)))
        for task_row in task_rows:
            task = uow.production.get(task_row.id)
            positions = [position] if position else list(range(1, min(task.quantity, 50) + 1))
            for p in positions:
                if 1 <= p <= task.quantity:
                    candidates.append(dict(kind="assembly", task_id=str(task.id), task_number=task.number, position=p,
                                           serial=None, name=task.name, item=task.item,
                                           operation_id=str(op) if (op := _vswr_operation(task)) else None))
        return {"candidates": candidates}


def _grid(profile: Profile, top_hz: float) -> tuple[float, ...]:
    step = (profile.stop_hz - profile.start_hz) / (profile.points - 1)
    return tuple(f for f in (profile.start_hz + step * i for i in range(profile.points)) if f <= top_hz)


def _check_grid(data: TwoPort, profile: Profile, top_hz: float, what: str):
    expected = _grid(profile, top_hz)
    probe = TwoPort.from_traces(expected, {name: [0j] * len(expected) for name in TRACES})
    same_grid(select_band(data, top_hz), probe, what)


def _latest_file(uow, instrument_id):
    """The fixture's own `.s2p`: its newest named measurement (12.2.2 — without its file no measurement)."""
    for _, file_id in RfRepository(uow.session).latest_characterization_file_ids([instrument_id]):
        return uow.session.get(ProductionInstrumentFileRow, file_id)
    return None


def _card(uow, instrument_id):
    card = uow.instruments.get(instrument_id)
    if card is None:
        raise KeyError("СИ не найдено")
    return card, RfRepository(uow.session).get(instrument_id)


def _scale_text(trace) -> str:
    if trace.scale == "auto":
        return "авто"
    s = trace.scale
    if s.rlev is None:
        return f"{number_text(s.pdiv * 10)} полная шкала"
    low, high = s.rlev - s.rpos * s.pdiv, s.rlev + (10 - s.rpos) * s.pdiv
    unit = "" if trace.format == "SWR" else " дБ" if trace.format == "MLOG" else ""
    return f"{number_text(low, 3)}…{number_text(high, 3)}{unit}"


TRACE_NAMES = {"SWR": "КСВН", "MLOG": "Модуль", "PHAS": "Фаза", "GDEL": "ГВЗ", "SMIT": "Вольперт-Смит"}


def _plan(uow, row, target: Target) -> tuple[dict, dict | None]:
    """The plan card and the run metadata; a refusal is reported in the plan, not raised."""
    profile, model = _profile(row), row.snapshot.get("vna_model") or bench.VNA_MODEL
    u_table = uncertainty_for(model)

    def card(screen_name, screen, band, norm_vswr, source, hint):
        fitted = bench.fit_screen(screen, profile)
        traces = [dict(label=f"{TRACE_NAMES[t.format]} {t.param}", scale=_scale_text(t)) for t in fitted.traces if not t.hidden]
        u = u_table.for_value(band[1] / 1e9, norm_vswr) if u_table and norm_vswr else None
        return dict(screen_name=screen_name, band_mhz=[band[0] / 1e6, band[1] / 1e6], points=len(_grid(profile, band[1])),
                    ifbw_hz=profile.if_bandwidth_hz, power_dbm=profile.power_dbm, traces=traces, norm_vswr=norm_vswr,
                    norm_source=source, uncertainty=u, connection_hint=hint, refusal=None), fitted

    def refused(code, message):
        return dict(screen_name="", band_mhz=[profile.start_hz / 1e6, profile.stop_hz / 1e6], points=profile.points,
                    ifbw_hz=profile.if_bandwidth_hz, power_dbm=profile.power_dbm, traces=[], norm_vswr=None,
                    norm_source=None, uncertainty=None, connection_hint=None,
                    refusal=dict(code=code, message=message)), None

    try:
        if target.kind == "instrument":
            if target.instrument_id is None:
                raise ValueError("Укажите СИ")
            if row.snapshot["profile_kind"] != "adapter":
                return refused("other_calibration", "Переход измеряют на сетке «ОТК — переход» — откройте сеанс без КБ")
            item, rf = _card(uow, target.instrument_id)
            norm = norm_for(item.name, item.doc_designation or "", rf)
            top = bench.fmax_hz(item.name, series_cap_ghz(tuple(p.series for p in rf.interfaces)), norm, profile,
                                target.fmax_ghz)
            fixtures, chain = [], []
            for side, fixture in (("left", target.left), ("right", target.right)):
                if fixture is None:
                    continue
                if fixture.instrument_id == target.instrument_id or any(f["instrument_id"] == str(fixture.instrument_id)
                                                                        for f in fixtures):
                    raise ValueError("Изделие и оснастка должны быть разными экземплярами")
                fixture_card, _ = _card(uow, fixture.instrument_id)
                file = _latest_file(uow, fixture.instrument_id)
                if file is None:
                    return refused("no_fixture_file", f"У оснастки {fixture_card.inventory_number} нет своего файла .s2p "
                                                      "— замер с ней не выполняют (СТО п. 12.2.2)")
                _check_grid(read_touchstone(file.content, file.file_name), profile, top,
                            f"Оснастка {fixture_card.inventory_number}")
                fixtures.append(dict(side=side, instrument_id=str(fixture.instrument_id),
                                     inventory_number=fixture_card.inventory_number, file_id=str(file.id),
                                     file_name=file.file_name, sha256=hashlib.sha256(file.content).hexdigest(),
                                     reversed=fixture.reversed))
                chain.append(f"{'слева' if side == 'left' else 'справа'} {fixture_card.inventory_number}"
                             + (" (обратно)" if fixture.reversed else ""))
            limits = item_limits(item.inventory_number, item.name, rf, stop_hz=top)
            norm_vswr = max((max(line.start_value, line.stop_value) for line in limits.swr), default=None)
            chain_text = ", ".join(chain) or "напрямую"
            plan, screen = card("ОТК — переход", bench_screen("adapter", item=limits), (profile.start_hz, top), norm_vswr,
                                getattr(norm, "reference", None) if norm else None,
                                f"Цепочка «{chain_text}»: сторона 1 — к порту 1 ВАЦ, сторона 2 — к порту 2")
            meta = bench.run("instrument", instrument_id=str(item.id), number=item.inventory_number, name=item.name,
                             serial=item.serial_number or "", fmax_hz=top, norm_id=rf.norm_id, fixtures=fixtures,
                             chain=chain_text, plan=plan, screen=screen.model_dump(mode="json"))
            return plan, meta
        assembly = row.snapshot.get("assembly")
        if not assembly:
            return refused("other_calibration", "Сеанс откалиброван на сетке перехода — для КБ откройте сеанс с заданием")
        if target.task_id is None or str(target.task_id) != assembly["task_id"] or target.position is None:
            return refused("other_calibration", "Сеанс откалиброван по ЭТК другого задания — откройте новый сеанс")
        task = uow.production.get(target.task_id)
        if task is None or target.position > task.quantity:
            raise ValueError("Позиция изделия выходит за количество задания")
        low, high = (float(v) * 1e6 for v in assembly["band_mhz"])
        plan, screen = card("ОТК — кабельная сборка", bench_screen("assembly", band_hz=(low, high),
                                                                  assembly_vswr=assembly["vswr_max"]),
                            (low, high), assembly["vswr_max"], assembly.get("norm_source"),
                            f"Изделие {task.number} № {target.position}: сторона А — к порту 1, сторона Б — к порту 2")
        meta = bench.run("assembly", task_id=assembly["task_id"], product_position=target.position, task_number=task.number,
                         task_name=task.name, task_item=task.item, plan=plan, screen=screen.model_dump(mode="json"))
        return plan, meta
    except (ProductionValidationError, VnaDataError, ValueError) as error:
        return refused(bench.refusal_code(str(error), "invalid"), str(error))


@router.post(PREFIX + "/sessions/{session_id}/plan")
def plan(session_id: UUID, payload: PlanInput, principal: Person, factory: Factory):
    with _refusals(), factory() as uow:
        return _plan(uow, _owned(uow, session_id, principal), payload.target)[0]


def _gates(uow, row, now, kind):
    """СТО п. 11.5 and разд. 14 before an item; the device itself is read again by the item's first step."""
    if refusal := bench.calibration_refusal(row.calibrated_at, now):
        raise Refusal(bench.refusal_code(refusal), refusal)
    if row.state != "ready":
        raise Refusal("device_busy", "Прибор выполняет команду — дождитесь её окончания")
    check = uow.session.get(VnaCheckRow, row.calibration_check_id)
    if check is None or not check.passed or check.kind != "session":
        raise Refusal("calibration_expired", "Проверка калибровки не пройдена — откалибруйте заново")
    if row.profile_hash != bench.profile_hash(row.snapshot["profile"]):
        raise Refusal("profile_changed", "Профиль сессии изменён после калибровки — откалибруйте заново")
    if kind in MEASURING:
        failed = [d for d in VnaCheckRepository(uow.session).due(row.instrument_id, now) if d["status"] == "failed"]
        if failed:
            raise Refusal("control_failed", f"Последняя проверка «{failed[0]['label']}» не пройдена — замеры остановлены "
                                            "до выяснения причины (СТО ФРАК-002-2026, разд. 14)")


@router.post(PREFIX + "/sessions/{session_id}/items")
def measure(session_id: UUID, payload: ItemInput, principal: Person, now: Now, factory: Factory,
            techcards_factory: Techcards, refs_factory: Refs):
    """«Измерить»: the item is connected — read the device, put its screen and sweep (connection 1)."""
    def action(uow, repository, row):
        _version(row, payload.version)
        _gates(uow, row, now, payload.target.kind)
        plan_card, meta = _plan(uow, row, payload.target)
        if plan_card["refusal"]:
            raise Refusal(plan_card["refusal"]["code"], plan_card["refusal"]["message"])
        if meta["kind"] == "assembly":
            assembly = row.snapshot["assembly"]
            task = uow.production.get(UUID(assembly["task_id"]))
            document, etk, current = _task_etk(techcards_factory, task)
            if task_norm(document, current, etk["current_revision"], CoaxRefs(refs_factory), row.created_at) != assembly["norm"]:
                raise Refusal("other_calibration", "Норма КСВН изменилась после начала сеанса — откройте новый сеанс")
        screen = Screen.model_validate(meta.pop("screen"))
        # A new КБ item is a new decision: its first sweep never continues an older «на границе» series.
        extra = (dict(task_id=meta["task_id"], product_position=meta["product_position"], fresh_series=True)
                 if meta["kind"] == "assembly" else {})
        label = ("Развёртка: " + plan_card["connection_hint"]) if plan_card["connection_hint"] else "Развёртка"
        repository.append(row, bench.item_steps(_profile(row), screen, label, meta, **extra), now, error="")
    return _act(factory, session_id, principal, now, action)


@router.post(PREFIX + "/sessions/{session_id}/items/{item_id}/connections")
def next_connection(session_id: UUID, item_id: str, payload: Version, principal: Person, now: Now, factory: Factory):
    """12.4: «на границе» — the next connection after turning the connector by ~120° (the cables stay put)."""
    def action(uow, repository, row):
        _version(row, payload.version)
        run = next((r for r in _runs(row) if r["run_id"] == item_id and r["kind"] in ITEMS), None)
        if run is None:
            raise KeyError("Объект сеанса не найден")
        _gates(uow, row, now, run["kind"])
        rows = _last_group(repository.items(row.id, item_id))
        if not rows:
            raise Refusal("stale", "Первое подключение ещё не оценено")
        last, head = rows[-1], {k: v for k, v in run.items() if k != "first_step"}
        restarted = last.result.get("calibration", {}).get("check_id") != str(row.calibration_check_id)
        if run["kind"] == "instrument":
            if rows[0].verdict != "border" or len(rows) >= bench.REPEATS:
                raise Refusal("stale", "Повтор не нужен: решение уже окончательное")
            group, number = (str(uuid4()), 1) if restarted else (str(last.group_id), len(rows) + 1)
            meta = {**head, "target": "dut", "group_id": group, "connection_no": number}
            what = run["number"]
        else:
            if last.verdict != "border":
                raise Refusal("stale", "Повтор не нужен: решение уже окончательное")
            origin = restarted or last.result.get("repeat_of") is None
            number = 1 if origin else last.connection_no + 1
            if number > bench.REPEATS:
                raise Refusal("stale", "Повтор уже выполнен в трёх подключениях")
            meta = {**head, "target": "dut", "group_id": str(uuid4()) if origin else str(last.group_id),
                    "connection_no": number, "task_id": run["task_id"], "product_position": run["product_position"],
                    "fresh_series": restarted}
            what = f"изделие № {run['product_position']}"
        label = bench.repeat_label(number, what)
        if restarted:
            label = ("Калибровка сменилась — серия повтора начата заново с подключения 1 (одна калибровка на решение). "
                     + label)
        hash_ = bench.last_screen_hash([s for _, s in _run_steps(row, item_id)])
        repository.append(row, [bench.step("device_status", item=head),
                                bench.capture_step(_profile(row), hash_, label, meta, human=False)], now, error="")
    return _act(factory, session_id, principal, now, action)


def _last_group(rows):
    return [r for r in rows if r.group_id == rows[-1].group_id] if rows else []


def _instrument_data(uow, run, raw: TwoPort):
    """The item itself: the sweep up to its upper frequency without its fixtures (T-matrices on the same grid)."""
    top = run["fmax_hz"]
    measured = select_band(raw, top)
    sides = {"left": None, "right": None}
    for fixture in run["fixtures"]:
        data = _file_data(uow, fixture["file_id"], fixture["sha256"], f"оснастки {fixture['inventory_number']}")
        if fixture["reversed"]:
            data = bench.reversed_two_port(data)
        sides[fixture["side"]] = _on_grid(select_band(data, top), measured, f"Оснастка {fixture['inventory_number']}")
    return bench.adapter_data(measured, top, sides["left"], sides["right"]), top


def _instrument_file(uow, row, run, item_row_or_none, raw, command, when, temperatures, calibration):
    """15.1: «Замер ВАЦ S50180, АКМ, <дата>, <СИ.…>.s2p» — rebuilt identically from the stored sweep."""
    data, top = _instrument_data(uow, run, raw)
    snapshot = row.snapshot
    model = snapshot.get("vna_model") or bench.VNA_MODEL
    connection_no = item_row_or_none.connection_no if item_row_or_none is not None else command["connection_no"]
    file_name = f"Замер ВАЦ {model}, АКМ, {when:%d.%m.%Y}, {run['number']}.s2p"
    comments = [f"{run['number']} {run['name']}; serial={run['serial']}",
                f"VNA serial={row.instrument_serial}; ACM serial={snapshot['acm']['serial']}",
                f"Temperatures VNA={'/'.join(str(t) for t in temperatures['vna_c'])}; ACM={temperatures['acm_c']} degC",
                f"Chain: {run['chain']}; stored port 1 faces VNA port 1",
                f"Operator={snapshot['operator']['name']}; tool=ERP bench session {row.id}; "
                f"calibration={calibration['calibrated_at'] or ''}; check={calibration['check_id'] or ''}; "
                f"load_check={calibration['load_check']}",
                f"Raw=ERP command {command['id']}; captured={as_utc(when).isoformat()}; deembedding=T-matrix; "
                f"fmax_Hz={top:g}; connection={connection_no}"]
    return file_name, write_touchstone(data, comments).encode(), data


@router.post(PREFIX + "/sessions/{session_id}/items/{item_id}/record")
def record(session_id: UUID, item_id: str, payload: Version, principal: Person, now: Now, factory: Factory):
    """«Записать в карточку» (15.1): every connection of the deciding series goes to the СИ card as its `.s2p`, so the
    passport and the label decide by the same files. A КБ is recorded in «Замеры КСВН» at its sweep already."""
    def action(uow, repository, row):
        _version(row, payload.version)
        run = next((r for r in _runs(row) if r["run_id"] == item_id and r["kind"] in ITEMS), None)
        if run is None:
            raise KeyError("Объект сеанса не найден")
        if run["kind"] == "assembly":
            return
        rows = _last_group(repository.items(row.id, item_id))
        if not rows or (rows[0].verdict == "border" and len(rows) < bench.REPEATS):
            raise Refusal("not_final", "Решение ещё не окончательное — выполните все подключения")
        card, _ = _card(uow, UUID(run["instrument_id"]))
        actor = _actor(row.snapshot)
        for it in rows:
            if it.recorded_file_id is not None:
                continue
            artifact = repository.artifact(it.command_id)
            raw = read_touchstone(bytes(artifact.content), artifact.file_name)
            name, content, _ = _instrument_file(uow, row, run, it, raw, dict(id=str(it.command_id)), it.created_at,
                                                it.result["temperatures"], it.result["calibration"])
            file = InstrumentFile(id=uuid4(), instrument_id=card.id, kind=InstrumentFileKind.OTHER, file_name=name,
                                  size=len(content), content=content, uploaded_at=as_utc(now), uploaded_by=actor.id,
                                  uploaded_by_name=actor.name)
            uow.instrument_files.add(file)
            uow.session.flush()
            uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=card.id, event_type="production.instrument_file_uploaded",
                                     actor=Actor(id=principal.actor.id, name=principal.actor.name,
                                                 department=principal.actor.department), occurred_at=now,
                                     details={"file_id": str(file.id), "kind": file.kind.value, "file_name": name,
                                              "size": file.size, "source": f"vna-bench:{it.command_id}"}))
            repository.record_file(it, file.id, now)
    return _act(factory, session_id, principal, now, action)


# ---- checks ----------------------------------------------------------------------------------------------------------

def _one(uow, rows, what):
    if len(rows) != 1:
        raise Refusal("station_setup", f"{what}: укажите карточку (найдено {len(rows)})")
    return rows[0]


@router.post(PREFIX + "/sessions/{session_id}/checks")
def start_check(session_id: UUID, payload: CheckInput, principal: Person, now: Now, factory: Factory):
    def action(uow, repository, row):
        _version(row, payload.version)
        kind = CHECKS[payload.kind]
        _gates(uow, row, now, kind)
        profile = _profile(row)
        if kind != "flex" and row.snapshot["profile_kind"] != "adapter":
            raise Refusal("other_calibration", "Проверки мерами — на сетке «ОТК — переход»; откройте сеанс без КБ")
        if kind == "flex":
            meta = bench.run("flex")
            steps = bench.item_steps(profile, bench_screen("load_check"), None, meta)
        elif kind == "control_standard":
            if payload.instrument_id is not None:
                card_id = payload.instrument_id
            else:
                card_id = _one(uow, list(uow.session.scalars(select(ProductionInstrumentFileRow.instrument_id).where(
                    ProductionInstrumentFileRow.is_reference.is_(True)))), "Контрольная мера с эталонной кривой")
            card, rf = _card(uow, card_id)
            file = uow.session.scalar(select(ProductionInstrumentFileRow).where(
                ProductionInstrumentFileRow.instrument_id == card.id, ProductionInstrumentFileRow.is_reference.is_(True)))
            if file is None:
                raise Refusal("no_reference", "У контрольной меры нет эталонной кривой — отметьте файл в карточке СИ")
            reference = read_touchstone(file.content, file.file_name)
            _check_grid(reference, profile, reference.frequencies[-1], "Эталонная кривая")
            meta = bench.run("control_standard", instrument_id=str(card.id), number=card.inventory_number,
                             reference_id=str(file.id), reference=file.file_name,
                             reference_sha256=hashlib.sha256(file.content).hexdigest())
            screen = bench_screen("adapter", item=item_limits(card.inventory_number, card.name, rf,
                                                              stop_hz=reference.frequencies[-1]))
            steps = bench.item_steps(profile, screen, None, meta)
        else:
            rf = RfRepository(uow.session)
            if payload.instrument_id is not None:
                card_id = payload.instrument_id
            else:
                kits = [r.id for r in uow.session.scalars(select(ProductionInstrumentRow))
                        if rf.get(r.id).role == "solt_kit" and rf.get(r.id).kit_definition == "6650F27-full"]
                card_id = _one(uow, kits, "Набор мер 6650F27")
            card, card_rf = _card(uow, card_id)
            if card_rf.role != "solt_kit":
                raise Refusal("station_setup", "Ежемесячная проверка — набором мер (роль «Набор SOLT» в карточке)")
            vna_jobs.control_station(uow.session, row.instrument_serial, now, machine_id=row.machine_id, ops={"read_kit"})
            meta = bench.run("kit_standards", instrument_id=str(card.id), number=card.inventory_number, label=payload.label,
                             selection=payload.selection)
            steps = bench.item_steps(profile, bench_screen("engineer"), None, meta)
            steps.append(bench.step("read_kit", dict(label=payload.label), item=meta))
        repository.append(row, steps, now, error="")
    return _act(factory, session_id, principal, now, action)


@router.post(PREFIX + "/sessions/{session_id}/checks/{check_id}/next")
def check_next(session_id: UUID, check_id: str, payload: Version, principal: Person, now: Now, factory: Factory):
    """The operator connected what the current phase says: sweep it (the bend check memorizes the straight thru)."""
    def action(uow, repository, row):
        _version(row, payload.version)
        run = next((r for r in _runs(row) if r["run_id"] == check_id and r["kind"] in CHECK_NAMES), None)
        if run is None:
            raise KeyError("Проверка сеанса не найдена")
        _gates(uow, row, now, run["kind"])
        commands = _commands(uow, row)
        rows = repository.items(row.id, check_id)
        try:
            phases = _phases(uow, row, run, commands)
        except ProductionValidationError as error:
            raise Refusal("kit_choice", str(error)) from error
        if any(it.result.get("check_id") for it in rows):
            raise Refusal("stale", "Проверка уже завершена")
        counted = _series_rows(row, run, rows, phases)
        restarted = bool(rows) and not counted
        pending = [p for p in phases if _phase_done(counted, p) is None]
        if not phases or not pending:
            raise Refusal("stale", "Проверка не ожидает подключения")
        phase, profile, meta = pending[0], _profile(row), {k: v for k, v in run.items() if k != "first_step"}
        hash_ = bench.last_screen_hash([s for _, s in _run_steps(row, check_id)])
        # One group per standard and calibration: a recalibrated series gets a new group from connection 1.
        group = str(uuid5(UUID(check_id), f"{phase['target']}:{phase['standard']}:{row.calibration_check_id}"))
        item = bench.connection(meta, phase["target"], phase["connection_no"] or 1, group,
                                **({"standard": phase["standard"], "port": phase["port"], "definition": phase["definition"]}
                                   if run["kind"] == "kit_standards" else {}))
        if restarted:
            phase = {**phase, "label": "Калибровка сменилась — проверка начата заново с первой фазы (одна калибровка "
                                       "на проверку). " + phase["label"]}
        if run["kind"] == "flex" and phase["key"] == "thru_memory":
            bend = bench.fit_screen(bench_screen("flex"), profile)
            hash_ = screen_hash(bend)
            memorized = [n for n, t in enumerate(bend.traces, start=1) if t.memory]
            steps = [bench.screen_step(bend, meta), bench.capture_step(profile, hash_, phase["label"], item, human=False),
                     bench.step("store_memory", dict(screen_hash=hash_, traces=memorized), item=meta)]
        else:
            steps = [bench.capture_step(profile, hash_, phase["label"], item, human=False)]
        steps.insert(0, bench.step("device_status", item=meta))   # the same ACM, «Кор», ±1 °C before each phase
        repository.append(row, steps, now, error="")
    return _act(factory, session_id, principal, now, action)


# ---- receipts: judged on the server in the agent's result request ----------------------------------------------------

def complete_command(uow, row, command_id, payload, now):
    """The bench branch of POST /api/stock/vna/commands/{id}/result: store the receipt, judge it, continue — one UoW."""
    repository = VnaSessionRepository(uow.session)
    command = repository.complete(row, command_id, payload.expected_version, payload.expected_step, payload.result,
                                  payload.error, now, backup_saved=payload.backup_saved, advance=False)
    if command is None:
        return
    if command.op == "release":
        repository.finish(row, now)
        return
    item = row.steps[row.step].get("item") or {}
    try:
        with uow.session.begin_nested():
            decision = _judge(uow, repository, row, command, item, now)
    except JUDGE_ERRORS as error:
        message = str(error.args[0]) if isinstance(error, KeyError) and error.args else str(error)
        uncalibrated = bench.UNCALIBRATED if item.get("kind") in ("calibration", "load") else {}
        decision = dict(tail=[], error=message or "Замер не принят", **uncalibrated)
        if command.op == "capture":
            _refused(uow, row, command, item, decision["error"], now)
    if command.op == "capture":
        if note := _capture_matings(uow, row, command, item, now):
            decision["error"] = "; ".join(filter(None, (decision.get("error"), note)))
    repository.proceed(row, now, **decision)


def _judge(uow, repository, row, command, item, now) -> dict:
    op, result = command.op, command.result
    if op == "acm_calibrate":
        acm, cables = row.snapshot["acm"], row.snapshot["cables"]
        entries = [dict(instrument_id=acm["id"], end=end, delta=1, kind="calibration") for end in ("A", "B")]
        entries += [dict(instrument_id=c["instrument_id"], end=c["end"], delta=1, kind="calibration") for c in cables]
        _matings(uow, f"vna-calibration-{command.id}", entries, row, now)
        return {}
    if op == "acm_confidence":
        return _confidence(uow, row, command, now)
    if op == "device_status":
        refusal = (bench.device_refusal(result, row.snapshot["acm"]["serial"], _profile(row).traces)
                   or bench.temperature_refusal(row.calibration_temps, result["temperatures"]))
        return dict(tail=[], error=refusal, **bench.UNCALIBRATED) if refusal else {}
    if op == "read_kit":
        bench.kit_choice(result["standards"], item.get("selection") or {})   # refuse an ambiguous kit at once
        return {}
    if op != "capture":
        return {}
    artifact = repository.artifact(command.id)
    data = read_touchstone(bytes(artifact.content), artifact.file_name)
    temperatures = result["temperatures"]
    kind = item["kind"]
    if kind == "load":
        return _load(uow, repository, row, command, artifact, item, data, now)
    refusal = (bench.calibration_refusal(row.calibrated_at, now)
               or bench.temperature_refusal(row.calibration_temps, temperatures))
    if refusal:
        _refused(uow, row, command, item, refusal, now)
        return dict(tail=[], error=refusal, **bench.UNCALIBRATED)
    judge = {"instrument": _instrument, "assembly": _assembly, "flex": _flex, "control_standard": _control,
             "kit_standards": _kit}[kind]
    return judge(uow, repository, row, command, artifact, item, data, now)


def _calibration_ref(row) -> dict:
    """The calibration a sweep was judged under — kept with the row: the card file and a repeat series use this one,
    never whatever calibration the session holds later."""
    return dict(check_id=str(row.calibration_check_id) if row.calibration_check_id else None,
                calibrated_at=as_utc(row.calibrated_at).isoformat() if row.calibrated_at else None, load_check="pass")


def _add(repository, row, command, artifact_id, item, verdict, result, now, **values):
    if item["kind"] != "load":
        result = {**result, "calibration": _calibration_ref(row)}
    return repository.add_item(row, command, artifact_id, item, verdict, result, now, **values)


def _same_calibration(rows) -> list:
    """The rows of a series made under the calibration of its latest row."""
    if not rows:
        return []
    check = rows[-1].result.get("calibration", {}).get("check_id")
    return [r for r in rows if r.result.get("calibration", {}).get("check_id") == check]


def _open_series(rows, current_check, complete: bool) -> list:
    """A finished series stays as it is; an unfinished one continues only under the current calibration — after a
    recalibration it starts again (СТО п. 11.5, 12.4: the connections of one decision share one calibration)."""
    series = _same_calibration(rows)
    if complete or not series:
        return series
    check = series[-1].result.get("calibration", {}).get("check_id")
    return series if current_check is not None and check == str(current_check) else []


def _capture_entries(row, item) -> tuple[str, list[dict]]:
    """Every physical mating of a sweep (10.7), judged or refused: the connector was mated either way."""
    cables = row.snapshot["cables"]
    kind, target = item["kind"], item.get("target")

    def cable(index, mating="measurement"):
        return dict(instrument_id=cables[index]["instrument_id"], end=cables[index]["end"], delta=1, kind=mating)

    def card(instrument_id, *ends):
        return [dict(instrument_id=instrument_id, end=end, delta=1, kind="measurement") for end in ends]
    if kind == "load":
        return "calibration", [cable(0 if target == "port1" else 1, "calibration")]
    if kind == "instrument":
        return "measurement", _instrument_matings(row, item)
    if kind == "assembly":
        return "measurement", [cable(0), cable(1)]
    if kind == "control_standard":
        return "measurement", card(item["instrument_id"], "A", "B") + [cable(0), cable(1)]
    if kind == "flex":
        if target in ("port1", "port2"):
            return "measurement", [cable(0 if target == "port1" else 1)]
        return "measurement", [cable(0), cable(1)] if target == "thru" else []   # the bent thru stays connected
    if kind == "kit_standards":
        if target == "thru":
            return "measurement", card(item["instrument_id"], "A", "B") + [cable(0), cable(1)]
        return "measurement", card(item["instrument_id"], "A") + [cable(0 if target == "port1" else 1)]
    return "measurement", []


def _capture_matings(uow, row, command, item, now) -> str:
    kind, entries = _capture_entries(row, item)
    try:
        with uow.session.begin_nested():
            _matings(uow, f"vna-{kind}-{command.id}", entries, row, now)
    except JUDGE_ERRORS as error:
        return f"Сочленения не записаны: {error}"
    return ""


def _refused(uow, row, command, item, reason, now):
    """A refused sweep holds no connection number and decides nothing: it goes to the audit journal only, and the
    operator takes the same connection again."""
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=row.id, event_type="production.vna_bench_capture_refused",
                             actor=_actor(row.snapshot), occurred_at=now,
                             details={"command_id": str(command.id), "run_id": item.get("run_id"), "kind": item.get("kind"),
                                      "target": item.get("target"), "connection_no": item.get("connection_no"),
                                      "reason": reason}))


def _matings(uow, source, entries, row, now):
    """Only the sides a card has (a load or a one-port item has no side B) — as the bench program did."""
    kept = [e for e in entries if e["end"] in end_names(RfRepository(uow.session).get(UUID(e["instrument_id"])))]
    if kept:
        record_batch(uow, source, kept, _actor(row.snapshot), now)


def _profile_text(row) -> str:
    p = _profile(row)
    kind = "ОТК — переход" if row.snapshot["profile_kind"] == "adapter" else "КБ по ЭТК"
    return (f"{kind}; {p.start_hz / 1e6:g} МГц – {p.stop_hz / 1e9:g} ГГц, {p.points} т., ПЧ {p.if_bandwidth_hz:g} Гц, "
            f"{p.power_dbm:g} дБм")


def _celsius(value):
    return value if value is not None and -40 <= value <= 125 else None


def _journal(uow, repository, row, command, kind, values: dict, temperatures: dict, now, note="") -> VnaCheckRow:
    """Form В.1: one append-only row; its verdict comes from the numbers (CHECKs in the database agree)."""
    values = {name: (journal_number(value) if isinstance(value, float) else value) for name, value in values.items()}
    passed = expected_verdict(kind, values)
    calibration = repository.receipts(row, "acm_calibrate")
    payload = dict(kind=kind, session=str(row.id), command=str(command.id), values=values, note=note)
    record_row = VnaCheckRepository(uow.session).add(VnaCheckRow(
        id=uuid4(), instrument_id=row.instrument_id, kind=kind, passed=passed, performed_at=now, recorded_at=now,
        source=f"erp-vna-bench:{command.id}", payload_hash=hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest(),
        operator=row.snapshot["operator"]["name"], actor=row.snapshot["operator"]["name"],
        calibration_id=str(calibration[-1].id) if calibration else "", acm_serial=row.snapshot["acm"]["serial"],
        profile=_profile_text(row)[:300], vna_temperature_c=_celsius((temperatures.get("vna_c") or [None])[0]),
        acm_temperature_c=_celsius(temperatures.get("acm_c")),
        **{name: values.get(name) for name in VALUE_FIELDS if name != "reference"},
        reference=values.get("reference") or "", note=note[:2000]))
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=row.instrument_id, event_type="production.vna_check_recorded",
                             actor=_actor(row.snapshot), occurred_at=now,
                             details={"check_id": str(record_row.id), "kind": kind, "passed": passed,
                                      "source": record_row.source}))
    return record_row


def _confidence(uow, row, command, now):
    temperatures = command.result["temperatures"]
    if bench.temperature_refusal(temperatures, temperatures):
        raise ProductionValidationError("Температура ВАЦ или АКМ не прочитана — калибровка не проверена, откалибруйте заново")
    confidence = confidence_from_receipt(command.result)
    if confidence.passed:
        return {}
    _journal(uow, VnaSessionRepository(uow.session), row, command, "session",
             dict(confidence_passed=False, confidence_tx_dev_db=confidence.tx_dev_db,
                  confidence_rx_dev_db=confidence.rx_dev_db), temperatures, now)
    return dict(tail=[], error="Доверительный тест АКМ не пройден — проверьте соединения и откалибруйте заново "
                               "(СТО ФРАК-002-2026 п. 11.4)", **bench.UNCALIBRATED)


def _load(uow, repository, row, command, artifact, item, data, now):
    """11.4.2: LOAD at the calibration plane of each port, max VSWR ≤ 1,06 over the whole band."""
    confidence_receipt = repository.receipts(row, "acm_confidence")[-1]
    calibration_temps = confidence_receipt.result["temperatures"]
    temperatures = command.result["temperatures"]
    if refusal := bench.temperature_refusal(calibration_temps, temperatures):
        raise ProductionValidationError(refusal)
    port = 1 if item["target"] == "port1" else 2
    value = load_vswr(data, port)
    _add(repository, row, command, artifact.id, item, "pass" if value <= LOAD_LIMIT else "fail",
                        dict(vswr=journal_number(value), limit=LOAD_LIMIT, temperatures=temperatures), now)
    if port == 1:
        return {}
    loads = {i.target: i.result["vswr"] for i in repository.items(row.id, item["run_id"])}
    confidence = confidence_from_receipt(confidence_receipt.result)
    record_row = _journal(uow, repository, row, command, "session",
                          dict(confidence_passed=confidence.passed, confidence_tx_dev_db=confidence.tx_dev_db,
                               confidence_rx_dev_db=confidence.rx_dev_db, load_p1_vswr=loads["port1"],
                               load_p2_vswr=loads["port2"]), calibration_temps, now)
    if not record_row.passed:
        return dict(error="LOAD на порту превышает КСВН 1,06 — проверьте соединения и откалибруйте заново "
                          "(СТО ФРАК-002-2026 п. 11.4)", **bench.UNCALIBRATED)
    calibrated = repository.receipts(row, "acm_calibrate")[-1]
    return dict(calibrated_at=calibrated.finished_at, calibration_temps=calibration_temps,
                profile_hash=bench.profile_hash(row.snapshot["profile"]), calibration_check_id=record_row.id)


def _file_data(uow, file_id, sha256, what) -> TwoPort:
    file = uow.session.get(ProductionInstrumentFileRow, UUID(file_id))
    if file is None or hashlib.sha256(file.content).hexdigest() != sha256:
        raise ProductionValidationError(f"Файл {what} изменился после начала замера — повторите замер")
    return read_touchstone(file.content, file.file_name)


def _on_grid(data: TwoPort, grid: TwoPort, what: str) -> TwoPort:
    """The same S2VNA grid read back from a file: equal within max(1 Гц, 1e-9·f), then the measured grid is used —
    never an interpolation."""
    same_grid(data, grid, what)
    return TwoPort(grid.frequencies, data.s11, data.s21, data.s12, data.s22)


def _peak(data: TwoPort) -> tuple[float, float]:
    index, value = max(enumerate(max(vswr(a), vswr(b)) for a, b in zip(data.s11, data.s22)), key=lambda p: p[1])
    return journal_number(value), data.frequencies[index] / 1e6


def _instrument(uow, repository, row, command, artifact, item, data, now):
    """12.2 / 13.1 / 13.3: judged on the de-embedded file the card would get; with the earlier connections of the series
    after the 12.4 repeat. Matings are recorded at the sweep (10.7); the file goes to the card on «Записать»."""
    temperatures = command.result["temperatures"]
    name, content, result_data = _instrument_file(uow, row, item, None, data, dict(id=str(command.id), **item), now,
                                                  temperatures, _calibration_ref(row))
    snapshot = row.snapshot
    model = snapshot.get("vna_model") or bench.VNA_MODEL
    card, rf = _card(uow, UUID(item["instrument_id"]))
    current = bench.characterize(name, content, card.id, snapshot["operator"], now)
    repeats = []
    for previous in repository.items(row.id, item["run_id"]):
        if previous.group_id != UUID(item["group_id"]):
            continue
        stored = repository.artifact(previous.command_id)
        raw = read_touchstone(bytes(stored.content), stored.file_name)
        p_name, p_content, _ = _instrument_file(uow, row, item, previous, raw, dict(id=str(previous.command_id)),
                                                previous.created_at, previous.result["temperatures"],
                                                previous.result["calibration"])
        repeats.append(bench.characterize(p_name, p_content, card.id, snapshot["operator"], previous.created_at))
    judged = application(current, tuple(p.series for p in rf.interfaces), norm_for(card.name, card.doc_designation or "", rf),
                         uncertainty=uncertainty_for(model), loss_uncertainty=loss_uncertainty_for(model),
                         repeats=tuple(repeats))
    verdict = bench.verdict_of(judged)
    totals = summary(result_data)
    worst_vswr, worst_mhz = _peak(result_data)
    _add(repository, row, command, artifact.id, item, verdict, dict(
        file_name=name, fmax_hz=item["fmax_hz"], temperatures=temperatures, worst_vswr=worst_vswr, worst_mhz=worst_mhz,
        vswr1=journal_number(totals.vswr1), vswr2=journal_number(totals.vswr2), loss_db=journal_number(totals.loss_db),
        **bench.application_view(judged)), now)
    return {}


def _instrument_matings(row, item):
    """10.7: the item's both sides every connection; a fixture's inner side every connection and its outer side with
    the cable once per item (it stays on the cable); without a fixture the cable end mates the item every time."""
    entries = [dict(instrument_id=item["instrument_id"], end=end, delta=1, kind="measurement") for end in ("A", "B")]
    fixtures = {f["side"]: f for f in item["fixtures"]}
    for index, side in enumerate(("left", "right")):
        cable = row.snapshot["cables"][index]
        cable_entry = dict(instrument_id=cable["instrument_id"], end=cable["end"], delta=1, kind="measurement")
        fixture = fixtures.get(side)
        if fixture is None:
            entries.append(cable_entry)
            continue
        inner = "B" if (index == 0) != bool(fixture["reversed"]) else "A"
        entries.append(dict(instrument_id=fixture["instrument_id"], end=inner, delta=1, kind="measurement"))
        if item["connection_no"] == 1:
            entries += [cable_entry, dict(instrument_id=fixture["instrument_id"], end="A" if inner == "B" else "B",
                                          delta=1, kind="measurement")]
    return entries


def _assembly(uow, repository, row, command, artifact, item, data, now):
    """12.3 via the ОТК record (record_vswr): its 13.3 rule decides «на границе» and the repeat; the attempt is the
    record of the sweep (in «Замеры КСВН» at once, as every ОТК attempt)."""
    result = command.result
    settings = {**result["profile"], "session_id": str(row.id), "instrument_serial": result["serial"],
                "orientation": "both", "port_to_end": {"1": "A", "2": "B"}, "traces": list(result["profile"]["traces"]),
                "correction": result["correction"], "correction_enabled": True, "correction_status": "COR",
                "smoothing_enabled": False, "averaging_enabled": False, "qualification_reason": "",
                "automatic_acceptance": True, "bench_command_id": str(command.id)}
    files = (VswrFileInput(file_name=artifact.file_name, content=bytes(artifact.content), end=None, capture_settings=settings,
                           capture_verified=True, evidence_id=artifact.id),)
    measurement = record_vswr(RecordVswrCommand(
        task_id=UUID(item["task_id"]), position=item["product_position"], instrument_id=row.instrument_id, file_name="",
        content=b"", files=files, port_mapping={"1": "A", "2": "B"}, norm=row.snapshot["assembly"]["norm"],
        actor=_actor(row.snapshot), now=now, continue_series=not item.get("fresh_series")), uow, manage_transaction=False)
    _add(repository, row, command, artifact.id, item, measurement.verdict.value, dict(
        measurement_id=str(measurement.id), attempt=measurement.attempt, max_vswr=float(measurement.max_vswr),
        max_vswr_mhz=float(measurement.max_vswr_mhz), reason=measurement.verdict_reason,
        repeat_of=str(measurement.repeat_of) if measurement.repeat_of else None,
        uncertainty=float(measurement.uncertainty) if measurement.uncertainty is not None else None,
        temperatures=result["temperatures"]), now, vswr_measurement_id=measurement.id)
    return {}


def _flex(uow, repository, row, command, artifact, item, data, now):
    target, temperatures = item["target"], command.result["temperatures"]
    if target in ("port1", "port2"):
        value = load_vswr(data, 1 if target == "port1" else 2)
        _add(repository, row, command, artifact.id, item, "pass" if value <= LOAD_LIMIT else "fail",
                            dict(vswr=journal_number(value), limit=LOAD_LIMIT, temperatures=temperatures,
                                 result_text=f"КСВН {number_text(value, 4)} ≤ 1,06"), now)
        return {}
    if target == "thru":
        loss = summary(data).loss_db
        _add(repository, row, command, artifact.id, item, None, dict(
            temperatures=temperatures, result_text=f"перемычка запомнена: потери до {number_text(loss, 3)} дБ"), now)
        return {}
    done = {i.target: i for i in repository.items(row.id, item["run_id"])
            if i.result.get("calibration", {}).get("check_id") == str(row.calibration_check_id)}
    before_artifact = repository.artifact(done["thru"].command_id)
    bend = flex(read_touchstone(bytes(before_artifact.content), before_artifact.file_name), data)
    record_row = _journal(uow, repository, row, command, "flex", dict(
        s21_dev_db=bend.s21_dev_db, vswr_dev=bend.vswr_dev, load_p1_vswr=done["port1"].result["vswr"],
        load_p2_vswr=done["port2"].result["vswr"]), temperatures, now)
    text = f"S21 {number_text(record_row.s21_dev_db, 3)} дБ ≤ 0,05; КСВН ±{number_text(record_row.vswr_dev, 4)}"
    _add(repository, row, command, artifact.id, item, "pass" if record_row.passed else "fail",
                        dict(s21_dev_db=record_row.s21_dev_db, vswr_dev=record_row.vswr_dev, check_id=str(record_row.id), check_passed=record_row.passed,
                             temperatures=temperatures, result_text=text), now)
    return {} if record_row.passed else dict(error="Проверка на изгиб не пройдена — замените кабель (СТО п. 10.6)")


def _control(uow, repository, row, command, artifact, item, data, now):
    """Разд. 14, weekly: the control standard against its reference curve, deviation within U."""
    reference = _file_data(uow, item["reference_id"], item["reference_sha256"], "эталонной кривой")
    measured = select_band(data, reference.frequencies[-1])
    check = control_standard(measured, _on_grid(reference, measured, "Эталонная кривая"))
    temperatures = command.result["temperatures"]
    record_row = _journal(uow, repository, row, command, "control_standard", dict(
        standard_inventory=item["number"], reference=item["reference"][:300], vswr_dev=check.vswr_dev,
        vswr_limit=check.vswr_limit, s21_dev_db=check.s21_dev_db, s21_limit_db=check.s21_limit_db), temperatures, now)
    text = (f"КСВН {number_text(record_row.vswr_dev, 4)} при U {number_text(record_row.vswr_limit, 4)}; "
            f"S21 {number_text(record_row.s21_dev_db, 3)} дБ при U {number_text(record_row.s21_limit_db, 2)} дБ")
    _add(repository, row, command, artifact.id, item, "pass" if record_row.passed else "fail",
                        dict(check_id=str(record_row.id), check_passed=record_row.passed, temperatures=temperatures, result_text=text), now)
    return {} if record_row.passed else dict(
        error="Контрольная мера вне U — замеры остановить, проверить калибровку и кабели (СТО разд. 14)")


def _kit_text(kind: str, check) -> str:
    """One connection of a standard in words: «|Г| 0,012 ≤ 0,035 — годен»."""
    symbol, unit = {"LOAD": ("|Г|", ""), "THRU": ("ΔS21", " дБ")}.get(kind, ("Δ отражения", " дБ"))
    verdict = "годен" if check.deviation <= check.limit + 1e-12 else "не годен"
    if check.ratio == 0:   # no deviation at any point: the judging point (and its limit) is not defined
        return f"{symbol} {number_text(check.deviation, 3)}{unit} — {verdict}"
    return f"{symbol} {number_text(check.deviation, 3)} ≤ {number_text(check.limit, 3)}{unit} — {verdict}"


def _kit(uow, repository, row, command, artifact, item, data, now):
    """Разд. 14, monthly (МП п. 10.6, метод 4): each standard in three connections; the worst one decides."""
    temperatures = command.result["temperatures"]
    standard = bench.kit_standard(item["definition"])
    single = judge_kit_standard(standard, item["port"], [data], KitTolerance())
    connection_text = _kit_text(item["standard"], single)
    if item["connection_no"] < bench.REPEATS:
        _add(repository, row, command, artifact.id, item, None, dict(temperatures=temperatures,
                                                                     result_text=connection_text), now)
        return {}
    group = [i for i in repository.items(row.id, item["run_id"]) if i.group_id == UUID(item["group_id"])]
    sweeps = []
    for previous in group:
        stored = repository.artifact(previous.command_id)
        sweeps.append(read_touchstone(bytes(stored.content), stored.file_name))
    sweeps.append(data)
    check = judge_kit_standard(standard, item["port"], sweeps, KitTolerance())
    ratio = journal_number(check.ratio)
    label = item["definition"].get("label") or item["standard"]
    judged = [i for i in repository.items(row.id, item["run_id"]) if i.connection_no == bench.REPEATS
              and i.result.get("calibration", {}).get("check_id") == str(row.calibration_check_id)]
    result = dict(ratio=ratio, deviation=journal_number(check.deviation), limit=journal_number(check.limit), label=label,
                  temperatures=temperatures,
                  result_text=f"{connection_text}; по трём подключениям {number_text(ratio, 2)} допуска")
    if item["standard"] != "THRU":
        _add(repository, row, command, artifact.id, item, "pass" if ratio <= 1 else "fail", result, now)
        return {}
    ratios = [(i.result["label"], i.target, i.result["ratio"]) for i in judged] + [(label, "thru", ratio)]
    note = "Доля допуска √(U²+δ²) по мерам: " + "; ".join(
        f"{name}{' п.' + target[-1] if target != 'thru' else ''} {number_text(value, 2)}" for name, target, value in ratios)
    record_row = _journal(uow, repository, row, command, "kit_standards",
                          dict(standard_inventory=item["number"], kit_ratio=max(v for _, _, v in ratios)),
                          temperatures, now, note=note)
    _add(repository, row, command, artifact.id, item, "pass" if ratio <= 1 else "fail",
                        {**result, "check_id": str(record_row.id), "check_passed": record_row.passed,
                         "result_text": f"худшая доля допуска {number_text(record_row.kit_ratio, 2)} (≤ 1 — годен)"}, now)
    return {} if record_row.passed else dict(error="Меры набора вне допуска — замеры остановить до выяснения причины "
                                                   "(СТО разд. 14)")


"""«ВАЦ — рабочее место» from ERP: a bench session (purpose='bench', protocol v2), one ACM calibration, many items.

S3: open a session (the station: S2VNA card, its PC, its АКМ and two test cables) → profile → «подключите АКМ» →
acm_calibrate → «Кор» → the device read again (judged here) → journal row «только калибровка» (form В.1) → calibrated.
The confidence test and LOAD on each port after the ACM were dropped by the owner (26.11.2026); an old session that
still holds those steps is shown and judged as before. Before every item the calibration must still hold (п. 11.5): < 4 h, ±1 °C, the
same ВАЦ/АКМ, the same profile, «Кор»; a failed weekly/monthly control stops measurements (разд. 14).
S4: items — a СИ (переход, 12.2/13.1/13.3, «на границе» → 12.4 three connections, «Записать в карточку»), a КБ
position (12.3 through the ОТК VSWR record), and checks — weekly control standard, monthly 6650F27.

The screen's contract is `frontend/src/vnaBenchApi.ts` (S5): every POST queues agent work and returns the state at once,
the screen polls `GET state`; refusals are `detail: {code, message}`. Each accepted capture is judged in the agent's
result request in the same unit of work as its receipt (journal row, matings, VSWR attempt, the session's next step).
Arinst v3 selects a separate diagnostic state DTO and preserves raw receipt artifacts. Quality save endpoints
remain blocked until characterized standards and an independent physical bench comparison are commissioned.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from heapq import nlargest
import hashlib
import json
import re
from typing import Annotated, Callable, Literal
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import Integer, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import object_session

from app.modules.production import kit_characterization_run as kc
from app.modules.production import vna_bench as bench
from app.modules.production.api import techcards_uow_factory
from app.modules.production.arinst_calibration import CalibrationContext
from app.modules.production.application import ConcurrentProductionUpdate
from app.modules.production.autoplan_api import references_uow_factory
from app.modules.production.domain import ProductionValidationError
from app.modules.production.infrastructure import (ProductionInstrumentFileRow, ProductionInstrumentRow,
                                                   ProductionSerialRow, ProductionTaskRow)
from app.modules.production.instrument_characterization import application, characterization, connector_line, series_cap_ghz
from app.modules.production.instrument_characterization import _date as _measurement_date_hint
from app.modules.production.instrument_norms import loss_uncertainty_for, norm_for, uncertainty_for, vna_model
from app.modules.production.mating_api import record_batch
from app.modules.production.matings import end_names
from app.modules.production.kit_characterization_data import CharacterizationCurve
from app.modules.production.rf_infrastructure import RfDetailsRow, RfInterfaceRow, RfRepository
from app.modules.production.rf_kit_characterization import CHARACTERIZED_DEFINITION, LOADS_DEFINITION, REQUIRED_PIECES
from app.modules.production.rf_kit_characterization_infrastructure import (RfKitRepository, RfKitStandardRow,
                                                                           RfStandardCharacterizationRow)
from app.modules.production.rf_interfaces import RfDetails, parse_interfaces
from app.modules.production.traceability import InstrumentFile, InstrumentFileKind, factory_number_key
from app.modules.production.vna_bench_protocol import ArinstOptions, ArinstProfile, Screen, screen_hash
from app.modules.production.vna_check_infrastructure import VALUE_FIELDS, VnaCheckRepository, VnaCheckRow
from app.modules.production.vna_characterization import is_characterization_file
from app.modules.production.vna_checks import (CALIBRATION_ONLY_NOTE, LOAD_LIMIT, KitTolerance, confidence_from_receipt,
                                               control_standard, expected_verdict, flex, journal_number,
                                               kit_standard as judge_kit_standard, load_vswr, same_grid)
from app.modules.production.vna_dsp import TRACES, TwoPort, VnaDataError, read_touchstone, select_band, summary, vswr, write_touchstone
from app.modules.production.vna_screen_infrastructure import VnaScreenRepository, definition_of
from app.modules.production.vna_screens import ItemLimits, ScreenDef, item_limits, number_text, screen as bench_screen
from app.modules.production.vna_session_infrastructure import VnaBenchItemRow, VnaCommandRow, VnaSessionRepository, VnaSessionRow
from app.modules.production.vna_sessions import TERMINAL, Profile, SessionConflict, StrictModel, resolved_profile
from app.modules.production.vswr import RecordVswrCommand, VswrFileInput, record_vswr
from app.modules.warehouse import vna_jobs
from app.modules.warehouse.devices import DeviceRow, VnaBindingRow, VnaControlRow, instrument_identity
from app.modules.warehouse.scan_agents import LIVE_FOR, ScanAgentRow
from app.modules.warehouse.scan_jobs import ScanJobClosed
from app.shared.clock import MOSCOW, as_utc
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from .coax_vswr_api import CoaxRefs, _task_etk, task_norm
from .vna_capture_instrument import arinst_station, capture_instrument
from .vna_measurement_api import Factory, Now, Person, _expire
from .vna_plan_api import vna_plan

router = APIRouter(tags=["production"])
PREFIX = "/api/production/vna-bench"
Techcards = Annotated[Callable | None, Depends(techcards_uow_factory)]
Refs = Annotated[Callable | None, Depends(references_uow_factory)]
MEASURING = ("instrument", "assembly", "kit_characterization")   # stopped by a failed weekly/monthly control (разд. 14)
ITEMS = ("instrument", "assembly")
# "flex" stays only to show the bend checks already made (history, an old open session); it is no longer started.
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
FLEX_DROPPED = "Шевеление кабелей отменено владельцем (26.11.2026): после калибровки АКМ оно не нужно"


class Refusal(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _agent_code(message: str) -> str:
    if "Подключённый ВАЦ с нужным серийным номером не найден" in message:
        return "vna_not_connected"
    if "Агент на закреплённом ПК не отвечает" in message:
        return "agent_offline"
    if "не закреплён" in message:
        return "station_setup"
    if "Привязка ВАЦ изменилась" in message:
        return "conflict"
    if "Метод калибровки не подтверждён" in message:
        return "agent_capability_missing"
    if "версии 1" in message or "не поддерживает рабочее место" in message:
        return "agent_old"
    if "серийный" in message.lower():
        return "serial_changed"
    if "занят" in message or "Сначала завершите" in message:
        return "device_busy"
    return "refused"


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


class CharacterizationTarget(StrictModel):
    kit_id: UUID   # СИ.004 SMA-MF06


class SessionInput(StrictModel):
    """Everything optional: by default the station is the S2VNA card with its accessories (АКМ, two test cables)."""
    agent_id: str | None = Field(default=None, min_length=1, max_length=80)
    instrument_id: UUID | None = None
    acm_id: UUID | None = None
    cables: list[Cable] | None = Field(default=None, min_length=2, max_length=2)
    # Calibrate on the ETK band of this КБ (СТО прил. А.2); without it — the «ОТК — переход» grid (СИ and checks).
    assembly: AssemblyProfile | None = None
    # «Характеристика мер»: the SMA-MF06 kit, calibrated on the 1 MHz grid «до 3 ГГц», then «3–6 ГГц».
    characterization: CharacterizationTarget | None = None
    arinst_profile: ArinstProfile | None = None


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


class ShowScreenInput(Version):
    screen_id: UUID


class CheckInput(Version):
    kind: Literal["flex", "weekly", "monthly"]
    instrument_id: UUID | None = None                                # СИ.009 / the 6650F27 card; default — the only one
    label: str = Field(default=bench.KIT_LABEL, min_length=1, max_length=64)
    selection: dict[str, str] = Field(default_factory=dict, max_length=7)   # OPEN_1 … THRU → label of the standard


# ---- station and state ---------------------------------------------------------------------------------------------

def _station(uow, instrument_id=None):
    """The bench ВАЦ: the given card or the S2VNA card with a serial bound to a PC (one bench in the shop)."""
    query = select(ProductionInstrumentRow).where(ProductionInstrumentRow.vna_app.in_(
        ("S2VNA", "Arinst VL") if instrument_id else ("S2VNA",)))
    if instrument_id is not None:
        query = query.where(ProductionInstrumentRow.id == instrument_id)
    cards = [c for c in uow.session.scalars(query.order_by(ProductionInstrumentRow.inventory_number))
             if (c.serial_number or "").strip() or c.vna_app == "Arinst VL"]
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


def _admin(principal) -> bool:
    return bool(principal.full_access and not principal.service and principal.viewer is None)


def _owned(uow, session_id, principal, *, admin_ok=False):
    """The opener's session. `admin_ok`: an administrator may also stop or recover someone else's session
    (the opener is away and the bench is stuck); such actions are journaled."""
    row = VnaSessionRepository(uow.session).get(session_id)
    if row.purpose != "bench" or (row.operator_id != principal.actor.id and not (admin_ok and _admin(principal))):
        raise KeyError("Сеанс рабочего места ВАЦ не найден")
    return row


def _actor(snapshot) -> Actor:
    operator = snapshot["operator"]
    return Actor(id=UUID(operator["id"]), name=operator["name"], department=operator["department"])


def _profile(row) -> Profile:
    _planar_only(row)
    return Profile.model_validate(row.snapshot["profile"])


def _planar_only(row):
    if row.snapshot.get("source") == "arinst":
        raise Refusal("commissioning_required", bench.ARINST_COMMISSIONING)


def _screen(uow, purpose: str) -> ScreenDef:
    """The stored default screen of a purpose (production_vna_screens; the code preset while the table is empty)."""
    return VnaScreenRepository(uow.session).default(purpose)


def _commands(uow, row):
    return list(uow.session.scalars(select(VnaCommandRow).where(VnaCommandRow.session_id == row.id)
                                    .order_by(VnaCommandRow.created_at, VnaCommandRow.expected_version)))


def _latest_status(commands):
    done = [c for c in commands if c.op == "device_status" and c.status == "done"]
    return done[-1] if done else None


def _device(uow, vna, row, commands, now, *, can_transfer=False):
    serial = (vna.serial_number or "").strip() if vna else ""
    binding = uow.session.get(VnaBindingRow, serial) if serial else None
    agent = uow.session.get(ScanAgentRow, binding.machine_id) if binding else None
    live = lambda moment: moment is not None and as_utc(bench.aware(moment, now)) >= as_utc(now) - LIVE_FOR  # noqa: E731
    reports = list(uow.session.execute(select(DeviceRow, ScanAgentRow).join(ScanAgentRow).where(
        DeviceRow.kind == "s2vna", DeviceRow.device_id == serial, ScanAgentRow.legacy.is_(False))
        .order_by(DeviceRow.last_seen.desc(), ScanAgentRow.machine_id))) if serial else []
    device = next((d for d, pc in reports if binding and pc.machine_id == binding.machine_id), None)
    agent_online = bool(agent and not agent.legacy and live(agent.last_seen))
    vna_online = bool(agent_online and device and device.present and live(device.last_seen))
    last_device, last_pc = reports[0] if reports else (None, None)
    targets = [dict(id=pc.machine_id, pc=pc.pc_name or pc.machine_id, version=pc.version,
                    vna=dict(name=d.name, ready=True)) for d, pc in reports
               if binding and pc.machine_id != binding.machine_id and live(pc.last_seen) and d.present
               and live(d.last_seen) and (device is None or as_utc(d.last_seen) > as_utc(device.last_seen))]
    transfer_blocked = bool(targets and VnaSessionRepository(uow.session).active(vna.id, serial))
    status = _latest_status(commands)
    reading = status.result if status and vna_online and row and row.machine_id == binding.machine_id else {}
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
    if refusal and refusal["code"] == "vna_not_connected":
        seen = (f" Последний раз виден {as_utc(bench.aware(last_device.last_seen, now)).astimezone(MOSCOW):%d.%m %H:%M} МСК"
                f" на {last_pc.pc_name or last_pc.machine_id}." if last_device else "")
        refusal["message"] = f"ВАЦ не подключён.{seen} Включите ВАЦ, подключите USB и запустите S2VNA."
    model = row.snapshot.get("vna_model") if row else (vna_model(f"{vna.name} {vna.doc_designation or ''}") if vna else "")
    acm = reading.get("acm")
    bound_pc = (agent.pc_name or agent.machine_id) if agent else None
    return dict(pc_name=bound_pc, bound_pc=bound_pc, bound_agent_id=binding.machine_id if binding else None,
                agent_online=agent_online, vna_online=vna_online,
                agent_last_seen=bench.aware(agent.last_seen, now) if agent else None,
                agent_ip=(agent.ip_lan or agent.ip_seen) if agent else None,
                last_seen_pc=(last_pc.pc_name or last_pc.machine_id) if last_pc else None,
                last_seen_at=bench.aware(last_device.last_seen, now) if last_device else None,
                transfer=dict(serial=serial, owner_id=binding.machine_id, agents=targets)
                if can_transfer and targets and not vna_online and not transfer_blocked else None,
                transfer_blocked_reason="Сначала завершите сеанс ВАЦ на закреплённом ПК, затем перенесите прибор"
                if can_transfer and transfer_blocked and not vna_online else None,
                agent_version=agent.version if agent else None,
                instrument_number=vna.inventory_number if vna else "", instrument_id=str(vna.id) if vna else None,
                model=f"Planar {model}" if model else (vna.name if vna else ""),
                serial=serial if vna_online else None,
                s2vna_ready=reading.get("ready") if reading else (True if vna_online else None),
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
    # The weekly and monthly controls only: the bend check after every calibration was dropped by the owner (26.11.2026).
    statuses = {"ok": "ok", "overdue": "overdue", "never": "due", "failed": "failed"}
    return [dict(kind=CHECK_NAMES[d["kind"]], due_at=d["due_at"], last_at=d["last_at"], status=statuses[d["status"]])
            for d in VnaCheckRepository(uow.session).due(vna.id, now)]


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


def _run_screen(row, run_id) -> Screen:
    applied = [s for _, s in _run_steps(row, run_id) if s["op"] == "apply_screen"]
    return Screen.model_validate(applied[-1]["args"]["screen"])


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
    """The calibration's steps for the screen: since 26.11.2026 one «Калибровка АКМ» (acm_calibrate → «Кор» → the
    device reading that makes it valid); an old session that still holds the confidence test and LOAD shows all four."""
    calibration = _latest_run(row, ("calibration",))
    if calibration is None:
        return []
    indexes = {s["op"]: i for i, s in _run_steps(row, calibration["run_id"])}
    if "acm_confidence" not in indexes:
        return [_calibration_step(uow, row, commands, calibration, indexes)]
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
        view.append(_step_view(row, key, status, text, command))
    return view


def _calibration_step(uow, row, commands, calibration, indexes) -> dict:
    """One step over the whole calibration run: running while any of its commands is, passed once the closing device
    reading was accepted (its journal row exists), failed when a command or the judgement refused it."""
    run_commands = [c for i, _ in _run_steps(row, calibration["run_id"]) if (c := _command_for(commands, i)) is not None]
    final = indexes.get("device_status")
    last = run_commands[-1] if run_commands else None
    if any(c.status in ("pending", "claimed") for c in run_commands):
        status = "running"
    elif last is None:
        status = "pending"
    elif last.status != "done":
        status = _status(last)
    elif final is None or last.expected_step != final:
        # The judgement cut the calibration short (e.g. a grid the ACM cannot cover), or the ACM is still to connect.
        status = "failed" if final is None else "pending"
    else:
        accepted = VnaCheckRepository(uow.session).by_source(f"erp-vna-bench:{last.id}") is not None
        status = "passed" if accepted else "failed"
    return _step_view(row, "calibration", status, None, last if status != "pending" else None)


def _step_view(row, key, status, text, command) -> dict:
    refusal = None
    if status in ("failed", "refused") and row.error:
        refusal = dict(code=bench.refusal_code(row.error), message=row.error)
    if command is not None and command.status == "failed":
        refusal = dict(code=bench.refusal_code(command.result.get("error", "")), message=command.result.get("error", ""))
    return dict(key=key, status=status, result_text=text, refusal=refusal,
                finished_at=command.finished_at if command else None)


def _command_view(row, commands):
    active = [c for c in commands if c.status in ("pending", "claimed")]
    if not active:
        return None
    command = active[-1]
    step = row.steps[command.expected_step] if command.expected_step < len(row.steps) else {}
    label = OP_LABELS.get(command.op, command.op)
    if command.op == "apply_screen" and (step.get("item") or {}).get("kind") == "view":
        label = f"экран «{step['item']['name']}» на ВАЦ"
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
                      position=run["product_position"],
                      serial=_factory_numbers(uow, UUID(run["task_id"])).get(run["product_position"]) if run.get("task_id") else None,
                      name=run.get("task_name", ""), item=run.get("task_item", ""))
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
    if vna is not None and vna.vna_app == "Arinst VL":
        return _arinst_state(uow, principal, now, vna, row)
    commands = _commands(uow, row) if row else []
    session = None
    if row is not None:
        repository = VnaSessionRepository(uow.session)
        items = repository.items(row.id)
        runs = _runs(row)
        status = "open" if row.state not in TERMINAL else "closed" if row.state == "recorded" else "aborted"
        session = dict(id=str(row.id), version=row.version, status=status, state=row.state, started_at=row.created_at,
                       operator=row.snapshot["operator"]["name"], owned=row.operator_id == principal.actor.id,
                       can_recover=row.operator_id == principal.actor.id or _admin(principal),
                       can_release_unrestored=_admin(principal) and row.state == "recovery_required",
                       error=row.error or None, steps=_steps_view(uow, row, commands, items),
                       view_screen_id=_view_screen_id(row),
                       command=_command_view(row, commands),
                       items=[_item_view(uow, repository, row, r, runs, items, commands) for r in runs if r["kind"] in ITEMS],
                       checks=[_check_view(uow, row, r, runs, items, commands) for r in runs if r["kind"] in CHECK_NAMES],
                       characterization=_kit_view(uow, row, commands)
                       if row.snapshot.get("profile_kind") == "characterization" else None)
    return dict(device=_device(uow, vna, row, commands, now,
                             can_transfer=principal.full_access and not principal.service and principal.viewer is None),
                calibration=_calibration(row, now),
                due=_due(uow, vna, row, now), session=session)


def _view_screen_id(row) -> str | None:
    """The saved screen the operator put on S2VNA, while it is still there (the next item or check replaces it)."""
    applied = [s for s in row.steps if s["op"] == "apply_screen"]
    item = (applied[-1].get("item") or {}) if applied else {}
    return item.get("screen_id") if item.get("kind") == "view" else None


@router.get(PREFIX + "/state")
def bench_state(principal: Person, now: Now, factory: Factory, instrument_id: UUID | None = None):
    with _refusals():
        with factory() as uow:
            vna = _station(uow, instrument_id)
            row = _session_row(uow, vna.id if vna else None)
            session_id = row.id if row else None
        if session_id:
            _expire(factory, now, session_id=session_id)
        with factory() as uow:
            return _state(uow, principal, now, vna=_station(uow, instrument_id))


def _version(row, version):
    if row.version != version:
        raise Refusal("stale", "Состояние уже изменилось — экран обновлён, повторите действие")


RECOVERY_TEXT = ("Ответ ВАЦ потерян — нажмите «Восстановить» на карточке сеанса: прибор остановится и вернёт "
                 "настройки оператора")


def _act(factory, session_id, principal, now, action, *, recovery=False):
    """Run one session change under the session lock and answer the full state.
    A session whose device answer was lost accepts only recovery (the abort endpoint) until it is restored."""
    with _refusals():
        with factory() as uow:
            _owned(uow, session_id, principal, admin_ok=recovery)
        _expire(factory, now, session_id=session_id)
        with factory() as uow:
            row = _owned(uow, session_id, principal, admin_ok=recovery)
            repository = VnaSessionRepository(uow.session)
            repository.lock(row)
            if row.state == "recovery_required" and not recovery:
                raise Refusal("recovery_required", RECOVERY_TEXT)
            action(uow, repository, row)
            state = _state(uow, principal, now, row=row)
            uow.commit()
            return state


# ---- session ---------------------------------------------------------------------------------------------------------


class ArinstProfileInput(Version):
    profile: ArinstProfile


class ArinstCalibrationInput(Version):
    kit_id: UUID
    orientation: Literal["forward", "reverse"] = "forward"
    transmission: bool = True
    reference_plane: str = Field(min_length=1, max_length=300)
    fixture_ids: list[UUID] = Field(default_factory=list, max_length=10)
    # A characterized SMA-MF06: the gender of the standards mating the measurement plane (female for male cable ends)
    # and which THRU end faces port 1.
    standard_gender: Literal["male", "female"] | None = None
    thru_orientation: Literal["forward", "reverse"] = "forward"


class ArinstCaptureInput(Version):
    orientation: Literal["forward", "reverse"]
    target: Target | None = None


def _arinst_state(uow, principal, now, vna, row):
    identity = instrument_identity(uow.session, vna.id)
    serial = identity.serial if identity else ""
    binding = uow.session.get(VnaBindingRow, serial) if serial else None
    pc = uow.session.get(ScanAgentRow, binding.machine_id) if binding else None
    # Connecting/closing refreshes an expired observation; only the persisted owner must be online.
    connection_ready = bool(identity and identity.source == "arinst" and pc and not pc.legacy
                            and pc.last_seen is not None
                            and as_utc(bench.aware(pc.last_seen, now)) >= as_utc(now) - LIVE_FOR)
    control = uow.session.get(VnaControlRow, (binding.machine_id, serial)) if binding else None
    license_view = bench.arinst_license(control.license_state if control else None)
    repository = VnaSessionRepository(uow.session)
    view = repository.view(row) if row else None
    # Anchor receipts at dispatch, conservatively before the device observation.
    # HTTP delivery can be delayed; its arrival must never replace a newer heartbeat
    # observation with an older cached receipt.
    receipts = [c for c in _commands(uow, row) if c.status == "done" and c.result.get("license")] if row else []
    if receipts:
        latest = receipts[-1]
        observed = latest.claimed_at
        if control is None or as_utc(bench.aware(observed, now)) >= as_utc(bench.aware(control.reported_at, now)):
            license_view = bench.arinst_license(latest.result["license"]["license_state"])
    readiness = None
    ops = set()
    if identity:
        try:
            # Read-only state lookup: capability/presence, never the binding mutation lock.
            from app.modules.warehouse.devices import control_ops
            ops = control_ops(uow.session, binding.machine_id if binding else "", now, serial=serial, source="arinst")
            device = uow.session.get(DeviceRow, (binding.machine_id, "arinst", serial)) if binding else None
            ready = bool(ops and pc and device and device.present and not pc.legacy
                         and as_utc(bench.aware(pc.last_seen, now)) >= as_utc(now) - LIVE_FOR
                         and as_utc(bench.aware(device.last_seen, now)) >= as_utc(now) - LIVE_FOR
                         and license_view["state"] == "not_required")
            if not ready:
                readiness = "Прибор или агент недоступен; проверьте подключение"
        except ScanJobClosed as error:
            ready, readiness = False, str(error)
    else:
        ready, readiness = False, "Подтвердите аппаратный UID Arinst в карточке СИ"
    if view:
        view.update(owned=row.operator_id == principal.actor.id,
                    operator=row.snapshot["operator"]["name"],
                    status="closed" if row.state in TERMINAL else "open",
                    command=next((c for c in view["commands"] if c["status"] in ("pending", "claimed")), None))
    acquisitions = [c for c in receipts if c.result.get("acquisition")]
    latest_trace = None
    if acquisitions:
        last = acquisitions[-1]
        artifact = next(a for a in view["artifacts"] if a["command_id"] == str(last.id)
                        and a["file_name"] == "arinst-receipt.json")
        latest_trace = dict(command_id=str(last.id), artifact_id=artifact["id"], sha256=artifact["sha256"],
                            acquisition=last.result["acquisition"], profile=last.result["profile"],
                            acceptance_eligible=False)
    return dict(source="arinst", device=dict(instrument_id=str(vna.id), inventory_number=vna.inventory_number,
        serial=serial, printed_serial=vna.serial_number, hardware_uid=identity.hardware_uid if identity else None,
        firmware=row.snapshot.get("firmware") if row else identity.firmware if identity else None,
        options=row.snapshot.get("options") if row else None,
        machine_id=binding.machine_id if binding else None, connection_ready=connection_ready,
        control_ready=ready, reason=readiness,
        license=license_view, supported_ops=sorted(ops or ()), power_dbm=None, temperature_c=None, rf_stopped=None),
        calibration=_arinst_calibration_view(uow, row),
        kits=_arinst_kits(uow), calibration_kit=_arinst_setup_view(row),
        profile=row.snapshot.get("profile") if row else None, session=view,
        acceptance=dict(eligible=False, code="commissioning_required", message=bench.ARINST_COMMISSIONING),
        due=[], supported_channels=["S11", "S21"], latest_trace=latest_trace)


def _arinst_audit(uow, row, action, now, data):
    uow.audit.add(AuditEvent(id=uuid4(), occurred_at=now, actor=_actor(row.snapshot),
        event_type="production." + action, aggregate_id=row.id, details=data))


def _open_arinst(payload, principal, now, factory):
    if payload.acm_id or payload.cables or payload.assembly:
        raise ValueError("Для Arinst задайте профиль; оснастку и набор выбирают в шаге SOL/THRU")
    instrument = capture_instrument(factory(), payload.instrument_id, direct=True)
    with factory() as uow:
        stale = _session_row(uow, payload.instrument_id)
        stale_id = stale.id if stale else None
    if stale_id:
        _expire(factory, now, session_id=stale_id)
    with factory() as uow:
        owner = arinst_station(uow.session, payload.instrument_id, instrument["serial"], now,
                               machine_id=payload.agent_id)
        if payload.arinst_profile is not None:
            arinst_station(uow.session, payload.instrument_id, instrument["serial"], now,
                           machine_id=owner, op="apply_profile")
        vna_jobs.refuse_open_capture(uow.session, payload.instrument_id, instrument["serial"])
        repository = VnaSessionRepository(uow.session)
        if repository.active(payload.instrument_id, instrument["serial"]):
            raise Refusal("device_busy", "Прибор занят активной сессией ВАЦ")
        snapshot = dict(source="arinst", protocol_version=3, hardware_uid=instrument["hardware_uid"],
            firmware=instrument["firmware"], printed_serial=instrument["printed_serial"],
            profile=None, requested_profile=payload.arinst_profile.model_dump(mode="json") if payload.arinst_profile else None,
            operator=dict(id=str(principal.actor.id), name=principal.actor.name, department=principal.actor.department),
            kit_definitions={}, connection_id=str(uuid4()), calibration=dict(valid=False, status="required"))
        try:
            with uow.session.begin_nested():
                row = repository.create(now=now, operator_id=principal.actor.id, machine_id=owner, purpose="bench",
                    instrument_id=payload.instrument_id, instrument_serial=instrument["serial"], task_id=None,
                    operation_id=None, product_position=None, snapshot=snapshot,
                    steps=[bench.arinst_step("prepare")])
        except IntegrityError:
            if repository.active(payload.instrument_id, instrument["serial"]):
                raise Refusal("device_busy", "Прибор занят активной сессией ВАЦ") from None
            raise
        repository.act(row, "plan", row.version, row.step, now)
        repository.act(row, "start", row.version, row.step, now)
        _arinst_audit(uow, row, "vna_arinst_session_opened", now, dict(serial=row.instrument_serial))
        state = _state(uow, principal, now, row=row)
        uow.commit()
        return state


def _arinst_ready(uow, row, version, now, op):
    _version(row, version)
    if row.snapshot.get("source") != "arinst":
        raise ValueError("Операция доступна только для Arinst")
    if row.state != "ready":
        raise Refusal("device_busy", "Дождитесь завершения текущего шага")
    arinst_station(uow.session, row.instrument_id, row.instrument_serial, now, machine_id=row.machine_id, op=op)


@router.post(PREFIX + "/sessions/{session_id}/arinst/connect")
def arinst_connect(session_id: UUID, payload: Version, principal: Person, now: Now, factory: Factory):
    def action(uow, repository, row):
        _arinst_ready(uow, row, payload.version, now, "connect")
        snapshot = bench.arinst_invalidated(row.snapshot, "Повторное подключение требует новой калибровки")
        snapshot["connection_id"] = str(uuid4())
        repository.append(row, [bench.arinst_step("prepare")], now, snapshot=snapshot)
    return _act(factory, session_id, principal, now, action)


@router.post(PREFIX + "/sessions/{session_id}/arinst/profile")
def arinst_profile(session_id: UUID, payload: ArinstProfileInput, principal: Person, now: Now, factory: Factory):
    def action(uow, repository, row):
        _arinst_ready(uow, row, payload.version, now, "apply_profile")
        if row.snapshot.get("options"):
            ArinstOptions.model_validate(row.snapshot["options"]).validate_profile(payload.profile)
        snapshot = bench.arinst_invalidated(row.snapshot, "Профиль изменён; требуется новая калибровка")
        repository.append(row, [bench.arinst_step("apply_profile", dict(profile=payload.profile.model_dump(mode="json")))],
                          now, snapshot=snapshot)
        _arinst_audit(uow, row, "vna_arinst_profile_requested", now, dict(profile=payload.profile.model_dump(mode="json")))
    return _act(factory, session_id, principal, now, action)


@router.post(PREFIX + "/sessions/{session_id}/arinst/calibration")
def arinst_calibrate(session_id: UUID, payload: ArinstCalibrationInput, principal: Person, now: Now, factory: Factory):
    def action(uow, repository, row):
        _arinst_ready(uow, row, payload.version, now, "measure_standard")
        if row.snapshot.get("profile") is None:
            raise ValueError("Сначала задайте профиль измерения Arinst")
        kit = uow.instruments.get(payload.kit_id)
        rf = RfRepository(uow.session)
        if kit is not None and rf.get(payload.kit_id).kit_definition == LOADS_DEFINITION:
            raise ValueError("Набор SMA-MF06 ещё не охарактеризован: снимите меры на Planar («Характеристика мер») "
                             "и активируйте набор")
        if kit is None or rf.get(payload.kit_id).role != "solt_kit":
            raise ValueError("Выберите зарегистрированный калибровочный набор")
        # Loads-only SMA-MF06 is not promoted to characterized SOL standards.
        fixtures = []
        if len(set(payload.fixture_ids)) != len(payload.fixture_ids):
            raise ValueError("Оснастка повторяется")
        for identity in payload.fixture_ids:
            card = uow.instruments.get(identity)
            if card is None or rf.get(identity).role not in ("test_cable", "adapter"):
                raise ValueError("Оснастка не найдена в реестре")
            fixtures.append(dict(id=str(identity), definition=asdict(rf.get(identity))))
        snapshot = bench.arinst_invalidated(row.snapshot, "Начата калибровка SOL/THRU")
        run_id = str(uuid4())
        snapshot["calibration_setup"] = dict(run_id=run_id, kit_id=str(payload.kit_id),
            kit_definition=asdict(rf.get(payload.kit_id)), fixture_fingerprint=bench.fingerprint(fixtures),
            fixtures=fixtures, reference_plane=payload.reference_plane, orientation=payload.orientation,
            transmission=payload.transmission)
        labels = None
        if rf.get(payload.kit_id).kit_definition == CHARACTERIZED_DEFINITION:
            labels = _arinst_characterized(uow, snapshot, kit, payload)
        snapshot["calibration"] = dict(valid=False, status="acquiring", run_id=run_id)
        repository.append(row, bench.arinst_calibration_steps(snapshot, orientation=payload.orientation,
                          transmission=payload.transmission, run_id=run_id, labels=labels), now, snapshot=snapshot)
        _arinst_audit(uow, row, "vna_arinst_calibration_started", now, snapshot["calibration_setup"])
    return _act(factory, session_id, principal, now, action)


@router.post(PREFIX + "/sessions/{session_id}/arinst/confirm")
def arinst_confirm(session_id: UUID, payload: Version, principal: Person, now: Now, factory: Factory):
    def action(uow, repository, row):
        _version(row, payload.version)
        if row.snapshot.get("source") != "arinst" or row.state not in ("awaiting_standard", "awaiting_dut"):
            raise Refusal("conflict", "Нет шага, ожидающего подключения")
        current = row.steps[row.step]
        arinst_station(uow.session, row.instrument_id, row.instrument_serial, now, machine_id=row.machine_id,
                       op=current["op"])
        if current["op"] == "measure_standard" and (stale := _arinst_kit_stale(uow, row.snapshot)):
            raise Refusal("stale", stale)
        repository.act(row, "confirm", row.version, row.step, now)
    return _act(factory, session_id, principal, now, action)


@router.post(PREFIX + "/sessions/{session_id}/arinst/capture")
def arinst_capture(session_id: UUID, payload: ArinstCaptureInput, principal: Person, now: Now, factory: Factory):
    def action(uow, repository, row):
        _arinst_ready(uow, row, payload.version, now, "capture")
        if row.snapshot.get("profile") is None:
            raise ValueError("Сначала задайте профиль измерения Arinst")
        # A diagnostic acquisition never consumes a successful connection number or writes a quality decision.
        if payload.target is not None:
            raise Refusal("commissioning_required", bench.ARINST_COMMISSIONING)
        args = dict(profile=ArinstProfile.model_validate(row.snapshot["profile"]).model_dump(mode="json"),
                    orientation=payload.orientation)
        snapshot = row.snapshot
        if stale := _arinst_kit_stale(uow, snapshot):
            snapshot = bench.arinst_invalidated(snapshot, stale)
        setup = snapshot.get("calibration_setup", {})
        if setup.get("orientation") != payload.orientation:
            snapshot = bench.arinst_invalidated(snapshot, "Ориентация изменена; требуется отдельная калибровка")
        repository.append(row, [bench.arinst_step("capture", args, human=True,
            label="Подключите изделие" if payload.orientation == "forward" else "Переверните кабель: конец Б к порту 1",
            item=dict(kind="arinst_diagnostic", orientation=payload.orientation))], now, snapshot=snapshot)
    return _act(factory, session_id, principal, now, action)


def _complete_arinst(uow, repository, row, command_id, payload, now):
    if payload.backup_saved is not None:
        raise ValueError("Arinst не сохраняет резервную копию настроек Planar")
    arinst_station(uow.session, row.instrument_id, row.instrument_serial, now, machine_id=row.machine_id, op="close")
    previous_version = row.version
    command = repository.complete(row, command_id, payload.expected_version, payload.expected_step,
                                  payload.result, payload.error, now, advance=False)
    if command is None:
        if row.version != previous_version:
            _arinst_audit(uow, row, "vna_arinst_command_completed", now,
                dict(command_id=str(command_id), error=payload.error, state=row.state, acceptance_eligible=False))
        return
    result = command.result
    snapshot, tail, message = dict(row.snapshot), None, ""
    snapshot["options"] = result["options"]
    if result["firmware"] != snapshot["firmware"]:
        snapshot = bench.arinst_invalidated(snapshot, "Прошивка прибора изменилась")
        snapshot["firmware"] = result["firmware"]
    if result["profile"] is not None or command.op == "device_status":
        if snapshot["profile"] != result["profile"]:
            snapshot = bench.arinst_invalidated(snapshot, "Фактический профиль изменился")
        snapshot["profile"] = result["profile"]
    if command.op == "prepare":
        snapshot = bench.arinst_invalidated(snapshot, "Подключение требует новой калибровки")
        if snapshot.get("requested_profile"):
            tail = [bench.arinst_step("apply_profile", dict(profile=snapshot.pop("requested_profile")))]
        else:
            tail = [bench.arinst_step("device_status")]
    if command.op == "measure_standard":
        run_id = row.steps[row.step]["item"]["run_id"]
        steps = [s for s in row.steps if (s.get("item") or {}).get("run_id") == run_id]
        if result["standard"] == steps[-1]["args"]["standard"]:
            standards = []
            for receipt in repository.receipts(row, "measure_standard"):
                if (row.steps[receipt.expected_step].get("item") or {}).get("run_id") == run_id:
                    artifact = repository.artifact(receipt.id, "arinst-receipt.json")
                    standards.append((receipt.result, str(artifact.id), artifact.sha256))
            try:
                if stale := _arinst_kit_stale(uow, snapshot):
                    snapshot = bench.arinst_invalidated(snapshot, stale)
                    raise ValueError(stale)
                coefficients = bench.arinst_solve(snapshot, standards)
                artifact = repository.store_artifact(row, command, "arinst-calibration.json", coefficients,
                    dict(source="arinst", kind="coefficients", acceptance_eligible=False), now)
                snapshot["calibration"] = dict(valid=False, status="verification_required",
                    coefficient_artifact_id=str(artifact.id), sha256=artifact.sha256)
            except ValueError as error:
                message = str(error)
                snapshot["calibration"] = dict(valid=False, status="blocked", reason=message, run_id=run_id)
    _arinst_audit(uow, row, "vna_arinst_command_completed", now,
                  dict(command_id=str(command.id), op=result["op"], acceptance_eligible=False))
    if command.op == "release":
        repository._change(row, now, snapshot=bench.arinst_invalidated(snapshot, "Сеанс закрыт"))
        repository.finish(row, now)
    else:
        repository.proceed(row, now, tail=tail, snapshot=snapshot, error=message)

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
            selected = _station(uow, payload.instrument_id)
            is_arinst = selected is not None and selected.vna_app == "Arinst VL"
        if is_arinst:
            return _open_arinst(payload, principal, now, factory)
        if payload.arinst_profile is not None:
            raise ValueError("Профиль Arinst допустим только для Arinst")
        if payload.characterization is not None and payload.assembly is not None:
            raise ValueError("Характеристика мер — отдельный сеанс, без задания КБ")
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
        assembly, characterization = None, None
        if payload.characterization is not None:
            with factory() as uow:
                kit, _ = _kit_card(uow, payload.characterization.kit_id)
                characterization = dict(kit_id=str(kit.id), kit_number=kit.inventory_number, kit_name=kit.name,
                                        band="low", captures=[])
            profile = kc.band_profile("low")
        elif payload.assembly is None:
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
            snapshot = dict(profile=profile.model_dump(mode="json"),
                            profile_kind="assembly" if assembly else "characterization" if characterization else "adapter",
                            assembly=assembly, **({"characterization": characterization} if characterization else {}), vna_model=vna_model(f"{vna.name} {vna.doc_designation or ''}") or bench.VNA_MODEL,
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
                active = _session_row(uow, vna_id)
                if active is not None and active.state == "recovery_required":
                    raise Refusal("recovery_required", RECOVERY_TEXT)
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
def run_step(session_id: UUID, key: Literal["calibration"], payload: Version, principal: Person, now: Now,
             factory: Factory):
    """«Калибровать АКМ»: the operator connected the ACM — confirm the waiting calibration or redo it (11.5). An old
    session still waiting for its LOAD (dropped 26.11.2026) drops that step and calibrates anew."""
    def action(uow, repository, row):
        _planar_only(row)
        _version(row, payload.version)
        current = row.steps[row.step] if row.step < len(row.steps) else {}
        waiting = row.state in ("awaiting_standard", "awaiting_dut")
        if waiting and current.get("op") == "acm_calibrate":
            repository.act(row, "confirm", row.version, row.step, now)
            return
        if waiting and (current.get("item") or {}).get("kind") == "load":
            repository.act(row, "cancel_item", row.version, row.step, now)
        if row.state != "ready":
            raise Refusal("device_busy", "Калибровку сейчас начать нельзя — дождитесь окончания команды")
        # 11.5: redo the calibration in the same session; the operator has connected the ACM.
        repository.append(row, bench.calibration_steps(row.snapshot, acm_connected=True), now, error="",
                          **bench.UNCALIBRATED)
    return _act(factory, session_id, principal, now, action)


def _session_journal(uow, principal, row, now, event_type, **details):
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=row.id, event_type=event_type,
                             actor=Actor(id=principal.actor.id, name=principal.actor.name,
                                         department=principal.actor.department), occurred_at=now,
                             details={"session_id": str(row.id), "state": row.state,
                                      "operator": row.snapshot["operator"]["name"], **details}))


@router.post(PREFIX + "/sessions/{session_id}/abort")
def abort(session_id: UUID, payload: Version, principal: Person, now: Now, factory: Factory):
    """Stop the running command, or recover a session whose device answer was lost (abort, then restore the
    operator's backup). The opener or an administrator (journaled when it is someone else's session)."""
    def action(uow, repository, row):
        _version(row, payload.version)
        if row.operator_id != principal.actor.id:
            _session_journal(uow, principal, row, now, "production.vna_bench_session_stopped_by_admin")
        if row.state in ("recovery_required",):
            repository.act(row, "recover", row.version, row.step, now)
        else:
            repository.act(row, "abort", row.version, row.step, now)
    return _act(factory, session_id, principal, now, action, recovery=True)


@router.post(PREFIX + "/sessions/{session_id}/release-unrestored")
def release_unrestored(session_id: UUID, payload: Version, principal: Person, now: Now, factory: Factory):
    """Admin escape: restoring the operator's backup keeps failing, the bench must not stay unusable. The agent
    releases the VNA without loading the backup — S2VNA settings are then checked by hand. Journaled."""
    def action(uow, repository, row):
        if not _admin(principal):
            raise Refusal("forbidden", "Закрыть без восстановления может только администратор", status=403)
        _version(row, payload.version)
        if row.state != "recovery_required":
            raise Refusal("stale", "Сеанс уже не ждёт восстановления — экран обновлён")
        _session_journal(uow, principal, row, now, "production.vna_bench_closed_unrestored", error=row.error or "")
        repository.act(row, "release_unrestored", row.version, row.step, now)
    return _act(factory, session_id, principal, now, action, recovery=True)


@router.post(PREFIX + "/sessions/{session_id}/close")
def close(session_id: UUID, payload: Version, principal: Person, now: Now, factory: Factory):
    def action(uow, repository, row):
        _version(row, payload.version)
        if row.state != "ready":
            raise Refusal("device_busy", "Закрыть сеанс можно, когда прибор не выполняет команду")
        repository.act(row, "close", row.version, row.step, now)
    return _act(factory, session_id, principal, now, action)


# ---- objects ---------------------------------------------------------------------------------------------------------

@router.get(PREFIX + "/targets")
def targets(_principal: Person, factory: Factory):
    """Registry RF targets; connection/band readiness remains the existing plan's decision."""
    with _refusals(), factory() as uow:
        repository = RfRepository(uow.session)
        details = repository.list()
        result = []
        for card in uow.session.scalars(select(ProductionInstrumentRow).order_by(ProductionInstrumentRow.inventory_number)):
            rf = details.get(card.id, RfDetails())
            # Reuse registry RF roles and the same name parser as connector_line/planner.
            ports = rf.interfaces or parse_interfaces(card.name)
            if card.vna_app or rf.role in ("vna", "auto_calibration"):
                continue
            if not ports and rf.role == "unknown":
                continue
            result.append(dict(kind="instrument", instrument_id=str(card.id), number=card.inventory_number,
                               name=card.name, serial_number=card.serial_number or None,
                               interfaces_text=connector_line(card.name, card.serial_number or "", rf), last_measured_at=None))
        ids = [UUID(entry["instrument_id"]) for entry in result]
        item = VnaBenchItemRow
        last = {instrument_id: measured.isoformat() for instrument_id, measured in uow.session.execute(
            select(item.instrument_id, func.max(item.created_at)).where(
                item.instrument_id.in_(ids), item.kind == "instrument", item.target == "dut").group_by(item.instrument_id))}
        # Rank metadata by filename date to retain backfilled curves; undated names use upload order.
        # Header-only dates may differ: cap validation at three candidates, never scan all historical bodies.
        file_row = ProductionInstrumentFileRow
        files = select(file_row.instrument_id, file_row.id, file_row.file_name, file_row.uploaded_at).where(
                                       file_row.instrument_id.in_(ids), func.lower(file_row.file_name).like("%.s2p"),
                                       func.length(file_row.content) <= 2 * 1024 * 1024).execution_options(yield_per=1)
        candidates = {}
        for row in uow.session.execute(files):
            if not is_characterization_file(row.file_name):
                continue
            ranked = candidates.setdefault(row.instrument_id, [])
            ranked.append(((_measurement_date_hint(row.file_name) or row.uploaded_at.date(), row.uploaded_at, row.id), row.id))
            candidates[row.instrument_id] = nlargest(3, ranked)
        for instrument_id, ranked in candidates.items():
            for _, file_id in ranked:
                measured = characterization(uow.instrument_files.get(instrument_id, file_id))
                if measured:
                    if measured.measured_on.isoformat() > last.get(instrument_id, "")[:10]:
                        last[instrument_id] = measured.measured_on.isoformat()
                    break
        for entry in result:
            entry["last_measured_at"] = last.get(UUID(entry["instrument_id"]))
        return {"targets": result}


@router.post(PREFIX + "/resolve")
def resolve(payload: ResolveInput, principal: Person, factory: Factory):
    """A scan or a number: «СИ.035» (or 35), a task «ПЗ-0001/3», a product serial number «ПЗ-0001-0003» or its
    factory number «FRX.831К.КБ1.3», «FRX.831К.34.226.3», «FRX.708К.120907/AA.12» (0222; case, spaces, «K/К», «KB/КБ»
    and zeros in k as scanned). An assembly candidate carries the factory number in `serial` once the label is printed."""
    query = payload.query.strip()
    with _refusals(), factory() as uow:
        candidates = []
        number = f"СИ.{int(query):03d}" if query.isdigit() else query.upper().replace("CИ", "СИ")
        card = uow.instruments.by_inventory_number(number)
        if card is not None:
            rf = RfRepository(uow.session).get(card.id)
            kit = "SMA-MF06" if rf.kit_definition in (LOADS_DEFINITION, CHARACTERIZED_DEFINITION) else None
            candidates.append(dict(kind="instrument", instrument_id=str(card.id), number=card.inventory_number,
                                   name=card.name, serial_number=card.serial_number or None, kit=kit))
        task_number, position = query, None
        if match := re.fullmatch(r"(.+?)\s*/\s*(\d+)", query):
            task_number, position = match[1], int(match[2])
        serials = list(uow.session.scalars(select(ProductionSerialRow).where(ProductionSerialRow.serial_number == query)))
        if not serials and (key := factory_number_key(query)) is not None:
            # Factory numbers ending in this k, compared by the scan key: dots and «/» of the designation are kept.
            serials = [row for row in uow.session.scalars(select(ProductionSerialRow).where(
                ProductionSerialRow.item_serial == key[1], ProductionSerialRow.factory_number.is_not(None)))
                if factory_number_key(row.factory_number) == key]
        if serials:
            task_number = None
            found = {row.task_id: row.position for row in serials}
            task_rows = [uow.session.get(ProductionTaskRow, task_id) for task_id in found]
        else:
            task_rows = list(uow.session.scalars(select(ProductionTaskRow).where(ProductionTaskRow.number == task_number)))
        for task_row in task_rows:
            task = uow.production.get(task_row.id)
            if serials:
                position = found[task.id]
            positions = [position] if position else list(range(1, min(task.quantity, 50) + 1))
            numbers = _factory_numbers(uow, task.id)
            for p in positions:
                if 1 <= p <= task.quantity:
                    candidates.append(dict(kind="assembly", task_id=str(task.id), task_number=task.number, position=p,
                                           serial=numbers.get(p), name=task.name, item=task.item,
                                           operation_id=str(op) if (op := _vswr_operation(task)) else None))
        return {"candidates": candidates}


def _factory_numbers(uow, task_id) -> dict[int, str]:
    """Заводские номера изделий задания по позиции (0222): выданы при печати этикетки; до печати — нет."""
    return dict(uow.session.execute(select(ProductionSerialRow.position, ProductionSerialRow.factory_number).where(
        ProductionSerialRow.task_id == task_id, ProductionSerialRow.factory_number.is_not(None))).all())


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
    _planar_only(row)
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
            spec = _screen(uow, "adapter")
            plan, screen = card(spec.name, bench_screen(spec, item=limits), (profile.start_hz, top), norm_vswr,
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
        spec = _screen(uow, "assembly")
        plan, screen = card(spec.name, bench_screen(spec, band_hz=(low, high), assembly_vswr=assembly["vswr_max"]),
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
    _planar_only(row)
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


def _view_context(uow, row) -> tuple[ItemLimits | None, tuple[float, float], float | None]:
    """What a view draws its lines from: the ETK band and VSWR norm of an assembly session, else the norm of the СИ
    measured last (cut at its upper frequency); without either a view has no norm lines."""
    profile = _profile(row)
    if assembly := row.snapshot.get("assembly"):
        low, high = (float(v) * 1e6 for v in assembly["band_mhz"])
        return None, (low, high), assembly["vswr_max"]
    run = _latest_run(row, (*ITEMS, *CHECK_NAMES))
    item = None
    if run is not None and run["kind"] == "instrument":
        card, rf = _card(uow, UUID(run["instrument_id"]))
        item = item_limits(card.inventory_number, card.name, rf, stop_hz=run["fmax_hz"])
    return item, (profile.start_hz, profile.stop_hz), None


@router.post(PREFIX + "/sessions/{session_id}/screen")
def show_screen(session_id: UUID, payload: ShowScreenInput, principal: Person, now: Now, factory: Factory):
    """«Показать на ВАЦ»: a saved screen goes onto S2VNA in the operator's calibrated session — apply_screen only, no
    sweep; the calibration («Кор») stays. Without a valid calibration it is refused (calibration is never touched)."""
    def action(uow, repository, row):
        _version(row, payload.version)
        _gates(uow, row, now, "view")
        stored = VnaScreenRepository(uow.session).get(payload.screen_id)
        if not stored.active:
            raise Refusal("invalid", f"Экран «{stored.name}» отключён — включите его в «Экраны»", 422)
        item, band, vswr = _view_context(uow, row)
        screen = bench.fit_screen(bench_screen(definition_of(stored), item=item, band_hz=band, assembly_vswr=vswr),
                                  _profile(row))
        view = dict(kind="view", screen_id=str(stored.id), name=stored.name, screen_version=stored.version)
        repository.append(row, [bench.screen_step(screen, view)], now)
        uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=stored.id, event_type="production.vna_screen_shown",
                                 actor=Actor(id=principal.actor.id, name=principal.actor.name,
                                             department=principal.actor.department), occurred_at=now,
                                 details={"session_id": str(row.id), "name": stored.name, "version": stored.version,
                                          "screen_hash": screen_hash(screen)}))
    return _act(factory, session_id, principal, now, action)


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
        # The screen on S2VNA now: the item's own or a view the operator put there meanwhile (every bench screen is
        # fitted to the session profile, so any of them carries the captured traces).
        hash_ = bench.last_screen_hash(row.steps)
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
        _planar_only(row)
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
        if payload.kind == "flex":
            raise Refusal("invalid", FLEX_DROPPED, 422)
        kind = CHECKS[payload.kind]
        _gates(uow, row, now, kind)
        profile = _profile(row)
        if row.snapshot["profile_kind"] != "adapter":
            raise Refusal("other_calibration", "Проверки мерами — на сетке «ОТК — переход»; откройте сеанс без КБ")
        if kind == "control_standard":
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
            screen = bench_screen(_screen(uow, "adapter"), item=item_limits(
                card.inventory_number, card.name, rf, stop_hz=reference.frequencies[-1]))
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
            # Only the full 6650F27 has the passport description the monthly check judges against; an activated
            # SMA-MF06 is also «Набор SOLT» but its standards are the ERP's own measured curves.
            if (card_rf.role, card_rf.kit_definition) != ("solt_kit", "6650F27-full"):
                raise Refusal("station_setup", "Ежемесячная проверка — набором мер 6650F27 (роль «Набор SOLT», "
                                               "состав «6650F27» в карточке)")
            vna_jobs.control_station(uow.session, row.instrument_serial, now, machine_id=row.machine_id, ops={"read_kit"})
            meta = bench.run("kit_standards", instrument_id=str(card.id), number=card.inventory_number, label=payload.label,
                             selection=payload.selection)
            steps = bench.item_steps(profile, bench_screen(_screen(uow, "engineer")), None, meta)
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
            bend = bench.fit_screen(bench_screen(_screen(uow, "flex")), profile)
            hash_ = screen_hash(bend)
            memorized = [n for n, t in enumerate(bend.traces, start=1) if t.memory]
            steps = [bench.screen_step(bend, meta), bench.capture_step(profile, hash_, phase["label"], item, human=False)]
            if memorized:   # a stored flex screen always has one (ScreenDef); an older one without — no memory step
                steps.append(bench.step("store_memory", dict(screen_hash=hash_, traces=memorized), item=meta))
        else:
            steps = [bench.capture_step(profile, hash_, phase["label"], item, human=False)]
            if hash_ != bench.last_screen_hash(row.steps):
                # The operator put another view on S2VNA meanwhile: a check sweeps on its own screen (memory, lines).
                steps.insert(0, bench.screen_step(_run_screen(row, check_id), meta))
        steps.insert(0, bench.step("device_status", item=meta))   # the same ACM, «Кор», ±1 °C before each phase
        repository.append(row, steps, now, error="")
    return _act(factory, session_id, principal, now, action)


# ---- receipts: judged on the server in the agent's result request ----------------------------------------------------

def complete_command(uow, row, command_id, payload, now):
    """The bench branch of POST /api/stock/vna/commands/{id}/result: store the receipt, judge it, continue — one UoW."""
    repository = VnaSessionRepository(uow.session)
    if row.snapshot.get("source") == "arinst":
        return _complete_arinst(uow, repository, row, command_id, payload, now)
    command = repository.complete(row, command_id, payload.expected_version, payload.expected_step, payload.result,
                                  payload.error, now, backup_saved=payload.backup_saved, advance=False)
    if command is None:
        return
    if command.op == "release":
        if (row.steps[row.step].get("item") or {}).get("unrestored"):
            repository.abandon(row, now)
        else:
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
        # Refuse before anything is written: record_batch also queues audit events that a savepoint rollback would
        # not take back, so a refused calibration must not reach it.
        if row.snapshot["profile_kind"] == "characterization" and result.get("module_min_hz", 0) > _profile(row).start_hz:
            raise ProductionValidationError(f"АКМ калибрует от {result['module_min_hz'] / 1e6:g} МГц — сетка характеристики "
                                            f"начинается с {_profile(row).start_hz / 1e6:g} МГц")
        acm, cables = row.snapshot["acm"], row.snapshot["cables"]
        entries = [dict(instrument_id=acm["id"], end=end, delta=1, kind="calibration") for end in ("A", "B")]
        entries += [dict(instrument_id=c["instrument_id"], end=c["end"], delta=1, kind="calibration") for c in cables]
        _matings(uow, f"vna-calibration-{command.id}", entries, row, now)
        return {}
    if op == "acm_confidence":
        return _confidence(uow, row, command, now)   # an old session's step (before 26.11.2026)
    if op == "device_status" and item.get("kind") == "calibration":
        return _calibrated(uow, repository, row, command, now)
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
             "kit_standards": _kit, "kit_characterization": _kit_capture}[kind]
    return judge(uow, repository, row, command, artifact, item, data, now)


def _calibration_ref(row) -> dict:
    """The calibration a sweep was judged under — kept with the row: the card file and a repeat series use this one,
    never whatever calibration the session holds later."""
    check = object_session(row).get(VnaCheckRow, row.calibration_check_id) if row.calibration_check_id else None
    return dict(check_id=str(row.calibration_check_id) if row.calibration_check_id else None,
                calibrated_at=as_utc(row.calibrated_at).isoformat() if row.calibrated_at else None,
                # «not_required»: the owner dropped LOAD after the ACM (26.11.2026); older calibrations passed it.
                load_check="pass" if check is None or check.load_p1_vswr is not None else "not_required")


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
    if kind == "kit_characterization":
        # Decided when the capture was requested (_kit_connections): only the connections that capture made.
        return "measurement", [dict(instrument_id=m["instrument_id"], end=m["end"], delta=1, kind="measurement")
                               for m in item.get("matings", [])]
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
    kind = {"adapter": "ОТК — переход", "characterization": "Характеристика мер"}.get(row.snapshot["profile_kind"], "КБ по ЭТК")
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


def _calibrated(uow, repository, row, command, now):
    """11.2 (owner, 26.11.2026): the device read right after the ACM calibration — S2VNA ready, the same ACM, «Кор» on
    every trace, every temperature readable — makes the calibration valid with these temperatures; the journal row is
    «только калибровка» (no confidence test, no LOAD)."""
    result = command.result
    temperatures = result["temperatures"]
    if refusal := bench.device_refusal(result, row.snapshot["acm"]["serial"], _profile(row).traces):
        raise ProductionValidationError(refusal)
    if bench.temperature_refusal(temperatures, temperatures):
        raise ProductionValidationError("Температура ВАЦ или АКМ не прочитана — калибровка не принята, откалибруйте заново")
    record_row = _journal(uow, repository, row, command, "session", dict(calibration_only=True), temperatures, now,
                          note=CALIBRATION_ONLY_NOTE)
    calibrated = repository.receipts(row, "acm_calibrate")[-1]
    return dict(calibrated_at=calibrated.finished_at, calibration_temps=temperatures,
                profile_hash=bench.profile_hash(row.snapshot["profile"]), calibration_check_id=record_row.id)


def _confidence(uow, row, command, now):
    """An old session's confidence test (before 26.11.2026), judged as it was."""
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
    """An old session's LOAD (before 26.11.2026): at the calibration plane of each port, max VSWR ≤ 1,06."""
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


# ---- «Характеристика мер»: SMA-MF06 (СИ.004) on the Planar, consumed by Arinst -----------------------------------------
# Two calibrated passes (kit_characterization_run.BANDS), every piece identified by its registered UUID. Accepted captures
# live in the session snapshot next to their immutable raw artifacts; «Сохранить» rebuilds the curve from those artifacts
# into an immutable revision (RfKitRepository.append_revision); activation is the kit's own transaction.

class BandInput(Version):
    band: Literal["low", "high"]


class KitFixture(StrictModel):
    instrument_id: UUID
    reversed: bool = False   # port 1 side: its side 2 faces the cable; port 2 side: its side 1 faces the cable


class KitCaptureInput(Version):
    standard_id: UUID | None = None    # a registered piece of the session's kit …
    fixture_id: UUID | None = None     # … or an F–F fixture itself, between the two cable ends
    fixtures: list[KitFixture] = Field(default_factory=list, max_length=2)   # male pieces: port 1 side first


KIT_ORDER = [("OPEN", "female"), ("SHORT", "female"), ("LOAD", "female"), ("THRU", "female"),
             ("OPEN", "male"), ("SHORT", "male"), ("LOAD", "male"), ("THRU", "male")]


def _kit_card(uow, kit_id):
    card = uow.instruments.get(kit_id)
    rf = RfRepository(uow.session).get(kit_id) if card is not None else None
    if card is None or (rf.role, rf.kit_definition) not in (("load_kit", LOADS_DEFINITION),
                                                             ("solt_kit", CHARACTERIZED_DEFINITION)):
        raise Refusal("station_setup", "Характеристика мер — только для набора SMA-MF06 (СИ.004)", 422)
    return card, rf


def _characterization(row) -> dict:
    if row.snapshot.get("profile_kind") != "characterization":
        raise Refusal("other_calibration", "Меры снимают в сеансе «Характеристика мер» — откройте его для набора SMA-MF06")
    return row.snapshot["characterization"]


def _piece(uow, characterization, standard_id) -> RfKitStandardRow:
    piece = uow.session.get(RfKitStandardRow, standard_id)
    if piece is None or str(piece.kit_instrument_id) != characterization["kit_id"]:
        raise KeyError("Мера набора не найдена")
    return piece


def _fixture_card(uow, instrument_id):
    card, rf = _card(uow, instrument_id)
    if rf.role != "adapter" or len(rf.interfaces) != 2 or any(p.body != "розетка" for p in rf.interfaces):
        raise Refusal("station_setup", f"{card.inventory_number}: для мер «вилка» нужен переход розетка–розетка "
                                       "(роль «Переход», оба разъёма — розетка)", 422)
    return card


def _kit_connections(row, characterization, arrangement: dict, inner: list[dict]) -> tuple[list[dict], dict]:
    """10.7 for «Характеристика мер»: the connections this capture actually makes, and what then stays on the cables.

    arrangement: per cable index (0 — port 1, 1 — port 2) what is on its end: None (free), "piece" (the piece itself)
    or {instrument_id, end} — a fixture side. A fixture side already on that cable since an earlier capture under the
    same calibration is not mated again (it stays on the cable between captures); the piece mates every time, and so
    does the fixture side the piece is screwed onto (inner). A calibration takes everything off the cables."""
    mounted = characterization.get("mounted") or {}
    current = str(row.calibration_check_id) if row.calibration_check_id else None
    before = mounted.get("cables", {}) if mounted.get("calibration_check_id") == current else {}
    cables, matings, after = row.snapshot["cables"], [], {}
    for index in (0, 1):
        required = arrangement.get(index)
        if required is None:
            continue
        cable = dict(instrument_id=cables[index]["instrument_id"], end=cables[index]["end"])
        if required == "piece":
            matings.append(cable)
            continue
        after[str(index)] = required
        if before.get(str(index)) != required:
            matings += [cable, required]
    matings += inner
    return matings, dict(calibration_check_id=current, cables=after)


def _fixture_end(side: int) -> str:
    return "A" if side == 1 else "B"   # the fixture card's interface 1 / 2 = its side 1 (marked) / 2


def _latest(captures, key, value) -> dict:
    """The newest accepted capture of a piece or fixture in each pass."""
    found = {}
    for capture in captures:
        if capture.get(key) == value:
            found[capture["band"]] = capture
    return found


def _kit_audit(uow, row, action, now, details):
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=row.id, event_type="production." + action, actor=_actor(row.snapshot),
                             occurred_at=now, details=details))


@router.post(PREFIX + "/sessions/{session_id}/characterization/band")
def characterization_band(session_id: UUID, payload: BandInput, principal: Person, now: Now, factory: Factory):
    """The other pass: its profile and a full new ACM calibration (11.2)."""
    def action(uow, repository, row):
        _version(row, payload.version)
        characterization = _characterization(row)
        if row.state != "ready":
            raise Refusal("device_busy", "Дождитесь окончания команды")
        profile = kc.band_profile(payload.band)
        snapshot = {**row.snapshot, "profile": profile.model_dump(mode="json"),
                    "characterization": {**characterization, "band": payload.band}}
        steps = [bench.step("apply_profile", dict(profile=profile)),
                 *bench.calibration_steps(snapshot)]
        repository.append(row, steps, now, snapshot=snapshot, error="", **bench.UNCALIBRATED)
        _kit_audit(uow, row, "vna_kit_characterization_band", now, dict(band=payload.band))
    return _act(factory, session_id, principal, now, action)


KIT_CALIBRATION_PENDING = ("Характеристику мер SMA-MF06 на сокращённой калибровке (без доверительного теста и LOAD) не "
                           "снимают: порядок калибровки для неё владелец решает отдельно")


def _full_calibration_for_kit(uow, row):
    """Owner 27.11: dropping the ACM confidence test and LOAD (26.11) covers measurements, not the SMA-MF06 metrology
    pass — that is decided separately. Until then a characterization sweep needs a calibration with both."""
    check = uow.session.get(VnaCheckRow, row.calibration_check_id) if row.calibration_check_id else None
    if check is None or check.confidence_passed is None:
        raise Refusal("kit_calibration_pending", KIT_CALIBRATION_PENDING)


@router.post(PREFIX + "/sessions/{session_id}/characterization/captures")
def characterization_capture(session_id: UUID, payload: KitCaptureInput, principal: Person, now: Now, factory: Factory):
    """«Снять»: the piece (or an F–F fixture) is connected as the hint says — read the device, put the screen, sweep."""
    def action(uow, repository, row):
        _version(row, payload.version)
        characterization = _characterization(row)
        _gates(uow, row, now, "kit_characterization")
        _full_calibration_for_kit(uow, row)
        band = characterization["band"]
        if kc.band_of(row.snapshot["profile"]) != band:
            raise Refusal("profile_changed", "Профиль сеанса не совпадает с сеткой характеристики — откалибруйте заново")
        if (payload.standard_id is None) == (payload.fixture_id is None):
            raise ValueError("Укажите меру набора или оснастку")
        if payload.fixture_id is not None:
            if payload.fixtures:
                raise ValueError("Оснастку снимают напрямую между концами кабелей")
            card = _fixture_card(uow, payload.fixture_id)
            matings, mounted = _kit_connections(row, characterization, {
                0: dict(instrument_id=str(card.id), end=_fixture_end(1)),
                1: dict(instrument_id=str(card.id), end=_fixture_end(2))}, [])
            meta = bench.run("kit_characterization", subject="fixture", band=band, instrument_id=str(card.id),
                             number=card.inventory_number, name=card.name, matings=matings)
            hint = f"Оснастка {card.inventory_number} — {kc.fixture_hint()}"
        else:
            piece = _piece(uow, characterization, payload.standard_id)
            if band not in kc.bands_for(piece.f_min_hz, piece.f_max_hz):
                raise Refusal("other_calibration", f"{piece.piece_label}: полоса меры "
                              f"{kc.band_text(piece.f_min_hz, piece.f_max_hz)} — в диапазоне «{kc.BAND_LABELS[band]}» "
                              "её не снимают", 422)
            if len(payload.fixtures) != kc.fixture_count(piece.kind, piece.gender):
                raise ValueError("Мере «вилка» нужна оснастка розетка–розетка (проходу М–М — по одной на каждый кабель); "
                                 "мера «розетка» подключается прямо к кабелю")
            if len({f.instrument_id for f in payload.fixtures}) != len(payload.fixtures):
                raise ValueError("Оснастки на портах 1 и 2 должны быть разными экземплярами")
            fixtures = []
            for fixture in payload.fixtures:
                card = _fixture_card(uow, fixture.instrument_id)
                found = _latest(characterization["captures"], "instrument_id", str(card.id)).get(band)
                if found is None or found["calibration_check_id"] != str(row.calibration_check_id):
                    raise Refusal("no_fixture_file", f"Сначала снимите оснастку {card.inventory_number} в диапазоне "
                                                     f"«{kc.BAND_LABELS[band]}» на текущей калибровке", 422)
                fixtures.append(dict(instrument_id=str(card.id), number=card.inventory_number, reversed=fixture.reversed,
                                     command_id=found["command_id"], artifact_id=found["artifact_id"],
                                     sha256=found["sha256"]))
            arrangement, inner = {}, []
            if not fixtures:
                arrangement = {0: "piece", 1: "piece"} if piece.kind == "THRU" else {0: "piece"}
            for port, fixture in enumerate(fixtures, 1):
                facing = kc.cable_side(port, fixture["reversed"])
                arrangement[port - 1] = dict(instrument_id=fixture["instrument_id"], end=_fixture_end(facing))
                inner.append(dict(instrument_id=fixture["instrument_id"], end=_fixture_end(3 - facing)))
            matings, mounted = _kit_connections(row, characterization, arrangement, inner)
            meta = bench.run("kit_characterization", subject="piece", band=band, standard_id=str(piece.id),
                             piece_label=piece.piece_label, piece_kind=piece.kind, piece_gender=piece.gender,
                             fixtures=fixtures, matings=matings)
            hint = kc.connection_hint(piece.kind, piece.gender, piece.piece_label,
                                      [(f["number"], f["reversed"]) for f in fixtures])
        snapshot = {**row.snapshot, "characterization": {**characterization, "mounted": mounted}}
        repository.append(row, bench.item_steps(_profile(row), bench_screen(_screen(uow, "engineer")),
                                                "Развёртка: " + hint, meta), now, snapshot=snapshot, error="")
    return _act(factory, session_id, principal, now, action)


def _capture_data(repository, capture: dict, band: str, what: str) -> TwoPort:
    """An accepted sweep read back from its immutable artifact, on the exact pass grid."""
    artifact = repository.artifact(UUID(capture["command_id"]))
    if str(artifact.id) != capture["artifact_id"] or artifact.sha256 != capture["sha256"]:
        raise ProductionValidationError(f"Замер «{what}» изменился — снимите заново")
    return kc.snap(read_touchstone(bytes(artifact.content), artifact.file_name), band, what)


def _piece_curve(repository, piece, raw: TwoPort, fixtures: list[dict], band: str) -> CharacterizationCurve:
    return kc.piece_curve(piece.kind, piece.gender, raw, [
        (_capture_data(repository, f, band, f"оснастка {f['number']}"), f["reversed"]) for f in fixtures])


def _kit_capture(uow, repository, row, command, artifact, item, data, now):
    """An accepted characterization sweep: on the pass grid, the piece de-embedded without an ill-conditioned point;
    kept in the session with its preview. Nothing is published until «Сохранить»."""
    characterization = row.snapshot["characterization"]
    band = item["band"]
    if characterization["band"] != band or kc.band_of(row.snapshot["profile"]) != band:
        raise ProductionValidationError("Диапазон сеанса сменился — снимите заново")
    raw = kc.snap(data, band, "Замер")
    if item["subject"] == "fixture":
        curve = CharacterizationCurve.from_two_port(raw)
    else:
        piece = _piece(uow, characterization, UUID(item["standard_id"]))
        for fixture in item["fixtures"]:
            found = next((c for c in characterization["captures"] if c["command_id"] == fixture["command_id"]), None)
            if found is None or found["calibration_check_id"] != str(row.calibration_check_id):
                raise ProductionValidationError(f"Оснастка {fixture['number']} снята на другой калибровке — снимите её заново")
        curve = _piece_curve(repository, piece, raw, item["fixtures"], band)
    entry = dict(command_id=str(command.id), artifact_id=str(artifact.id), sha256=artifact.sha256, band=band,
                 subject=item["subject"], standard_id=item.get("standard_id"), instrument_id=item.get("instrument_id"),
                 number=item.get("number"), fixtures=item.get("fixtures", []),
                 calibration_check_id=str(row.calibration_check_id), calibrated_at=as_utc(row.calibrated_at).isoformat(),
                 captured_at=as_utc(now).isoformat(), temperatures=command.result["temperatures"],
                 summary=kc.summary(curve), preview=kc.preview(curve))
    return dict(snapshot={**row.snapshot, "characterization": {**characterization,
                                                                "captures": [*characterization["captures"], entry]}})


def _flat_temperatures(temperatures: dict) -> dict:
    flat = {f"vna_{i}_c": value for i, value in enumerate(temperatures.get("vna_c") or [], start=1)}
    if temperatures.get("acm_c") is not None:
        flat["acm_c"] = temperatures["acm_c"]
    return flat


def _kit_provenance(row, piece, entries: list[dict], curve: CharacterizationCurve) -> dict:
    first, profile = entries[0], kc.band_profile(entries[0]["band"])
    numbers = [f["number"] for f in first["fixtures"]]
    return dict(
        planar_instrument_id=str(row.instrument_id), planar_serial=row.instrument_serial, session_id=str(row.id),
        command_id=first["command_id"], calibration_id=first["calibration_check_id"], method="ACM",
        plane=kc.plane(piece.kind, piece.gender, numbers), operator=row.snapshot["operator"]["name"],
        processing_version=kc.PROCESSING_VERSION, captured_at=first["captured_at"],
        if_bandwidth_hz=profile.if_bandwidth_hz, power_dbm=profile.power_dbm,
        temperatures=_flat_temperatures(first["temperatures"]),
        fixtures=[dict(instrument_id=f["instrument_id"], number=f["number"], sha256=f["sha256"],
                       orientation="reverse" if f["reversed"] else "forward", side="port1" if i == 0 else "port2",
                       band=entry["band"], command_id=f["command_id"], artifact_id=f["artifact_id"])
                  for entry in entries for i, f in enumerate(entry["fixtures"])],
        acquisitions=[dict(band=e["band"], command_id=e["command_id"], artifact_id=e["artifact_id"], sha256=e["sha256"],
                           calibration_id=e["calibration_check_id"], calibrated_at=e["calibrated_at"],
                           captured_at=e["captured_at"], temperatures=_flat_temperatures(e["temperatures"]),
                           grid=dict(start_hz=kc.BANDS[e["band"]][0], stop_hz=kc.BANDS[e["band"]][1],
                                     points=kc.band_profile(e["band"]).points))
                      for e in entries],
        acm=dict(id=row.snapshot["acm"]["id"], serial=row.snapshot["acm"]["serial"]), cables=row.snapshot["cables"],
        kit_id=str(piece.kit_instrument_id), standard_id=str(piece.id), piece_label=piece.piece_label,
        torque=kc.TORQUE, stored_grid=dict(f_min_hz=curve.frequencies[0], f_max_hz=curve.frequencies[-1],
                                           points=len(curve.frequencies), step_hz=kc.STEP_HZ,
                                           interpolation_source_step_hz=2 * kc.STEP_HZ))


@router.post(PREFIX + "/sessions/{session_id}/characterization/pieces/{standard_id}/save")
def characterization_save(session_id: UUID, standard_id: UUID, payload: Version, principal: Person, now: Now,
                          factory: Factory):
    """«Сохранить»: the piece's curve from its latest captures of every needed pass — merged, cut to its band, checked
    (midpoints) — becomes a new immutable revision. A failed check is kept as evidence and blocks activation."""
    def action(uow, repository, row):
        _version(row, payload.version)
        characterization = _characterization(row)
        if row.state != "ready":
            raise Refusal("device_busy", "Дождитесь окончания команды")
        piece = _piece(uow, characterization, standard_id)
        latest = _latest(characterization["captures"], "standard_id", str(piece.id))
        bands = kc.bands_for(piece.f_min_hz, piece.f_max_hz)
        missing = [kc.BAND_LABELS[b] for b in bands if b not in latest]
        if missing:
            raise Refusal("not_final", f"{piece.piece_label}: снимите меру в диапазоне «{missing[0]}»", 422)
        entries = [latest[b] for b in bands]
        parts = {e["band"]: _piece_curve(repository, piece, _capture_data(repository, e, e["band"], piece.piece_label),
                                         e["fixtures"], e["band"]) for e in entries}
        curve = kc.merge(parts, piece.f_min_hz, piece.f_max_hz)
        validation, _ = kc.evidence(curve, parts)
        try:
            revision = RfKitRepository(uow.session).append_revision(
                piece.id, expected_version=piece.version, format="s2p" if piece.kind == "THRU" else "s1p",
                content=kc.content(curve), provenance=_kit_provenance(row, piece, entries, curve), validation=validation,
                actor=principal.actor.name, now=now, raw_artifact_id=UUID(entries[0]["artifact_id"]))
        except ConcurrentProductionUpdate as error:
            raise Refusal("stale", str(error)) from error
        uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=piece.kit_instrument_id,
                                 event_type="production.rf_kit_revision_saved",
                                 actor=Actor(id=principal.actor.id, name=principal.actor.name,
                                             department=principal.actor.department), occurred_at=now,
                                 details=dict(standard_id=str(piece.id), revision_id=str(revision.id),
                                              revision=revision.revision, sha256=revision.sha256,
                                              valid=validation["valid"], reason=validation["reason"],
                                              session_id=str(row.id), commands=[e["command_id"] for e in entries])))
    return _act(factory, session_id, principal, now, action)


def _piece_state(piece, latest: dict, revision, generation: int) -> str:
    if revision is not None and generation and piece.current_revision_id == revision.id:
        return "active"
    if revision is not None and revision.validation.get("valid"):
        return "validated"
    return "captured" if latest or revision is not None else "none"


def _revision_view(kit_id, piece, revision) -> dict | None:
    if revision is None:
        return None
    midpoints = revision.validation.get("midpoints") or {}
    join = revision.validation.get("join") or {}
    return dict(id=str(revision.id), revision=revision.revision, sha256=revision.sha256,
                valid=bool(revision.validation.get("valid")), reason=revision.validation.get("reason") or None,
                max_residual=midpoints.get("max_residual"), limit=midpoints.get("limit"),
                join_step=join.get("step"), join_limit=join.get("limit"),
                f_min_hz=revision.f_min_hz, f_max_hz=revision.f_max_hz, points=revision.points,
                created_at=revision.created_at, created_by=revision.created_by,
                download=f"/api/production/instruments/{kit_id}/standards/{piece.id}/revisions/{revision.id}/file")


def _kit_view(uow, row, commands) -> dict:
    """The guided list of the eight pieces with their state (нет данных / снято / проверено / действует)."""
    characterization = row.snapshot["characterization"]
    kit_id = UUID(characterization["kit_id"])
    details = uow.session.get(RfDetailsRow, kit_id)
    pieces = list(uow.session.scalars(select(RfKitStandardRow).where(RfKitStandardRow.kit_instrument_id == kit_id)))
    revisions = {}
    if pieces:
        for revision in uow.session.scalars(select(RfStandardCharacterizationRow).where(
                RfStandardCharacterizationRow.standard_id.in_([p.id for p in pieces]))
                .order_by(RfStandardCharacterizationRow.revision)):
            revisions[revision.standard_id] = revision
    captures, band = characterization["captures"], characterization["band"]
    current = str(row.calibration_check_id) if row.calibration_check_id else None
    generation = details.characterization_generation if details else 0
    order = {signature: i for i, signature in enumerate(KIT_ORDER)}
    views = []
    for piece in sorted(pieces, key=lambda p: order.get((p.kind, p.gender), 99)):
        latest = _latest(captures, "standard_id", str(piece.id))
        revision = revisions.get(piece.id)
        needed = kc.bands_for(piece.f_min_hz, piece.f_max_hz)
        saved = {a["command_id"] for a in (revision.provenance.get("acquisitions") or [])} if revision else set()
        shown = latest.get(band) or next(iter(latest.values()), None)
        numbers = [f["number"] for f in (shown or {}).get("fixtures") or []]
        active = bool(generation and revision is not None and piece.current_revision_id == revision.id)
        views.append(dict(
            id=str(piece.id), label=piece.piece_label, kind=piece.kind, gender=piece.gender, gender2=piece.gender2,
            name=kc.piece_name(piece.kind, piece.gender), f_min_hz=piece.f_min_hz, f_max_hz=piece.f_max_hz,
            version=piece.version, bands=list(needed), fixtures_needed=kc.fixture_count(piece.kind, piece.gender),
            in_band=band in needed, state=_piece_state(piece, latest, revision, generation), active=active,
            captures={b: dict(command_id=c["command_id"], captured_at=c["captured_at"], summary=c["summary"],
                              current=c["calibration_check_id"] == current, fixtures=[f["number"] for f in c["fixtures"]])
                      for b, c in latest.items()},
            preview=dict(band=shown["band"], points=shown["preview"]) if shown else None,
            plane=kc.plane(piece.kind, piece.gender, numbers if len(numbers) == kc.fixture_count(piece.kind, piece.gender) else ["?", "?"]),
            can_save=all(b in latest for b in needed) and {c["command_id"] for c in latest.values()} != saved,
            latest_revision=_revision_view(kit_id, piece, revision)))
    registered = {(p.kind, p.gender) for p in pieces}
    missing = [dict(kind=kind, gender=gender, name=kc.piece_name(kind, gender),
                    f_max_hz=3e9 if gender == "female" and kind in ("OPEN", "SHORT") else 6e9)
               for kind, gender in KIT_ORDER if (kind, gender) not in registered]
    ready = (details is not None and not missing and len(views) == len(REQUIRED_PIECES)
             and all(v["latest_revision"] and v["latest_revision"]["valid"] for v in views))
    activation = dict(expected_kit_version=details.version, revisions={v["id"]: v["latest_revision"]["id"] for v in views},
                      expected_standard_versions={v["id"]: v["version"] for v in views}) if ready else None
    fixtures = {}
    for capture in captures:
        if capture["subject"] == "fixture":
            view = fixtures.setdefault(capture["instrument_id"], dict(instrument_id=capture["instrument_id"],
                                                                     number=capture["number"], bands={}))
            view["bands"][capture["band"]] = dict(captured_at=capture["captured_at"], summary=capture["summary"],
                                                  current=capture["calibration_check_id"] == current)
    return dict(kit=dict(id=str(kit_id), number=characterization["kit_number"], name=characterization["kit_name"],
                         version=details.version if details else None, generation=generation,
                         hash=details.characterization_hash if details else None,
                         manifest=f"/api/production/instruments/{kit_id}/standards/manifest" if generation else None),
                band=band, bands=[dict(key=key, label=kc.BAND_LABELS[key], start_mhz=start / 1e6, stop_mhz=stop / 1e6,
                                       points=kc.band_profile(key).points, step_mhz=kc.STEP_HZ / 1e6)
                                  for key, (start, stop) in kc.BANDS.items()],
                torque=kc.TORQUE, pieces=views, missing=missing, fixtures=list(fixtures.values()),
                fixture_choices=_fixture_choices(uow),
                can_activate=bool(activation) and not all(v["active"] for v in views), activation=activation)


# ---- Arinst: server-built standards from the ACTIVE SMA-MF06 generation (plan §4) -------------------------------------

def _arinst_characterized(uow, snapshot, kit, payload) -> dict:
    """Measured standards of the active generation on the exact session grid, pinned by generation and hash.

    Refused (422, Russian) when the kit is not active, a piece has no published revision, or the grid leaves the
    stored curve (female OPEN/SHORT stop at 3 GHz). Never an ideal standard."""
    details = uow.session.get(RfDetailsRow, kit.id)
    if details is None or not details.characterization_generation:
        raise ValueError("Набор SMA-MF06 не активирован — нет опубликованных характеристик мер")
    if payload.standard_gender is None:
        raise ValueError("Укажите, какие меры подключаются: «розетка» (к кабелям с вилкой SMA) или «вилка»")
    profile = ArinstProfile.model_validate(snapshot["profile"])
    target = tuple(float(f) for f in profile.frequencies())
    kinds = ("OPEN", "SHORT", "LOAD", "THRU") if payload.transmission else ("OPEN", "SHORT", "LOAD")
    by_kind = {p.kind: p for p in RfKitRepository(uow.session).standards(kit.id) if p.gender == payload.standard_gender}
    generation, digest = details.characterization_generation, details.characterization_hash
    version = f"{generation}:{digest}"
    standards, pieces, labels = {}, [], {}
    for kind in kinds:
        piece = by_kind.get(kind)
        revision = (uow.session.get(RfStandardCharacterizationRow, piece.current_revision_id)
                    if piece is not None and piece.current_revision_id else None)
        if revision is None:
            raise ValueError(f"В активном наборе нет меры «{kc.piece_name(kind, payload.standard_gender)}»")
        try:
            standard = kc.arinst_standard(
                dict(id=piece.id, piece_label=piece.piece_label, kind=piece.kind, gender=piece.gender,
                     gender2=piece.gender2, f_min_hz=piece.f_min_hz, f_max_hz=piece.f_max_hz),
                dict(revision=revision.revision, sha256=revision.sha256, content=revision.content), target,
                kit_id=str(kit.id), kit_version=version, source=f"SMA-MF06 {kit.inventory_number}, поколение {generation}",
                orientation=payload.thru_orientation if kind == "THRU" else "forward")
        except VnaDataError as error:
            raise ValueError(str(error)) from error
        standards[kind] = kc.standard_json(standard)
        pieces.append(dict(kind=kind, standard_id=str(piece.id), label=piece.piece_label, revision=revision.revision,
                           sha256=revision.sha256, f_min_hz=revision.f_min_hz, f_max_hz=revision.f_max_hz))
        labels[kind] = f"{kc.piece_name(kind, piece.gender)} {piece.piece_label}"
    chosen = [by_kind[kind] for kind in kinds]
    context = dict(instrument_uid=snapshot["hardware_uid"], firmware=snapshot["firmware"],
                   profile_fingerprint=bench.fingerprint(profile.model_dump(mode="json")),
                   if_bandwidth_hz=profile.if_bandwidth_hz, kit_id=str(kit.id), kit_version=version,
                   fixture_fingerprint=snapshot["calibration_setup"]["fixture_fingerprint"],
                   reference_plane=snapshot["calibration_setup"]["reference_plane"],
                   orientation=snapshot["calibration_setup"]["orientation"], connection_id=snapshot["connection_id"],
                   standard_genders=[payload.standard_gender] * (2 if payload.transmission else 1),
                   usable_band_hz=[max(p.f_min_hz for p in chosen), min(p.f_max_hz for p in chosen)])
    try:
        CalibrationContext(**context)
    except (VnaDataError, TypeError) as error:
        raise ValueError(str(error)) from error
    snapshot["calibration_context"] = context
    snapshot["characterized_standards"] = standards
    snapshot["calibration_setup"].update(kit_generation=generation, kit_hash=digest,
                                         standard_gender=payload.standard_gender,
                                         thru_orientation=payload.thru_orientation, pieces=pieces)
    return labels


def _arinst_kit_stale(uow, snapshot) -> str:
    """A newer activation (or none) after the run pinned its generation: the run and its results are stale."""
    setup = (snapshot or {}).get("calibration_setup") or {}
    if setup.get("kit_generation") is None:
        return ""
    details = uow.session.get(RfDetailsRow, UUID(setup["kit_id"]))
    if details is None or (details.characterization_generation, details.characterization_hash) != (
            setup["kit_generation"], setup["kit_hash"]):
        return "Набор мер переактивирован после начала калибровки — начните калибровку Arinst заново"
    return ""


def _arinst_calibration_view(uow, row) -> dict:
    if row is None:
        return dict(valid=False, status="required")
    calibration = row.snapshot.get("calibration") or dict(valid=False, status="required")
    stale = _arinst_kit_stale(uow, row.snapshot)
    # Any calibration built on the pinned generation — acquiring, awaiting verification or already valid — is stale
    # once another generation is active; «required»/«blocked» hold nothing reusable and keep their own reason.
    if stale and calibration.get("status") not in ("required", "blocked"):
        return dict(valid=False, status="stale", reason=stale, coefficient_artifact_id=None)
    return calibration


def _arinst_kits(uow) -> list[dict]:
    """SMA-MF06 kits for the Arinst kit choice: the active generation's pieces and bands by gender."""
    kits = []
    for details in uow.session.scalars(select(RfDetailsRow).where(
            RfDetailsRow.kit_definition.in_((CHARACTERIZED_DEFINITION, LOADS_DEFINITION)))):
        card = uow.session.get(ProductionInstrumentRow, details.instrument_id)
        pieces = list(uow.session.scalars(select(RfKitStandardRow).where(
            RfKitStandardRow.kit_instrument_id == details.instrument_id)))
        current = {r.id: r for r in uow.session.scalars(select(RfStandardCharacterizationRow).where(
            RfStandardCharacterizationRow.id.in_([p.current_revision_id for p in pieces if p.current_revision_id])))} \
            if any(p.current_revision_id for p in pieces) else {}
        sets = []
        for gender in ("female", "male"):
            own = {p.kind: current.get(p.current_revision_id) for p in pieces if p.gender == gender}
            sol = [own.get(kind) for kind in ("OPEN", "SHORT", "LOAD")]
            if details.characterization_generation and all(sol):
                thru = own.get("THRU")
                sets.append(dict(gender=gender, sol_mhz=[max(r.f_min_hz for r in sol) / 1e6, min(r.f_max_hz for r in sol) / 1e6],
                                 thru_mhz=[thru.f_min_hz / 1e6, thru.f_max_hz / 1e6] if thru else None,
                                 pieces=[dict(kind=p.kind, label=p.piece_label, revision=current[p.current_revision_id].revision)
                                         for p in pieces if p.gender == gender and p.current_revision_id in current]))
        kits.append(dict(id=str(details.instrument_id), number=card.inventory_number if card else "",
                         name=card.name if card else "", generation=details.characterization_generation,
                         hash=details.characterization_hash, active=bool(details.characterization_generation),
                         sets=sets))
    return sorted(kits, key=lambda kit: kit["number"])


def _arinst_setup_view(row) -> dict | None:
    setup = (row.snapshot.get("calibration_setup") if row else None) or {}
    if setup.get("kit_generation") is None:
        return None
    return dict(kit_id=setup["kit_id"], generation=setup["kit_generation"], hash=setup["kit_hash"],
                standard_gender=setup["standard_gender"], pieces=setup["pieces"])


def _fixture_choices(uow) -> list[dict]:
    """Registered F–F adapters (role «Переход», both connectors розетка): the fixtures for male pieces."""
    both = (select(RfInterfaceRow.instrument_id).group_by(RfInterfaceRow.instrument_id)
            .having(func.count() == 2, func.sum((RfInterfaceRow.body == "розетка").cast(Integer)) == 2))
    rows = uow.session.execute(select(ProductionInstrumentRow.id, ProductionInstrumentRow.inventory_number,
                                      ProductionInstrumentRow.name).join(
        RfDetailsRow, RfDetailsRow.instrument_id == ProductionInstrumentRow.id).where(
        RfDetailsRow.role == "adapter", ProductionInstrumentRow.id.in_(both))
        .order_by(ProductionInstrumentRow.inventory_number))
    return [dict(instrument_id=str(i), number=number, name=name) for i, number, name in rows]

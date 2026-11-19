"""Compose P1 planning, production sessions and the authenticated warehouse agent transport."""

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Callable, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, ValidationError, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.modules.production.api import production_uow_factory, techcards_uow_factory
from app.modules.production.autoplan_api import references_uow_factory
from app.modules.production.rf_infrastructure import RfRepository
from app.modules.production.vna_bench_protocol import V2_OPS
from app.modules.production.vna_session_infrastructure import VnaArtifactRow, VnaSessionRepository, VnaSessionRow
from app.modules.production.vna_sessions import TERMINAL, Override, SessionConflict, StrictModel, ordered_steps, resolved_profile
from app.modules.production.vswr import RecordVswrCommand, VswrFileInput, VswrVerdict, record_vswr
from app.modules.warehouse import devices, vna_jobs
from app.modules.warehouse.scan_agents import ScanAgentRow
from app.modules.warehouse.scan_jobs import ScanJobClosed, agent_machine
from app.modules.warehouse.scan_jobs_api import UowFactory, _queue, scan_agent_principal
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_quality_principal
from app.shared.stored_files import file_response
from .coax_vswr_api import CoaxRefs, _measurement_result, _task_etk, task_norm
from .vna_capture_instrument import capture_instrument
from .vna_plan_api import vna_plan

router = APIRouter(tags=["production"])
PREFIX = "/api/production/vna-sessions"
Person = Annotated[Principal, Depends(production_quality_principal)]
Now = Annotated[datetime, Depends(moscow_now)]
Factory = Annotated[Callable, Depends(production_uow_factory)]


@contextmanager
def _errors():
    try:
        yield
    except KeyError as error:
        raise HTTPException(404, str(error.args[0])) from error
    except (SessionConflict, ScanJobClosed) as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


class StartInput(StrictModel):
    task_id: UUID
    operation_id: UUID
    instrument_id: UUID
    product_position: int = Field(ge=1, le=1_000_000, strict=True)
    agent_id: str = Field(min_length=1, max_length=80)
    overrides: list[Override] = Field(default_factory=list, max_length=3)
    qualification_reason: str = Field(default="", max_length=500)


class ActionInput(StrictModel):
    action: Literal["plan", "start", "confirm", "abort", "recover"]
    expected_version: int = Field(ge=1, strict=True)
    expected_step: int = Field(ge=0, strict=True)


class RecordInput(StrictModel):
    expected_version: int = Field(ge=1, strict=True)


class CommandResultInput(StrictModel):
    session_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    expected_step: int = Field(ge=0, strict=True)
    result: dict | None = None
    error: str = Field(default="", max_length=2000)
    backup_saved: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def one_answer(self):
        if (self.result is None) == (not bool(self.error.strip())):
            raise ValueError("Нужен результат либо непустая причина ошибки")
        if self.backup_saved is not None and self.result is not None:
            raise ValueError("Признак резервной копии допустим только для ошибки подготовки")
        return self


def _owned(repository, session_id, principal):
    row = repository.get(session_id)
    if row.operator_id != principal.actor.id:
        raise KeyError("Сессия ВАЦ не найдена")
    return row


def _expire(factory, now, **filters):
    with factory() as uow:
        VnaSessionRepository(uow.session).expire(now, **filters)
        uow.commit()  # Persist recovery even if the following stale callback is rejected.


@router.post(PREFIX)
def create_session(payload: StartInput, principal: Person, now: Now, factory: Factory,
                   techcards_factory: Annotated[Callable | None, Depends(techcards_uow_factory)],
                   refs_factory: Annotated[Callable | None, Depends(references_uow_factory)]):
    with _errors():
        with factory() as uow:
            # Expire the instrument's sessions first: a stale command or a 12 h idle bench session frees the lease.
            active = [row.id for row in uow.session.scalars(select(VnaSessionRow).where(
                VnaSessionRow.instrument_id == payload.instrument_id, VnaSessionRow.state.not_in(TERMINAL)))]
        for session_id in active:
            _expire(factory, now, session_id=session_id)
        instrument = capture_instrument(factory(), payload.instrument_id)
        if instrument["source"] != "s2vna" or not instrument["serial"]:
            raise ValueError("Управление доступно для S2VNA с серийным номером; используйте импорт файла")
        plan = vna_plan(payload.task_id, payload.operation_id, payload.instrument_id, principal, now,
                        factory, techcards_factory, refs_factory)
        if (plan["status"] == "qualified" or plan["chains"]["status"] == "qualified") and not payload.qualification_reason.strip():
            raise ValueError("Для квалифицированного метода укажите причину согласования; автоматическая приёмка запрещена")
        profile = resolved_profile(plan, payload.overrides)
        with factory() as uow:
            if uow.session.get(ScanAgentRow, payload.agent_id) is None:
                raise SessionConflict("ПК агента не найден")
            task = uow.production.get(payload.task_id)
            if task is None or payload.product_position > task.quantity:
                raise ValueError("Позиция изделия выходит за количество задания")
            rf = RfRepository(uow.session)
            if profile.points > (rf.get(payload.instrument_id).max_points or 0):
                raise ValueError("Число точек превышает подтверждённую ёмкость ВАЦ")
            calibrations = plan["chains"]["calibration_connections"]
            calibrations = calibrations if isinstance(calibrations, list) else [calibrations]
            kits = {}
            for calibration in calibrations:
                kit_id = calibration["kit_id"]
                kit = uow.instruments.get(UUID(kit_id))
                if kit is None:
                    raise ValueError("Калибровочный набор не найден в реестре")
                kits[kit_id] = {**asdict(rf.get(UUID(kit_id))), "serial_number": (kit.serial_number or "").strip()}
            steps = ordered_steps(plan, profile, kits)
            methods = {s["args"]["method"] for s in steps if s["op"] == "begin_calibration"}
            owner = vna_jobs.control_station(uow.session, instrument["serial"], now,
                                            machine_id=payload.agent_id, methods=methods)
            vna_jobs.refuse_open_capture(uow.session, payload.instrument_id, instrument["serial"])
            repository = VnaSessionRepository(uow.session)
            if repository.active(payload.instrument_id, instrument["serial"]):
                raise SessionConflict("Прибор занят активной сессией ВАЦ")
            try:
                # Only uniqueness errors from inserting the lease become conflicts.
                with uow.session.begin_nested():
                    row = repository.create(now=now, operator_id=principal.actor.id, machine_id=owner,
                        instrument_id=payload.instrument_id, instrument_serial=instrument["serial"],
                        task_id=payload.task_id, operation_id=payload.operation_id, product_position=payload.product_position,
                        snapshot=dict(plan=plan, profile=profile.model_dump(mode="json"), kit_definitions=kits,
                                      overrides=[o.model_dump() for o in payload.overrides],
                                      qualification_reason=payload.qualification_reason.strip()), steps=steps)
            except IntegrityError:
                if repository.active(payload.instrument_id, instrument["serial"]):
                    raise SessionConflict("Прибор занят активной сессией ВАЦ") from None
                raise
            view = repository.view(row)
            uow.commit()
            return view


@router.get(PREFIX + "/{session_id}")
def session_view(session_id: UUID, principal: Person, now: Now, factory: Factory):
    with _errors():
        # Check ownership before applying the lazy watchdog.
        with factory() as uow:
            _owned(VnaSessionRepository(uow.session), session_id, principal)
        _expire(factory, now, session_id=session_id)
        with factory() as uow:
            repository = VnaSessionRepository(uow.session)
            return repository.view(_owned(repository, session_id, principal))


@router.post(PREFIX + "/{session_id}/actions")
def session_action(session_id: UUID, payload: ActionInput, principal: Person, now: Now, factory: Factory):
    with _errors():
        with factory() as uow:
            _owned(VnaSessionRepository(uow.session), session_id, principal)
        _expire(factory, now, session_id=session_id)
        with factory() as uow:
            repository = VnaSessionRepository(uow.session)
            row = _owned(repository, session_id, principal)
            repository.act(row, payload.action, payload.expected_version, payload.expected_step, now)
            view = repository.view(row)
            uow.commit()
            return view


def _automatic_acceptance(snapshot):
    plan = snapshot["plan"]
    return plan["status"] == "ready" and plan["chains"]["automatic_acceptance"]


def _session_files(repository, completed):
    """Compose P4 inputs from durable capture receipts, including one-port reversal."""
    commands = {command["id"]: command for command in completed["commands"]}
    snapshot = completed["snapshot"]
    files, mappings = [], []
    for metadata in completed["artifacts"]:
        artifact = repository.session.get(VnaArtifactRow, UUID(metadata["id"]))
        command = commands[metadata["command_id"]]
        if command["op"] != "capture" or command["status"] != "done":
            raise ValueError("Файл сессии не подтверждён завершённым измерением")
        content = bytes(artifact.content)
        if sha256(content).hexdigest() != artifact.sha256:
            raise ValueError("Контрольная сумма файла сессии не совпадает")
        args, result = command["args"], command["result"]
        receipt = next((item for item in result["artifacts"] if item["file_name"] == artifact.file_name), None)
        if receipt is None or receipt["sha256"] != artifact.sha256:
            raise ValueError("Файл сессии не совпадает с сохранённым ответом ВАЦ")
        traces = receipt["traces"]
        mapping = {port: {"А": "A", "Б": "B"}[end] for port, end in args["port_to_end"].items()}
        mappings.append(mapping)
        end = None
        if artifact.file_name.endswith(".s1p"):
            if len(traces) != 1 or traces[0] not in ("S11", "S22"):
                raise ValueError("Однопортовый файл должен содержать отражение S11 или S22")
            end = mapping.get("1" if traces[0] == "S11" else "2")
            if end is None:
                raise ValueError("Для трассы сессии не указан конец изделия")
        elif set(mapping) != {"1", "2"}:
            raise ValueError("Для двухпортового файла нужна привязка обоих портов")
        settings = {
            **result["profile"], "session_id": completed["id"], "instrument_serial": result["serial"],
            "orientation": args["orientation"], "port_to_end": mapping, "traces": traces,
            "correction": result["correction"], "correction_enabled": True, "correction_status": "COR",
            "smoothing_enabled": result["profile"]["smoothing"],
            "averaging_enabled": result["profile"]["averaging"],
            "qualification_reason": snapshot["qualification_reason"],
            "automatic_acceptance": _automatic_acceptance(snapshot),
        }
        if len(traces) == 1:
            settings["s_parameter"] = traces[0]
        files.append(VswrFileInput(file_name=artifact.file_name, content=content, end=end,
                                  capture_settings=settings, capture_verified=True, evidence_id=artifact.id))
    if not files:
        raise ValueError("В завершённой сессии нет файлов измерения")
    # A reversal has per-file ends; its two physical port-1 captures must not overwrite each other.
    mapping = {"1": "A", "2": "B"} if snapshot["plan"]["chains"].get("mode") == "one_port_reversal" else mappings[0]
    if snapshot["plan"]["chains"].get("mode") != "one_port_reversal" and any(item != mapping for item in mappings):
        raise ValueError("Файлы сессии имеют разную привязку портов")
    return tuple(files), mapping


@router.post(PREFIX + "/{session_id}/record")
def record_session(session_id: UUID, payload: RecordInput, principal: Person, now: Now, factory: Factory,
                   techcards_factory: Annotated[Callable | None, Depends(techcards_uow_factory)],
                   refs_factory: Annotated[Callable | None, Depends(references_uow_factory)]):
    """QC may record a completed session; bytes and calibration claims never come from the browser."""
    with _errors(), factory() as uow:
        repository = VnaSessionRepository(uow.session)
        row = repository.get(session_id)
        repository.lock(row)
        if row.state == "recorded":
            # A lost response is retried with the review version; a refreshed client has the recorded version.
            if payload.expected_version not in (row.version - 1, row.version):
                raise SessionConflict("Сессия изменилась — обновите шаг")
        else:
            repository.expect(row, payload.expected_version, row.step)
            completed = repository.completed(session_id)
            task = uow.production.get(row.task_id)
            if task is None:
                raise KeyError("Задание не найдено")
            document, etk, current = _task_etk(techcards_factory, task)
            # The timestamp only labels computed legacy norms; retain its original date while reloading live refs.
            norm = task_norm(document, current, etk["current_revision"], CoaxRefs(refs_factory), row.created_at)
            if norm != row.snapshot["plan"]["profile"]["norm"]:
                raise SessionConflict("Норма КСВН изменилась после начала сессии — выполните повторный замер")
            instrument = uow.instruments.get(row.instrument_id)
            if instrument is None or (instrument.serial_number or "").strip() != row.instrument_serial:
                raise SessionConflict("Прибор сессии изменился — выполните повторный замер")
            files, mapping = _session_files(repository, completed)
            measurement = record_vswr(RecordVswrCommand(
                task_id=row.task_id, position=row.product_position, instrument_id=row.instrument_id,
                file_name="", content=b"", files=files, port_mapping=mapping, norm=norm,
                actor=principal.actor, now=now,
            ), uow, manage_transaction=False)
            if measurement.verdict is VswrVerdict.PASS and not _automatic_acceptance(row.snapshot):
                raise SessionConflict("Метод сессии не допускает автоматическую приёмку — "
                                      "выполните повторный замер по подтверждённому плану")
            repository.mark_recorded(session_id, expected_version=payload.expected_version,
                                     reference=str(measurement.id), now=now)
        # Return the persisted representation on both first recording and retry (including DB precision/timezone).
        uow.session.flush()
        serial = uow.serials.by_position(row.task_id, row.product_position)
        measurement = next((item for item in uow.vswr.list_for_serial(serial.id)
                            if str(item.id) == row.recorded_reference), None) if serial else None
        if measurement is None:
            raise SessionConflict("Записанная попытка сессии не найдена")
        response = {"measurement": _measurement_result(measurement, trace=True), "position": row.product_position,
                    "session": repository.view(row)}
        uow.commit()
        return response


@router.get(PREFIX + "/{session_id}/artifacts/{artifact_id}")
def artifact_file(session_id: UUID, artifact_id: UUID, principal: Person, factory: Factory):
    with _errors(), factory() as uow:
        _owned(VnaSessionRepository(uow.session), session_id, principal)
        artifact = uow.session.get(VnaArtifactRow, artifact_id)
        if artifact is None or artifact.session_id != session_id:
            raise KeyError("Файл сессии не найден")
        return file_response(artifact.content, artifact.file_name, download=True)


def refuse_session_capture(session, instrument_id, serial="", vna_name=""):
    serial = serial or devices.idn_serial(vna_name)
    if serial:
        # Match session creation's binding-row lock, even for a capture caller without expected_vna_serial.
        session.execute(update(devices.VnaBindingRow).where(devices.VnaBindingRow.serial == serial)
                        .values(serial=serial))
    if VnaSessionRepository(session).active(instrument_id, serial):
        raise ScanJobClosed("Прибор занят сессией ВАЦ; завершите её или выполните восстановление")


def claim_session_command(factory, machine_id, observed_serial, now):
    try:
        _expire(factory, now, machine_id=machine_id)
    except SessionConflict as error:
        raise ScanJobClosed(str(error)) from error
    with factory() as uow:
        devices.control_capability(uow.session, machine_id, now)
        repository = VnaSessionRepository(uow.session)
        command = repository.pending(machine_id)
        if command is None:
            return None
        row = repository.get(command.session_id)
        if observed_serial != row.instrument_serial:
            raise ScanJobClosed("Перед командой прочитайте серийный номер подключённого ВАЦ; он не совпадает с сессией")
        methods = {s["args"]["method"] for s in row.steps if s["op"] == "begin_calibration"}
        recovery = command.op in ("abort", "restore")
        # A bench session (protocol 2) goes only to an agent advertising version 2 and this very op.
        ops = None if row.purpose != "bench" else set() if recovery or command.op not in V2_OPS else {command.op}
        vna_jobs.control_station(uow.session, row.instrument_serial, now, machine_id=machine_id,
                                methods=methods if not recovery else None, ops=ops)
        try:
            envelope = repository.claim(command, now)
        except SessionConflict as error:
            raise ScanJobClosed(str(error)) from error
        uow.commit()
        return envelope


@router.get("/api/stock/vna/sessions/{session_id}")
def agent_session_status(session_id: UUID, request: Request,
                         agent: Annotated[Principal, Depends(scan_agent_principal)], now: Now,
                         uow_factory: UowFactory):
    """Token-bound reconciliation for journal fencing and terminal backup cleanup."""
    machine_id = _queue(lambda: agent_machine(
        uow_factory(), agent.actor.id, request.headers.get("x-agent-machine-id", "")))
    with _errors():
        with uow_factory() as uow:
            row = VnaSessionRepository(uow.session).get(session_id)
            if row.machine_id != machine_id:
                raise HTTPException(403, "Сессия выдана другому ПК")
        _expire(uow_factory, now, session_id=session_id)
        with uow_factory() as uow:
            repository = VnaSessionRepository(uow.session)
            row = repository.get(session_id)
            view = repository.view(row)
            return dict(session_id=str(row.id), state=row.state, version=row.version, step=row.step,
                lease_held=row.state not in TERMINAL, instrument_id=str(row.instrument_id),
                instrument_serial=row.instrument_serial, kit_definitions=row.snapshot["kit_definitions"],
                commands=[dict(command_id=c["command_id"], status=c["status"],
                               expected_version=c["expected_version"], expected_step=c["expected_step"])
                          for c in view["commands"]])


@router.post("/api/stock/vna/commands/{command_id}/result")
async def command_result(command_id: UUID, request: Request,
                         agent: Annotated[Principal, Depends(scan_agent_principal)], now: Now,
                         uow_factory: UowFactory, production_factory: Factory):
    machine_id = await run_in_threadpool(lambda: _queue(lambda: agent_machine(
        uow_factory(), agent.actor.id, request.headers.get("x-agent-machine-id", ""))))
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 3 * 1024 * 1024:
            raise HTTPException(422, "Ответ ВАЦ больше 3 МиБ")
    try:
        payload = CommandResultInput.model_validate_json(bytes(body))
    except ValidationError:
        raise HTTPException(422, "Некорректный ответ команды ВАЦ") from None

    def save():
        with _errors():
            with uow_factory() as uow:
                row = VnaSessionRepository(uow.session).get(payload.session_id)
                if row.machine_id != machine_id:
                    raise HTTPException(403, "Сессия выдана другому ПК")
                purpose = row.purpose
            _expire(uow_factory, now, session_id=payload.session_id)
            if purpose == "bench":
                from .vna_bench_api import complete_command

                # The bench judges each receipt on the server: card file, matings, journal and next step in one UoW.
                with production_factory() as uow:
                    row = VnaSessionRepository(uow.session).get(payload.session_id)
                    complete_command(uow, row, command_id, payload, now)
                    response = dict(ok=True, session_id=str(row.id), state=row.state, version=row.version, step=row.step)
                    uow.commit()
                    return response
            with uow_factory() as uow:
                repository = VnaSessionRepository(uow.session)
                row = repository.get(payload.session_id)
                repository.complete(row, command_id, payload.expected_version, payload.expected_step,
                                    payload.result, payload.error, now, backup_saved=payload.backup_saved)
                response = dict(ok=True, session_id=str(row.id), state=row.state, version=row.version, step=row.step)
                uow.commit()
                return response
    return await run_in_threadpool(save)

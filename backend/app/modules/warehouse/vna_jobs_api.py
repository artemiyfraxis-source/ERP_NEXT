"""Capture transport endpoints, protected by the same quality permission as VSWR uploads."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, access_admin_principal, production_quality_principal
from app.shared.stored_files import file_response
from app.workflows.vna_capture_instrument import capture_instrument

from . import devices, vna_jobs
from .scan_jobs import DONE, cancel_job, create_job, get_job
from .scan_jobs_api import UowFactory, _queue

router = APIRouter(prefix="/api/stock/vna/jobs", tags=["warehouse"])
Person = Annotated[Principal, Depends(production_quality_principal)]
Now = Annotated[datetime, Depends(moscow_now)]


class StartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: UUID
    agent_id: str = Field(default="", max_length=80)
    source: Literal["s2vna", "arinst"] | None = None
    choose_device: bool = False


class TransferInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: UUID
    serial: str = Field(min_length=1, max_length=300)
    owner_id: str = Field(min_length=1, max_length=80)
    agent_id: str = Field(min_length=1, max_length=80)


@router.post("/transfer")
def transfer(payload: TransferInput, principal: Annotated[Principal, Depends(access_admin_principal)],
             now: Now, uow_factory: UowFactory):
    try:
        instrument = capture_instrument(uow_factory(), payload.instrument_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if instrument["source"] != "s2vna" or not instrument["serial"]:
        raise HTTPException(422, "Перенос доступен только для ВАЦ S2VNA с серийным номером")
    if payload.serial != instrument["serial"]:
        raise HTTPException(409, "Серийный номер СИ изменился — проверьте прибор ещё раз")
    _queue(lambda: devices.transfer_vna(uow_factory(), serial=instrument["serial"], owner=payload.owner_id,
        target=payload.agent_id, instrument_id=payload.instrument_id, actor=principal.actor, now=now))
    return {"status": "transferred"}


def _owned(uow_factory, job_id, principal, now):
    job = _queue(lambda: get_job(uow_factory(), job_id, now=now))
    if job.kind != "vna" or job.requested_by != str(principal.actor.id):
        raise HTTPException(404, "Задание ВАЦ не найдено")
    return job


def _view(job):
    return {"id": str(job.id), "status": job.status, "error": job.error,
            "vna": {"name": job.vna_name}, "source": job.source,
            "instrument_id": str(job.instrument_id) if job.instrument_id else None,
            "capture_settings": job.capture_settings,
            "file_name": job.capture_file_name or "vna.s1p"}


@router.post("")
def start(request: Request, principal: Person, now: Now, uow_factory: UowFactory, payload: StartInput):
    try:
        instrument = capture_instrument(uow_factory(), payload.instrument_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    source = instrument["source"]
    if payload.source is not None and payload.source != source:
        raise HTTPException(422, "Источник не соответствует программе выбранного СИ")
    choices = devices.vna_agents(uow_factory(), now, source, instrument["serial"])
    if source == "s2vna":
        owner = devices.vna_binding(uow_factory(), instrument["serial"])
        conflicts = [pc for pc in choices if owner and pc["id"] != owner["id"]]
        if conflicts:
            message = (f"ВАЦ № {instrument['serial']} закреплён за ПК {owner['pc']}; "
                       f"ПК {', '.join(pc['pc'] for pc in conflicts)} тоже сообщает этот номер — "
                       "перенесите прибор, если его переставили")
            return {"status": "no_agent", "message": message,
                    "transfer": {"serial": instrument["serial"], "owner_id": owner["id"],
                                 "agents": conflicts} if principal.full_access else None}
        choices = [pc for pc in choices if owner and pc["id"] == owner["id"]]
    if not choices:
        previous = devices.vna_agents(uow_factory(), now, source, instrument["serial"], online=False)
        if source == "s2vna":
            previous = [pc for pc in previous if owner and pc["id"] == owner["id"]]
        if source == "s2vna" and not instrument["serial"]:
            message = f"У ВАЦ {instrument['number']} не указан серийный номер. Заполните его в карточке СИ."
        elif previous:
            seen = datetime.fromisoformat(previous[0]["last_seen"]).strftime("%d.%m.%Y %H:%M")
            message = (f"ВАЦ {instrument['number']} не в сети: на ПК {previous[0]['pc']} "
                       + ("не запущена S2VNA или прибор не подключён" if source == "s2vna" else "недоступен агент Arinst")
                       + f" (последний раз {seen})")
        else:
            message = (f"ВАЦ {instrument['number']} не в сети: "
                       + ("запустите S2VNA и подключите прибор на ПК с агентом ERP" if source == "s2vna"
                          else "запустите агент ERP на ПК с Arinst Virtual Lab"))
        return {"status": "no_agent", "message": message}
    preferred = vna_jobs.preferred_machine(uow_factory(), principal.actor.id) if source == "arinst" else ""
    # Planar identity is authoritative; browser/local preferences cannot redirect it.
    wanted = payload.agent_id or preferred if source == "arinst" else ""
    chosen = next((pc for pc in choices if pc["id"] == wanted), None)
    if len(choices) == 1 and not payload.choose_device:
        chosen = choices[0]
    if payload.choose_device:
        chosen = None
    if chosen is None:
        return {"status": "choose", "agents": choices,
                "preferred_agent_id": preferred}
    if source == "arinst":
        vna_jobs.preferred_machine(uow_factory(), principal.actor.id, chosen["id"])
    job = _queue(lambda: create_job(uow_factory(), requested_by=str(principal.actor.id), now=now,
                     target=chosen["id"], kind="vna", source=source,
                     instrument_id=payload.instrument_id, requester_id=principal.actor.id,
                     expected_vna_serial=instrument["serial"] if source == "s2vna" else None,
                     vna_name=chosen["vna"]["name"] if source == "s2vna" else "Arinst VNA-DL"))
    return {**_view(job), "agent": chosen}


@router.get("/{job_id}")
def status(job_id: UUID, principal: Person, now: Now, uow_factory: UowFactory):
    return _view(_owned(uow_factory, job_id, principal, now))


@router.delete("/{job_id}")
def cancel(job_id: UUID, principal: Person, now: Now, uow_factory: UowFactory):
    _owned(uow_factory, job_id, principal, now)
    return _view(_queue(lambda: cancel_job(uow_factory(), job_id, now=now)))


@router.get("/{job_id}/file")
def file(job_id: UUID, principal: Person, now: Now, uow_factory: UowFactory):
    job = _owned(uow_factory, job_id, principal, now)
    if job.status != DONE:
        raise HTTPException(409, "Файл ВАЦ ещё не получен")
    return file_response(vna_jobs.content(uow_factory(), job.id), job.capture_file_name or "vna.s1p", download=True)

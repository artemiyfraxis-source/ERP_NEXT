"""Capture transport endpoints, protected by the same quality permission as VSWR uploads."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_quality_principal
from app.shared.stored_files import file_response
from app.workflows.vna_capture_instrument import capture_source

from . import vna_jobs
from .scan_jobs import DONE, cancel_job, create_job, get_job
from .scan_jobs_api import UowFactory, _client_ip, _queue

router = APIRouter(prefix="/api/stock/vna/jobs", tags=["warehouse"])
Person = Annotated[Principal, Depends(production_quality_principal)]
Now = Annotated[datetime, Depends(moscow_now)]


class StartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: UUID
    agent_id: str = Field(default="", max_length=80)
    source: Literal["s2vna", "arinst"] | None = None


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
        source = capture_source(uow_factory(), payload.instrument_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if payload.source is not None and payload.source != source:
        raise HTTPException(422, "Источник не соответствует программе выбранного СИ")
    browser_ip = _client_ip(request)
    all_agents = vna_jobs.agents(uow_factory(), now, browser_ip=browser_ip)
    if payload.agent_id and any(pc["id"] == payload.agent_id and source not in pc["vna_sources"] for pc in all_agents):
        raise HTTPException(422, "Выбранный источник ВАЦ недоступен на этом ПК")
    choices = [pc for pc in all_agents if source in pc["vna_sources"]]
    if not choices:
        return {"status": "no_agent"}
    chosen = next((pc for pc in choices if pc["id"] == payload.agent_id), None)
    if chosen is None:
        return {"status": "choose", "agents": choices,
                "preferred_agent_id": vna_jobs.preferred_machine(uow_factory(), principal.actor.id)}
    vna_jobs.preferred_machine(uow_factory(), principal.actor.id, chosen["id"])
    job = create_job(uow_factory(), requested_by=str(principal.actor.id), now=now,
                     target=chosen["id"], kind="vna", source=source,
                     instrument_id=payload.instrument_id,
                     vna_name=chosen["vna"]["name"] if source == "s2vna" else "Arinst VNA-DL")
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

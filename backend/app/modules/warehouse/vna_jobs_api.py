"""Capture transport endpoints, protected by the same quality permission as VSWR uploads."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_quality_principal
from app.shared.stored_files import file_response

from . import vna_jobs
from .scan_jobs import DONE, cancel_job, create_job, get_job
from .scan_jobs_api import UowFactory, _queue

router = APIRouter(prefix="/api/stock/vna/jobs", tags=["warehouse"])
Person = Annotated[Principal, Depends(production_quality_principal)]
Now = Annotated[datetime, Depends(moscow_now)]


class StartInput(BaseModel):
    agent_id: str = Field(default="", max_length=80)


def _owned(uow_factory, job_id, principal, now):
    job = _queue(lambda: get_job(uow_factory(), job_id, now=now))
    if job.kind != "vna" or job.requested_by != str(principal.actor.id):
        raise HTTPException(404, "Задание ВАЦ не найдено")
    return job


def _view(job):
    return {"id": str(job.id), "status": job.status, "error": job.error,
            "vna": {"name": job.vna_name}, "file_name": "vna.s1p"}


@router.post("")
def start(principal: Person, now: Now, uow_factory: UowFactory, payload: StartInput | None = None):
    choices = vna_jobs.agents(uow_factory(), now)
    if not choices:
        return {"status": "no_agent"}
    chosen = next((pc for pc in choices if pc["id"] == (payload.agent_id if payload else "")), None)
    if chosen is None:
        return {"status": "choose", "agents": choices}
    job = create_job(uow_factory(), requested_by=str(principal.actor.id), now=now,
                     target=chosen["id"], kind="vna", vna_name=chosen["vna"]["name"])
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
    return file_response(vna_jobs.content(uow_factory(), job.id), "vna.s1p", download=True)

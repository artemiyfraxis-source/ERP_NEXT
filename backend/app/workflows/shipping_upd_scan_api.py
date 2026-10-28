"""«Данные отправок → УПД сканом или файлом ЭДО» — HTTP contract (owner 28.10.2026).

The shipment's own УПД (we are the seller) is read the way «Склад → Поставка» reads a supplier's: the same scanner
queue of the warehouse PC agent, the same files and answers (`warehouse.scan_jobs_api`), under the SHIPPING edit
right — the manager (`pm`) has no warehouse right. Every route is scoped to a shipment:
- `POST /api/shipping/shipments/{id}/upd-scan/jobs` (scanner; answers `choose` / `no_agent` as the warehouse does),
  `GET|DELETE …/jobs/{job_id}`, `POST …/file` (multipart `file`: PDF / photo / ЭДО XML / ZIP),
  `POST …/merge` `{files}` («+ Страница»);
- `POST …/apply` `{version, scan_file, upd_number, upd_date, invoice_number, number?}` — the answer lands in the
  shipment: the stored scan becomes its УПД file, `upd_type` is ЭДО for an XML / ZIP and БУМАГА for a scan, the
  document's own № and date are set (shipment version checked: 409 when stale), and the invoice № of «Основание»
  goes to the project card's «№ счёта» when it is empty. A different № already on the shipment is never overwritten
  silently: the answer is `{status: "conflict", current, found}` and nothing changed — repeat with `number:
  "replace"` (the document's №) or `"keep"` (the file and type only). Answer `{status: "applied", shipment, invoice}`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.modules.shipping import api as shipping
from app.modules.shipping.application import (
    AttachUpdCommand,
    SetProjectAddresseeCommand,
    UpdateDocumentsCommand,
    attach_upd_file,
    set_project_addressee,
    update_documents,
)
from app.modules.shipping.domain import UpdType
from app.modules.warehouse import scan_jobs_api
from app.modules.warehouse.api import warehouse_uow_factory
from app.modules.warehouse.receipts_application import scan_file, scan_file_id
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal
from app.workflows.edo_receipt import is_edo_upload

router = APIRouter(prefix="/api/shipping/shipments/{shipment_id}/upd-scan", tags=["shipping"])
Editor = Annotated[Principal, Depends(shipping.shipping_editor_principal)]
Now = Annotated[datetime, Depends(moscow_now)]
WarehouseFactory = Annotated[Callable, Depends(warehouse_uow_factory)]
ShippingFactory = Annotated[Callable, Depends(shipping.shipping_uow_factory)]
# «Сканирование документов» (owner 28.10.2026): the scanner job routes take it on top of the shipping edit right.
Scans = [Depends(scan_jobs_api.scan_edit_principal)]
NO_SCAN = "Скан УПД не найден — отсканируйте или выберите файл заново"


def _exists(shipment_id: UUID, uow_factory: Callable) -> None:
    with uow_factory() as uow:
        if uow.shipments.get(shipment_id) is None:
            raise HTTPException(status_code=404, detail="Отправка не найдена")


@router.post("/jobs", dependencies=Scans)
async def new_scan_job(shipment_id: UUID, request: Request, principal: Editor, now: Now, uow_factory: WarehouseFactory,
                       shipping_factory: ShippingFactory, payload: scan_jobs_api.ScanStartInput | None = None) -> dict:
    await run_in_threadpool(_exists, shipment_id, shipping_factory)
    return await run_in_threadpool(scan_jobs_api.new_scan_job, request, principal, now, uow_factory, payload)


@router.get("/jobs/{job_id}", dependencies=Scans)
def scan_job(shipment_id: UUID, job_id: UUID, request: Request, background: BackgroundTasks, principal: Editor,
             now: Now, uow_factory: WarehouseFactory) -> dict:
    return scan_jobs_api.scan_job(job_id, request, background, principal, now, uow_factory)


@router.delete("/jobs/{job_id}", dependencies=Scans)
def cancel_scan_job(shipment_id: UUID, job_id: UUID, principal: Editor, now: Now, uow_factory: WarehouseFactory) -> dict:
    return scan_jobs_api.cancel_scan_job(job_id, principal, now, uow_factory)


@router.post("/file")
async def scan_document(shipment_id: UUID, request: Request, background: BackgroundTasks, principal: Editor, now: Now,
                        uow_factory: WarehouseFactory, shipping_factory: ShippingFactory) -> dict:
    await run_in_threadpool(_exists, shipment_id, shipping_factory)
    return await scan_jobs_api.scan_receipt_document(request, background, principal, now, uow_factory)


@router.post("/merge")
async def merge_scans(shipment_id: UUID, payload: scan_jobs_api.ScanMergeInput, request: Request,
                      background: BackgroundTasks, principal: Editor, now: Now, uow_factory: WarehouseFactory,
                      shipping_factory: ShippingFactory) -> dict:
    await run_in_threadpool(_exists, shipment_id, shipping_factory)
    return await scan_jobs_api.merge_receipt_scans(payload, request, background, principal, now, uow_factory)


class ApplyInput(BaseModel):
    version: int = Field(ge=1)
    scan_file: str = Field(min_length=1, max_length=1024)
    upd_number: str = Field(default="", max_length=255)
    upd_date: date | None = None
    invoice_number: str = Field(default="", max_length=255)
    number: Literal["replace", "keep"] | None = None


def _apply(shipment_id: UUID, payload: ApplyInput, principal: Principal, now: datetime, warehouse: Callable,
           uow_factory: Callable) -> dict:
    file_id = scan_file_id(payload.scan_file)
    stored = scan_file(warehouse(), file_id) if file_id is not None else None
    if stored is None:
        raise HTTPException(status_code=404, detail=NO_SCAN)
    file_name, content = stored
    with uow_factory() as uow:
        shipment = uow.shipments.get(shipment_id)
    if shipment is None:
        raise HTTPException(status_code=404, detail="Отправка не найдена")
    if shipment.version != payload.version:
        raise HTTPException(status_code=409, detail="Отгрузка изменилась после загрузки формы")
    found = payload.upd_number.strip()
    current = shipment.upd_number.strip()
    if payload.number is None and found and current and found != current:
        return {"status": "conflict", "current": current, "found": found}
    take = payload.number != "keep" and bool(found)
    number = found if take else current
    day = (payload.upd_date or shipment.upd_date) if take else shipment.upd_date
    kind = UpdType.EDO if is_edo_upload(file_name, content) else UpdType.PAPER
    actor = principal.actor

    def attach():
        documented = update_documents(UpdateDocumentsCommand(
            shipment_id=shipment_id, expected_version=payload.version, actor=actor, now=now, upd_type=kind,
            upd_number=number, upd_date=day, upd_url=shipment.upd_url, track_number=shipment.track_number,
        ), uow_factory())
        return attach_upd_file(AttachUpdCommand(
            shipment_id=shipment_id, expected_version=documented.version, actor=actor, now=now, file_name=file_name,
            content_type="", content=content,
        ), uow_factory())

    attached = shipping._run(attach)
    invoice = ""
    project = attached.lines[0].project if attached.lines else ""
    typed = payload.invoice_number.strip()
    if typed and project:
        with uow_factory() as uow:
            card = uow.addressees.get(project)
        if card is None or not card.invoice.strip():
            shipping._run(lambda: set_project_addressee(SetProjectAddresseeCommand(
                project=project, fields={"invoice": typed}, expected_version=None, actor=actor, now=now,
            ), uow_factory()))
            invoice = typed
    with uow_factory() as uow:
        fresh = uow.shipments.get(shipment_id) or attached
    return {"status": "applied", "shipment": shipping._shipment_result(fresh), "invoice": invoice}


@router.post("/apply")
async def apply(shipment_id: UUID, payload: ApplyInput, principal: Editor, now: Now, warehouse: WarehouseFactory,
                uow_factory: ShippingFactory) -> dict:
    """The answer of the reading lands in the shipment: the scan becomes its УПД file, № and date are set."""
    return await run_in_threadpool(_apply, shipment_id, payload, principal, now, warehouse, uow_factory)

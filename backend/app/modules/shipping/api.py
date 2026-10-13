"""HTTP contract for finished goods and shipment execution."""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.clock import moscow_now
from app.shared.http_auth import (
    Principal, require_section, section_allowed, shipping_manager_principal, shipping_view_principal,
    shipping_warehouse_principal,
)
from app.shared.stored_files import decode_upload, file_response

from .application import (
    AttachUpdCommand, attach_upd_file,
    ConcurrentShippingUpdate, CreateShipmentCommand, DropProjectCommand, EditHeadCommand,
    LayOutCommand, PackProjectCommand, ProjectShipCommand, ProjectShipLine, QueueProjectCommand, RegisterGoodCommand,
    RequestedGood, RollbackShippedCommand, SetProjectAddresseeCommand, SetRequestedCommand, ShipmentCommand,
    ShippingPermissionError, UnitOfWork, UnpickCommand, UpdUpload, UpdateDocumentsCommand, cancel_shipment,
    close_shipment, create_shipment, drop_project, edit_shipment_head, lay_out_shipment, mark_shipped, pack_project,
    pack_shipment, pick_shipment, queue_project, register_good, rollback_shipped, set_project_addressee, set_requested,
    ship_project, unpick_line, update_documents,
)
from .documents import build_box_labels, build_delivery_note, build_packing_lists, packer_short
from .domain import (
    ADDRESSEE_REQUISITE_FIELDS, DeliveryTerms, FinishedGood, ProjectAddressee, ProjectAssembly, Shipment,
    ShippingValidationError, UpdType, total_gross,
)


router = APIRouter(prefix="/api/shipping", tags=["shipping"])
# SUPPLYMAP `portalIssueGuard_(…, 'ship')`: упаковать в карточке проекта, сформировать отгрузочные и
# поправить «Заказано» может и менеджер («Отгрузка»), и кладовщик («Отгрузка — склад») — правка любого из двух.
shipping_editor_principal = require_section(("shipping", "shipping_goods"), "edit")
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]


class GoodPayload(BaseModel):
    project: str = Field(min_length=1, max_length=255)
    item_index: str = Field(min_length=1, max_length=255)
    customer: str = Field(default="", max_length=512)
    article: str = Field(default="", max_length=255)
    name: str = Field(min_length=1, max_length=512)
    ordered: Quantity
    unit: str = Field(default="шт", min_length=1, max_length=64)
    note: str = Field(default="", max_length=5000)


class AssemblyPayload(BaseModel):
    item_index: str = Field(default="", max_length=255)
    name: str = Field(default="", max_length=512)
    run: Decimal = Field(default=Decimal("0"), max_digits=18, decimal_places=6)
    article: str = Field(default="", max_length=255)


class ProjectQueuePayload(BaseModel):
    project: str = Field(min_length=1, max_length=255)
    customer: str = Field(default="", max_length=512)
    assemblies: list[AssemblyPayload] = Field(default_factory=list, max_length=1000)


class ProjectDropPayload(BaseModel):
    project: str = Field(min_length=1, max_length=255)


class ShipmentLinePayload(BaseModel):
    good_id: UUID
    quantity: Quantity


class ShipmentPayload(BaseModel):
    """Empty receiver, point, contact, phone, terms, carrier and УПД type inherit the project card."""

    customer: str = Field(default="", max_length=512)
    receiver: str = Field(default="", max_length=512)
    shipping_point: str = Field(default="", max_length=5000)
    contact: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=128)
    terms: DeliveryTerms | None = None
    carrier: str = Field(default="", max_length=255)
    deadline: date | None = None
    upd_type: UpdType | None = None
    upd_number: str = Field(default="", max_length=255)
    upd_date: date | None = None
    upd_url: str = Field(default="", max_length=5000)
    note: str = Field(default="", max_length=5000)
    no_docs: bool = False
    lines: list[ShipmentLinePayload] = Field(min_length=1, max_length=500)


class AddresseePayload(BaseModel):
    """Card edit: only the sent fields change; an empty value erases the stored one."""

    project: str = Field(min_length=1, max_length=255)
    version: int | None = Field(default=None, ge=0)
    receiver: str | None = Field(default=None, max_length=512)
    shipping_point: str | None = Field(default=None, max_length=5000)
    contact: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=128)
    terms: DeliveryTerms | None = None
    carrier: str | None = Field(default=None, max_length=255)
    upd_type: UpdType | None = None
    po: str | None = Field(default=None, max_length=255)
    contract: str | None = Field(default=None, max_length=255)
    spec: str | None = Field(default=None, max_length=255)
    invoice: str | None = Field(default=None, max_length=255)
    pays: str | None = Field(default=None, max_length=255)
    customer_address: str | None = Field(default=None, max_length=5000)
    hours: str | None = Field(default=None, max_length=255)


class ProjectPackPayload(BaseModel):
    project: str = Field(min_length=1, max_length=255)
    lines: list[ShipmentLinePayload] = Field(min_length=1, max_length=500)


class VersionPayload(BaseModel):
    version: int = Field(ge=1)


class UnpickPayload(VersionPayload):
    line_id: UUID


class DocumentsPayload(VersionPayload):
    upd_type: UpdType | None = None
    upd_number: str = Field(default="", max_length=255)
    upd_date: date | None = None
    upd_url: str = Field(default="", max_length=5000)
    track_number: str = Field(default="", max_length=255)


class HeadPayload(VersionPayload):
    """Шапка отправки: приходят только правленые поля; пустое поле адресата — снова из карточки проекта."""
    receiver: str | None = Field(default=None, max_length=512)
    shipping_point: str | None = Field(default=None, max_length=1000)
    contact: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=255)
    terms: DeliveryTerms | None = None
    carrier: str | None = Field(default=None, max_length=255)
    deadline: date | None = None
    note: str | None = Field(default=None, max_length=5000)


class RequestedPayload(VersionPayload):
    quantity: Quantity


class BoxItemPayload(BaseModel):
    item_index: str = Field(max_length=255)
    quantity: Decimal = Field(ge=0, max_digits=18, decimal_places=6)


class BoxPayload(BaseModel):
    kind: str = Field(default="", max_length=255)
    dims: str = Field(default="", max_length=255)
    net: str = Field(default="", max_length=64)
    gross: str = Field(default="", max_length=64)
    seal: str = Field(default="", max_length=255)
    items: list[BoxItemPayload] = Field(default_factory=list, max_length=500)


class BoxesPayload(VersionPayload):
    boxes: list[BoxPayload] = Field(max_length=30)
    tk_note: str = Field(default="", max_length=5000)


class UpdFilePayload(VersionPayload):
    shipment_id: UUID
    file_name: str = Field(min_length=1, max_length=512)
    content_type: str = Field(default="", max_length=255)
    content_base64: str = Field(min_length=1, max_length=28_000_000)


class ProjectShipLinePayload(BaseModel):
    good_id: UUID
    version: int = Field(ge=1)
    quantity: Quantity


class ProjectShipAddresseePayload(BaseModel):
    """Правленые поля карточки проекта; неприсланное не меняется, пустое стирает."""

    receiver: str | None = Field(default=None, max_length=512)
    shipping_point: str | None = Field(default=None, max_length=5000)
    contact: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=128)
    terms: DeliveryTerms | None = None
    carrier: str | None = Field(default=None, max_length=255)
    upd_type: UpdType | None = None


class ProjectShipDocumentsPayload(BaseModel):
    upd_number: str = Field(default="", max_length=255)
    upd_date: date | None = None
    deadline: date | None = None


class UpdUploadPayload(BaseModel):
    file_name: str = Field(min_length=1, max_length=512)
    content_type: str = Field(default="", max_length=255)
    content_base64: str = Field(min_length=1, max_length=28_000_000)


class ProjectShipPayload(BaseModel):
    """«Отгрузить» / «Упаковать, отгрузить позже»: всё, что раньше уходило четырьмя запросами."""

    project: str = Field(min_length=1, max_length=255)
    lines: list[ProjectShipLinePayload] = Field(min_length=1, max_length=500)
    addressee_version: int = Field(ge=0)
    addressee: ProjectShipAddresseePayload | None = None
    documents: ProjectShipDocumentsPayload = Field(default_factory=ProjectShipDocumentsPayload)
    upd_file: UpdUploadPayload | None = None
    ship: bool = False


def shipping_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "shipping_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _good_result(good: FinishedGood) -> dict:
    return {
        "id": str(good.id), "version": good.version, "project": good.project,
        "item_index": good.item_index, "customer": good.customer, "article": good.article,
        "name": good.name, "ordered": _number(good.ordered), "unit": good.unit,
        "accepted": _number(good.accepted), "shipped": _number(good.shipped),
        "remaining": _number(good.remaining), "sent": _number(good.sent), "before_erp": _number(good.before_erp),
        "received_at": good.received_at.isoformat() if good.received_at else None,
        "grn": good.grn, "note": good.note,
    }


def _addressee_result(addressee: ProjectAddressee) -> dict:
    return {
        "project": addressee.project, "version": addressee.version, "receiver": addressee.receiver,
        "shipping_point": addressee.shipping_point, "contact": addressee.contact, "phone": addressee.phone,
        "terms": addressee.terms.value if addressee.terms else None, "carrier": addressee.carrier,
        "upd_type": addressee.upd_type.value if addressee.upd_type else None,
        "updated_by": addressee.updated_by,
        "updated_at": addressee.updated_at.isoformat() if addressee.updated_at else None,
        **{key: getattr(addressee, key) for key in ADDRESSEE_REQUISITE_FIELDS},
    }


def _shipment_result(shipment: Shipment) -> dict:
    return {
        "id": str(shipment.id), "number": shipment.number, "version": shipment.version,
        "created_at": shipment.created_at.isoformat(), "status": shipment.status.value,
        "customer": shipment.customer, "receiver": shipment.receiver,
        "shipping_point": shipment.shipping_point, "contact": shipment.contact,
        "phone": shipment.phone, "terms": shipment.terms.value if shipment.terms else None, "carrier": shipment.carrier,
        "deadline": shipment.deadline.isoformat() if shipment.deadline else None,
        "upd_type": shipment.upd_type.value if shipment.upd_type else None, "upd_number": shipment.upd_number,
        "upd_date": shipment.upd_date.isoformat() if shipment.upd_date else None,
        "upd_url": shipment.upd_url, "pl_number": shipment.pl_number,
        "dn_number": shipment.dn_number, "track_number": shipment.track_number,
        "packed_at": shipment.packed_at.isoformat() if shipment.packed_at else None,
        "shipped_at": shipment.shipped_at.isoformat() if shipment.shipped_at else None,
        "manager": shipment.manager, "note": shipment.note, "tk_note": shipment.tk_note,
        "places": len(shipment.boxes), "weight": total_gross(shipment.boxes),
        "inherited": sorted(shipment.inherited),
        "boxes": [{
            "place": box.place, "kind": box.kind, "dims": box.dims, "net": box.net, "gross": box.gross,
            "seal": box.seal,
            "items": [{"item_index": item.item_index, "quantity": _number(item.quantity)} for item in box.items],
        } for box in shipment.boxes],
        "lines": [{
            "id": str(line.id), "good_id": str(line.finished_good_id),
            "project": line.project, "item_index": line.item_index, "article": line.article,
            "name": line.name, "requested": _number(line.requested),
            "picked": _number(line.picked), "shortage": _number(line.shortage),
            "unit": line.unit, "grn": line.grn,
        } for line in shipment.lines],
    }


def _run(operation):
    try:
        return operation()
    except ShippingValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConcurrentShippingUpdate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ShippingPermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: обновите данные") from error


@router.get("/goods")
def goods(
    _principal: Annotated[Principal, Depends(shipping_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> dict:
    with uow_factory() as uow: rows = uow.goods.list(limit)
    return {"goods": [_good_result(good) for good in rows]}


@router.post("/project-queue")
def queue_project_goods(
    payload: ProjectQueuePayload,
    principal: Annotated[Principal, Depends(shipping_manager_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    result = _run(lambda: queue_project(QueueProjectCommand(
        project=payload.project, customer=payload.customer,
        assemblies=tuple(ProjectAssembly(item_index=item.item_index, name=item.name, run=item.run, article=item.article) for item in payload.assemblies),
        actor=principal.actor, now=now,
    ), uow_factory()))
    return {"added": result.added, "updated": result.updated, "skipped": list(result.skipped)}


@router.post("/project-queue/drop")
def drop_project_goods(
    payload: ProjectDropPayload,
    principal: Annotated[Principal, Depends(shipping_manager_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    result = _run(lambda: drop_project(DropProjectCommand(project=payload.project, actor=principal.actor, now=now), uow_factory()))
    return {"deleted": result.deleted, "kept": result.kept}


@router.post("/goods")
def add_good(
    payload: GoodPayload,
    principal: Annotated[Principal, Depends(shipping_manager_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    good = _run(lambda: register_good(RegisterGoodCommand(**payload.model_dump(), actor=principal.actor, now=now), uow_factory()))
    return {"good": _good_result(good)}


# «Принять» одной КБ (POST /goods/{good_id}/accept) — в app/workflows/finished_goods_api.py: предел «сдано цехом»
# читается из производства, поэтому приёмка живёт в межмодульном сценарии вместе с пакетной «Из производства».


@router.get("/project-addressee")
def project_addressee(
    _principal: Annotated[Principal, Depends(shipping_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
    project: Annotated[str, Query(min_length=1, max_length=255)],
) -> dict:
    name = project.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Проект обязателен")
    with uow_factory() as uow: addressee = uow.addressees.get(name)
    return {"addressee": _addressee_result(addressee or ProjectAddressee(project=name))}


@router.get("/project-addressees")
def project_addressees(
    _principal: Annotated[Principal, Depends(shipping_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    """All project cards at once, like `portalShipList` returns `projects` for the «Проекты» tab."""
    with uow_factory() as uow: rows = uow.addressees.list()
    return {"addressees": [_addressee_result(addressee) for addressee in rows]}


@router.post("/project-pack")
def pack_project_goods(
    payload: ProjectPackPayload,
    principal: Annotated[Principal, Depends(shipping_editor_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    shipment = _run(lambda: pack_project(PackProjectCommand(
        project=payload.project,
        lines=tuple(RequestedGood(good_id=line.good_id, quantity=line.quantity) for line in payload.lines),
        actor=principal.actor, now=now,
    ), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.post("/project-ship")
def ship_project_goods(
    payload: ProjectShipPayload,
    principal: Annotated[Principal, Depends(shipping_editor_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    """Карточка адресата, отправка, файл УПД и отгрузка — одна транзакция; повтор с тем же ключом не дублирует."""
    if payload.ship and not section_allowed(principal, ("shipping_goods",), "edit"):
        raise HTTPException(status_code=403, detail="Отгрузить может склад (раздел «Отгрузка — склад»)")
    fields = payload.addressee.model_dump(exclude_unset=True) if payload.addressee is not None else {}
    fields = {key: value.value if hasattr(value, "value") else value for key, value in fields.items()}
    upload = None
    if payload.upd_file is not None:
        upload = UpdUpload(
            file_name=payload.upd_file.file_name, content_type=payload.upd_file.content_type,
            content=decode_upload(payload.upd_file.content_base64, "Файл УПД повреждён"),
        )
    result = _run(lambda: ship_project(ProjectShipCommand(
        idempotency_key=idempotency_key, project=payload.project,
        lines=tuple(ProjectShipLine(good_id=line.good_id, version=line.version, quantity=line.quantity) for line in payload.lines),
        addressee_version=payload.addressee_version, addressee_fields=fields,
        deadline=payload.documents.deadline, upd_number=payload.documents.upd_number, upd_date=payload.documents.upd_date,
        upd_file=upload, ship=payload.ship, as_manager=section_allowed(principal, ("shipping",), "edit"),
        actor=principal.actor, now=now,
    ), uow_factory()))
    status = result.shipment.status.value
    return {
        "shipment": _shipment_result(result.shipment), "shipped": status in {"Отгружена", "Закрыта"},
        "closed": status == "Закрыта", "why": result.why, "replayed": result.replayed,
    }


@router.put("/project-addressee")
def save_project_addressee(
    payload: AddresseePayload,
    principal: Annotated[Principal, Depends(shipping_manager_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    fields = payload.model_dump(exclude={"project", "version"}, exclude_unset=True)
    fields = {key: value.value if hasattr(value, "value") else value for key, value in fields.items()}
    addressee = _run(lambda: set_project_addressee(SetProjectAddresseeCommand(
        project=payload.project, fields=fields, expected_version=payload.version,
        actor=principal.actor, now=now,
    ), uow_factory()))
    return {"addressee": _addressee_result(addressee)}


@router.get("/shipments")
def shipments(
    _principal: Annotated[Principal, Depends(shipping_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict:
    with uow_factory() as uow: rows = uow.shipments.list(limit)
    return {"shipments": [_shipment_result(shipment) for shipment in rows]}


@router.post("/shipments")
def add_shipment(
    payload: ShipmentPayload,
    principal: Annotated[Principal, Depends(shipping_manager_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    data = payload.model_dump(exclude={"lines"})
    shipment = _run(lambda: create_shipment(CreateShipmentCommand(
        **data, lines=tuple(RequestedGood(good_id=line.good_id, quantity=line.quantity) for line in payload.lines),
        actor=principal.actor, now=now,
    ), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


def _command(shipment_id: UUID, payload: VersionPayload, principal: Principal, now: datetime) -> ShipmentCommand:
    return ShipmentCommand(shipment_id=shipment_id, expected_version=payload.version, actor=principal.actor, now=now)


@router.post("/shipments/{shipment_id}/pick")
def pick(shipment_id: UUID, payload: VersionPayload, principal: Annotated[Principal, Depends(shipping_warehouse_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: pick_shipment(_command(shipment_id, payload, principal, now), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.post("/shipments/{shipment_id}/unpick")
def unpick(shipment_id: UUID, payload: UnpickPayload, principal: Annotated[Principal, Depends(shipping_warehouse_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: unpick_line(UnpickCommand(shipment_id=shipment_id, expected_version=payload.version, line_id=payload.line_id, actor=principal.actor, now=now), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.post("/shipments/{shipment_id}/pack")
def pack(shipment_id: UUID, payload: VersionPayload, principal: Annotated[Principal, Depends(shipping_warehouse_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: pack_shipment(_command(shipment_id, payload, principal, now), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.put("/shipments/{shipment_id}/documents")
def documents(shipment_id: UUID, payload: DocumentsPayload, principal: Annotated[Principal, Depends(shipping_manager_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: update_documents(UpdateDocumentsCommand(
        shipment_id=shipment_id, expected_version=payload.version, actor=principal.actor, now=now,
        upd_type=payload.upd_type, upd_number=payload.upd_number, upd_date=payload.upd_date,
        upd_url=payload.upd_url, track_number=payload.track_number,
    ), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.put("/shipments/{shipment_id}/head")
def head(shipment_id: UUID, payload: HeadPayload, principal: Annotated[Principal, Depends(shipping_editor_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    fields = {key: getattr(payload, key) for key in payload.model_fields_set if key != "version"}
    shipment = _run(lambda: edit_shipment_head(EditHeadCommand(
        shipment_id=shipment_id, expected_version=payload.version, actor=principal.actor, now=now, fields=fields,
    ), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.post("/shipments/{shipment_id}/ship")
def ship(shipment_id: UUID, payload: VersionPayload, principal: Annotated[Principal, Depends(shipping_warehouse_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    result = _run(lambda: mark_shipped(_command(shipment_id, payload, principal, now), uow_factory()))
    return {"shipment": _shipment_result(result.shipment), "closed": result.shipment.status.value == "Закрыта", "why": result.why}


class UnshipPayload(VersionPayload):
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/shipments/{shipment_id}/unship")
def unship(shipment_id: UUID, payload: UnshipPayload, principal: Annotated[Principal, Depends(shipping_editor_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    """Откат «Отгружено»: только руководитель (администратор), с причиной; штуки КБ — снова «Упаковано»."""
    shipment = _run(lambda: rollback_shipped(RollbackShippedCommand(
        shipment_id=shipment_id, expected_version=payload.version, actor=principal.actor, now=now,
        reason=payload.reason, allowed=principal.full_access and not principal.service,
    ), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.put("/shipments/{shipment_id}/lines/{line_id}")
def change_requested(shipment_id: UUID, line_id: UUID, payload: RequestedPayload, principal: Annotated[Principal, Depends(shipping_editor_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: set_requested(SetRequestedCommand(
        shipment_id=shipment_id, expected_version=payload.version, line_id=line_id, quantity=payload.quantity,
        actor=principal.actor, now=now,
    ), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.put("/shipments/{shipment_id}/boxes")
def lay_out(shipment_id: UUID, payload: BoxesPayload, principal: Annotated[Principal, Depends(shipping_editor_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: lay_out_shipment(LayOutCommand(
        shipment_id=shipment_id, expected_version=payload.version, actor=principal.actor, now=now,
        boxes=tuple(box.model_dump() for box in payload.boxes), tk_note=payload.tk_note,
    ), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.get("/shipments/{shipment_id}/print")
def print_documents(
    shipment_id: UUID,
    document: Annotated[str, Query(pattern="^(pl|dn)$")],
    _principal: Annotated[Principal, Depends(shipping_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    """PL (лист на каждый короб) или DN — по актуальным данным отправки; повторная печать = перевыпуск."""
    with uow_factory() as uow:
        shipment = uow.shipments.get(shipment_id)
        if shipment is None:
            raise HTTPException(status_code=404, detail="Отгрузка не найдена")
        # Заказчик DN — как в реестре отгрузок: из заявки, иначе из позиции ГП, иначе получатель.
        goods = (uow.goods.get(line.finished_good_id) for line in shipment.lines)
        customer = shipment.customer or next((good.customer for good in goods if good and good.customer), "") or shipment.receiver
        # «Штамп / подпись кладовщика» — кто упаковал (владелец 13.10.2026), не менеджер отправки.
        packer = packer_short(uow.shipments.packer(shipment_id))
    try:
        forms = build_packing_lists(shipment, packer) if document == "pl" else [build_delivery_note(shipment, customer, packer)]
    except ShippingValidationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"forms": [form.to_dict() for form in forms]}


@router.get("/shipments/{shipment_id}/labels")
def print_labels(
    shipment_id: UUID,
    _principal: Annotated[Principal, Depends(shipping_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    """«Печать этикеток»: этикетка на каждый короб по актуальным данным отправки."""
    with uow_factory() as uow:
        shipment = uow.shipments.get(shipment_id)
        if shipment is None:
            raise HTTPException(status_code=404, detail="Отгрузка не найдена")
    try:
        labels = build_box_labels(shipment)
    except ShippingValidationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"labels": [label.to_dict() for label in labels]}


@router.post("/shipments/{shipment_id}/close")
def close(shipment_id: UUID, payload: VersionPayload, principal: Annotated[Principal, Depends(shipping_warehouse_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: close_shipment(_command(shipment_id, payload, principal, now), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.post("/shipments/{shipment_id}/cancel")
def cancel(shipment_id: UUID, payload: VersionPayload, principal: Annotated[Principal, Depends(shipping_manager_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: cancel_shipment(_command(shipment_id, payload, principal, now), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.post("/upd-files")
def upload_upd(
    payload: UpdFilePayload,
    principal: Annotated[Principal, Depends(shipping_editor_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    """`portalShipUpdFile`: файл УПД к отправке, ссылка на него встаёт в отправку."""
    content = decode_upload(payload.content_base64, "Файл УПД повреждён")
    shipment = _run(lambda: attach_upd_file(AttachUpdCommand(
        shipment_id=payload.shipment_id, expected_version=payload.version, actor=principal.actor, now=now,
        file_name=payload.file_name, content_type=payload.content_type, content=content,
    ), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.get("/files/{file_id}")
def download_file(
    file_id: UUID,
    _principal: Annotated[Principal, Depends(shipping_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> Response:
    with uow_factory() as uow:
        found = uow.files.get(file_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    meta, content = found
    return file_response(content, meta.file_name)

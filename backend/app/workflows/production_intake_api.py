"""«Приёмка с производства» по HTTP (workflows/production_intake.py): всё, что в работе, по КБ — сколько нужно,
запущено, сдано цехом, принято, упаковано, уехало и сколько можно принять сейчас.

Читают раздел «Отгрузка» или «Отгрузка — склад» (просмотр); принимает склад через `finished_goods_api`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.modules.shipping.api import _number
from app.modules.warehouse.labels_application import COPIES_MAX
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, shipping_view_principal, warehouse_edit_principal

from .finished_goods_label import print_act_label
from .production_intake import intake_uow_factory, production_intake

router = APIRouter(prefix="/api/shipping", tags=["shipping"])


def _intake(request: Request) -> Callable[[], object]:
    factory = intake_uow_factory(getattr(request.app.state, "shipping_uow_factory", None))
    if factory is None:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


@router.get("/intake")
def list_intake(
    _principal: Annotated[Principal, Depends(shipping_view_principal)],
    intake: Annotated[Callable[[], object], Depends(_intake)],
) -> dict:
    """КБ открытых проектов, пока заказанное не уехало целиком; `remaining` — сдано цехом и ещё не принято."""
    return {"rows": [{
        "project": row.project, "customer": row.customer,
        "deadline": row.deadline.isoformat() if row.deadline else None,
        "item_index": row.item_index, "name": row.name, "unit": row.unit, "stage": row.stage,
        "project_stage": row.project_stage, "pack_decided": row.pack_decided,
        "needed": _number(row.needed), "launched": _number(row.launched), "delivered": _number(row.delivered),
        "accepted": _number(row.accepted), "packed": _number(row.packed), "sent": _number(row.sent),
        "remaining": _number(row.remaining),
        "good_id": str(row.good.id) if row.good else None, "good_version": row.good.version if row.good else None,
        "grn": row.good.grn if row.good else None, "location": row.location,
        "acts": [{
            "grn": act.grn, "quantity": _number(act.net), "received_at": act.received_at.isoformat(), "location": act.location,
        } for act in row.acts],
        "tasks": [{
            "number": task.number, "status": task.status, "site": task.site, "quantity": task.quantity,
            "delivered": _number(task.delivered),
        } for task in row.tasks],
    } for row in production_intake(intake)]}


class LabelPayload(BaseModel):
    copies: int = Field(default=1, ge=1, le=COPIES_MAX)


@router.post("/intake/{grn}/label")
def print_intake_label(
    grn: str,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    request: Request,
    payload: LabelPayload | None = None,
) -> dict:
    """Этикетка ГРН акта приёмки «ГП.YY.NNNNNNN» → та же очередь печати склада, что у партий (`{copies}`, по умолчанию 1):
    `{status: "queued", reason, file, copies, job_id}`. Права — как у печати этикеток склада («Склад» на правку)."""
    shipping = getattr(request.app.state, "shipping_uow_factory", None)
    warehouse = getattr(request.app.state, "warehouse_uow_factory", None)
    sessions = getattr(shipping() if callable(shipping) else None, "session_factory", None)
    if not callable(sessions) or not callable(warehouse):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    try:
        return print_act_label(
            sessions, warehouse, grn, (payload or LabelPayload()).copies, now, created_by=principal.actor.name,
        ).as_dict()
    except KeyError as error:
        raise HTTPException(status_code=404, detail=error.args[0]) from error
    except ValueError as error:  # код не лезет на этикетку
        raise HTTPException(status_code=422, detail=str(error)) from error

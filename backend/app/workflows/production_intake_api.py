"""«Приёмка с производства» по HTTP (workflows/production_intake.py): всё, что в работе, по КБ — сколько нужно,
запущено, сдано цехом, принято, упаковано, уехало и сколько можно принять сейчас.

Читают раздел «Отгрузка» или «Отгрузка — склад» (просмотр); принимает склад через `finished_goods_api`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.modules.shipping.api import _number
from app.shared.http_auth import Principal, shipping_view_principal

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

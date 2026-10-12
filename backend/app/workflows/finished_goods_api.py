"""Регистр готовой продукции по HTTP (workflows/finished_goods.py): «Из производства», приёмка пакетом и одной КБ,
сквозной статус проекта по КБ.

Читают раздел «Отгрузка» или «Отгрузка — склад» (просмотр); принимает склад — «Отгрузка — склад» на правку, как
прежнее «Принять» и сдача коробки в ГП. Токен API сюда не проходит (гейты не называют его область)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.modules.shipping.api import _good_result, _number, _run
from app.modules.shipping.domain import GOODS_STATE_LABELS, MOVE_KIND_LABELS, GoodsState
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, shipping_view_principal, shipping_warehouse_principal

from .finished_goods import (
    AcceptBatchCommand,
    AcceptLine,
    AcceptOneCommand,
    accept_batch,
    accept_one,
    flow_uow_factory,
    from_production,
    project_status,
)

router = APIRouter(prefix="/api/shipping", tags=["shipping"])
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]


class AcceptOnePayload(BaseModel):
    version: int = Field(ge=1)
    quantity: Quantity
    received_at: date | None = None


class AcceptLinePayload(BaseModel):
    project: str = Field(min_length=1, max_length=255)
    item_index: str = Field(min_length=1, max_length=255)
    quantity: Quantity


class AcceptBatchPayload(BaseModel):
    lines: list[AcceptLinePayload] = Field(min_length=1, max_length=500)
    received_at: date | None = None


def _flow(request: Request) -> Callable[[], object]:
    factory = flow_uow_factory(getattr(request.app.state, "shipping_uow_factory", None))
    if factory is None:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def _share(task) -> dict:
    return {
        "number": task.number, "status": task.status, "site": task.site, "quantity": task.quantity,
        "delivered": _number(task.delivered),
    }


@router.get("/finished-goods/from-production")
def list_from_production(
    _principal: Annotated[Principal, Depends(shipping_view_principal)],
    flow: Annotated[Callable[[], object], Depends(_flow)],
) -> dict:
    """«Из производства»: КБ с живыми заданиями, которые склад принял не целиком; `remaining` — можно принять сейчас."""
    rows = from_production(flow)
    return {"rows": [{
        "project": row.project, "item_index": row.item_index, "name": row.name, "unit": row.unit,
        "needed": _number(row.needed), "delivered": _number(row.delivered), "accepted": _number(row.accepted),
        "remaining": _number(row.remaining), "in_production": _number(row.in_production),
        "good_id": str(row.good.id) if row.good else None, "good_version": row.good.version if row.good else None,
        "tasks": [_share(task) for task in row.tasks],
    } for row in rows]}


@router.post("/finished-goods/accept")
def accept_from_production(
    payload: AcceptBatchPayload,
    principal: Annotated[Principal, Depends(shipping_warehouse_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    flow: Annotated[Callable[[], object], Depends(_flow)],
) -> dict:
    """Приёмка пакетом: отмеченные КБ с количеством — все или ни одной; не больше сданного цехом."""
    result = _run(lambda: accept_batch(AcceptBatchCommand(
        idempotency_key=idempotency_key,
        lines=tuple(AcceptLine(line.project, line.item_index, line.quantity) for line in payload.lines),
        received_at=payload.received_at, actor=principal.actor, now=now,
    ), flow))
    return {"goods": [_good_result(good) for good in result.goods], "replayed": result.replayed}


@router.post("/goods/{good_id}/accept")
def receive_good(
    good_id: UUID, payload: AcceptOnePayload,
    principal: Annotated[Principal, Depends(shipping_warehouse_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    flow: Annotated[Callable[[], object], Depends(_flow)],
) -> dict:
    good = _run(lambda: accept_one(AcceptOneCommand(
        good_id=good_id, expected_version=payload.version, quantity=payload.quantity,
        received_at=payload.received_at, actor=principal.actor, now=now,
    ), flow))
    return {"good": _good_result(good)}


@router.get("/finished-goods/project")
def finished_goods_project(
    _principal: Annotated[Principal, Depends(shipping_view_principal)],
    flow: Annotated[Callable[[], object], Depends(_flow)],
    project: Annotated[str, Query(min_length=1, max_length=255)],
) -> dict:
    """Сквозной статус проекта: по КБ штуки в каждом состоянии (В производстве → Принято складом → Упаковано →
    Отгружено), сдано цехом, отправки КБ и журнал ходов (новые сверху)."""
    name = project.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Проект обязателен")
    status = project_status(name, flow)
    return {
        "project": status.project,
        "states": [{"key": state.value, "label": GOODS_STATE_LABELS[state]} for state in GoodsState],
        "kbs": [{
            "item_index": kb.item_index, "name": kb.name, "unit": kb.unit,
            "good_id": str(kb.good.id) if kb.good else None,
            "needed": _number(kb.register.needed),
            "delivered": _number(kb.register.delivered) if kb.register.delivered is not None else None,
            "waiting": _number(kb.register.waiting),
            "pieces": {
                GoodsState.PRODUCTION.value: _number(kb.register.in_production),
                GoodsState.ACCEPTED.value: _number(kb.register.accepted),
                GoodsState.PACKED.value: _number(kb.register.packed),
                GoodsState.SHIPPED.value: _number(kb.register.shipped),
            },
            "totals": {
                "accepted": _number(kb.register.accepted_total), "packed": _number(kb.register.packed_total),
                "shipped": _number(kb.register.shipped_total),
            },
            "has_tasks": kb.has_tasks, "journal_ok": kb.journal_ok,
            "shipments": [{
                "id": str(item.shipment_id), "number": item.number, "status": item.status, "picked": _number(item.picked),
            } for item in kb.shipments],
        } for kb in status.kbs],
        "moves": [{
            "id": str(move.id), "item_index": move.item_index, "kind": move.kind.value,
            "kind_label": MOVE_KIND_LABELS[move.kind], "quantity": _number(move.quantity),
            "from": move.from_state.value, "to": move.to_state.value,
            "from_label": GOODS_STATE_LABELS[move.from_state], "to_label": GOODS_STATE_LABELS[move.to_state],
            "shipment_id": str(move.shipment_id) if move.shipment_id else None,
            "shipment_number": status.shipment_numbers.get(move.shipment_id) if move.shipment_id else None,
            "actor": move.actor, "occurred_at": move.occurred_at.isoformat(), "reason": move.reason,
        } for move in status.moves],
    }

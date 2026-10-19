"""«Печать этикеток ГРН» HTTP contract (stage option warehouse.labels; Roller_Buyer `routes/labels.ts`).

Every route answers 404 while the option is off — checked after the access gate, so a viewer is refused a write (403)
before anything else. Reads open on view; settings, the test print and a lot's print take warehouse edit (or admin).
The lot routes are shared with the ТСД screen: `GET /api/stock/batches/{lot_id}/label.png` and
`POST /api/stock/batches/{lot_id}/label` `{copies: 1..50}` → `{status, reason, file, copies}`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.shared import stage_options
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, warehouse_edit_principal, warehouse_view_principal

from .api import warehouse_uow_factory
from .application import UnitOfWork
from .labels.printer import list_printers, on_windows, print_dir
from .labels.settings import CODE_TYPE_LABELS, PrintSettings
from .labels_application import (
    COPIES_MAX,
    SAMPLE,
    find_lot,
    label_settings,
    lot_label_data,
    preview_png,
    print_label,
    save_label_settings,
)

OPTION = "warehouse.labels"
router = APIRouter(prefix="/api/stock", tags=["warehouse"])
UowFactory = Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)]


class PrintPayload(BaseModel):
    copies: int = Field(default=1, ge=1, le=COPIES_MAX)


def _require_option() -> None:
    if not stage_options.enabled(OPTION):
        raise HTTPException(status_code=404, detail="Not Found")


def _png(content: bytes) -> Response:
    # The preview changes with the settings and the balance: never served from a cache.
    return Response(content=content, media_type="image/png", headers={"Cache-Control": "no-store"})


def _rendered(operation):
    try:
        return operation()
    except KeyError as error:
        raise HTTPException(status_code=404, detail=error.args[0]) from error
    except ValueError as error:  # the code does not fit the label size
        raise HTTPException(status_code=422, detail=str(error)) from error


def _settings_dict(settings: PrintSettings) -> dict:
    return settings.model_dump()


@router.get("/labels/settings")
def get_settings(_principal: Annotated[Principal, Depends(warehouse_view_principal)], uow_factory: UowFactory) -> dict:
    _require_option()
    return {"settings": _settings_dict(label_settings(uow_factory())), "code_types": CODE_TYPE_LABELS,
            "print_dir": str(print_dir()), "copies_max": COPIES_MAX}


@router.put("/labels/settings")
def put_settings(
    payload: PrintSettings,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    _require_option()
    try:
        saved = save_label_settings(uow_factory(), payload, principal.actor, now)
    except IntegrityError as error:
        raise HTTPException(status_code=422, detail="Настройки печати вне допустимых границ") from error
    return {"settings": _settings_dict(saved)}


@router.get("/labels/printers")
def printers(_principal: Annotated[Principal, Depends(warehouse_view_principal)]) -> dict:
    """Printers of the Windows the ERP server runs on (the label printer is plugged into that PC by USB)."""
    _require_option()
    return {"printers": list_printers(), "on_windows": on_windows()}


@router.get("/labels/sample.png")
def sample_png(_principal: Annotated[Principal, Depends(warehouse_view_principal)], uow_factory: UowFactory) -> Response:
    _require_option()
    return _png(_rendered(lambda: preview_png(SAMPLE, label_settings(uow_factory()))))


@router.post("/labels/test")
def test_print(
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    _require_option()
    return _rendered(lambda: print_label(SAMPLE, label_settings(uow_factory()), 1, now)).as_dict()


@router.get("/labels/lot")
def lot_by_key(
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    uow_factory: UowFactory,
    grn: Annotated[str, Query(min_length=1, max_length=32)],
    warehouse: Annotated[str, Query(max_length=8)] = "ЦАР",
    package: Annotated[str, Query(max_length=32)] = "",
) -> dict:
    """ГРН + ПАКЕТ → the lot, for «Печать этикеток» right after receiving. 404 while the lot is not on stock yet."""
    _require_option()
    return {"lot_id": str(_rendered(lambda: find_lot(uow_factory(), warehouse, grn, package)))}


@router.get("/batches/{lot_id}/label.png")
def lot_label_png(
    lot_id: UUID,
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    uow_factory: UowFactory,
) -> Response:
    _require_option()
    return _png(_rendered(lambda: preview_png(lot_label_data(uow_factory(), lot_id), label_settings(uow_factory()))))


@router.post("/batches/{lot_id}/label")
def print_lot_label(
    lot_id: UUID,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
    payload: PrintPayload | None = None,
) -> dict:
    """`{copies}` → `{status: printed | saved | failed, reason, file, copies}`; the job file is saved in any case."""
    _require_option()
    copies = (payload or PrintPayload()).copies
    return _rendered(
        lambda: print_label(lot_label_data(uow_factory(), lot_id), label_settings(uow_factory()), copies, now)
    ).as_dict()

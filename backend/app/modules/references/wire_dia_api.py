"""GET /api/references/wire-dia — «Ø изоляции по маркам» from the база монтажных проводов (`wire_dia`), the table the
ETK generator reads wire Ø, core Ø and ГОСТ → AWG from."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends

from app.shared.http_auth import Principal, references_view_principal

from .api import references_uow_factory
from .application import UnitOfWork
from .wire_dia import COLUMNS, SERIES, wire_dia_records, wire_dia_rows

router = APIRouter(prefix="/api/references", tags=["references"])


@router.get("/wire-dia")
def wire_dia(
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        rows = wire_dia_rows(uow.cable_structure.gauges(), uow.cable_structure.hookups())
    return {"columns": list(COLUMNS), "series": list(SERIES), "records": wire_dia_records(rows)}

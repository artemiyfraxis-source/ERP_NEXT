"""HTTP: rows into a «Справочник» table by its natural key (owner 12.10.2026: «надо добавить функционал API к базе
данных, это критически важно — парсить контакты, разъёмы и т. д. вручную гемор»).

`POST /api/references/import/<table>?dry_run=1|0&source=api` — body `{"rows": [{"fields": {…}, "old": {…} | null}]}`
(shared/bulk_import.py `ImportRequest`), answer `ImportReport`; `hookups` takes the hookup-wires.json document and
answers its own report (the same sections plus its own). Without `dry_run=0` it is a check. Gate: a token with
`refs:write` or a person with «Справочник» edit; the supplier / country maps also want the buyer's right
(`procurement` edit) from a person, as on screen. `GET /api/references/import/<table>/schema` — the key and fields.
docs/api-references.md is the caller's manual."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.shared import bulk_import
from app.shared.bulk_import import MAX_IMPORT_ROWS, ImportRefused, ImportRequest
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, references_edit_principal, references_view_principal

from .api import references_uow_factory
from .application import UnitOfWork
from .domain import ReferenceValidationError
from .importers import IMPORT_TABLES, MAP_TABLES, import_hookups, import_rows, import_schema, unknown_table

router = APIRouter(prefix="/api/references/import", tags=["references"])

Source = Literal["erp", "api", "warehouse"]


def _known(table: str) -> None:
    if table not in IMPORT_TABLES:
        raise HTTPException(status_code=404, detail=str(unknown_table(table)))


@router.get("/{table}/schema")
def table_schema(
    table: str,
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    _known(table)
    return import_schema(table, uow_factory())


@router.post("/{table}")
def import_table(
    table: str,
    body: Annotated[dict[str, Any], Body()],
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
    dry_run: Annotated[bool, Query()] = True,
    source: Annotated[Source, Query()] = "api",
) -> dict:
    _known(table)
    if table in MAP_TABLES and not principal.service and not (
        principal.full_access or principal.permissions.get("procurement") == "edit"
    ):
        raise HTTPException(status_code=403, detail="Карты поставщиков и стран правит закупщик.")
    try:
        if table == "hookups":
            bulk_import.RATE_LIMITER.check(principal.actor.id)
            return import_hookups(uow_factory(), body, principal.actor, now, dry_run=dry_run, source=source).as_dict()
        rows = body.get("rows")
        if isinstance(rows, list) and len(rows) > MAX_IMPORT_ROWS:
            raise bulk_import.ImportTooLarge(f"Строк за раз: не больше {MAX_IMPORT_ROWS}")
        try:
            request = ImportRequest.model_validate(body)
        except ValidationError as error:
            raise HTTPException(status_code=422, detail=error.errors(include_url=False, include_context=False)) from error
        report = import_rows(table, uow_factory(), request.rows, principal.actor, now, dry_run=dry_run, source=source,
                             limiter=bulk_import.RATE_LIMITER)
        return report.model_dump(mode="json")
    except ImportRefused as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except ReferenceValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="Справочник изменён конкурентно — повторите загрузку") from error

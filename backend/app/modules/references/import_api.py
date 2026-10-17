"""HTTP: rows into a «Справочник» table by its natural key (owner 12.10.2026: «надо добавить функционал API к базе
данных, это критически важно — парсить контакты, разъёмы и т. д. вручную гемор»).

`POST /api/references/import/<table>?dry_run=1|0&source=api` — body `{"rows": [{"fields": {…}, "old": {…} | null}]}`
(shared/bulk_import.py `ImportRequest`), answer `ImportReport`; `hookups` takes the hookup-wires.json document and
answers its own report (the same sections plus its own); `&fill_empty=1` writes only its empty cells (importers.py).
Without `dry_run=0` it is a check. Gate: a token with
`refs:write` or a person with «Справочник» edit; the supplier / country maps also want the buyer's right
(`procurement` edit) from a person, as on screen. `GET /api/references/import/<table>/schema` — the key and fields.
docs/api-references.md is the caller's manual.

«Скачать для правки» / «Загрузить исправленную» (owner 14.10.2026, roundtrip.py): `GET <table>/file/fields`,
`POST <table>/file/export` (the screen's titles → .xlsx), `POST <table>/file?dry_run=1|0` (the edited .xlsx as base64
→ the preview, or the apply). The same gates: reading the table to download, editing it to load."""

import base64
import binascii
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import IntegrityError

from app.shared import bulk_import
from app.shared.bulk_import import MAX_IMPORT_ROWS, ImportRefused, ImportRequest
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, references_edit_principal, references_view_principal

from .api import references_uow_factory
from .application import UnitOfWork
from .domain import ReferenceValidationError
from .importers import IMPORT_TABLES, MAP_TABLES, import_hookups, import_rows, import_schema, unknown_table
from .roundtrip import MAP_ROUNDTRIP_TABLES, ROUNDTRIP_TABLES, RoundtripError, export_file, import_file, table_fields
from .xlsx_file import MAX_FILE_BYTES

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


def _buyer_check(table: str, principal: Principal) -> None:
    if table in (*MAP_TABLES, *MAP_ROUNDTRIP_TABLES) and not principal.service and not (
        principal.full_access or principal.permissions.get("procurement") == "edit"
    ):
        raise HTTPException(status_code=403, detail="Карты поставщиков и стран правит закупщик.")


@router.post("/{table}")
def import_table(
    table: str,
    body: Annotated[dict[str, Any], Body()],
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
    dry_run: Annotated[bool, Query()] = True,
    source: Annotated[Source, Query()] = "api",
    fill_empty: Annotated[bool, Query()] = False,
) -> dict:
    _known(table)
    if fill_empty and table != "hookups":
        raise HTTPException(status_code=422, detail="fill_empty — только для базы монтажных проводов (hookups)")
    _buyer_check(table, principal)
    try:
        if table == "hookups":
            bulk_import.RATE_LIMITER.check(principal.actor.id)
            return import_hookups(uow_factory(), body, principal.actor, now, dry_run=dry_run, source=source,
                                  fill_empty=fill_empty).as_dict()
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


# --- «Скачать для правки» / «Загрузить исправленную» (roundtrip.py) ---

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class FileExportPayload(BaseModel):
    titles: dict[str, str] = Field(default_factory=dict, max_length=1000)  # field → the title the screen shows
    kind: str = Field(default="", max_length=16)  # a list of the tab: БД.ПРОВ coax / wire / multi / other, БД.МАТ cov


class FileImportPayload(BaseModel):
    file_name: str = Field(default="", max_length=255)
    content_base64: str = Field(min_length=1, max_length=(MAX_FILE_BYTES * 4) // 3 + 8)


def _roundtrip(table: str) -> None:
    if table not in ROUNDTRIP_TABLES:
        raise HTTPException(status_code=404, detail=f"Нет таблицы «{table}» для правки файлом")


def _run_roundtrip(operation):
    try:
        return operation()
    except (RoundtripError, ReferenceValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="Справочник изменён конкурентно — загрузите файл ещё раз") from error


@router.get("/{table}/file/fields")
def file_fields(
    table: str,
    _principal: Annotated[Principal, Depends(references_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
    kind: Annotated[str, Query(max_length=16)] = "",
) -> dict:
    _roundtrip(table)
    return _run_roundtrip(lambda: table_fields(table, uow_factory, kind))


@router.post("/{table}/file/export")
def file_export(
    table: str,
    payload: FileExportPayload,
    _principal: Annotated[Principal, Depends(references_view_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
) -> dict:
    """The .xlsx as base64 in JSON (as the other files of ERP travel), with the name the screen saves it under."""
    _roundtrip(table)
    exported = _run_roundtrip(lambda: export_file(table, uow_factory, payload.titles, now, payload.kind))
    return {"file_name": f"{exported.label} — для правки {now:%d.%m.%Y}.xlsx", "content_type": XLSX_TYPE,
            "content_base64": base64.b64encode(exported.content).decode("ascii"), "rows": exported.rows}


@router.post("/{table}/file")
def file_import(
    table: str,
    payload: FileImportPayload,
    principal: Annotated[Principal, Depends(references_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(references_uow_factory)],
    dry_run: Annotated[bool, Query()] = True,
) -> dict:
    """The edited .xlsx: `dry_run=1` (default) — the preview, nothing written; `dry_run=0` — the apply in one
    transaction; a conflict, an unknown ID or a refused row is skipped and reported, the other rows apply."""
    _roundtrip(table)
    _buyer_check(table, principal)
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=422, detail="Файл повреждён") from error
    try:
        bulk_import.RATE_LIMITER.check(principal.actor.id)
    except ImportRefused as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    report = _run_roundtrip(lambda: import_file(table, uow_factory, content, principal.actor, now, dry_run=dry_run))
    return {**report.as_dict(), "file_name": payload.file_name}

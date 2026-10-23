"""«Печать этикеток ГРН» HTTP contract (Roller_Buyer `routes/labels.ts`), always on.

Reads open on view; settings, the test print and a lot's print take warehouse edit (or admin). The lot routes are
shared with the ТСД screen: `GET /api/stock/batches/{lot_id}/label.png` and `POST /api/stock/batches/{lot_id}/label`
`{copies: 1..50}` → `{status: "queued", reason, file, copies, job_id}` — the job waits in the print queue.

The print agent of the warehouse PC (owner 22.10.2026; the TSC MB341 is on USB there) calls with
`Authorization: Bearer <API token with the scope labels:print>`; a person's session is refused (403):
- `POST /api/stock/labels/agent/claim` `{printer, host}` → `{job: {id, copies, tspl_b64} | null}` — the job is the
  oldest pending one or a taken one whose lease ran out; it stays taken for 120 s. `copies` is informational: the
  TSPL already ends with `PRINT <copies>,1`, the bytes go to the printer once.
- `POST /api/stock/labels/agent/jobs/{id}/result` `{ok, error}` → `{ok: true}`; 404 — no such job, 409 — its result
  is already in (a retry after a lost answer: drop the job).
`GET /api/stock/labels/agent/status` (warehouse view) — the agent's last call and the queue, for «Оборудование».

The label designer (owner 23.10.2026), kind = «lot» | «intake», format = «58x30» | «58x40» | «100x50»:
- `GET /api/stock/labels/templates/{kind}` (view) → the fields of the kind and the template of every format;
- `PUT /api/stock/labels/templates/{kind}/{format}` (edit) `{template, version|null}` → saved; 409 — stale version;
- `DELETE /api/stock/labels/templates/{kind}/{format}` (edit) → back to the built-in template;
- `POST /api/stock/labels/templates/{kind}/{format}/preview` (edit — every POST of the warehouse takes edit)
  `{template, sample}` → image/png.
Every print and preview draws the template of its kind in the settings' current format (labels_application.py)."""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Security
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.shared.clock import moscow_now
from app.shared.http_auth import (
    TOKEN_SCOPE_PREFIX, Principal, authenticated_principal, warehouse_edit_principal, warehouse_view_principal,
)

from .api import warehouse_uow_factory
from .application import UnitOfWork
from .label_jobs import JobFinished, agent_status, claim, report
from .label_templates import StaleTemplate
from .labels.settings import CODE_TYPE_LABELS, PrintSettings
from .labels.template import FORMATS, TEMPLATE_KINDS, lot_content
from .labels_application import (
    COPIES_MAX,
    SAMPLE,
    find_lot,
    label_settings,
    lot_label_data,
    preview_png,
    preview_template_png,
    print_label,
    reset_label_template,
    save_label_settings,
    save_label_template,
    templates_view,
)

AGENT_SCOPE = "labels:print"
AGENT_ONLY = "Очередь печати забирает только агент печати склада (токен API «labels:print»)."
router = APIRouter(prefix="/api/stock", tags=["warehouse"])
UowFactory = Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)]


class PrintPayload(BaseModel):
    copies: int = Field(default=1, ge=1, le=COPIES_MAX)


class ClaimPayload(BaseModel):
    printer: str = Field(default="", max_length=255)
    host: str = Field(default="", max_length=255)


class ResultPayload(BaseModel):
    ok: bool
    error: str = ""


def agent_principal(
    principal: Annotated[Principal, Security(authenticated_principal, scopes=[TOKEN_SCOPE_PREFIX + AGENT_SCOPE])],
) -> Principal:
    """The print agent only: an API token with `labels:print` (a token without it is refused by the security scope),
    never a person's session — whatever its rights, a browser must not take jobs off the queue."""
    if not principal.service:
        raise HTTPException(status_code=403, detail=AGENT_ONLY)
    return principal


agent_principal.token_scopes = frozenset({AGENT_SCOPE})  # type: ignore[attr-defined]  (the token contract test reads it)


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
    # `print_dir` stays for the current screen: the jobs live in the print queue now, not in a folder.
    return {"settings": _settings_dict(label_settings(uow_factory())), "code_types": CODE_TYPE_LABELS,
            "print_dir": "очередь печати ERP", "copies_max": COPIES_MAX}


@router.put("/labels/settings")
def put_settings(
    payload: PrintSettings,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    try:
        saved = save_label_settings(uow_factory(), payload, principal.actor, now)
    except IntegrityError as error:
        raise HTTPException(status_code=422, detail="Настройки печати вне допустимых границ") from error
    return {"settings": _settings_dict(saved)}


@router.get("/labels/printers")
def printers(_principal: Annotated[Principal, Depends(warehouse_view_principal)], uow_factory: UowFactory) -> dict:
    """The printer the agent of the warehouse PC reported; `on_windows` — that agent has called (it runs on Windows)."""
    agent = agent_status(uow_factory())
    return {"printers": [agent["printer"]] if agent["printer"] else [], "on_windows": agent["last_seen"] is not None}


@router.get("/labels/sample.png")
def sample_png(_principal: Annotated[Principal, Depends(warehouse_view_principal)], uow_factory: UowFactory) -> Response:
    return _png(_rendered(lambda: preview_png(uow_factory(), lot_content(SAMPLE), label_settings(uow_factory()))))


@router.post("/labels/test")
def test_print(
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    return _rendered(lambda: print_label(
        uow_factory(), lot_content(SAMPLE), label_settings(uow_factory()), 1, now, kind="sample", lot_id=None,
        created_by=principal.actor.name,
    )).as_dict()


@router.get("/labels/lot")
def lot_by_key(
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    uow_factory: UowFactory,
    grn: Annotated[str, Query(min_length=1, max_length=32)],
    warehouse: Annotated[str, Query(max_length=8)] = "ЦАР",
    package: Annotated[str, Query(max_length=32)] = "",
) -> dict:
    """ГРН + ПАКЕТ → the lot, for «Печать этикеток» right after receiving. 404 while the lot is not on stock yet."""
    return {"lot_id": str(_rendered(lambda: find_lot(uow_factory(), warehouse, grn, package)))}


@router.get("/batches/{lot_id}/label.png")
def lot_label_png(
    lot_id: UUID,
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    uow_factory: UowFactory,
) -> Response:
    return _png(_rendered(lambda: preview_png(
        uow_factory(), lot_content(lot_label_data(uow_factory(), lot_id)), label_settings(uow_factory()))))


@router.post("/batches/{lot_id}/label")
def print_lot_label(
    lot_id: UUID,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
    payload: PrintPayload | None = None,
) -> dict:
    """`{copies}` → `{status: queued, reason, file, copies, job_id}`: the job waits for the agent of the warehouse PC."""
    copies = (payload or PrintPayload()).copies
    return _rendered(lambda: print_label(
        uow_factory(), lot_content(lot_label_data(uow_factory(), lot_id)), label_settings(uow_factory()), copies, now,
        kind="lot", lot_id=lot_id, created_by=principal.actor.name,
    )).as_dict()


# --- The label designer's templates (owner 23.10.2026) ------------------------------------------------------------


class TemplatePayload(BaseModel):
    template: Any = None  # checked by `parse_template`, so the 422 speaks Russian
    version: int | None = None


class PreviewPayload(BaseModel):
    template: Any = None
    sample: str | None = None  # a lot's id for kind «lot»; null — the sample label


def _kind_format(kind: str, label_format: str | None = None) -> None:
    if kind not in TEMPLATE_KINDS:
        raise HTTPException(status_code=404, detail=f"Нет такого вида этикетки: «{kind}» (есть «lot», «intake»)")
    if label_format is not None and label_format not in FORMATS:
        raise HTTPException(status_code=404, detail=f"Нет такого формата этикетки: «{label_format}» (есть {', '.join(FORMATS)})")


def _designed(operation):
    try:
        return operation()
    except KeyError as error:
        raise HTTPException(status_code=404, detail=error.args[0]) from error
    except StaleTemplate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:  # TemplateError, or a code does not fit its element
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/labels/templates/{kind}")
def get_templates(
    kind: str, _principal: Annotated[Principal, Depends(warehouse_view_principal)], uow_factory: UowFactory,
) -> dict:
    """`{kind, kind_label, fields: [{key, label}], formats: {"58x30"|"58x40"|"100x50": {template, is_default, version,
    width_mm, height_mm, updated_at, updated_by}}, current_format, code_type}`."""
    _kind_format(kind)
    return templates_view(uow_factory(), kind, label_settings(uow_factory()))


@router.put("/labels/templates/{kind}/{label_format}")
def put_template(
    kind: str,
    label_format: str,
    payload: TemplatePayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    """`{template, version}` (the version loaded, null — over the built-in one) → the saved `{template, is_default:
    false, version, …}`; 409 — saved or reset by someone since; 422 — not a valid template (Russian)."""
    _kind_format(kind, label_format)
    return _designed(lambda: save_label_template(
        uow_factory(), kind, label_format, payload.template, payload.version, label_settings(uow_factory()),
        principal.actor, now,
    ))


@router.delete("/labels/templates/{kind}/{label_format}")
def delete_template(
    kind: str,
    label_format: str,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    """Back to the built-in template → `{template, is_default: true, version: null, …}`."""
    _kind_format(kind, label_format)
    return reset_label_template(uow_factory(), kind, label_format, label_settings(uow_factory()), principal.actor, now)


@router.post("/labels/templates/{kind}/{label_format}/preview")
def preview_template(
    kind: str,
    label_format: str,
    payload: PreviewPayload,
    _principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    uow_factory: UowFactory,
) -> Response:
    """The unsaved template as image/png, dot for dot what would print (the format's size, the settings' dpi)."""
    _kind_format(kind, label_format)

    def content():
        if kind != "lot" or not payload.sample:
            return None  # the sample label of the kind
        try:
            lot_id = UUID(payload.sample)
        except ValueError as error:
            raise KeyError("Партия не найдена") from error
        return lot_content(lot_label_data(uow_factory(), lot_id))

    return _png(_designed(lambda: preview_template_png(
        payload.template, kind, label_format, label_settings(uow_factory()), content())))


@router.post("/labels/agent/claim")
def agent_claim(
    payload: ClaimPayload,
    _agent: Annotated[Principal, Depends(agent_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    job = claim(uow_factory(), printer=payload.printer, host=payload.host, now=now)
    if job is None:
        return {"job": None}
    return {"job": {"id": str(job.id), "copies": job.copies, "tspl_b64": base64.b64encode(job.tspl).decode("ascii")}}


@router.post("/labels/agent/jobs/{job_id}/result")
def agent_result(
    job_id: UUID,
    payload: ResultPayload,
    _agent: Annotated[Principal, Depends(agent_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    try:
        report(uow_factory(), job_id, ok=payload.ok, error=payload.error, now=now)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=error.args[0]) from error
    except JobFinished as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"ok": True}


@router.get("/labels/agent/status")
def agent_queue_status(_principal: Annotated[Principal, Depends(warehouse_view_principal)], uow_factory: UowFactory) -> dict:
    """`{last_seen, host, printer, queue, last_error, last_job: {status, finished_at, error} | null}`."""
    return agent_status(uow_factory())

"""What the harness editor reads besides the drawing: the ERP reference books (GET /api/techcards/harness-catalog,
H-T4) and the component library (GET /api/techcards/harness-components…, migration 0091).

The catalog is read-only; the library is also written — a new template, the next revision of one (POST, «Проекты:
правка»; a stored revision never changes). The catalog: БД.ТЕР, БД.ПРОВ, СПР.КАБ, БД.КОАКС, БД.МАТ и база разъёмов в форме снимков TECHMAP-GRAPHER
(`harness_catalog.build_harness_catalog`); при сохранении чертежа новые ссылки на эти справочники сверяются с ними же
(`reference_tables` → `harness_catalog.reference_book`). The library: connector templates by connector reference key
and revision (`harness.components`); a drawing pins them on its connectors (`harness_api`, …/components).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel, Field

from app.modules.references.application import UnitOfWork as ReferencesUnitOfWork
from app.modules.references.domain import TechOperationTab
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, projects_edit_principal, projects_view_principal
from app.shared.stored_files import decode_upload, file_response

from .api import optional_references_uow_factory, techcards_uow_factory
from .harness import application as harness
from .harness.components import ComponentError, ComponentTemplate, picture, summary_json, template_json
from .harness_catalog import TABS, build_harness_catalog

# The catalog and the component library are read with the right the drawing itself is read with: «Проекты: просмотр»
# (the editor opens from a position of a project). The routes' own contract gate: an API token never reads them.
router = APIRouter(prefix="/api/techcards", tags=["Редактор жгута"])

RECORD_LIMIT = 100_000


@router.get("/harness-catalog", dependencies=[Depends(projects_view_principal)])
def harness_catalog(
    references_factory: Annotated[
        Callable[[], ReferencesUnitOfWork] | None,
        Depends(optional_references_uow_factory),
    ],
) -> dict:
    """Каталог пикеров редактора: терминалы (с L+ / L−), провода и кабели, коаксиал и его разделка, защитные
    покрытия, семейства разъёмов — только из справочников ERP."""
    if references_factory is None:
        raise HTTPException(status_code=503, detail="Справочники ERP не подключены")
    metadata, tables = reference_tables(references_factory)
    stamp = metadata and (metadata["source_updated_at"] or metadata["imported_at"])
    return build_harness_catalog(tables, captured_utc=stamp.isoformat() if stamp else "")


def reference_tables(references_factory: Callable[[], ReferencesUnitOfWork]) -> tuple[dict | None, dict[str, list]]:
    """The reference tabs the harness catalog reads (tab → record fields) and the import metadata."""
    with references_factory() as uow:
        metadata = uow.tech_operations.metadata()
        tables = {
            tab: [record.fields for record in uow.tech_operations.list(tab=TechOperationTab(tab), query="",
                                                                       limit=RECORD_LIMIT)]
            for tab in TABS
        }
    return metadata, tables


Factory = Callable[[], Any]
Revision = Annotated[int, Path(ge=1, le=1_000_000)]


def _found(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except harness.HarnessNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/harness-components", dependencies=[Depends(projects_view_principal)])
def harness_components(factory: Annotated[Factory, Depends(techcards_uow_factory)]) -> list[dict]:
    """Библиотека компонентов: последняя версия каждого шаблона разъёма."""
    return [summary_json(template) for template in harness.library(factory)]


@router.get("/harness-components/{template_id}", dependencies=[Depends(projects_view_principal)])
def harness_component(template_id: UUID, factory: Annotated[Factory, Depends(techcards_uow_factory)]) -> dict:
    """Последняя версия шаблона целиком — для размещения на чертеже."""
    return template_json(_found(lambda: harness.library_template(factory, template_id)))


@router.get("/harness-components/{template_id}/versions/{revision}", dependencies=[Depends(projects_view_principal)])
def harness_component_revision(
    template_id: UUID, revision: Revision, factory: Annotated[Factory, Depends(techcards_uow_factory)],
) -> dict:
    """Версия шаблона целиком — та, что закреплена соединителем чертежа."""
    return template_json(_found(lambda: harness.library_template(factory, template_id, revision)))


@router.get("/harness-components/{template_id}/versions/{revision}/assets/{asset_id}",
            dependencies=[Depends(projects_view_principal)])
def harness_component_image(
    template_id: UUID, revision: Revision, asset_id: UUID,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
) -> Response:
    """Рисунок версии шаблона (PNG): версии неизменны, поэтому кэшируется надолго."""
    image = _found(lambda: harness.library_image(factory, template_id, revision, str(asset_id)))
    # The shared serving policy (audit F04): a real PNG opens inline, any other bytes are a download.
    response = file_response(image, f"{asset_id}.png")
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


# ── Publishing: create a template, publish its next revision (harness.application, after TECHMAP-GRAPHER
# ComponentTemplateEndpoints.cs © Василий, SaveOurSouls, with the author's permission) ─────────────────────────────
# Written with the right the drawing is edited with: «Проекты: правка».


class TemplatePayload(BaseModel):
    # The TECHMAP-GRAPHER component template model (schemaVersion 3–5); checked by the use case (Russian 422).
    model: Any = None
    # The picture of the revision, PNG in base64 (a `data:` URL prefix is allowed).
    png_base64: str | None = Field(default=None, max_length=14_000_000)
    # The connector code and the name; not given — the model's first article / the newest revision's.
    code: str | None = None
    name: str | None = None


class VersionPayload(TemplatePayload):
    # The revision the edit was made on: not the newest — 409 naming who published the newest.
    base_revision: int = Field(ge=1, le=1_000_000)


def _picture(payload: TemplatePayload) -> bytes | None:
    if payload.png_base64 is None or not payload.png_base64.strip():
        return None
    return decode_upload(payload.png_base64.split(",", 1)[-1].strip(), "Рисунок шаблона передан не в base64")


def _published(call: Callable[[], ComponentTemplate]) -> dict:
    """Refusals → HTTP: no such template 404, a stale or racing publish 409, a template that cannot be stored 422."""
    try:
        return template_json(call())
    except harness.HarnessNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except harness.HarnessConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ComponentError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/harness-components")
def create_harness_component(
    payload: TemplatePayload,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """Новый шаблон компонента — его версия 1. Код — переданный или первый артикул модели; такой шаблон уже есть —
    с тем же содержимым возвращается он, с другим — 409 (публикуйте его новую версию). Модель не шаблон v3–v5 — 422."""
    image = _picture(payload)
    return _published(lambda: harness.create_template(
        factory, payload.model, image=picture(image), actor=principal.actor, now=now, code=payload.code,
        name=payload.name,
    ))


@router.post("/harness-components/{template_id}/versions")
def publish_harness_component(
    template_id: UUID, payload: VersionPayload,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """Следующая версия шаблона (N+1), сделанная на версии `base_revision`: она не последняя — 409 «Шаблон уже изменил
    <кто> — версия N». То же содержимое, что у последней, — она и возвращается, новой версии нет. Чертежи, где шаблон
    уже размещён, остаются на своей версии: версии не меняются."""
    image = _picture(payload)
    return _published(lambda: harness.publish_version(
        factory, template_id, payload.base_revision, payload.model, image=picture(image), actor=principal.actor,
        now=now, code=payload.code, name=payload.name,
    ))

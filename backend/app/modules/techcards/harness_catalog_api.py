"""What the harness editor reads besides the drawing: the ERP reference books (GET /api/techcards/harness-catalog,
H-T4) and the component library (GET /api/techcards/harness-components…, migration 0091).

Read-only. The catalog: БД.ТЕР, БД.ПРОВ, СПР.КАБ, БД.КОАКС, БД.МАТ и база разъёмов в форме снимков TECHMAP-GRAPHER
(`harness_catalog.build_harness_catalog`); при сохранении чертежа новые ссылки на эти справочники сверяются с ними же
(`reference_tables` → `harness_catalog.reference_book`). The library: connector templates by connector reference key
and revision (`harness.components`); a drawing pins them on its connectors (`harness_api`, …/components).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Response

from app.modules.references.application import UnitOfWork as ReferencesUnitOfWork
from app.modules.references.domain import TechOperationTab
from app.shared.http_auth import projects_view_principal, techcards_view_principal

from .api import optional_references_uow_factory, techcards_uow_factory
from .harness import application as harness
from .harness.components import summary_json, template_json
from .harness_catalog import TABS, build_harness_catalog

# The component library is read with the right the drawing itself is read with: «Проекты: просмотр» (the editor opens
# from a position of a project). The gate is the routes' own contract gate, so an API token never reads them.
router = APIRouter(prefix="/api/techcards", tags=["Редактор жгута"])

RECORD_LIMIT = 100_000


@router.get("/harness-catalog", dependencies=[Depends(techcards_view_principal)])
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
    return Response(image, media_type="image/png", headers={"Cache-Control": "private, max-age=31536000, immutable"})

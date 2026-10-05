"""GET /api/techcards/harness-catalog — справочники ERP для редактора жгута (H-T4).

Read-only: БД.ТЕР, БД.ПРОВ, СПР.КАБ, БД.КОАКС, БД.МАТ и база разъёмов в форме снимков TECHMAP-GRAPHER
(`harness_catalog.build_harness_catalog`). Документ жгута хранится отдельно (`harness_api.py`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.modules.references.application import UnitOfWork as ReferencesUnitOfWork
from app.modules.references.domain import TechOperationTab
from app.shared.http_auth import techcards_view_principal

from .api import optional_references_uow_factory
from .harness_catalog import TABS, build_harness_catalog

# The editor belongs to «Техкарты проектов»: viewing that section is enough. The gate is the route's own (a contract
# gate of http_auth, no router-level deferral), so an API token never reads the catalog.
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
    with references_factory() as uow:
        metadata = uow.tech_operations.metadata()
        tables = {
            tab: [record.fields for record in uow.tech_operations.list(tab=TechOperationTab(tab), query="",
                                                                       limit=RECORD_LIMIT)]
            for tab in TABS
        }
    stamp = metadata and (metadata["source_updated_at"] or metadata["imported_at"])
    return build_harness_catalog(tables, captured_utc=stamp.isoformat() if stamp else "")

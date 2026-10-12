"""Fraxis ERP Next modular monolith entrypoint."""

from datetime import datetime
import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .database import configure_database
from .modules.operations.application import background_health
from .modules.techcards.kitset_names import tls_exception_hosts
from .shared.clock import moscow_now
from .shared.http_auth import Principal
from .modules.identity import api as identity_api
from .modules.identity.middleware import IdentitySessionMiddleware
from .shared.http_limits import RequestBodyLimitMiddleware
from .shared.security_headers import SecurityHeadersMiddleware
from .shared.stop_file import watch_stop_file
from .shared.warehouse_location import configured_rack_layout
from .modules.procurement import api as procurement_api
from .modules.receiving import api as receiving_api
from .modules.warehouse import api as warehouse_api
from .modules.fulfillment import api as fulfillment_api
from .modules.production import api as production_api
from .modules.production import plan_api as production_plan_api
from .modules.production import autoplan_api as production_autoplan_api
from .modules.shipping import api as shipping_api
from .modules.references import api as references_api
from .modules.operations import api as operations_api
from .modules.requests import api as requests_api
from .modules.techcards import api as techcards_api
from .modules.techcards import kitset_api as kitsets_api
from .modules.techcards import assembly_api
from .modules.techcards import harness_catalog_api
from .modules.techcards import harness_api
from .shared.bulk_import import MAX_IMPORT_BODY_BYTES
from .shared.http_auth import techcards_router_principal
from .workflows import employee_card_api, leave_api
from .workflows import documents_api

app = FastAPI(
    title="Fraxis ERP Next",
    description="Gradual replacement for the Google Apps Script ERP contour",
    version="0.1.0",
)
app.state.cookie_secure = os.getenv("ERP_COOKIE_SECURE", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
configured_rack_layout()  # a broken ERP_WAREHOUSE_RACK_LAYOUT stops the start, not the first placement
# Supplier domains read without a certificate check (audit U09): shown on «Системный журнал» and in /api/health?detail.
app.state.tls_exception_hosts = tls_exception_hosts()
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=1_048_576,
    path_limits={"/api/techcards/kitsets": 30 * 1_048_576, "/api/shipping/upd-files": 30 * 1_048_576,
                 "/api/procurement/invoice-files": 30 * 1_048_576, "/api/receiving/files": 30 * 1_048_576,
                 "/api/techcards/assembly/drawings": 30 * 1_048_576,
                 "/api/references/import": MAX_IMPORT_BODY_BYTES},
)
app.add_middleware(IdentitySessionMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(
    techcards_api.router,
    dependencies=[Depends(techcards_router_principal)],
)
app.include_router(harness_catalog_api.router)
app.include_router(identity_api.router)
app.include_router(requests_api.router)
app.include_router(procurement_api.router)
app.include_router(receiving_api.router)
app.include_router(warehouse_api.router)
app.include_router(fulfillment_api.router)
app.include_router(production_api.router)
app.include_router(production_plan_api.router)
app.include_router(production_autoplan_api.router)
app.include_router(kitsets_api.router)
app.include_router(assembly_api.router)
app.include_router(harness_api.router)
app.include_router(shipping_api.router)
app.include_router(references_api.router)
app.include_router(operations_api.router)
app.include_router(employee_card_api.router)
app.include_router(leave_api.router)
app.include_router(documents_api.router)

if database_url := os.getenv("ERP_DATABASE_URL"):
    configure_database(app, database_url)
watch_stop_file()  # локальная ERP на ПК: обновление останавливает API файлом-стопом (scripts/update-local.ps1)


def signed_in_principal(request: Request) -> Principal | None:
    """The caller if signed in, else None — /api/health itself stays open to the container health check."""
    principal = getattr(request.state, "principal", None)
    return principal if isinstance(principal, Principal) else None


@app.get("/api/health")
def health(
    request: Request,
    principal: Annotated[Principal | None, Depends(signed_in_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    db: int = 0,
    detail: int = 0,
):
    if detail:
        return _health_detail(request, principal, now)
    if not db:
        return {"ok": True, "service": "erp-next"}
    try:
        with app.state.database_engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 - наружу уходит только короткий текст, без стека и адреса БД
        return JSONResponse(status_code=503, content={"ok": False, "error": "База данных недоступна или не размечена миграциями"})
    return {"ok": True, "revision": revision}


def _health_detail(request: Request, principal: Principal | None, now: datetime):
    """Audit U08: does the background actually work — heartbeats of the worker's job groups and the age of the oldest
    pending message. Administrators only (it names the jobs and their last errors). 503 when any of it is red, so a
    monitor can poll this one address."""
    if principal is None:
        return JSONResponse(status_code=401, content={"detail": "Требуется вход в ERP"})
    if principal.service or not principal.full_access:
        return JSONResponse(status_code=403, content={"detail": "Только администратору."})
    factory = getattr(app.state, "operations_uow_factory", None)
    if not callable(factory):
        return JSONResponse(status_code=503, content={"ok": False, "error": "База ERP не настроена"})
    try:
        background, counts = background_health(now, factory())
    except Exception:  # noqa: BLE001 - наружу уходит только короткий текст, без стека и адреса БД
        return JSONResponse(status_code=503, content={"ok": False, "error": "База данных недоступна"})
    body = {
        **operations_api.background_payload(background, counts),
        "service": "erp-next",
        "tls_exceptions": operations_api.tls_exceptions(request),
    }
    return JSONResponse(status_code=200 if background.ok else 503, content=body)


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

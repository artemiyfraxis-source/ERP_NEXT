"""Fraxis ERP Next modular monolith entrypoint."""

import os
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from .database import configure_database
from .modules.identity import api as identity_api
from .modules.legacy_access import api as legacy_access_api
from .modules.identity.middleware import IdentitySessionMiddleware
from .shared.http_limits import RequestBodyLimitMiddleware
from .shared.security_headers import SecurityHeadersMiddleware
from .shared.warehouse_location import configured_rack_layout
from .modules.procurement import api as procurement_api
from .modules.receiving import api as receiving_api
from .modules.warehouse import api as warehouse_api
from .modules.fulfillment import api as fulfillment_api
from .modules.production import api as production_api
from .modules.production import plan_api as production_plan_api
from .modules.shipping import api as shipping_api
from .modules.references import api as references_api
from .modules.operations import api as operations_api
from .modules.requests import api as requests_api
from .modules.techcards import api as techcards_api
from .modules.techcards import kitset_api as kitsets_api
from .shared.http_auth import techcards_router_principal

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
app.state.legacy_bridge_secret = os.getenv("ERP_LEGACY_BRIDGE_SECRET", "")
configured_rack_layout()  # a broken ERP_WAREHOUSE_RACK_LAYOUT stops the start, not the first placement
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=1_048_576,
    path_limits={"/api/techcards/kitsets": 30 * 1_048_576},
)
app.add_middleware(IdentitySessionMiddleware)
app.add_middleware(
    SecurityHeadersMiddleware,
    legacy_origin=os.getenv("ERP_LEGACY_ORIGIN", "http://localhost:5177"),
)

app.include_router(
    techcards_api.router,
    dependencies=[Depends(techcards_router_principal)],
)
app.include_router(identity_api.router)
app.include_router(legacy_access_api.router)
app.include_router(requests_api.router)
app.include_router(procurement_api.router)
app.include_router(receiving_api.router)
app.include_router(warehouse_api.router)
app.include_router(fulfillment_api.router)
app.include_router(production_api.router)
app.include_router(production_plan_api.router)
app.include_router(kitsets_api.router)
app.include_router(shipping_api.router)
app.include_router(references_api.router)
app.include_router(operations_api.router)

if database_url := os.getenv("ERP_DATABASE_URL"):
    configure_database(app, database_url)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "erp-next"}


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

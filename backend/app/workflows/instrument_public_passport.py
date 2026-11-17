"""Read-only bearer-link passports, with bounded per-client protection before token or database work."""

import base64
from collections import OrderedDict
import hashlib
import re
from threading import Lock
import time
from uuid import UUID

from fastapi import APIRouter, Request, Response

from app.modules.production.infrastructure import SqlAlchemyInstrumentRepository
from app.modules.production.instrument_passport import public_passport_pdf
from app.modules.production.instrument_public import public_passport
from app.modules.production.instrument_public_html import PUBLIC_STYLE, public_passport_html
from app.modules.production.rf_infrastructure import RfRepository
from app.shared.clock import moscow_now
from app.shared.public_url import public_passport_url, public_settings, verify_passport_token
from .instrument_labels import _characterization

router = APIRouter(prefix="/p", tags=["public-passports"])
_STYLE_HASH = base64.b64encode(hashlib.sha256(PUBLIC_STYLE.encode()).digest()).decode()
PUBLIC_HEADERS = {
    "X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'sha256-" + _STYLE_HASH + "'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
}
_NOT_FOUND = '<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Паспорт недоступен</title></head><body><h1>Паспорт не найден</h1></body></html>'


class PublicPassportRateLimiter:
    """A fixed window per socket client, shared by HTML/PDF, with a hard memory ceiling.

    New clients are refused if all slots are occupied, rather than evicting active limits.
    Deployments with several worker processes have an independent allowance in each process.
    """
    def __init__(self, limit=30, window_seconds=60, max_clients=8192):
        self.limit, self.window_seconds, self.max_clients = limit, window_seconds, max_clients
        self._clients = OrderedDict()
        self._lock = Lock()

    def allow(self, client: str, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            while self._clients and next(iter(self._clients.values()))[0] + self.window_seconds <= now:
                self._clients.popitem(last=False)
            record = self._clients.get(client)
            if record is None:
                if len(self._clients) >= self.max_clients:
                    return False
                self._clients[client] = (now, 1)
                return True
            started, count = record
            if count >= self.limit:
                return False
            self._clients[client] = (started, count + 1)
            return True


PUBLIC_PASSPORT_LIMITER = PublicPassportRateLimiter()


def _missing() -> Response:
    return Response(_NOT_FOUND, status_code=404, media_type="text/html", headers=PUBLIC_HEADERS)


@router.get("/{locator:path}", include_in_schema=False)
def view_public_passport(locator: str, request: Request) -> Response:
    client = request.client.host if request.client else "unknown"
    if not PUBLIC_PASSPORT_LIMITER.allow(client):
        return Response("Слишком много запросов. Повторите через минуту.", status_code=429,
                        media_type="text/plain", headers={**PUBLIC_HEADERS, "Retry-After": "60"})
    match = re.fullmatch(r"([0-9a-f]{32})\.([A-Za-z0-9_-]{32})(\.pdf)?", locator)
    if match is None:
        return _missing()
    settings = public_settings()
    instrument_id = UUID(hex=match[1])
    if not verify_passport_token(instrument_id, match[2], settings.passport_secret):
        return _missing()
    factory = getattr(request.app.state, "production_uow_factory", None)
    if not callable(factory):
        return Response("Паспорт временно недоступен", status_code=503, media_type="text/plain", headers=PUBLIC_HEADERS)
    with factory().session_factory() as session:
        item = SqlAlchemyInstrumentRepository(session).get(instrument_id)
        if item is None:
            return _missing()
        projection = public_passport(item, RfRepository(session).get(item.id), _characterization(session, item.id))
    if match[3]:
        content = public_passport_pdf(projection, generated=moscow_now(), public_url=public_passport_url(instrument_id, settings))
        return Response(content, media_type="application/pdf",
                        headers={**PUBLIC_HEADERS, "Content-Disposition": 'attachment; filename="passport.pdf"'})
    return Response(public_passport_html(projection, pdf_path=f"/p/{match[1]}.{match[2]}.pdf"),
                    media_type="text/html", headers=PUBLIC_HEADERS)

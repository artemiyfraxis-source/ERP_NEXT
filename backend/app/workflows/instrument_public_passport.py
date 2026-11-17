"""Read-only bearer-link passports with link, failure and rendering admission limits."""

import base64
from contextlib import nullcontext
import hashlib
import re
from uuid import UUID

from fastapi import APIRouter, Request, Response

from app.modules.production.infrastructure import SqlAlchemyInstrumentRepository
from app.modules.production.instrument_passport import public_passport_pdf
from app.modules.production.instrument_public import public_passport
from app.modules.production.instrument_public_html import PUBLIC_STYLE, public_passport_html
from app.modules.production.rf_infrastructure import RfRepository
from app.shared.clock import moscow_now
from app.shared.public_passport_limits import PassportLimitsUnavailable, PublicPassportLimits
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


PUBLIC_PASSPORT_LIMITS = PublicPassportLimits()

def _limited() -> Response:
    return Response("Слишком много запросов. Повторите через минуту.", status_code=429,
                    media_type="text/plain", headers={**PUBLIC_HEADERS, "Retry-After": "60"})


def _unavailable() -> Response:
    return Response("Паспорт временно недоступен. Повторите запрос.", status_code=503,
                    media_type="text/plain", headers={**PUBLIC_HEADERS, "Retry-After": "1"})


def _missing() -> Response:
    if not PUBLIC_PASSPORT_LIMITS.allow_failure():
        return _limited()
    return Response(_NOT_FOUND, status_code=404, media_type="text/html", headers=PUBLIC_HEADERS)


@router.get("/{locator:path}", include_in_schema=False)
def view_public_passport(locator: str, request: Request) -> Response:
    try:
        return _view_public_passport(locator, request)
    except PassportLimitsUnavailable:
        return _unavailable()


def _view_public_passport(locator: str, request: Request) -> Response:
    match = re.fullmatch(r"([0-9a-f]{32})\.([A-Za-z0-9_-]{32})(\.pdf)?", locator)
    if match is None:
        return _missing()
    settings = public_settings()
    instrument_id = UUID(hex=match[1])
    # Failed callers never consume or block the allowance of a valid link.
    if not verify_passport_token(instrument_id, match[2], settings.passport_secret):
        return _missing()
    factory = getattr(request.app.state, "production_uow_factory", None)
    if not callable(factory):
        return _unavailable()
    with factory().session_factory() as session:
        item = SqlAlchemyInstrumentRepository(session).get(instrument_id)
        if item is None:
            return _missing()
        # A full PDF pool is a temporary server condition, not a successful view or bad link.
        with PUBLIC_PASSPORT_LIMITS.pdf_slot() if match[3] else nullcontext(True) as slot:
            if not slot:
                return _unavailable()
            with PUBLIC_PASSPORT_LIMITS.view(instrument_id, match[2]) as allowed:
                if not allowed:
                    return _limited()
                projection = public_passport(item, RfRepository(session).get(item.id), _characterization(session, item.id))
                if match[3]:
                    content = public_passport_pdf(projection, generated=moscow_now(),
                                                  public_url=public_passport_url(instrument_id, settings))
                    return Response(content, media_type="application/pdf",
                                    headers={**PUBLIC_HEADERS, "Content-Disposition": 'attachment; filename="passport.pdf"'})
                return Response(public_passport_html(projection, pdf_path=f"/p/{match[1]}.{match[2]}.pdf"),
                                media_type="text/html", headers=PUBLIC_HEADERS)

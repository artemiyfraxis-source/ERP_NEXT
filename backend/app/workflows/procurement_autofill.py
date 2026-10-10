"""Background auto-fill of a procurement line after «Взять в работу» — SUPPLYMAP Guards.gs `inWorkSideEffects_`,
Triggers.gs `fetchPageInfo_` and Schema.gs `pageBrand_`.

`take_procurement_in_work_batch` fills the supplier from the link's domain at once and queues the rest on the
outbox topic `procurement.autofill_requested` (same transaction as the take). This tick works the queue off the
request thread, because it goes to supplier sites (a take of 50 lines must not stall the button):

  1. claim the next live message (`next_autofill_request`) — a line changed since, or already complete, is closed;
  2. fetch the page behind the link OUTSIDE any transaction — a network error or a timeout is an empty answer;
  3. write the page title, the brand and the country by the brand into the line's EMPTY fields only
     (`apply_autofill`), and only if the line still has the version the take queued.

Network is behind the `PageInfoFetcher` port: tests substitute it, production uses `HttpPageInfoFetcher`, which is
the kitset-name fetcher (same httpx client: browser User-Agent, timeouts, 2 MB cap, no internal addresses, certificates checked) plus the
brand extraction. A tick never raises: a broken message is logged and retried with the outbox backoff.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
import re

from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.modules.procurement.application import (
    ApplyAutofillCommand,
    AutofillRequest,
    PageInfo,
    PageInfoFetcher,
    PoisonOutboxMessage,
    SupplierDirectory,
    apply_autofill,
    fail_autofill,
    next_autofill_request,
)
from app.modules.procurement.domain import is_web_link
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards.kitset_names import (
    HttpPageFetcher,
    decode_entities,
    fetch_page,
    page_title,
)
from app.shared.clock import moscow_now
from app.workflows.supplier_directory import ReferencesSupplierDirectory

# Lines worked off per tick. A site answers in a second or two, a dead one in the client's 15 s timeout; the tick
# runs on its own thread, so the number only bounds how long one thread run lasts before the next one starts.
AUTOFILL_TICK_CAP = 10


# ── brand extraction (Schema.gs pageBrand_ / cleanBrand_ / validBrand_) ──


def clean_brand(value: object) -> str:
    """`cleanBrand_`: entities decoded, spaces collapsed, no quotes or trailing punctuation, at most 60 characters."""
    text = re.sub(r"\s+", " ", decode_entities(value)).strip()
    text = re.sub(r"[\"«»]", "", text)
    text = re.sub(r"^[\s:：\-—|]+", "", text)
    text = re.sub(r"[.,;:]+$", "", text)
    return text[:60]


def valid_brand(value: str) -> bool:
    """`validBrand_`: looks like a brand name, not a scrap of markup or a sentence — a wrong ИЗГОТОВИТЕЛЬ is worse
    than an empty one (it is a traceability field)."""
    if not value or len(value) < 2 or len(value) > 40:
        return False
    if re.search(r"[<>\[\]{}|=]|href|http|ENTER|&[a-z#]", value, re.I):
        return False
    if not re.search(r"[A-Za-zА-Яа-яЁё]", value):
        return False
    return len(re.split(r"\s+", value)) <= 5


_BRAND_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r'"brand"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]{1,60})"',
        r'"brand"\s*:\s*"([^"]{1,60})"',
        r'"manufacturer"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]{1,60})"',
        r'"manufacturer"\s*:\s*"([^"]{1,60})"',
        r"<meta[^>]+(?:property|name|itemprop)\s*=\s*[\"'](?:product:brand|og:brand|brand)[\"'][^>]*\bcontent\s*=\s*[\"']([^\"']{1,60})[\"']",
        r"<meta[^>]+\bcontent\s*=\s*[\"']([^\"']{1,60})[\"'][^>]*(?:property|name|itemprop)\s*=\s*[\"'](?:product:brand|og:brand|brand)[\"']",
        r"itemprop\s*=\s*[\"']brand[\"'][^>]*\bcontent\s*=\s*[\"']([^\"']{1,60})[\"']",
        r"<[^>]+itemprop\s*=\s*[\"']brand[\"'][^>]*>\s*([^<]{1,60})<",
    )
)
_BRAND_LABEL = re.compile(
    r"(?:Производитель|Изготовитель|Бренд|Торговая марка)\s*:?\s*</(?:label|span|b|strong|th|td|div|p|dt)>"
    r"\s*(?:<[^>]+>\s*){0,4}([^<>{}]{2,50})<",
    re.I,
)


def page_brand(html: object) -> str:
    """`pageBrand_`: the manufacturer/brand from a product page — ONLY reliable sources (the text heuristic gave
    garbage): 1) schema.org "brand"/"manufacturer" in structured data; 2) product:brand / og:brand / itemprop=brand
    meta tags; 3) a labelled «Производитель/Бренд» cell of a card or table. Every candidate passes `valid_brand`.
    Nothing found or nothing valid — ''."""
    text = str(html or "")
    if not text:
        return ""
    for pattern in _BRAND_PATTERNS:
        match = pattern.search(text)
        if match:
            brand = clean_brand(match[1])
            if valid_brand(brand):
                return brand
    match = _BRAND_LABEL.search(text)
    if match:
        brand = clean_brand(match[1])
        if valid_brand(brand):
            return brand
    return ""


# ── the site (Triggers.gs fetchPageInfo_) ──


class HttpPageInfoFetcher(HttpPageFetcher):
    """`fetchPageInfo_`: ONE request gives both the title and the brand. The client, its timeouts, redirects,
    User-Agent, size cap and the refusal of internal addresses are the kitset-name fetcher's."""

    def info(self, url: str) -> PageInfo:
        page = fetch_page(self._client, url)
        if page is None:
            return PageInfo()
        return PageInfo(
            title=page_title(page.text), brand=page_brand(page.text), source=page.host, tls_checked=page.tls_checked,
        )


_default_pages: HttpPageInfoFetcher | None = None


def default_pages() -> HttpPageInfoFetcher:
    """One client for the worker's life (httpx clients are thread-safe and keep their connections)."""
    global _default_pages
    if _default_pages is None:
        _default_pages = HttpPageInfoFetcher()
    return _default_pages


# ── the tick ──


def _fill(
    request: AutofillRequest,
    pages: PageInfoFetcher,
    directory: SupplierDirectory,
    uow_factory: Callable[[], SqlAlchemyProcurementUnitOfWork],
    now: datetime,
) -> tuple[str, ...]:
    info = PageInfo()
    # Only what is still empty is worth a request: a typed name and manufacturer need no site.
    if is_web_link(request.link) and (not request.site_name or not request.manufacturer):
        try:
            info = pages.info(request.link)
        except Exception:  # noqa: BLE001 — the port promises an empty answer, a broken fake or client must not fail the tick
            logging.warning("procurement autofill: page fetch failed for %s", request.legacy_id, exc_info=True)
    # `countryForManufacturer_`: СТРАНА follows the manufacturer — the typed one, else the brand just found.
    manufacturer = request.manufacturer or info.brand
    country = ""
    if manufacturer and not request.country:
        try:
            country = directory.country_for_manufacturer(manufacturer)
        except Exception:  # noqa: BLE001 — the map is a hint
            logging.warning("procurement autofill: country map failed for %s", request.legacy_id, exc_info=True)
    return apply_autofill(
        ApplyAutofillCommand(
            request=request, site_name=info.title, manufacturer=info.brand, country=country, now=now,
            source=info.source if (info.title or info.brand) else "", tls_checked=info.tls_checked,
        ),
        uow_factory(),
    )


def procurement_autofill_tick(
    sessions: sessionmaker[Session],
    pages: PageInfoFetcher | None = None,
    directory: SupplierDirectory | None = None,
    *,
    cap: int = AUTOFILL_TICK_CAP,
    clock: Callable[[], datetime] = moscow_now,
) -> int:
    """Work off up to `cap` queued lines; returns how many got at least one field. Never raises."""
    directory = directory or ReferencesSupplierDirectory(lambda: SqlAlchemyReferencesUnitOfWork(sessions))
    uow_factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)  # noqa: E731
    filled = 0
    for _ in range(max(cap, 0)):
        try:
            request = next_autofill_request(clock(), uow_factory())
        except PoisonOutboxMessage as error:
            logging.warning("Quarantined invalid autofill message %s", error.message_id)
            continue
        except Exception:  # noqa: BLE001 — the queue itself is unreachable; the next tick tries again
            logging.exception("procurement autofill: cannot read the queue")
            break
        if request is None:
            break
        try:
            fields = _fill(request, pages or default_pages(), directory, uow_factory, clock())
        except Exception as error:  # noqa: BLE001 — a broken line must not stop the others
            if isinstance(error, StaleDataError):
                logging.info("procurement autofill: %s changed during the fill; retrying", request.legacy_id)
            else:
                logging.exception("procurement autofill: %s failed", request.legacy_id)
            try:
                fail_autofill(request.message_id, clock(), str(error) or type(error).__name__, uow_factory())
            except Exception:  # noqa: BLE001
                logging.exception("procurement autofill: cannot mark %s failed", request.legacy_id)
            continue
        if fields:
            filled += 1
    return filled

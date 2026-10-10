"""Наименования комплектующих набора с сайтов поставщиков — SUPPLYMAP TcKitset.gs `tcKitFetchNames`.

Имя берётся тем же путём, что в Закупке (`fetchPageInfo_` → `<title>` страницы по ССЫЛКЕ из расчёта), и
вписывается ТОЛЬКО в пустое наименование: правка человека сильнее сайта. Заголовок, равный артикулу, за имя
не считается — ссылка из расчёта часто ведёт на ПОИСК, и колонка заполнилась бы копией соседней.

Разбор страницы — чистые функции (`Schema.gs` pageTitle_/cleanTitle_/decodeEntities_/pageCharset_,
`Triggers.gs` pageText_, stripTrackingParams_). Сеть — за портом `PageFetcher`: в тестах подменяется,
в бою это `HttpPageFetcher` (httpx, с тайм-аутами).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import ipaddress
import os
import re
import socket
from typing import Protocol

import httpcore
import httpx

from .kitsets import Kitset


# Потолок ссылок за вызов (TC_KIT_FETCH_CAP_) и за один тик (TC_KIT_FETCH_TICK_CAP_).
FETCH_CAP = 30
FETCH_TICK_CAP = 15

# FETCH_OPTS_: браузерный User-Agent — часть сайтов отдаёт пустую/блок-страницу клиентам без UA.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_BODY_BYTES = 2 * 1024 * 1024

_LINK_RE = re.compile(r"^https?://", re.I)
_TRACKING_RE = re.compile(r"^(ysclid|yadiscount|gclid|fbclid|_openstat|from|ref|utm_[a-z]+)$", re.I)


def strip_tracking_params(url: object) -> str:
    """`stripTrackingParams_`: убрать ysclid/utm_*/gclid/… — «чистый» URL реже ловит антибот. #hash сохраняется."""
    text = "" if url is None else str(url)
    q = text.find("?")
    if q < 0:
        return text
    base, rest, fragment = text[:q], text[q + 1:], ""
    h = rest.find("#")
    if h >= 0:
        fragment, rest = rest[h:], rest[:h]
    kept = [part for part in rest.split("&") if part.split("=")[0] and not _TRACKING_RE.match(part.split("=")[0])]
    return base + ("?" + "&".join(kept) if kept else "") + fragment


def decode_entities(text: object) -> str:
    """`decodeEntities_`: частые HTML-сущности, в том же порядке замен."""
    s = str(text)
    for entity, char in (
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " "),
        ("&mdash;", "—"), ("&ndash;", "–"),
    ):
        s = s.replace(entity, char)
    return re.sub(r"&#(\d+);", lambda m: _char(int(m[1])), s)


def _char(code: int) -> str:
    try:
        return chr(code)
    except (ValueError, OverflowError):
        return ""


def clean_title(title: object) -> str:
    """`cleanTitle_`: схлопнуть пробелы, срезать ведущие эмодзи/символы и суффикс сайта после « | », « — » …"""
    t = re.sub(r"\s+", " ", str(title)).strip()
    t = re.sub(r"^[^0-9A-Za-zА-Яа-яЁё(]+", "", t)
    return re.split(r"\s[|–—•·»]\s", t)[0].strip()


def page_title(html: object) -> str:
    """`pageTitle_`: <title>…</title>, декодированный и очищенный; нет — ''."""
    match = re.search(r"<title[^>]*>([\s\S]*?)</title>", str(html), re.I)
    return clean_title(decode_entities(match[1])) if match else ""


def page_charset(content_type: str, head: str) -> str:
    """`pageCharset_`: charset из Content-Type, иначе из <meta>, иначе UTF-8."""
    match = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", content_type or "", re.I) or re.search(
        r"<meta[^>]*charset\s*=\s*[\"']?([\w.:-]+)", head or "", re.I
    )
    return match[1] if match else "UTF-8"


def page_text(body: bytes, content_type: str) -> str:
    """`pageText_`: текст в кодировке страницы — сайты на windows-1251 часто объявляют её только в <meta>."""
    head = body[:4096].decode("latin-1")
    try:
        return body.decode(page_charset(content_type, head), errors="replace")
    except LookupError:  # кодировка, которой Python не знает
        return body.decode("utf-8", errors="replace")


def name_looks_real(title: object, article: object) -> bool:
    """`tcKitNameLooksReal_`: заголовок, совпадающий с артикулом (без пробелов, «-», «_», «.»), — не имя."""
    t = str(title or "").strip()
    if not t:
        return False

    def norm(value: object) -> str:
        return re.sub(r"[\s\-_.]", "", str(value).lower())

    return norm(t) != norm(article)


class PageFetcher(Protocol):
    def title(self, url: str) -> str:
        """`fetchPageInfo_(url).title`: сетевая ошибка или код ≥ 400 → ''."""


# TLS exceptions (audit U09, 10.10.2026): certificates are verified for every supplier. A site with a broken certificate
# is read without the check only when its domain is listed here by hand — comma-separated, a domain covers its
# subdomains (`shop.example` → `www.shop.example`). The list is shown on «Системный журнал» and in /api/health?detail,
# and a field filled from such a site says so in the journal.
TLS_EXCEPTIONS_ENV = "ERP_TLS_UNVERIFIED_HOSTS"


def tls_exception_hosts(raw: str | None = None) -> tuple[str, ...]:
    """The configured domains read without a certificate check, lower-case, without dots at the ends."""
    text = os.getenv(TLS_EXCEPTIONS_ENV, "") if raw is None else raw
    hosts = {part.strip().strip(".").lower() for part in text.split(",")}
    return tuple(sorted(host for host in hosts if host))


def tls_unverified(host: str, exceptions: tuple[str, ...]) -> bool:
    name = (host or "").strip(".").lower()
    return any(name == entry or name.endswith("." + entry) for entry in exceptions)


def system_resolver(host: str, port: int) -> list[str]:
    """`socket.getaddrinfo`, looked up at call time (tests substitute it)."""
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _public_ip(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return None
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:  # ::ffff:10.0.0.1 is 10.0.0.1
        ip = mapped
    return ip if ip.is_global else None


def public_addresses(host: str, port: int, resolver=system_resolver) -> list[str]:
    """Resolve `host` and return its addresses only when EVERY one is public; otherwise refuse. The caller connects to
    exactly these addresses — the answer that was checked is the one used (no second lookup to rebind)."""
    try:
        addresses = list(dict.fromkeys(resolver(host, port)))
    except OSError as error:
        raise httpcore.ConnectError(f"host {host} does not resolve") from error
    if not addresses:
        raise httpcore.ConnectError(f"host {host} does not resolve")
    checked = [_public_ip(address) for address in addresses]
    if any(ip is None for ip in checked):
        raise httpcore.ConnectError(f"host {host} is not public")
    return [str(ip) for ip in checked]


class PinnedPublicBackend(httpcore.NetworkBackend):
    """httpcore network backend that resolves the host itself, refuses internal addresses and connects to the checked
    IP. TLS still runs against the host name (SNI and the certificate check use the URL's host, the Host header is the
    URL's), so a site behind a CDN works as before. Every new connection — the redirect hops included — goes through
    here, so a redirect to an internal host or a DNS answer that changed after a check never reaches the network."""

    def __init__(self, resolver=None, inner: httpcore.NetworkBackend | None = None):
        self._resolver = resolver or system_resolver
        self._inner = inner or httpcore.SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        last: Exception | None = None
        for address in public_addresses(host, port, self._resolver):
            try:
                return self._inner.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address, socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as error:
                last = error
        assert last is not None
        raise last

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("unix sockets are not fetched")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


TLS_UNVERIFIED_EXTENSION = "erp_tls_unverified"


class SupplierSiteTransport(httpx.BaseTransport):
    """The transport of the supplier-site client: pinned public addresses, certificates verified, and a separate pool
    without the check for the listed exception domains only. A response read without the check carries
    `extensions[TLS_UNVERIFIED_EXTENSION] = True`."""

    def __init__(
        self,
        *,
        resolver=None,
        backend: httpcore.NetworkBackend | None = None,
        exceptions: tuple[str, ...] | None = None,
    ):
        network = PinnedPublicBackend(resolver, backend)
        self.exceptions = tls_exception_hosts() if exceptions is None else exceptions
        self._verified = self._transport(httpx.create_ssl_context(verify=True), network)
        self._unverified = self._transport(httpx.create_ssl_context(verify=False), network)

    @staticmethod
    def _transport(ssl_context, network: httpcore.NetworkBackend) -> httpx.HTTPTransport:
        transport = httpx.HTTPTransport(trust_env=False)
        # httpx has no public knob for the network backend: its pool is rebuilt with ours (a test pins this).
        transport._pool = httpcore.ConnectionPool(
            ssl_context=ssl_context, network_backend=network, max_connections=20, max_keepalive_connections=5,
            keepalive_expiry=5.0,
        )
        return transport

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        unverified = request.url.scheme == "https" and tls_unverified(request.url.host, self.exceptions)
        response = (self._unverified if unverified else self._verified).handle_request(request)
        if unverified:
            response.extensions = {**response.extensions, TLS_UNVERIFIED_EXTENSION: True}
        return response

    def close(self) -> None:
        self._verified.close()
        self._unverified.close()


def supplier_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """The one client shape for supplier sites: redirects, browser UA, timeouts, no proxies from the environment."""
    return httpx.Client(
        transport=transport or SupplierSiteTransport(), timeout=TIMEOUT, follow_redirects=True, trust_env=False,
        headers={"User-Agent": USER_AGENT}, event_hooks={"request": [_refuse_private_host]},
    )


@dataclass(frozen=True)
class FetchedPage:
    """A page's text and where it really came from (the host after redirects, whether its certificate was checked)."""

    text: str
    host: str
    tls_checked: bool


def fetch_page(client: httpx.Client, url: str) -> FetchedPage | None:
    """GET the page (tracking parameters stripped), at most 2 MB; a network error or a code >= 400 is None."""
    try:
        with client.stream("GET", strip_tracking_params(url)) as response:
            if response.status_code >= 400:
                return None
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) >= MAX_BODY_BYTES:
                    break
            return FetchedPage(
                text=page_text(bytes(body), response.headers.get("content-type", "")),
                host=response.url.host,
                tls_checked=not response.extensions.get(TLS_UNVERIFIED_EXTENSION, False),
            )
    except Exception:  # noqa: BLE001 — любая сетевая ошибка = «сайт не отдал название»
        return None


class HttpPageFetcher:
    """httpx-адаптер `fetchPageInfo_`: редиректы, браузерный UA, тайм-ауты, тело не больше 2 МБ.

    Сертификаты проверяются (аудит U09); без проверки читаются только домены из ERP_TLS_UNVERIFIED_HOSTS. В отличие
    от UrlFetchApp запрос идёт из сети ERP, поэтому адреса внутренней сети (localhost, 10/8, 169.254/16 …) не
    запрашиваются ни напрямую, ни через редирект, ни через DNS-ответ, сменившийся после проверки: соединение идёт на
    проверенный IP (`PinnedPublicBackend`).
    """

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or supplier_client()

    def title(self, url: str) -> str:
        page = fetch_page(self._client, url)
        return page_title(page.text) if page is not None else ""


class PrivateHostRefused(httpx.RequestError):
    pass


def _refuse_private_host(request: httpx.Request) -> None:
    """Early refusal before any connection (an internal literal, a name that resolves inside). The transport checks
    again at connect time, on the address it really connects to."""
    host = request.url.host
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError as error:
        raise PrivateHostRefused(f"host {host} does not resolve", request=request) from error
    for address in addresses:
        if _public_ip(address) is None:
            raise PrivateHostRefused(f"host {host} is not public", request=request)


@dataclass(frozen=True)
class NameFill:
    index: str
    position: int
    article: str
    link: str
    name: str


@dataclass
class FetchNamesResult:
    project: str
    index: str = ""
    filled: int = 0
    tried: int = 0
    left: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)
    fills: list[NameFill] = field(default_factory=list)


def fetch_names(kitset: Kitset, fetcher: PageFetcher, *, index: str = "", cap: int = 0) -> FetchNamesResult:
    """`tcKitFetchNames(project, index, cap)`: пройти строки набора и найти имена для пустых наименований.

    Сеть — здесь; запись — `apply_names`, отдельно, чтобы долгий обход сайтов не держал транзакцию.
    """
    wanted = str(index or "").strip().upper()
    limit = cap if cap > 0 else FETCH_CAP
    result = FetchNamesResult(project=kitset.project, index=wanted)
    for assembly in kitset.assemblies:
        if wanted and assembly.index.upper() != wanted:
            continue
        for position, component in enumerate(assembly.components):
            article = component.article.strip()
            if not article:
                continue
            if component.name.strip():  # уже заполнено — не трогаем
                continue
            link = component.link.strip()
            if not _LINK_RE.match(link):
                result.skipped.append({"art": article, "why": "нет ссылки"})
                continue
            if result.tried >= limit:
                result.left += 1
                continue
            result.tried += 1
            title = fetcher.title(link)
            if not title:
                result.skipped.append({"art": article, "why": "сайт не отдал название"})
                continue
            if not name_looks_real(title, article):
                result.skipped.append({"art": article, "why": "на странице только артикул (ссылка на поиск)"})
                continue
            result.fills.append(NameFill(
                index=assembly.index, position=position, article=component.article, link=component.link, name=title,
            ))
            result.filled += 1
    return result


def apply_names(kitset: Kitset, fills: list[NameFill]) -> tuple[Kitset, int]:
    """Вписать найденные имена в свежую карточку — только туда, где строка та же и наименование всё ещё пусто:
    пока фон ходил по сайтам, человек мог вписать имя сам или поменять ссылку. Кто/когда правил — не меняется:
    это не правка человека (в SUPPLYMAP — setValue в ячейку)."""
    by_row = {(fill.index, fill.position): fill for fill in fills}
    written = 0
    assemblies = []
    for assembly in kitset.assemblies:
        components = []
        for position, component in enumerate(assembly.components):
            fill = by_row.get((assembly.index, position))
            if (
                fill is not None and not component.name.strip()
                and component.article == fill.article and component.link == fill.link
            ):
                component = replace(component, name=fill.name)
                written += 1
            components.append(component)
        assemblies.append(replace(assembly, components=tuple(components)))
    if not written:
        return kitset, 0
    return replace(kitset, assemblies=tuple(assemblies), version=kitset.version + 1), written

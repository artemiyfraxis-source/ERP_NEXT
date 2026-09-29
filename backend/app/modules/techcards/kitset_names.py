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
import re
import socket
from typing import Protocol

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


class HttpPageFetcher:
    """httpx-адаптер `fetchPageInfo_`: редиректы, браузерный UA, тайм-ауты, тело не больше 2 МБ.

    Сертификаты не проверяются, как в SUPPLYMAP (validateHttpsCertificates: false) — у части поставщиков они
    кривые. В отличие от UrlFetchApp запрос идёт из сети ERP, поэтому адреса внутренней сети (localhost,
    10/8, 169.254/16 …) не запрашиваются ни напрямую, ни через редирект.
    """

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(
            timeout=TIMEOUT, follow_redirects=True, verify=False,  # noqa: S501 — как FETCH_OPTS_
            headers={"User-Agent": USER_AGENT}, event_hooks={"request": [_refuse_private_host]},
        )

    def title(self, url: str) -> str:
        try:
            with self._client.stream("GET", strip_tracking_params(url)) as response:
                if response.status_code >= 400:
                    return ""
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) >= MAX_BODY_BYTES:
                        break
                return page_title(page_text(bytes(body), response.headers.get("content-type", "")))
        except Exception:  # noqa: BLE001 — любая сетевая ошибка = «сайт не отдал название»
            return ""


class PrivateHostRefused(httpx.RequestError):
    pass


def _refuse_private_host(request: httpx.Request) -> None:
    host = request.url.host
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError as error:
        raise PrivateHostRefused(f"host {host} does not resolve", request=request) from error
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
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

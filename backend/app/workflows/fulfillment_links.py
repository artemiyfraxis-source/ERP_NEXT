"""Read-only current component references for existing fulfillment requests, including old ones."""

from collections.abc import Callable, Sequence
from urllib.parse import urlsplit
from uuid import UUID

from app.modules.fulfillment.application import kit_key
from app.modules.fulfillment.domain import FulfillmentRequest, item_index_key
from app.modules.techcards.application import UnitOfWork
from app.modules.techcards.kitsets import project_key


def _safe_link(value: str) -> str:
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        return ""
    link = value.strip()
    if "\\" in link or any(char.isspace() for char in link):
        return ""
    try:
        parsed = urlsplit(link)
        if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            return ""
        parsed.port  # Validate malformed ports before exposing a browser link.
    except ValueError:
        return ""
    return link


def links_for_requests(
    requests: Sequence[FulfillmentRequest], factory: Callable[[], UnitOfWork] | None,
) -> dict[UUID, list[str]]:
    """Match project + КБ + exact trimmed article/name; no inferred procurement or historical snapshot."""
    keyed = [(request, kit_key(request.purpose)) for request in requests]
    projects = sorted({key[0] for _, key in keyed if key is not None})
    if not factory or not projects:
        return {}
    with factory() as uow:
        kitsets = uow.kitsets.for_projects(projects)
    source: dict[tuple[str, str, str, str], list[str]] = {}
    for kitset in kitsets.values():
        for assembly in kitset.assemblies:
            for component in assembly.components:
                link = _safe_link(component.link)
                if not link:
                    continue
                article = component.article.strip()
                key = (project_key(kitset.project), item_index_key(assembly.index), article,
                       component.name.strip() or article)
                links = source.setdefault(key, [])
                if link not in links:
                    links.append(link)
    return {
        line.id: list(source[(*key, line.article.strip(), line.name.strip())])
        for request, key in keyed if key is not None
        for line in request.lines if (*key, line.article.strip(), line.name.strip()) in source
    }

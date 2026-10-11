"""The component library of the harness drawing (pure): connector templates the editor places, one row per published
revision (`harness_component_templates`, migration 0091).

A template is keyed to its connector reference key (`connector_ref`, the connector's code — «SHR-04V-S», «DS1069»)
and numbered (`revision` 1, 2, …). Its `model` is the TECHMAP-GRAPHER component-template content (schemaVersion 3–5:
views, contacts, article variants) the editor materializes; `snapshot_png` — the picture its model draws (an image
node of the model names it by asset id).

A placement pins template + revision: the connector placed on a drawing carries `libraryBinding` with `templateId`,
`templateVersion` and `versionSha256`, and that IS the pin — the drawing keeps no copy of the template. The graph the
editor reads (`placement_graph`) is rebuilt from the drawing's connectors and the library on every read.

Drawings saved before 0091 carry the snapshots inside the document (`componentSnapshots` / `componentPlacements`,
TECHMAP-GRAPHER's export shape). The migration copied them into the library deduplicated by content; a write moves
any it still meets (`embedded_templates`) and stores the drawing without them; the reader resolves a pin from the
library first and from such an embedded copy second, so an old drawing still opens.

TECHMAP-GRAPHER © Василий (SaveOurSouls), template model and placement contract used with the author's permission.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Callable, Iterable, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from .schema import COMPONENT_GRAPH_KEYS, COMPONENT_PLACEMENTS, COMPONENT_SNAPSHOTS

TEMPLATE_SCHEMAS = (3, 4, 5)
MAX_REF = 200
_SHA = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE)


class ComponentError(ValueError):
    """A template that cannot be stored or placed; the message is for the technologist."""


@dataclass(frozen=True)
class ComponentTemplate:
    """One published revision of a library template. `version_sha256` is what a placed connector pins with the
    revision; `content_sha256` — the whole revision (identity, model and picture), the dedupe key."""
    id: UUID
    template_id: UUID
    connector_ref: str
    revision: int
    version_sha256: str
    content_sha256: str
    code: str
    name: str
    article_bindings: list[dict[str, Any]]
    assets: list[dict[str, Any]]
    model: dict[str, Any]
    created_by: str
    created_at: datetime
    image_sha256: str | None = None


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def connector_ref(code: object) -> str:
    """The reference key of a connector: its code, trimmed, spaces collapsed, upper case."""
    text = re.sub(r"\s+", " ", str(code or "")).strip().upper()
    if not text:
        raise ComponentError("У шаблона компонента нет кода разъёма")
    return text[:MAX_REF]


def template_identity(ref: str) -> UUID:
    """The stable template id of an ERP template (all its revisions): derived from the connector reference key."""
    return uuid5(NAMESPACE_URL, f"fraxis-erp:harness-component:{ref}")


def version_sha256(*, code: str, name: str, article_bindings: list, assets: list, model: Mapping[str, Any]) -> str:
    """The hash of a revision as the editor pins it (an ERP template; an imported one keeps its author's hash)."""
    return hashlib.sha256(_canonical({
        "code": code, "name": name, "articleBindings": article_bindings, "assets": assets, "content": model,
    })).hexdigest()


def content_sha256(*, template_id: UUID | str, revision: int, version: str, code: str, name: str,
                   article_bindings: list, assets: list, model: Mapping[str, Any]) -> str:
    """The dedupe key of a revision: the same template revision embedded in many drawings is one library row."""
    return hashlib.sha256(_canonical({
        "templateId": str(template_id), "revision": revision, "versionSha256": version, "code": code, "name": name,
        "articleBindings": article_bindings, "assets": assets, "content": model,
    })).hexdigest()


def _rows(value: object) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _positive(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


@dataclass(frozen=True)
class Candidate:
    """A template revision to put into the library (from an embedded snapshot or the seed)."""
    template_id: UUID
    revision: int
    version_sha256: str
    code: str
    name: str
    article_bindings: list[dict[str, Any]]
    assets: list[dict[str, Any]]
    model: dict[str, Any]

    @property
    def content_sha256(self) -> str:
        return content_sha256(template_id=self.template_id, revision=self.revision, version=self.version_sha256,
                              code=self.code, name=self.name, article_bindings=self.article_bindings,
                              assets=self.assets, model=self.model)

    @property
    def connector_ref(self) -> str:
        return connector_ref(self.code)


def candidate_from_snapshot(snapshot: Mapping[str, Any]) -> Candidate:
    """A TECHMAP-GRAPHER project component snapshot (`componentSnapshots` row) as a library revision; ComponentError
    names what is missing. The snapshot's own id, project and times are not part of the template."""
    template_id = _text(snapshot.get("sourceTemplateId"))
    if not _UUID.match(template_id):
        raise ComponentError("у снимка компонента нет id шаблона")
    revision = _positive(snapshot.get("sourceVersion"))
    if revision is None:
        raise ComponentError("у снимка компонента нет номера версии")
    version = _text(snapshot.get("sourceVersionSha256")).lower()
    if not _SHA.match(version):
        raise ComponentError("у снимка компонента нет хэша версии")
    model = snapshot.get("content")
    if not isinstance(model, Mapping) or model.get("schemaVersion") not in TEMPLATE_SCHEMAS:
        raise ComponentError("содержимое снимка компонента — не шаблон v3–v5")
    code, name = _text(snapshot.get("code")), _text(snapshot.get("name"))
    if not code or not name:
        raise ComponentError("у снимка компонента нет кода или названия")
    return Candidate(
        template_id=UUID(template_id), revision=revision, version_sha256=version, code=code, name=name,
        article_bindings=_rows(snapshot.get("articleBindings")), assets=_rows(snapshot.get("assets")),
        model=json.loads(json.dumps(model)),
    )


# ── Publishing from the ERP (create a template, publish its next revision) ──────────────────────────────────────────
# Ported from TECHMAP-GRAPHER `ComponentTemplateEndpoints.cs` / `SqliteComponentTemplateStore.cs` (create, update with
# the expected version, the picture of a version carried into the next one), © Василий (SaveOurSouls), used with the
# author's permission. Here a revision is an immutable library row: publishing never changes a stored one.

MAX_CODE, MAX_NAME = 120, 256
MAX_PICTURE = 10 * 1024 * 1024  # as TECHMAP-GRAPHER's MaximumAssetBytes
PNG_MEDIA = "image/png"
_PNG = b"\x89PNG\r\n\x1a\n"


def template_model(model: object) -> dict[str, Any]:
    """The model of a template to publish (a deep copy), or ComponentError: only a TECHMAP-GRAPHER component template
    of schemaVersion 3–5 — what the library reads and the editor places."""
    if not isinstance(model, Mapping):
        raise ComponentError("Модель компонента не передана — нужен шаблон TECHMAP-GRAPHER (версия схемы 3–5)")
    version = model.get("schemaVersion")
    if type(version) is not int or version not in TEMPLATE_SCHEMAS:
        shown = "не указана" if version is None else json.dumps(version, ensure_ascii=False)
        raise ComponentError(f"Модель компонента не поддерживается: нужен шаблон TECHMAP-GRAPHER версии схемы 3–5, "
                             f"а у этой версия схемы {shown}")
    return json.loads(json.dumps(model, ensure_ascii=False))


def model_bindings(model: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The articles a template binds (its `articleVariants`, each article once) — the catalog's `articleBindings`."""
    bindings: list[dict[str, Any]] = []
    for variant in _rows(model.get("articleVariants")):
        binding = {key: _text(variant.get(key)) for key in ("sourceId", "entityType", "articleKey")}
        if all(binding.values()) and binding not in bindings:
            bindings.append(binding)
    return bindings


def model_code(model: Mapping[str, Any]) -> str:
    """The connector code a model names when the request gives none: its first article."""
    bindings = model_bindings(model)
    return bindings[0]["articleKey"] if bindings else ""


def picture(content: bytes | None) -> bytes | None:
    """The picture of a revision as uploaded: a PNG of at most 10 MB, or ComponentError."""
    if content is None:
        return None
    if not content.startswith(_PNG):
        raise ComponentError("Рисунок шаблона — не PNG")
    if len(content) > MAX_PICTURE:
        raise ComponentError("Рисунок шаблона больше 10 МБ")
    return content


def publishable(model: Mapping[str, Any], *, template_id: UUID, revision: int, code: str, name: str,
                image: bytes | None) -> Candidate:
    """A revision to publish from an ERP request: the model (`template_model`), its code and name, the articles and
    pictures the model itself lists. The picture is the model's PNG asset of the same sha256 — or, when the model has
    exactly one PNG asset, that asset takes the uploaded picture's hash and size (the server hashes the bytes)."""
    code, name = _text(code), _text(name)
    if not code:
        raise ComponentError("У шаблона компонента нет кода разъёма: в модели нет артикула, код не передан")
    if len(code) > MAX_CODE:
        raise ComponentError(f"Код шаблона длиннее {MAX_CODE} символов")
    if len(name) > MAX_NAME:
        raise ComponentError(f"Название шаблона длиннее {MAX_NAME} символов")
    assets = _rows(model.get("assets"))
    if image is not None:
        digest = hashlib.sha256(image).hexdigest()
        pngs = [asset for asset in assets if asset.get("mediaType") == PNG_MEDIA]
        named = [asset for asset in pngs if _text(asset.get("sha256")).lower() == digest]
        if not named and len(pngs) != 1:
            raise ComponentError("Рисунок не к чему привязать: в модели должно быть одно изображение PNG "
                                 "(или изображение с тем же sha256)")
        for asset in named or pngs:
            asset.update(sha256=digest, sizeBytes=len(image))
    model = {**model, "assets": assets} if "assets" in model else dict(model)
    name = name or code
    bindings = model_bindings(model)
    return Candidate(
        template_id=template_id, revision=revision, code=code, name=name, article_bindings=bindings, assets=assets,
        model=model, version_sha256=version_sha256(code=code, name=name, article_bindings=bindings, assets=assets,
                                                   model=model),
    )


def image_of(template: ComponentTemplate, assets: Iterable[Mapping[str, Any]]) -> bool:
    """The picture of `template` is one the assets name (a next revision keeps it without an upload)."""
    return template.image_sha256 is not None and any(
        _text(asset.get("sha256")).lower() == template.image_sha256 for asset in assets)


def split_embedded(design: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The drawing without the pre-0091 component sections, and the snapshots it carried."""
    return ({key: value for key, value in design.items() if key not in COMPONENT_GRAPH_KEYS},
            _rows(design.get(COMPONENT_SNAPSHOTS)))


def embedded_templates(snapshots: Iterable[Mapping[str, Any]]) -> tuple[list[Candidate], list[str]]:
    """The embedded snapshots a write moves into the library, and why the others were left out."""
    found: dict[str, Candidate] = {}
    skipped: list[str] = []
    for snapshot in snapshots:
        try:
            candidate = candidate_from_snapshot(snapshot)
        except ComponentError as error:
            label = _text(snapshot.get("code")) or _text(snapshot.get("snapshotId")) or "без кода"
            skipped.append(f"Снимок компонента «{label}» не перенесён в библиотеку: {error}")
            continue
        found.setdefault(candidate.content_sha256, candidate)
    return list(found.values()), skipped


Pin = tuple[str, int, str]  # (template id, revision, version sha256) — what a placed connector holds


def library_pins(design: Mapping[str, Any]) -> dict[Pin, str]:
    """Pins of the library connectors of a drawing → the connector that holds it first («Соединитель XS1»)."""
    pins: dict[Pin, str] = {}
    for connector in _rows(design.get("connectors")):
        binding = connector.get("libraryBinding")
        if not isinstance(binding, Mapping) or binding.get("mode") != "template":
            continue
        revision = _positive(binding.get("templateVersion"))
        pin = (_text(binding.get("templateId")).lower(), revision or 0, _text(binding.get("versionSha256")).lower())
        label = _text(connector.get("designation")) or _text(connector.get("id"))
        pins.setdefault(pin, f"Соединитель {label}")
    return pins


def embedded_pins(snapshots: Iterable[Mapping[str, Any]]) -> dict[Pin, dict[str, Any]]:
    return {(_text(row.get("sourceTemplateId")).lower(), _positive(row.get("sourceVersion")) or 0,
             _text(row.get("sourceVersionSha256")).lower()): dict(row) for row in snapshots}


def asset_url(template_id: UUID | str, revision: int, asset_id: str) -> str:
    return f"/api/techcards/harness-components/{template_id}/versions/{revision}/assets/{asset_id}"


def _utc(value: datetime) -> str:
    return value.isoformat()


def summary_json(template: ComponentTemplate) -> dict[str, Any]:
    """A library row as the editor's catalog lists it (the latest revision of a template)."""
    return {
        "templateId": str(template.template_id), "version": template.revision, "code": template.code,
        "name": template.name, "connectorRef": template.connector_ref, "articleBindings": template.article_bindings,
        "createdUtc": _utc(template.created_at), "updatedUtc": _utc(template.created_at),
        "hasImage": template.image_sha256 is not None,
    }


def template_json(template: ComponentTemplate) -> dict[str, Any]:
    """One revision in full, as the editor places it."""
    return {**summary_json(template), "versionSha256": template.version_sha256, "assets": template.assets,
            "content": template.model}


def snapshot_json(template: ComponentTemplate, project: str) -> dict[str, Any]:
    """A library revision in the editor's «pinned snapshot» shape: the snapshot id is the library row id."""
    return {
        "snapshotId": str(template.id), "projectId": project, "sourceTemplateId": str(template.template_id),
        "sourceVersion": template.revision, "sourceVersionSha256": template.version_sha256, "code": template.code,
        "name": template.name, "articleBindings": template.article_bindings, "assets": template.assets,
        "schemaVersion": template.model.get("schemaVersion"), "content": template.model,
        "createdUtc": _utc(template.created_at), "updatedUtc": _utc(template.created_at),
    }


Resolver = Callable[[Pin], ComponentTemplate | None]


def placement_graph(design: Mapping[str, Any] | None, resolve: Resolver, *, project: str, harness: str,
                    updated: datetime | None) -> dict[str, Any]:
    """The library placements of a drawing and the revisions they pin, for the editor (`placements`, `snapshots`;
    every snapshot used, ids unique). A pin is resolved in the library, else in a pre-0091 embedded copy; a pin
    resolved nowhere (or a connector id the editor cannot place) is listed in `unresolved` — the editor draws that
    connector from its own copy of the contacts."""
    if not design:
        return {"placements": [], "snapshots": [], "unresolved": []}
    embedded = embedded_pins(_rows(design.get(COMPONENT_SNAPSHOTS)))
    stamp = _utc(updated) if updated else ""
    placements: list[dict[str, Any]] = []
    snapshots: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    for connector in _rows(design.get("connectors")):
        binding = connector.get("libraryBinding")
        if not isinstance(binding, Mapping) or binding.get("mode") != "template":
            continue
        pin = (_text(binding.get("templateId")).lower(), _positive(binding.get("templateVersion")) or 0,
               _text(binding.get("versionSha256")).lower())
        connector_id = _text(connector.get("id"))
        label = _text(connector.get("designation")) or connector_id
        found = resolve(pin)
        if found is not None:
            snapshot = snapshot_json(found, project)
        elif pin in embedded and _UUID.match(_text(embedded[pin].get("snapshotId"))):
            snapshot = {**embedded[pin], "projectId": project}
        else:
            unresolved.append(label)
            continue
        if not _UUID.match(connector_id):
            unresolved.append(label)
            continue
        snapshots.setdefault(snapshot["snapshotId"], snapshot)
        article = binding.get("article") if isinstance(binding.get("article"), Mapping) else {}
        placements.append({
            "placementId": connector_id, "harnessId": harness, "snapshotId": snapshot["snapshotId"],
            "sourceId": _text(article.get("sourceId")), "entityType": _text(article.get("entityType")),
            "articleKey": _text(article.get("articleKey")), "instance": connector,
            "createdUtc": snapshot.get("createdUtc") or stamp, "updatedUtc": stamp or snapshot.get("updatedUtc") or "",
        })
    return {"placements": placements, "snapshots": list(snapshots.values()), "unresolved": unresolved}


def export_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    """The graph as TECHMAP-GRAPHER keeps it in its export (project-level sections, the editor shapes)."""
    return {COMPONENT_SNAPSHOTS: list(graph.get("snapshots") or []),
            COMPONENT_PLACEMENTS: list(graph.get("placements") or [])}

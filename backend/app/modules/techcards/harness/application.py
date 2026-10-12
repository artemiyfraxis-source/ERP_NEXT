"""Use cases of the harness drawing of a position (application layer): read, save (also import and restore of an
earlier revision), history, «Отвязать» / «Вернуть». Each runs in one techcards unit of work and commits it.

A save:
1. refuses what must not be stored (`validate.validate_design`, Russian messages → HarnessInvalid);
2. checks the revision it was made on against the stored one (HarnessConflict, 409 in the API);
3. looks up in the ERP books only the references this write brings in (a book edited later never locks an existing
   drawing): an editor save is refused on an unknown one, an import or a restore only warns;
4. moves the library templates a pre-0091 document still carries into the component library and stores the drawing
   without them; a library connector the write brings in must pin a revision the library has (`components`) —
   refused for an editor save, a warning for an import or a restore;
5. writes the current bytes into the КД row and appends revision n+1 — both in one transaction; a concurrent save on
   the same revision hits UNIQUE(document_id, revision) and gets the conflict instead of a lost update.

The component library (read, its pictures, the placements of a drawing; create a template, publish its next
revision) is at the end of this file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from ..kitsets import KitDocument, Kitset, document_json, document_kind, item_id
from .components import (
    MAX_REF, Candidate, ComponentError, ComponentTemplate, Pin, connector_ref, embedded_pins, embedded_templates,
    image_of, library_pins, model_code, placement_graph, publishable, split_embedded, template_identity,
    template_model,
)
from .schema import (
    HARNESS_CONTENT_TYPE, HARNESS_UNLINKED_CONTENT_TYPE, Expected, HarnessError, HarnessRevision,
    HarnessWriteConflict, check_version, content_sha256, decode, encode, normalized,
)
from .snapshot import parse_design
from .validate import ReferenceBook, reference_pins, validate_design

TITLE = "Чертёж жгута"
BUSY = "У сборки {index} уже есть чертёж жгута — его только что привязали или создали; обновите страницу"
JUST_SAVED = "Чертёж жгута только что сохранили — обновите страницу и повторите правку"
HISTORY_LIMIT = 1000


class HarnessNotFound(LookupError):
    def __init__(self, message: str, *, gone: bool = False):
        super().__init__(message)
        self.gone = gone


class HarnessConflict(RuntimeError):
    """The drawing changed since the writer read it (or another write is racing this one)."""


class HarnessInvalid(ValueError):
    """The drawing must not be stored; `errors` — every reason, in Russian."""

    def __init__(self, errors: list[str], *, prefix: str = "Чертёж не сохранён"):
        self.errors = list(errors)
        more = f" (и ещё {len(errors) - 1})" if len(errors) > 1 else ""
        super().__init__(f"{prefix}: {errors[0]}{more}" if errors else prefix)


class TechcardsUnitOfWork(Protocol):
    kitsets: Any
    harness_revisions: Any
    harness_components: Any
    harness_routes: Any  # «Маршрут v3» (route_application)
    audit: Any

    def __enter__(self) -> "TechcardsUnitOfWork": ...
    def __exit__(self, *args) -> None: ...
    def commit(self) -> None: ...


Factory = Callable[[], TechcardsUnitOfWork]
ReferencesLoader = Callable[[], ReferenceBook] | None


@dataclass(frozen=True)
class Drawing:
    """The linked drawing of a position: its КД row, current bytes and current revision (0 — a row older than its
    history that no save has touched since; the first save records it as revision 1)."""
    document: KitDocument
    content: bytes
    revision: int
    latest: HarnessRevision | None


@dataclass(frozen=True)
class Saved:
    drawing: Drawing
    warnings: list[str]
    created: bool


def position(uow: TechcardsUnitOfWork, project: str, index: str) -> tuple[Kitset, str]:
    kitset = uow.kitsets.get(project)
    if kitset is None:
        raise HarnessNotFound(f"Карточка набора «{project}» не найдена", gone=True)
    wanted = str(index or "").strip().upper()
    assembly = kitset.assembly(wanted)
    if not wanted or assembly is None:
        raise HarnessNotFound(f"В наборе «{kitset.project}» нет сборки {wanted or '(пусто)'}")
    return kitset, assembly.index


def current(uow: TechcardsUnitOfWork, project: str, index: str) -> Drawing | None:
    """The linked harness КД of the position (one by the database rule of migration 0089)."""
    rows = [document for document in uow.kitsets.documents(project)
            if document.item_index.upper() == index.upper() and document.content_type == HARNESS_CONTENT_TYPE]
    for document in sorted(rows, key=lambda item: item.uploaded_at, reverse=True):
        found = uow.kitsets.document_content(project, document.id)
        if found is not None:
            latest = uow.harness_revisions.latest(document.id)
            return Drawing(found[0], found[1], latest.revision if latest else 0, latest)
    return None


def _when(value: datetime) -> str:
    return value.strftime("%d.%m.%Y %H:%M")


def _stale(drawing: Drawing, *, retry: bool = True) -> HarnessConflict:
    who, when = ((drawing.latest.saved_by, drawing.latest.saved_at) if drawing.latest
                 else (drawing.document.uploaded_by, drawing.document.uploaded_at))
    tail = " и повторите правку" if retry else ""
    return HarnessConflict(f"Чертёж жгута уже сохранил {who} {_when(when)} — обновите страницу{tail}")


def _checked(design: Mapping[str, Any], *, prefix: str) -> tuple[dict[str, Any], list[str]]:
    """The document as it will be stored, or HarnessInvalid with every reason (a document of another schema —
    HarnessError: it is not a drawing this ERP reads at all)."""
    errors = validate_design(check_version(design))
    if errors:
        raise HarnessInvalid(errors, prefix=prefix)
    return normalized(design)


def _references(document: Mapping[str, Any], stored: Mapping[str, Any] | None, references: ReferencesLoader, *,
                strict: bool) -> list[str]:
    """The references this write brings in that the ERP books lack: refused when `strict`, otherwise warnings."""
    before = set(reference_pins(stored or {}))
    new = {pin: where for pin, where in reference_pins(document).items() if pin not in before}
    if not new:
        return []
    if references is None:
        return ["Справочники ERP не подключены — новые ссылки на провода и терминалы не проверены"]
    missing = references().missing(new)
    if missing and strict:
        raise HarnessInvalid(missing)
    return missing


def absorb(repository: Any, candidate: Candidate, *, by: str, at: datetime,
           image: bytes | None = None, image_sha256: str | None = None) -> ComponentTemplate:
    """A template revision into the library, once: the same content already there is that row. The reference key is
    the connector code; when another template holds it (or this number of it), the key names the template too."""
    found = repository.by_content(candidate.content_sha256)
    if found is not None:
        return found
    base = candidate.connector_ref
    for ref in (base, f"{base} ({str(candidate.template_id)[:8]})", f"{base} ({candidate.version_sha256[:12]})"):
        ref = ref[:MAX_REF]
        if repository.ref_free(ref, candidate.template_id, candidate.revision):
            break
    else:
        raise ComponentError(f"Шаблон «{candidate.code}» версии {candidate.revision} уже есть в библиотеке с другим "
                             "содержимым")
    return repository.add(ComponentTemplate(
        id=uuid4(), template_id=candidate.template_id, connector_ref=ref, revision=candidate.revision,
        version_sha256=candidate.version_sha256, content_sha256=candidate.content_sha256, code=candidate.code,
        name=candidate.name, article_bindings=candidate.article_bindings, assets=candidate.assets,
        model=candidate.model, created_by=by, created_at=at, image_sha256=image_sha256,
    ), image)


def _pinned(uow: TechcardsUnitOfWork) -> Callable[[Pin], ComponentTemplate | None]:
    def resolve(pin: Pin) -> ComponentTemplate | None:
        try:
            template_id = UUID(pin[0])
        except ValueError:
            return None
        return uow.harness_components.pinned(template_id, pin[1], pin[2]) if pin[1] > 0 else None
    return resolve


def _library(uow: TechcardsUnitOfWork, document: Mapping[str, Any], stored: Mapping[str, Any] | None, *,
             actor: Actor, now: datetime, strict: bool) -> tuple[dict[str, Any], list[str]]:
    """The document as stored: the templates it carries (a pre-0091 document, an import of the author's export)
    moved into the library and dropped from it; the library connectors this write brings in must pin a revision the
    library has (or one the drawing still carries) — refused when `strict`, otherwise warnings."""
    document, embedded = split_embedded(document)
    candidates, warnings = embedded_templates(embedded)
    for candidate in candidates:
        try:
            absorb(uow.harness_components, candidate, by=actor.name, at=now)
        except ComponentError as error:
            warnings.append(str(error))
    before = set(library_pins(stored or {}))
    carried = embedded_pins(split_embedded(stored or {})[1])
    resolve = _pinned(uow)
    missing = [f"{where}: версии {pin[1]} его шаблона нет в библиотеке компонентов"
               for pin, where in library_pins(document).items()
               if pin not in before and pin not in carried and resolve(pin) is None]
    if missing and strict:
        raise HarnessInvalid(missing)
    return document, warnings + missing


def _stored_design(drawing: Drawing | None) -> dict[str, Any] | None:
    if drawing is None:
        return None
    try:
        return decode(drawing.content)
    except HarnessError:
        return None


def _write(uow: TechcardsUnitOfWork, kitset: Kitset, index: str, drawing: Drawing | None, content: bytes, *,
           actor: Actor, now: datetime, kind: str, restored_from: int | None = None,
           checkpoint: bool = False) -> Drawing:
    """The bytes into the КД row and the next revision (n+1) into the history (same transaction). An autosave of the
    author of the open «saved» row within FOLD_WINDOW of its start folds into that row; anything else — a new row
    (a checkpoint's row is closed at once: no autosave folds into it)."""
    product = item_id(kitset.project, index)
    file_name = f"{product} — {TITLE}"
    if drawing is None:
        document = KitDocument(
            id=uuid4(), project=kitset.project, item_index=index, file_name=file_name,
            content_type=HARNESS_CONTENT_TYPE, size=len(content), uploaded_at=now, uploaded_by=actor.name,
        )
        uow.kitsets.add_document(document, content)
        number = 1
    else:
        stored = drawing.document
        if drawing.latest is None:  # a drawing older than its history: its bytes become revision 1 first
            uow.harness_revisions.append(stored.id, 1, drawing.content, kind="initial",
                                         saved_by=stored.uploaded_by, saved_at=stored.uploaded_at)
        number = max(drawing.revision, 1) + 1
        if not uow.kitsets.replace_content(
            kitset.project, stored.id, file_name=file_name, content_type=HARNESS_CONTENT_TYPE, content=content,
            uploaded_at=now, uploaded_by=actor.name, expected_uploaded_at=stored.uploaded_at,
        ):
            raise HarnessConflict(JUST_SAVED)
        document = replace(stored, file_name=file_name, content_type=HARNESS_CONTENT_TYPE, size=len(content),
                           uploaded_at=now, uploaded_by=actor.name)
    held = drawing.latest if drawing is not None else None
    if (kind == "saved" and not checkpoint and held is not None and held.revision == drawing.revision
            and held.takes_autosave(actor.name, now)):
        if not uow.harness_revisions.fold(document.id, held.revision, number, content, saved_at=now):
            raise HarnessConflict(JUST_SAVED)
        latest = replace(held, revision=number, content_sha256=content_sha256(content), size=len(content),
                         saved_at=now)
    else:
        latest = uow.harness_revisions.append(document.id, number, content, kind=kind, saved_by=actor.name,
                                              saved_at=now, restored_from=restored_from, closed=checkpoint)
    return Drawing(document, content, number, latest)


def _audit(uow: TechcardsUnitOfWork, actor: Actor, now: datetime, event: str, details: dict[str, Any]) -> None:
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=actor.id, event_type=event, actor=actor, occurred_at=now,
                             details=details))


def save(factory: Factory, project: str, index: str, design: Mapping[str, Any], expected: Expected, *,
         actor: Actor, now: datetime, references: ReferencesLoader, kind: str = "saved", checkpoint: bool = False,
         event: str = "techcards.kitset_harness_saved") -> Saved:
    """A save from the editor (`kind="saved"`) or an import of a TECHMAP-GRAPHER export (`kind="imported"`, unknown
    references only warn — the author's catalog is not ours)."""
    document, warnings = _checked(design, prefix="Чертёж не сохранён")
    with factory() as uow:
        kitset, index = position(uow, project, index)
        drawing = current(uow, kitset.project, index)
        stored = _stored_design(drawing)
        if drawing is None and expected.given:
            raise HarnessConflict("Чертёж жгута отвязан от позиции или удалён, пока вы его правили — обновите страницу")
        if drawing is not None and not expected.matches(drawing.revision, drawing.content):
            raise _stale(drawing)
        document, moved = _library(uow, document, stored, actor=actor, now=now, strict=kind == "saved")
        content = encode(document)
        warnings += moved
        warnings += _references(document, stored, references, strict=kind == "saved")
        try:
            written = _write(uow, kitset, index, drawing, content, actor=actor, now=now, kind=kind,
                             checkpoint=checkpoint)
            _audit(uow, actor, now, event, {
                "project": kitset.project, "item_index": index, "file": written.document.file_name,
                "size": str(len(content)), "revision": written.revision,
            })
            uow.commit()
        except HarnessWriteConflict as error:
            # A concurrent first save linked its drawing first (migration 0089), or a concurrent save took this
            # revision number (UNIQUE(document_id, revision)).
            raise HarnessConflict(BUSY.format(index=index) if drawing is None else JUST_SAVED) from error
    return Saved(written, list(dict.fromkeys(warnings)), drawing is None)


def checkpoint(factory: Factory, project: str, index: str, expected: Expected, *, actor: Actor,
               now: datetime) -> HarnessRevision | None:
    """«Сохранить версию»: the history row holding the current revision is closed — the next save starts a new row,
    this one stays as it is. The revision on screen, if given, must be the current one (unsaved edits on screen are
    not in it). None — nothing to close (no history yet, or the row is already final)."""
    with factory() as uow:
        kitset, index = position(uow, project, index)
        drawing = current(uow, kitset.project, index)
        if drawing is None:
            raise HarnessNotFound(f"У сборки {index} нет чертежа жгута")
        if expected.given and not expected.matches(drawing.revision, drawing.content):
            raise _stale(drawing)
        held = drawing.latest
        if held is None or held.revision != drawing.revision or held.closed:
            return held
        if not uow.harness_revisions.close(drawing.document.id, held.revision):
            raise HarnessConflict(JUST_SAVED)
        _audit(uow, actor, now, "techcards.kitset_harness_checkpoint", {
            "project": kitset.project, "item_index": index, "revision": held.revision,
        })
        uow.commit()
    return replace(held, closed=True)


def product_of(factory: Factory, project: str, index: str) -> str:
    """The full product id of the position (HarnessNotFound when there is no such position)."""
    with factory() as uow:
        kitset, index = position(uow, project, index)
    return item_id(kitset.project, index)


def saved_design(factory: Factory, project: str, index: str) -> tuple[str, dict[str, Any]]:
    """The full product id of the position and its saved drawing (HarnessNotFound when it has none)."""
    with factory() as uow:
        kitset, index = position(uow, project, index)
        drawing = current(uow, kitset.project, index)
    if drawing is None:
        raise HarnessNotFound(f"У сборки {index} нет чертежа жгута")
    return item_id(kitset.project, index), decode(drawing.content)


def history(factory: Factory, project: str, index: str, *, limit: int = HISTORY_LIMIT) -> tuple[Drawing, list[HarnessRevision], int]:
    """The linked drawing, its revisions newest first (at most `limit`) and how many there are."""
    with factory() as uow:
        kitset, index = position(uow, project, index)
        drawing = current(uow, kitset.project, index)
        if drawing is None:
            raise HarnessNotFound(f"У сборки {index} нет чертежа жгута")
        return (drawing, uow.harness_revisions.list(drawing.document.id, limit=limit),
                uow.harness_revisions.count(drawing.document.id))


def revision(factory: Factory, project: str, index: str, number: int) -> tuple[HarnessRevision, dict[str, Any]]:
    with factory() as uow:
        kitset, index = position(uow, project, index)
        drawing = current(uow, kitset.project, index)
        if drawing is None:
            raise HarnessNotFound(f"У сборки {index} нет чертежа жгута")
        found = uow.harness_revisions.get(drawing.document.id, number)
    if found is None:
        raise HarnessNotFound(f"У чертежа {index} нет версии {number}")
    return found[0], decode(found[1])


def restore_revision(factory: Factory, project: str, index: str, number: int, expected: Expected, *, actor: Actor,
                     now: datetime, references: ReferencesLoader) -> Saved:
    """Revision `number` becomes the drawing again — as a NEW revision (n+1, «восстановлена из N»); nothing in the
    history changes. The old content is checked by today's rules; references only warn (they were valid then)."""
    prefix = f"Версию {number} не восстановить"
    with factory() as uow:
        kitset, index = position(uow, project, index)
        drawing = current(uow, kitset.project, index)
        if drawing is None:
            raise HarnessNotFound(f"У сборки {index} нет чертежа жгута")
        if expected.given and not expected.matches(drawing.revision, drawing.content):
            raise _stale(drawing)
        found = uow.harness_revisions.get(drawing.document.id, number)
        if found is None:
            raise HarnessNotFound(f"У чертежа {index} нет версии {number}")
        if number == drawing.revision:
            raise HarnessInvalid([f"версия {number} — текущая"], prefix=prefix)
        design = decode(found[1])
        document, warnings = _checked(design, prefix=prefix)
        stored = _stored_design(drawing)
        document, moved = _library(uow, document, stored, actor=actor, now=now, strict=False)
        content = encode(document)
        warnings += moved
        warnings += _references(document, stored, references, strict=False)
        try:
            written = _write(uow, kitset, index, drawing, content, actor=actor, now=now, kind="restored",
                             restored_from=number)
            _audit(uow, actor, now, "techcards.kitset_harness_revision_restored", {
                "project": kitset.project, "item_index": index, "file": written.document.file_name,
                "revision": written.revision, "restored_from": number,
            })
            uow.commit()
        except HarnessWriteConflict as error:  # a concurrent save took this revision number
            raise HarnessConflict(JUST_SAVED) from error
    return Saved(written, list(dict.fromkeys(warnings)), False)


def unlink(factory: Factory, project: str, index: str, expected: Expected, *, actor: Actor,
           now: datetime) -> KitDocument:
    """«Отвязать»: the КД row, its bytes and its history stay (unlinked, «Вернуть» in the card); the position no
    longer reads it. The revision on screen, if given, must be the current one."""
    with factory() as uow:
        kitset, index = position(uow, project, index)
        drawing = current(uow, kitset.project, index)
        if drawing is None:
            raise HarnessNotFound(f"У сборки {index} нет чертежа жгута")
        stored = drawing.document
        if expected.given and not expected.matches(drawing.revision, drawing.content):
            raise _stale(drawing, retry=False)
        if not uow.kitsets.relabel_document(kitset.project, stored.id, from_type=stored.content_type,
                                            to_type=HARNESS_UNLINKED_CONTENT_TYPE):
            raise HarnessConflict("Чертёж жгута только что изменили — обновите страницу")
        _audit(uow, actor, now, "techcards.kitset_harness_unlinked", {
            "project": kitset.project, "item_index": index, "file": stored.file_name, "document_id": str(stored.id),
        })
        uow.commit()
    return replace(stored, content_type=HARNESS_UNLINKED_CONTENT_TYPE)


def relink(factory: Factory, project: str, index: str, document_id, *, actor: Actor, now: datetime) -> Drawing:
    """«Вернуть» an unlinked drawing of this position; its history continues. The position already has a drawing —
    conflict: two drawings at one position do not exist, the other one is unlinked first."""
    with factory() as uow:
        kitset, index = position(uow, project, index)
        document = uow.kitsets.document(kitset.project, document_id)
        if (document is None or document.item_index.upper() != index.upper()
                or document_kind(document) != "harness_unlinked"):
            raise HarnessNotFound(f"У сборки {index} нет такого отвязанного чертежа жгута")
        if current(uow, kitset.project, index) is not None:
            raise HarnessConflict(f"У сборки {index} уже есть чертёж жгута — сначала отвяжите его, потом верните этот")
        # Another «Вернуть» linking a drawing between the check above and here: the database refuses this one
        # (migration 0089, IntegrityError) — the API answers 409.
        relabelled = uow.kitsets.relabel_document(
            kitset.project, document.id, from_type=document.content_type, to_type=HARNESS_CONTENT_TYPE,
        )
        if not relabelled:
            raise HarnessConflict("Чертёж жгута только что изменили — обновите страницу")
        _audit(uow, actor, now, "techcards.kitset_harness_restored", {
            "project": kitset.project, "item_index": index, "file": document.file_name, "document_id": str(document.id),
        })
        found = current(uow, kitset.project, index)
        uow.commit()
    if found is None:
        raise HarnessConflict("Чертёж жгута только что изменили — обновите страницу")
    return found


def view(drawing: Drawing | None, warnings: list[str] | None = None) -> dict[str, Any]:
    """The drawing as the editor reads it. `version` — the deprecated name of `revision` (one release, for browser
    tabs opened before the update)."""
    if drawing is None:
        return {"document": None, "design": None, "revision": None, "version": None, "warnings": warnings or []}
    design = split_embedded(decode(drawing.content))[0]  # a pre-0091 copy of templates: the library serves them
    if warnings is None:
        warnings = parse_design(design)[1]
    return {"document": document_json(drawing.document), "design": design, "revision": drawing.revision,
            "version": str(drawing.revision), "warnings": warnings}


def revision_json(item: HarnessRevision) -> dict[str, Any]:
    """A history row: the drawing as of `revision`, saved by `saved_by` from `started_at` (`first_revision`) to
    `saved_at`; `closed` — «Сохранить версию» fixed it."""
    return {"revision": item.revision, "first_revision": item.first_revision or item.revision,
            "started_at": (item.started_at or item.saved_at).isoformat(), "saved_at": item.saved_at.isoformat(),
            "saved_by": item.saved_by, "size": item.size, "kind": item.kind, "restored_from": item.restored_from,
            "closed": item.closed}


# ── Component library (harness.components, migration 0091) ─────────────────────────────────────────────────────────


def library(factory: Factory) -> list[ComponentTemplate]:
    """The newest revision of every library template."""
    with factory() as uow:
        return uow.harness_components.latest()


def library_template(factory: Factory, template_id: UUID, revision: int | None = None) -> ComponentTemplate:
    """One revision of a template (the newest when `revision` is None)."""
    with factory() as uow:
        found = uow.harness_components.revision(template_id, revision)
    if found is None:
        raise HarnessNotFound("Шаблона компонента нет в библиотеке" if revision is None
                              else f"Версии {revision} шаблона компонента нет в библиотеке")
    return found


def library_image(factory: Factory, template_id: UUID, revision: int, asset_id: str) -> bytes:
    """The picture of a revision under the asset id its model draws it by."""
    with factory() as uow:
        found = uow.harness_components.image(template_id, revision)
    if found is not None:
        template, image = found
        if any(str(asset.get("assetId")) == asset_id and asset.get("sha256") == template.image_sha256
               for asset in template.assets):
            return image
    raise HarnessNotFound("Изображения шаблона компонента нет в библиотеке")


def placements(factory: Factory, project: str, index: str) -> dict[str, Any]:
    """The library placements of the drawing of a position and the revisions they pin (empty without a drawing)."""
    with factory() as uow:
        kitset, index = position(uow, project, index)
        drawing = current(uow, kitset.project, index)
        design = _stored_design(drawing)
        return placement_graph(design, _pinned(uow), project=kitset.project, harness=index,
                               updated=drawing.document.uploaded_at if drawing else None)


# ── Publishing into the library: create a template, publish its next revision ─────────────────────────────────────
# After TECHMAP-GRAPHER `ComponentTemplateEndpoints.cs` (POST create, PUT with the expected version → 409 on a stale
# one), © Василий (SaveOurSouls), used with the author's permission. A revision is a row that never changes: a drawing
# that placed revision N keeps it; publishing adds N+1.

TEMPLATE_STALE = "Шаблон уже изменил {who} — версия {revision}"
TEMPLATE_TAKEN = "Шаблон «{ref}» уже есть в библиотеке ({who}, версия {revision}) — опубликуйте его новую версию"
TEMPLATE_RACED = "Шаблон только что изменили — обновите библиотеку"


def _same(stored: ComponentTemplate, candidate: Candidate, image: bytes | None) -> bool:
    """The revision asked for is `stored` already (model, identity and picture)."""
    wanted = hashlib.sha256(image).hexdigest() if image is not None else None
    return stored.version_sha256 == candidate.version_sha256 and stored.image_sha256 == wanted


def _publish(uow: TechcardsUnitOfWork, candidate: Candidate, ref: str, image: bytes | None, *, actor: Actor,
             now: datetime, event: str) -> ComponentTemplate:
    """The revision as a new row and its journal event, committed (a number taken meanwhile → HarnessWriteConflict)."""
    template = uow.harness_components.add(ComponentTemplate(
        id=uuid4(), template_id=candidate.template_id, connector_ref=ref, revision=candidate.revision,
        version_sha256=candidate.version_sha256, content_sha256=candidate.content_sha256, code=candidate.code,
        name=candidate.name, article_bindings=candidate.article_bindings, assets=candidate.assets,
        model=candidate.model, created_by=actor.name, created_at=now,
        image_sha256=hashlib.sha256(image).hexdigest() if image is not None else None,
    ), image)
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=template.template_id, event_type=event, actor=actor,
                             occurred_at=now, details={
                                 "template_id": str(template.template_id), "connector_ref": template.connector_ref,
                                 "code": template.code, "name": template.name, "revision": str(template.revision),
                                 "version_sha256": template.version_sha256,
                             }))
    uow.commit()
    return template


def create_template(factory: Factory, model: object, *, image: bytes | None, actor: Actor, now: datetime,
                    code: str | None = None, name: str | None = None) -> ComponentTemplate:
    """A new library template — its revision 1. Its key is the connector code (given, or the model's first article);
    a key the library already holds is that template: the same content returns its newest revision, other content
    is a conflict (publish a version of it instead)."""
    model = template_model(model)
    code = (code or "").strip() or model_code(model)
    if not code:
        raise ComponentError("У шаблона компонента нет кода разъёма: в модели нет артикула, код не передан")
    ref = connector_ref(code)
    candidate = publishable(model, template_id=template_identity(ref), revision=1, code=code, name=name or "",
                            image=image)

    def held(uow: TechcardsUnitOfWork) -> ComponentTemplate | None:
        return uow.harness_components.by_ref(ref) or uow.harness_components.revision(candidate.template_id)

    with factory() as uow:
        found = held(uow)
        if found is None:
            try:
                return _publish(uow, candidate, ref, image, actor=actor, now=now,
                                event="techcards.harness_component_created")
            except HarnessWriteConflict:  # a concurrent create took the key: answer by what it stored
                pass
    if found is None:
        with factory() as uow:
            found = held(uow)
    if found is not None and _same(found, candidate, image):
        return found
    raise HarnessConflict(TEMPLATE_TAKEN.format(ref=found.connector_ref, who=found.created_by,
                                                revision=found.revision) if found else TEMPLATE_RACED)


def publish_version(factory: Factory, template_id: UUID, base_revision: int, model: object, *, image: bytes | None,
                    actor: Actor, now: datetime, code: str | None = None,
                    name: str | None = None) -> ComponentTemplate:
    """The next revision (N+1) of a template, made on revision `base_revision` — which must be the newest, else a
    conflict naming who published the newest. Content equal to the newest is that revision (a retry, an unchanged
    save): no new row. Code and name not given are the newest's; its picture goes on when the model still names it
    and no other is uploaded. No stored row changes."""
    model = template_model(model)
    with factory() as uow:
        newest = uow.harness_components.revision(template_id)
        if newest is None:
            raise HarnessNotFound("Шаблона компонента нет в библиотеке")
        candidate = publishable(model, template_id=newest.template_id, revision=newest.revision + 1,
                                code=(code or "").strip() or newest.code, name=(name or "").strip() or newest.name,
                                image=image)
        if image is None and image_of(newest, candidate.assets):
            carried = uow.harness_components.image(newest.template_id, newest.revision)
            image = carried[1] if carried is not None else None
        if _same(newest, candidate, image):
            return newest
        if base_revision != newest.revision:
            raise HarnessConflict(TEMPLATE_STALE.format(who=newest.created_by, revision=newest.revision))
        try:
            return _publish(uow, candidate, newest.connector_ref, image, actor=actor, now=now,
                            event="techcards.harness_component_published")
        except HarnessWriteConflict:  # a concurrent publish took revision N+1: answer by what it stored
            pass
    with factory() as uow:
        newest = uow.harness_components.revision(template_id)
    if newest is not None and _same(newest, candidate, image):
        return newest
    raise HarnessConflict(TEMPLATE_STALE.format(who=newest.created_by, revision=newest.revision) if newest
                          else TEMPLATE_RACED)

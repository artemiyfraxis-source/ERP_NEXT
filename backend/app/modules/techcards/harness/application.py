"""Use cases of the harness drawing of a position (application layer): read, save (also import and restore of an
earlier revision), history, «Отвязать» / «Вернуть». Each runs in one techcards unit of work and commits it.

A save:
1. refuses what must not be stored (`validate.validate_design`, Russian messages → HarnessInvalid);
2. checks the revision it was made on against the stored one (HarnessConflict, 409 in the API);
3. looks up in the ERP books only the references this write brings in (a book edited later never locks an existing
   drawing): an editor save is refused on an unknown one, an import or a restore only warns;
4. writes the current bytes into the КД row and appends revision n+1 — both in one transaction; a concurrent save on
   the same revision hits UNIQUE(document_id, revision) and gets the conflict instead of a lost update.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Mapping, Protocol
from uuid import uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from ..kitsets import KitDocument, Kitset, document_json, document_kind, item_id
from .schema import (
    COMPONENT_GRAPH_KEYS, HARNESS_CONTENT_TYPE, HARNESS_UNLINKED_CONTENT_TYPE, Expected, HarnessError, HarnessRevision,
    HarnessWriteConflict,
    carry_component_graph, check_version, decode, encode, normalized,
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


def _stored_design(drawing: Drawing | None) -> dict[str, Any] | None:
    if drawing is None:
        return None
    try:
        return decode(drawing.content)
    except HarnessError:
        return None


def _write(uow: TechcardsUnitOfWork, kitset: Kitset, index: str, drawing: Drawing | None, content: bytes, *,
           actor: Actor, now: datetime, kind: str, restored_from: int | None = None) -> Drawing:
    """The bytes into the КД row and the next revision into the history (same transaction)."""
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
    latest = uow.harness_revisions.append(document.id, number, content, kind=kind, saved_by=actor.name,
                                          saved_at=now, restored_from=restored_from)
    return Drawing(document, content, number, latest)


def _audit(uow: TechcardsUnitOfWork, actor: Actor, now: datetime, event: str, details: dict[str, Any]) -> None:
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=actor.id, event_type=event, actor=actor, occurred_at=now,
                             details=details))


def save(factory: Factory, project: str, index: str, design: Mapping[str, Any], expected: Expected, *,
         actor: Actor, now: datetime, references: ReferencesLoader, kind: str = "saved",
         event: str = "techcards.kitset_harness_saved") -> Saved:
    """A save from the editor (`kind="saved"`) or an import of a TECHMAP-GRAPHER export (`kind="imported"`, unknown
    references only warn — the author's catalog is not ours)."""
    document, warnings = _checked(design, prefix="Чертёж не сохранён")
    content = encode(document)
    with factory() as uow:
        kitset, index = position(uow, project, index)
        drawing = current(uow, kitset.project, index)
        stored = _stored_design(drawing)
        if drawing is None and expected.given:
            raise HarnessConflict("Чертёж жгута отвязан от позиции или удалён, пока вы его правили — обновите страницу")
        if drawing is not None and not expected.matches(drawing.revision, drawing.content):
            raise _stale(drawing)
        if stored is not None and any(key not in document for key in COMPONENT_GRAPH_KEYS):
            kept = carry_component_graph(document, stored)
            if kept != document:
                document, content = kept, encode(kept)
        warnings += _references(document, stored, references, strict=kind == "saved")
        try:
            written = _write(uow, kitset, index, drawing, content, actor=actor, now=now, kind=kind)
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
        content = encode(document)
        warnings += _references(document, _stored_design(drawing), references, strict=False)
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
    design = decode(drawing.content)
    if warnings is None:
        warnings = parse_design(design)[1]
    return {"document": document_json(drawing.document), "design": design, "revision": drawing.revision,
            "version": str(drawing.revision), "warnings": warnings}


def revision_json(item: HarnessRevision) -> dict[str, Any]:
    return {"revision": item.revision, "saved_at": item.saved_at.isoformat(), "saved_by": item.saved_by,
            "size": item.size, "kind": item.kind, "restored_from": item.restored_from}

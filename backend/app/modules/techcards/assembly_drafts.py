"""Черновики схем сборки («чертёж → схема сборки», owner 05.10.2026): use cases and the repository.

An agent (Claude Code today through an API token, a server-side agent later) reads a drawing of a project position
and sends a draft «Ручная сборка» config. The draft is checked by the real builder in memory and stored next to the
position; it never touches the project techcard. The master corrects it in «Ручная сборка» and builds the card with
the draft named: the built config becomes the draft's final config and the card's revision its final revision. The
pair draft/final plus the master's lesson is one example of the corpus (docs/assembly-corpus) the next drafts learn
from.

The draft's note (what the agent read on the drawing, its doubts) has no column of its own in 0088: it rides in the
creation audit event and is read back from there."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow

from .assembly_drafts_infrastructure import ASSEMBLY_DRAFT_SOURCES, AssemblyDraftRow
from .etk_builder import MANUAL_MODE, build_etk
from .kitsets import project_key
from .project_cards import _document as checked_document

CREATED_EVENT = "techcards.assembly_draft_created"
LESSON_EVENT = "techcards.assembly_draft_lesson"
FINAL_EVENT = "techcards.assembly_draft_finalised"
CORPUS_LIMIT = 50
LIST_LIMIT = 200


class AssemblyDraftError(ValueError):
    """The request cannot be met; the message is for the person (422)."""


class AssemblyDraftNotFound(LookupError):
    """No such draft (404)."""


@dataclass(frozen=True)
class AssemblyDraft:
    id: UUID
    project: str
    item_index: str
    drawing_document_id: UUID | None
    source: str
    author: str
    draft_config: dict[str, Any]
    final_config: dict[str, Any] | None
    final_revision: int | None
    lesson: str
    status: str
    created_at: datetime
    updated_at: datetime
    note: str = ""


class SqlAlchemyAssemblyDraftRepository:
    """`techcard_assembly_drafts` inside the techcards unit of work (its session, its transaction)."""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: AssemblyDraftRow, note: str = "") -> AssemblyDraft:
        return AssemblyDraft(
            id=row.id, project=row.project, item_index=row.item_index, drawing_document_id=row.drawing_document_id,
            source=row.source, author=row.author, draft_config=deepcopy(row.draft_config),
            final_config=deepcopy(row.final_config) if row.final_config is not None else None,
            final_revision=row.final_revision, lesson=row.lesson or "", status=row.status,
            created_at=row.created_at, updated_at=row.updated_at, note=note,
        )

    def _notes(self, ids: list[UUID]) -> dict[UUID, str]:
        if not ids:
            return {}
        rows = self.session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == CREATED_EVENT, AuditEventRow.aggregate_id.in_(ids),
        ))
        return {row.aggregate_id: str((row.details or {}).get("note") or "") for row in rows}

    def _many(self, rows: list[AssemblyDraftRow]) -> list[AssemblyDraft]:
        notes = self._notes([row.id for row in rows])
        return [self._domain(row, notes.get(row.id, "")) for row in rows]

    def add(self, draft: AssemblyDraft) -> None:
        self.session.add(AssemblyDraftRow(
            id=draft.id, project=draft.project, item_index=draft.item_index,
            drawing_document_id=draft.drawing_document_id, source=draft.source, author=draft.author,
            draft_config=deepcopy(draft.draft_config), final_config=None, final_revision=None, lesson=draft.lesson,
            status=draft.status, created_at=draft.created_at, updated_at=draft.updated_at,
        ))

    def get(self, draft_id: UUID) -> AssemblyDraft | None:
        row = self.session.get(AssemblyDraftRow, draft_id)
        return None if row is None else self._many([row])[0]

    def save(self, draft: AssemblyDraft) -> None:
        """The fields that change after creation: lesson, final config and revision, status."""
        row = self.session.get(AssemblyDraftRow, draft.id)
        if row is None:
            raise AssemblyDraftNotFound(str(draft.id))
        row.final_config = deepcopy(draft.final_config) if draft.final_config is not None else None
        row.final_revision = draft.final_revision
        row.lesson = draft.lesson
        row.status = draft.status
        row.updated_at = draft.updated_at

    def list(
        self, project: str = "", item_index: str = "", *, status: str = "", limit: int | None = LIST_LIMIT,
    ) -> list[AssemblyDraft]:
        """Newest first (last change). `project` matches by the project number rule (spaces, case, homoglyphs)."""
        query = select(AssemblyDraftRow)
        if item_index:
            query = query.where(AssemblyDraftRow.item_index == item_index)
        if status:
            query = query.where(AssemblyDraftRow.status == status)
        rows = list(self.session.scalars(query.order_by(AssemblyDraftRow.updated_at.desc(), AssemblyDraftRow.id)))
        if project:
            wanted = project_key(project)
            rows = [row for row in rows if project_key(row.project) == wanted]
        return self._many(rows if limit is None else rows[:limit])


def _drafts(uow) -> SqlAlchemyAssemblyDraftRepository:
    return SqlAlchemyAssemblyDraftRepository(uow.session)


def _index(value: object) -> str:
    return str(value or "").strip().upper()


def _position(uow, project: str, item_index: str) -> tuple[str, str]:
    """The project position as «Проекты» names it: the kit card's project number and its КБ."""
    index = _index(item_index)
    if not str(project or "").strip() or not index:
        raise AssemblyDraftError("Укажите проект и сборку (КБ)")
    kitset = uow.kitsets.get(project)
    if kitset is None:
        raise AssemblyDraftError(f"Карточка проекта «{project}» не найдена в разделе «Проекты»")
    if kitset.assembly(index) is None:
        raise AssemblyDraftError(f"В проекте «{kitset.project}» нет сборки {index}")
    return kitset.project, index


# ── Проверка конфига настоящим сборщиком ЭТК ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckedConfig:
    warnings: tuple[str, ...]
    operations: int
    route: dict[str, Any] | None
    # What the builder made of each operation (position, code, name, «Вход», комплектующие, «Выход»), so a draft author
    # can check the norms without a local build.
    details: tuple[dict[str, Any], ...] = ()


def check_config(config: Mapping[str, Any], uow) -> CheckedConfig:
    """Build the ETK in memory as «Собрать ЭТК» would (the library's cards) and drop it: errors are
    raised in Russian, warnings returned. Nothing is written."""
    if not isinstance(config, Mapping) or config.get("mode") != MANUAL_MODE:
        raise AssemblyDraftError("Черновик — это «Ручная сборка»: в build_config нужен mode = «manual»")
    cards = uow.operation_cards.list()
    try:
        document = checked_document(build_etk(config, cards))
    except ValueError as error:
        raise AssemblyDraftError(str(error)) from error
    except (TypeError, KeyError, AttributeError, ArithmeticError) as error:
        raise AssemblyDraftError(
            "Схема сборки не собирается: в build_config непонятные данные — сверьте с build-config.schema.json",
        ) from error
    warnings = list(document.get("warnings") or [])
    if not cards:
        warnings.insert(0, "Библиотека операционных карт не загружена — карточки операций не проверены")
    return CheckedConfig(tuple(warnings), len(document["operations"]), document.get("route"),
                         tuple(_operation_detail(operation) for operation in document["operations"]))


def _rows(rows: Any, keys: tuple[str, ...]) -> list[dict[str, str]]:
    return [{key: str(row.get(key) or "") for key in keys} for row in rows or [] if isinstance(row, Mapping)]


def _operation_detail(operation: Mapping[str, Any]) -> dict[str, Any]:
    card = operation.get("card") if isinstance(operation.get("card"), Mapping) else {}
    return {
        "position": operation.get("position"), "code": str(operation.get("code") or ""),
        "type": str(operation.get("type") or ""), "name": str(card.get("name") or operation.get("title") or ""),
        "input": _rows(operation.get("semis"), ("name", "norm", "unit")),
        "components": _rows(operation.get("components"), ("art", "name", "norm", "unit")),
        "output": _rows(operation.get("result"), ("name", "norm", "unit")),
    }


# ── Черновик ─────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SubmitDraftCommand:
    project: str
    item_index: str
    drawing_document_id: UUID | None
    source: str
    build_config: dict[str, Any]
    note: str
    dry_run: bool
    actor: Actor
    now: datetime


def submit_draft(command: SubmitDraftCommand, uow_factory: Callable[[], Any]) -> tuple[AssemblyDraft | None, CheckedConfig]:
    """Check the config; unless `dry_run`, store it as a draft of the position. Project techcards are not read or
    written here."""
    if command.source not in ASSEMBLY_DRAFT_SOURCES:
        raise AssemblyDraftError("Источник черновика: claude-code, server-agent или manual")
    with uow_factory() as uow:
        project, index = _position(uow, command.project, command.item_index)
        if command.drawing_document_id is not None:
            drawing = uow.kitsets.document(project, command.drawing_document_id)
            if drawing is None or _index(drawing.item_index) != index:
                raise AssemblyDraftError(f"Чертёж не найден в КД сборки {index} проекта «{project}»")
        checked = check_config(command.build_config, uow)
        if command.dry_run:
            return None, checked
        draft = AssemblyDraft(
            id=uuid4(), project=project, item_index=index, drawing_document_id=command.drawing_document_id,
            source=command.source, author=command.actor.name, draft_config=deepcopy(command.build_config),
            final_config=None, final_revision=None, lesson="", status="draft",
            created_at=command.now, updated_at=command.now, note=command.note.strip(),
        )
        _drafts(uow).add(draft)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=draft.id, event_type=CREATED_EVENT, actor=command.actor,
            occurred_at=command.now, details={
                "project": project, "item_index": index, "source": draft.source, "note": draft.note,
                "operations": str(checked.operations), "warnings": str(len(checked.warnings)),
            },
        ))
        uow.commit()
    return draft, checked


def list_drafts(project: str, item_index: str, uow_factory: Callable[[], Any]) -> list[AssemblyDraft]:
    with uow_factory() as uow:
        return _drafts(uow).list(project.strip(), _index(item_index))


def get_draft(draft_id: UUID, uow_factory: Callable[[], Any]) -> AssemblyDraft:
    with uow_factory() as uow:
        draft = _drafts(uow).get(draft_id)
    if draft is None:
        raise AssemblyDraftNotFound("Черновик схемы сборки не найден")
    return draft


def set_lesson(draft_id: UUID, lesson: str, actor: Actor, now: datetime, uow_factory: Callable[[], Any]) -> AssemblyDraft:
    """«Что поправил и почему» — written by the master or the agent comparing the pair; replaces the previous one."""
    with uow_factory() as uow:
        repository = _drafts(uow)
        draft = repository.get(draft_id)
        if draft is None:
            raise AssemblyDraftNotFound("Черновик схемы сборки не найден")
        updated = replace(draft, lesson=lesson.strip(), updated_at=now)
        repository.save(updated)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=draft.id, event_type=LESSON_EVENT, actor=actor, occurred_at=now,
            details={"project": draft.project, "item_index": draft.item_index, "length": str(len(updated.lesson))},
        ))
        uow.commit()
    return updated


def corpus(project: str, query: str, uow_factory: Callable[[], Any], limit: int = CORPUS_LIMIT) -> list[AssemblyDraft]:
    """Finalised examples, newest first: `query` is a case-insensitive substring of the lesson, the note, the
    position or the assembly name."""
    needle = query.strip().casefold()
    with uow_factory() as uow:
        finals = _drafts(uow).list(project.strip(), status="final", limit=None)

    def haystack(draft: AssemblyDraft) -> str:
        names = [str((config or {}).get("assemblyName") or "") for config in (draft.draft_config, draft.final_config)]
        return " ".join([draft.lesson, draft.note, draft.project, draft.item_index, *names]).casefold()

    return [draft for draft in finals if not needle or needle in haystack(draft)][:limit]


# ── «Собрать ЭТК» с черновиком ───────────────────────────────────────────────────────────────────────


def draft_for_card(draft_id: UUID, card_id: UUID, uow_factory: Callable[[], Any]) -> AssemblyDraft:
    """Before the build: the draft exists and belongs to the card's position, so the build is not wasted on a typo."""
    with uow_factory() as uow:
        draft = _drafts(uow).get(draft_id)
        card = uow.techcards.get(card_id)
    if draft is None:
        raise AssemblyDraftNotFound("Черновик схемы сборки не найден")
    if card is not None and (
        project_key(card.project) != project_key(draft.project) or _index(card.item_index) != _index(draft.item_index)
    ):
        raise AssemblyDraftError(
            f"Черновик относится к {draft.project} {draft.item_index}, а техкарта — к {card.project} {card.item_index}"
        )
    return draft


def finalise_draft(
    draft_id: UUID, config: Mapping[str, Any], revision: int, actor: Actor, now: datetime,
    uow_factory: Callable[[], Any],
) -> AssemblyDraft:
    """After a successful build: the config the master built is the draft's final, the card's revision its revision.
    A draft finalised before is finalised again with the newer build; its lesson stays."""
    with uow_factory() as uow:
        repository = _drafts(uow)
        draft = repository.get(draft_id)
        if draft is None:
            raise AssemblyDraftNotFound("Черновик схемы сборки не найден")
        updated = replace(draft, final_config=deepcopy(dict(config)), final_revision=revision, status="final",
                          updated_at=now)
        repository.save(updated)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=draft.id, event_type=FINAL_EVENT, actor=actor, occurred_at=now,
            details={"project": draft.project, "item_index": draft.item_index, "revision": str(revision)},
        ))
        uow.commit()
    return updated

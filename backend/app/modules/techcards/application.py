"""Use cases for persistent project techcards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .project_cards import ProjectTechcard, TechcardRevision, TechcardValidationError
from .operation_cards import OperationCardTemplate, parse_operation_library
from .etk_builder import build_etk


class TechcardRepository(Protocol):
    def get(self, card_id: UUID) -> ProjectTechcard | None: ...
    def by_key(self, project: str, item_index: str) -> ProjectTechcard | None: ...
    def list(self) -> Sequence[ProjectTechcard]: ...
    def save(self, card: ProjectTechcard) -> None: ...
    def add_revision(self, revision: TechcardRevision) -> None: ...
    def revisions(self, card_id: UUID) -> Sequence[TechcardRevision]: ...


class OperationCardRepository(Protocol):
    def list(self) -> Sequence[OperationCardTemplate]: ...
    def get(self, source_id: str) -> OperationCardTemplate | None: ...
    def replace(
        self, cards: tuple[OperationCardTemplate, ...], *, source_commit: str, imported_at: datetime,
    ) -> None: ...
    def metadata(self) -> dict | None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class UnitOfWork(Protocol):
    techcards: TechcardRepository
    operation_cards: OperationCardRepository
    audit: AppendOnlyRepository
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class CreateProjectTechcardCommand:
    project: str
    item_index: str
    product_name: str
    source_calculation: str
    deadline: date | None
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ChangeProjectTechcardCommand:
    card_id: UUID
    expected_version: int
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class RebuildProjectTechcardCommand(ChangeProjectTechcardCommand):
    document: dict[str, Any]


@dataclass(frozen=True)
class AutoBuildProjectTechcardCommand(ChangeProjectTechcardCommand):
    config: dict[str, Any]
    technology_source: dict[str, Any] | None = None


@dataclass(frozen=True)
class EditTechcardOperationCommand(ChangeProjectTechcardCommand):
    operation_key: str
    operation: dict[str, Any]


@dataclass(frozen=True)
class ReplaceOperationLibraryCommand:
    source_commit: str
    templates: tuple[dict[str, Any], ...]
    actor: Actor
    now: datetime


def _current(command: ChangeProjectTechcardCommand, uow: UnitOfWork) -> ProjectTechcard:
    card = uow.techcards.get(command.card_id)
    if card is None:
        raise KeyError(f"Project techcard not found: {command.card_id}")
    if card.version != command.expected_version:
        raise TechcardValidationError("Карточка проекта изменилась после загрузки формы")
    return card


def _revision(card: ProjectTechcard, reason: str, actor: Actor, now: datetime) -> TechcardRevision:
    if card.document is None or card.revision <= 0:
        raise TechcardValidationError("Нельзя записать пустую ревизию ЭТК")
    return TechcardRevision(
        id=uuid4(), project_card_id=card.id, revision=card.revision,
        reason=reason, actor_name=actor.name, occurred_at=now,
        document=card.document,
    )


def create_project_techcard(command: CreateProjectTechcardCommand, uow: UnitOfWork) -> ProjectTechcard:
    with uow:
        if uow.techcards.by_key(command.project.strip(), command.item_index.strip()) is not None:
            raise TechcardValidationError("Карточка такого изделия проекта уже существует")
        card = ProjectTechcard.create(
            project=command.project, item_index=command.item_index,
            product_name=command.product_name, source_calculation=command.source_calculation,
            deadline=command.deadline, now=command.now,
        )
        uow.techcards.save(card)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=card.id, event_type="techcards.project_created",
            actor=command.actor, occurred_at=command.now,
            details={"project": card.project, "item_index": card.item_index},
        ))
        uow.commit()
        return card


def rebuild_project_techcard(command: RebuildProjectTechcardCommand, uow: UnitOfWork) -> ProjectTechcard:
    with uow:
        current = _current(command, uow)
        updated = current.rebuild(command.document, command.now)
        uow.techcards.save(updated)
        if updated.revision > current.revision:
            uow.techcards.add_revision(_revision(updated, "Пересборка", command.actor, command.now))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=updated.id, event_type="techcards.project_rebuilt",
            actor=command.actor, occurred_at=command.now,
            details={"revision": updated.revision},
        ))
        uow.commit()
        return updated


def auto_build_project_techcard(command: AutoBuildProjectTechcardCommand, uow: UnitOfWork) -> ProjectTechcard:
    with uow:
        current = _current(command, uow)
        metadata = uow.operation_cards.metadata()
        if metadata is None:
            raise TechcardValidationError("Библиотека операционных карт не загружена")
        try:
            document = build_etk(
                command.config,
                uow.operation_cards.list(),
                operation_library_commit=metadata["source_commit"],
                technology_source=command.technology_source,
            )
        except ValueError as error:
            raise TechcardValidationError(str(error)) from error
        updated = current.rebuild(document, command.now)
        uow.techcards.save(updated)
        if updated.revision > current.revision:
            uow.techcards.add_revision(_revision(updated, "Автопересборка", command.actor, command.now))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=updated.id, event_type="techcards.project_auto_built",
            actor=command.actor, occurred_at=command.now,
            details={
                "revision": updated.revision,
                "operations": len(document["operations"]),
                "warnings": len(document["warnings"]),
                "operation_library_commit": metadata["source_commit"],
            },
        ))
        uow.commit()
        return updated


def issue_project_techcard(command: ChangeProjectTechcardCommand, uow: UnitOfWork) -> ProjectTechcard:
    with uow:
        updated = _current(command, uow).issue(command.now)
        uow.techcards.save(updated)
        uow.techcards.add_revision(_revision(updated, "Первая выдача", command.actor, command.now))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=updated.id, event_type="techcards.project_issued",
            actor=command.actor, occurred_at=command.now,
            details={"revision": updated.revision},
        ))
        uow.commit()
        return updated


def edit_project_operation(command: EditTechcardOperationCommand, uow: UnitOfWork) -> ProjectTechcard:
    with uow:
        current = _current(command, uow)
        updated = current.edit_operation(command.operation_key, command.operation, command.now)
        uow.techcards.save(updated)
        if updated.revision > current.revision:
            uow.techcards.add_revision(_revision(updated, f"Правка {command.operation_key}", command.actor, command.now))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=updated.id, event_type="techcards.operation_edited",
            actor=command.actor, occurred_at=command.now,
            details={"operation_key": command.operation_key, "revision": updated.revision},
        ))
        uow.commit()
        return updated


def replace_operation_library(command: ReplaceOperationLibraryCommand, uow: UnitOfWork) -> int:
    source_commit = str(command.source_commit or "").strip().lower()
    if len(source_commit) != 40 or any(character not in "0123456789abcdef" for character in source_commit):
        raise TechcardValidationError("Нужен полный commit SUPPLYMAP из 40 hex-символов")
    try:
        cards = parse_operation_library(command.templates)
    except ValueError as error:
        raise TechcardValidationError(str(error)) from error
    with uow:
        uow.operation_cards.replace(cards, source_commit=source_commit, imported_at=command.now)
        uow.audit.add(AuditEvent(
            id=uuid4(),
            aggregate_id=UUID("00000000-0000-0000-0000-000000000018"),
            event_type="techcards.operation_library_replaced",
            actor=command.actor,
            occurred_at=command.now,
            details={"source_commit": source_commit, "templates": str(len(cards))},
        ))
        uow.commit()
    return len(cards)

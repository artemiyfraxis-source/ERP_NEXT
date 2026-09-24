"""Transactional use cases for the native production-plan projection and journal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, Sequence
from uuid import uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .domain import ProductionValidationError
from .plan import (
    PLAN_KINDS,
    PlanEvent,
    PlanProjectSnapshot,
    normalize_project_number,
    summarize_journal,
    validate_plan_note,
)


class PlanProjectRepository(Protocol):
    def list(self) -> Sequence[PlanProjectSnapshot]: ...
    def replace(self, projects: tuple[PlanProjectSnapshot, ...]) -> None: ...


class PlanEventRepository(Protocol):
    def list(self, project: str | None = None, limit: int = 10_000) -> Sequence[PlanEvent]: ...
    def add(self, event: PlanEvent) -> PlanEvent: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class PlanUnitOfWork(Protocol):
    plan_projects: PlanProjectRepository
    plan_events: PlanEventRepository
    audit: AppendOnlyRepository

    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class ReplacePlanProjectsCommand:
    projects: tuple[PlanProjectSnapshot, ...]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class AddPlanEventCommand:
    project: str
    kind: str
    blocker: str
    text: str
    deadline: date | None
    known_projects: tuple[str, ...]
    actor: Actor
    now: datetime
    item_index: str = ""
    value: str = ""


def replace_plan_projects(
    command: ReplacePlanProjectsCommand, uow: PlanUnitOfWork,
) -> tuple[PlanProjectSnapshot, ...]:
    normalized = tuple(
        PlanProjectSnapshot(
            project=normalize_project_number(project.project),
            customer=str(project.customer or "").strip(),
            opened_on=project.opened_on,
            deadline=project.deadline,
            status=str(project.status or "").strip(),
            terminal=project.terminal,
            imported_at=command.now,
        )
        for project in command.projects
    )
    if any(not project.project for project in normalized):
        raise ProductionValidationError("Номер проекта в снимке обязателен")
    if len({project.project for project in normalized}) != len(normalized):
        raise ProductionValidationError("В снимке реестра есть дубль проекта")
    with uow:
        uow.plan_projects.replace(normalized)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=command.actor.id,
            event_type="production.plan_projects_replaced",
            actor=command.actor, occurred_at=command.now,
            details={"projects": len(normalized)},
        ))
        uow.commit()
    return normalized


def add_plan_event(command: AddPlanEventCommand, uow: PlanUnitOfWork) -> PlanEvent:
    payload = {
        "project": command.project, "kind": command.kind,
        "blocker": command.blocker, "text": command.text,
        "deadline": command.deadline,
        "item_index": command.item_index, "value": command.value,
    }
    validate_plan_note(payload, command.known_projects)
    project = normalize_project_number(command.project)
    kind = PLAN_KINDS[command.kind]
    text = str(command.text or "").strip()
    with uow:
        if command.kind == "deadline":
            current = summarize_journal(uow.plan_events.list(project=project)).get(project)
            previous = current.deadline.strftime("%d.%m.%Y") if current and current.deadline else "без дедлайна"
            next_deadline = command.deadline.strftime("%d.%m.%Y")
            text = f"{previous} → {next_deadline}" + (f" · {text}" if text else "")
        event = PlanEvent(
            project=project, occurred_at=command.now, who=command.actor.name,
            kind=kind, blocker=command.blocker if command.kind == "block" else "",
            text=text, deadline=command.deadline if command.kind == "deadline" else None,
            item_index=str(command.item_index or "").strip().upper(),
            value=str(command.value or "").strip(),
        )
        event = uow.plan_events.add(event)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=event.id, event_type="production.plan_event_added",
            actor=command.actor, occurred_at=command.now,
            details={"project": project, "kind": kind},
        ))
        uow.commit()
    return event

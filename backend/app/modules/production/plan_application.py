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
    KIT_STAGES,
    PLAN_KINDS,
    PLAN_STAGE_AUTO,
    PROJECT_TERMINALS,
    PlanEvent,
    PlanProjectSnapshot,
    StagePlan,
    kit_label,
    normalize_project_number,
    plan_mark_value,
    stage_change_text,
    summarize_journal,
    validate_plan_note,
)


class PlanProjectRepository(Protocol):
    def list(self) -> Sequence[PlanProjectSnapshot]: ...
    def replace(self, projects: tuple[PlanProjectSnapshot, ...]) -> None: ...


class PlanEventRepository(Protocol):
    def list(self, project: str | None = None, limit: int = 10_000) -> Sequence[PlanEvent]: ...
    def add(self, event: PlanEvent) -> PlanEvent: ...
    def of_project(self, project: str) -> Sequence[PlanEvent]: ...
    def delete_project(self, project: str) -> int: ...
    def last_sequence(self, project: str) -> int: ...


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
    field: str = ""


@dataclass(frozen=True)
class DeleteProjectPlanEventsCommand:
    """Whole journal of one project (deleted project: a project created again under the same number starts clean).
    ``apply=False`` — dry run: reads, writes nothing."""

    project: str
    actor: Actor
    now: datetime
    apply: bool = True


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
        "field": command.field,
    }
    validate_plan_note(payload, command.known_projects)
    project = normalize_project_number(command.project)
    kind = PLAN_KINDS[command.kind]
    text = str(command.text or "").strip()
    value = str(command.value or "").strip()
    field_name = ""
    if command.kind == "mark":
        # «Отметка плана»: ручное значение колонки листа «ЗАГРУЗ»; пустое — вернуть вычисленное.
        field_name = str(command.field or "").strip()
        normalized = plan_mark_value(field_name, command.value, command.text)
        if field_name == "comment":
            text, value = normalized, ""
        else:
            value = normalized or "авто"
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
            value=value, field=field_name,
        )
        event = uow.plan_events.add(event)
        details = {"project": project, "kind": kind}
        if field_name:
            details.update({"field": field_name, "value": text if field_name == "comment" else value})
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=event.id, event_type="production.plan_event_added",
            actor=command.actor, occurred_at=command.now,
            details=details,
        ))
        uow.commit()
    return event


class StageChangeConflict(Exception):
    """The project's journal moved between reading the stages and writing the change (409, not 422)."""


@dataclass(frozen=True)
class ChangeStageCommand:
    """A manual stage change already planned by `plan.plan_stage_change` over the stages read just before.

    ``reopen_from`` — the closed status of the project (Отказ/Слился/Отменён): a stage pick first returns it to work.
    ``expected_sequence`` — the project's last journal sequence the plan was read at; a newer one refuses the write."""

    project: str
    plan: StagePlan
    actor: Actor
    now: datetime
    reason: str = ""
    reopen_from: str = ""
    expected_sequence: int | None = None
    # Per КБ: what the same change also closed (`app.workflows.stage_cascade`) — ``notes`` is appended to the КБ's
    # «Этап КБ» row text, ``closed`` (labels) goes into the audit event.
    notes: tuple[tuple[str, str], ...] = ()
    closed: tuple[tuple[str, tuple[str, ...]], ...] = ()


def change_stage(command: ChangeStageCommand, uow: PlanUnitOfWork) -> tuple[PlanEvent, ...]:
    """The one write path of a manual stage: every «Этап КБ» row of the change (with the stage before it and the
    reason) and one audit event, in ONE transaction — a failure on any row writes nothing. Journal rows only: no
    stock, shipments, write-offs, production tasks or kit requests are touched (the stage cascade closes those in
    one shared transaction with the rows, through `write_stage_change`)."""
    if not _stage_change_needed(command):
        return ()
    with uow:
        events = write_stage_change(command, uow)
        uow.commit()
    return events


def _stage_change_needed(command: ChangeStageCommand) -> bool:
    """Refuses a bad change before any transaction is opened; False — nothing to write."""
    plan = command.plan
    if plan.error:
        raise ProductionValidationError(plan.error)
    reason = str(command.reason or "").strip()
    if plan.reason_required and not reason:
        raise ProductionValidationError("Этап назад — напишите причину.")
    project = normalize_project_number(command.project)
    if not project:
        raise ProductionValidationError("Номер проекта обязателен")
    for change in plan.changes:
        if change.value not in (*KIT_STAGES, PLAN_STAGE_AUTO) or change.from_stage not in KIT_STAGES:
            raise ProductionValidationError("Неизвестный этап.")
    return bool(plan.changes or str(command.reopen_from or "").strip())


def write_stage_change(command: ChangeStageCommand, uow: PlanUnitOfWork) -> tuple[PlanEvent, ...]:
    """The rows and the audit event of `change_stage` inside the caller's open unit of work; the caller commits."""
    if not _stage_change_needed(command):
        return ()
    plan = command.plan
    reason = str(command.reason or "").strip()
    project = normalize_project_number(command.project)
    reopen_from = str(command.reopen_from or "").strip()
    notes = dict(command.notes)
    closed = {kit_label(item): list(labels) for item, labels in command.closed if labels}
    if command.expected_sequence is not None and uow.plan_events.last_sequence(project) != command.expected_sequence:
        raise StageChangeConflict("Этапы проекта только что изменили — обновите и повторите.")
    events: list[PlanEvent] = []
    if reopen_from:
        text = f"{reopen_from} → в работе" + (f" · причина: {reason}" if reason else "")
        events.append(uow.plan_events.add(PlanEvent(
            project=project, occurred_at=command.now, who=command.actor.name, kind=PLAN_KINDS["status"],
            text=text, value=PLAN_STAGE_AUTO, reason=reason,
            from_value=reopen_from if reopen_from in PROJECT_TERMINALS else "",
        )))
    for change in plan.changes:
        events.append(uow.plan_events.add(PlanEvent(
            project=project, occurred_at=command.now, who=command.actor.name, kind=PLAN_KINDS["stage"],
            text=stage_change_text(change, reason) + notes.get(change.item_index, ""),
            item_index=change.item_index, value=change.value, from_value=change.from_stage, reason=reason,
        )))
    skipped = {stage for change in plan.changes for stage in change.skipped}
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=events[0].id, event_type="production.stage_changed",
        actor=command.actor, occurred_at=command.now,
        details={
            "project": project, "value": plan.value, "direction": plan.direction,
            "kbs": [kit_label(change.item_index) for change in plan.changes],
            "changes": [
                f"{kit_label(change.item_index)}: {change.from_stage} → {change.to_stage}" for change in plan.changes
            ],
            "skipped": [stage for stage in KIT_STAGES if stage in skipped],
            "reason": reason,
            **({"reopened": reopen_from} if reopen_from else {}),
            **({"closed": closed} if closed else {}),
        },
    ))
    return tuple(events)


def delete_project_plan_events(command: DeleteProjectPlanEventsCommand, uow: PlanUnitOfWork) -> tuple[PlanEvent, ...]:
    """The project's journal rows go (blockers, deadlines, comments, «Проверка ТК», «Отметка плана»); other projects'
    rows stay — the project is matched exactly after `normalize_project_number`, so «719К.2» never touches
    «719К.21». One audit event with the number of removed rows. Returns the rows (removed, or to be removed on a
    dry run) oldest first."""
    canon = normalize_project_number(command.project)
    if not canon:
        raise ProductionValidationError("Номер проекта обязателен")
    with uow:
        events = tuple(uow.plan_events.of_project(canon))
        if command.apply and events:
            uow.plan_events.delete_project(canon)
            uow.audit.add(AuditEvent(
                id=uuid4(), aggregate_id=command.actor.id, event_type="production.plan_events_deleted",
                actor=command.actor, occurred_at=command.now,
                details={"project": canon, "events": len(events), "reason": "project_deleted"},
            ))
            uow.commit()
    return events

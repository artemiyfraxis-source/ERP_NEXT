"""Portal inbox, the module tasks of a project, plus restricted audit and outbox diagnostics."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.modules.production.plan import normalize_project_number
from app.shared.clock import moscow_now
from app.shared.http_auth import (
    Principal,
    authenticated_principal,
    operations_view_principal,
    production_edit_principal,
    production_view_principal,
    projects_view_principal,
    require_section,
    shipping_manager_principal,
    shipping_view_principal,
)

from .application import (
    CloseProjectTaskCommand,
    MarkReadCommand,
    UnitOfWork,
    close_project_task,
    delete_notification,
    delete_notifications,
    mark_notifications_read,
)
from .domain import ProjectTask, ProjectTaskError, task_module_label


router = APIRouter(tags=["operations"])


class ReadPayload(BaseModel):
    ids: list[UUID] = Field(default_factory=list, max_length=100)


def operations_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "operations_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def _notification(item) -> dict:
    return {
        "id": str(item.id),
        "created_at": item.created_at.isoformat(),
        "type": item.kind,
        "reference": item.reference,
        "title": item.title,
        "body": item.body,
        "read": item.read_at is not None,
    }


@router.get("/api/notifications")
def notifications(
    principal: Annotated[Principal, Depends(authenticated_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
    limit: Annotated[int, Query(ge=1, le=100)] = 40,
) -> dict:
    with uow_factory() as uow:
        rows = uow.notifications.list_for(principal.email, limit)
    return {
        "notifications": [_notification(item) for item in rows],
        "unread": sum(item.read_at is None for item in rows),
    }


@router.get("/api/notifications/unread")
def unread(
    principal: Annotated[Principal, Depends(authenticated_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
) -> dict:
    """The bell on every screen (PortalApp.html paintBell_): only the count, polled once a minute."""
    with uow_factory() as uow:
        return {"unread": uow.notifications.unread_count(principal.email)}


@router.post("/api/notifications/read")
def mark_read(
    payload: ReadPayload,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
) -> dict:
    unread = mark_notifications_read(
        MarkReadCommand(principal.email, tuple(payload.ids), now),
        uow_factory(),
    )
    return {"unread": unread}


@router.delete("/api/notifications/{notification_id}")
def delete_one(
    notification_id: UUID,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
) -> dict:
    """Remove one item of the caller's own inbox; someone else's id answers 404 like a missing one."""
    try:
        unread = delete_notification(principal.email, notification_id, uow_factory())
    except KeyError:
        raise HTTPException(status_code=404, detail="Уведомление не найдено") from None
    return {"unread": unread}


@router.delete("/api/notifications")
def delete_many(
    principal: Annotated[Principal, Depends(authenticated_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
    scope: Annotated[Literal["all", "read"] | None, Query()] = None,
    ids: Annotated[list[UUID] | None, Query(alias="id", max_length=100)] = None,
) -> dict:
    """Clear the caller's own inbox: `?scope=all` («Удалить все»), `?scope=read` («Удалить прочитанные»)
    or `?id=…&id=…` (the selected rows). Exactly one of the two forms; other users' items are never touched."""
    if (scope is None) == (not ids):
        raise HTTPException(status_code=422, detail="Укажите либо scope, либо список id")
    deleted, unread = delete_notifications(
        principal.email, ids=tuple(ids or ()), only_read=scope == "read", uow=uow_factory(),
    )
    return {"deleted": deleted, "unread": unread}


@router.get("/api/operations/audit")
def audit(
    _principal: Annotated[Principal, Depends(operations_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
    event_type: Annotated[str, Query(max_length=128)] = "",
    aggregate_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    with uow_factory() as uow:
        rows = uow.operations.audit(
            event_type=event_type.strip(),
            aggregate_id=aggregate_id,
            limit=limit,
        )
    return {"events": [{
        "id": str(row["id"]),
        "aggregate_id": str(row["aggregate_id"]),
        "event_type": row["event_type"],
        "actor": row["actor"],
        "department": row["department"],
        "occurred_at": row["occurred_at"].isoformat(),
        "details": row["details"],
    } for row in rows]}


@router.get("/api/operations/outbox")
def outbox(
    _principal: Annotated[Principal, Depends(operations_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        health = uow.operations.outbox_health()
    return {
        **{key: health[key] for key in ("total", "published", "pending", "dead")},
        "failures": [{
            "id": str(row["id"]),
            "topic": row["topic"],
            "attempts": row["attempts"],
            "error": row["error"],
            "dead": row["dead"],
        } for row in health["failures"]],
    }


# ── Задачи модулям по проекту (ARCH-TRACK-3, C2) ─────────────────────────
# Читает задачу тот, кому открыт её раздел или «Проекты» (карточка проекта показывает всё по проекту); закрывает —
# тот, кто правит раздел задачи: те же ворота, что у самого действия (запуск задания, оформление отправки).
# Полный доступ проходит сквозь список ролей в `require_section`.
_TASK_GATES = {
    "production": (production_view_principal, production_edit_principal),
    "shipping": (shipping_view_principal, shipping_manager_principal),
}


def _task_gates(module: str):
    return _TASK_GATES.get(module) or (require_section(module, "view"), require_section(module, "edit"))


def _passes(gate, principal: Principal) -> bool:
    try:
        gate(principal)
    except HTTPException:
        return False
    return True


def _project_task(task: ProjectTask, can_close: bool) -> dict:
    return {
        "id": str(task.id),
        "project": task.project,
        "item_index": task.item_index,
        "module": task.module,
        "module_label": task_module_label(task.module),
        "kind": task.kind,
        "title": task.title,
        "status": task.status.value,
        "status_label": task.status_label,
        "created_at": task.created_at.isoformat(),
        "closed_at": task.closed_at.isoformat() if task.closed_at is not None else None,
        "closed_by": task.closed_by,
        "source_type": task.source_type,
        "can_close": can_close and task.is_open,
    }


@router.get("/api/project-tasks")
def project_tasks(
    principal: Annotated[Principal, Depends(authenticated_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
    project: Annotated[str, Query(max_length=255)] = "",
    module: Annotated[str, Query(max_length=64)] = "",
    status: Annotated[str, Query(pattern="^(open|done|cancelled)?$")] = "",
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> dict:
    """Задачи, которые арбитр поставил модулям: фильтр по проекту (как в треке — «708K.4» = «708К.4»), разделу, статусу."""
    with uow_factory() as uow:
        tasks = uow.project_tasks.list(
            project=normalize_project_number(project) if project.strip() else "",
            module=module.strip(), status=status, limit=limit,
        )
    whole_project = _passes(projects_view_principal, principal)
    rights: dict[str, tuple[bool, bool]] = {}
    for task in tasks:
        if task.module not in rights:
            view, edit = _task_gates(task.module)
            rights[task.module] = (_passes(view, principal), _passes(edit, principal))
    return {"tasks": [
        _project_task(task, rights[task.module][1]) for task in tasks if whole_project or rights[task.module][0]
    ]}


@router.post("/api/project-tasks/{task_id}/close")
def close_task(
    task_id: UUID,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
) -> dict:
    """Закрыть задачу руками — то, что арбитр не замечает сам. Право — правка раздела задачи."""
    with uow_factory() as uow:
        task = uow.project_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    _task_gates(task.module)[1](principal)
    try:
        closed = close_project_task(CloseProjectTaskCommand(task_id, principal.actor, now), uow_factory())
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Задача не найдена") from error
    except ProjectTaskError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"task": _project_task(closed, True)}

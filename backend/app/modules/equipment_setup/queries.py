"""Role/site-scoped reads. No current-techcard fallback or client-supplied employee scope."""

from app.shared.http_auth import Principal

from .application import SetupPermissionError


def _reader(principal, context):
    if not isinstance(principal, Principal) or principal.service:
        raise SetupPermissionError("Нужна личная сессия сотрудника")
    person = context.person(principal.actor.id)
    if person is None or not person.active or person.actor.id != principal.actor.id:
        raise SetupPermissionError("Сотрудник неактивен или не найден")
    return person


def _may_read(principal, person, task, context):
    if principal.full_access:
        return
    machine = context.machine(task.machine_id)
    if machine is not None and machine.site in {"ЦАР", "ЖУК"} and person.site == machine.site and (person.site_master or task.technician_id == person.actor.id):
        return
    raise SetupPermissionError("Задание наладки недоступно сотруднику")


def read_task(task_id, uow, *, principal):
    with uow:
        person = _reader(principal, uow.context)
        task = uow.tasks.get(task_id)
        if task is None:
            raise KeyError("Задание наладки не найдено")
        _may_read(principal, person, task, uow.context)
        return task


def list_tasks(uow, *, principal, limit=100, offset=0):
    if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
        raise ValueError("Неверные параметры страницы")
    with uow:
        person = _reader(principal, uow.context)
        scope = {}
        if not principal.full_access:
            scope["machine_ids"] = uow.context.machine_ids_for_site(person.site)
            if not person.site_master:
                scope["technician_id"] = person.actor.id
        return uow.tasks.list(**scope, limit=limit, offset=offset)

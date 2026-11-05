"""Role/site-scoped reads. No current-techcard fallback or client-supplied employee scope."""

from dataclasses import dataclass
from uuid import UUID

from app.shared.http_auth import Principal

from .application import SITE_UNKNOWN, SITES, SetupOperationUnavailable, SetupPermissionError


@dataclass(frozen=True)
class MachineChoice:
    id: str
    name: str
    model: str
    site: str


@dataclass(frozen=True)
class ExecutorChoice:
    id: UUID
    name: str
    site: str


@dataclass(frozen=True)
class SetupViewer:
    actor_id: UUID
    site: str
    can_assign: bool
    writable: bool


def read_viewer(uow, *, principal):
    with uow:
        person = _reader(principal, uow.context)
        return SetupViewer(person.actor.id, person.site,
            principal.full_access or (person.site_master and person.site in {"ЦАР", "ЖУК"}), principal.viewer is None)


def _page(limit, offset):
    if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
        raise ValueError("Неверные параметры страницы")


def _machine_scope(machine_id, principal, person, context):
    machine = context.machine(machine_id)
    if machine is None or machine.site not in {"ЦАР", "ЖУК"}:
        raise KeyError("Физический станок с известной площадкой не найден")
    if not principal.full_access and person.site != machine.site:
        raise SetupPermissionError("Станок другой площадки недоступен")
    return machine


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
    _page(limit, offset)
    with uow:
        person = _reader(principal, uow.context)
        scope = {}
        if not principal.full_access:
            scope["machine_ids"] = uow.context.machine_ids_for_site(person.site)
            if not person.site_master:
                scope["technician_id"] = person.actor.id
        return uow.tasks.list(**scope, limit=limit, offset=offset)


def list_machines(uow, *, principal, limit=100, offset=0, operation=None):
    _page(limit, offset)
    with uow:
        person = _reader(principal, uow.context)
        site = None if principal.full_access else person.site
        eligible = None
        if operation is not None:
            resolved = uow.context.issued_operation(operation)
            if resolved is None:
                raise KeyError("Точная выданная операция не найдена")
            if resolved.site:
                if not principal.full_access and resolved.site != person.site:
                    raise SetupPermissionError("Операция другой площадки недоступна")
                site = resolved.site
            elif not principal.full_access and person.site not in SITES:
                # The task fixes no site yet; the chosen machine will, but only among the viewer's own site.
                raise SetupOperationUnavailable(SITE_UNKNOWN)
            eligible = resolved.eligible_machine_ids
        return uow.context.machine_choices(site=site, eligible_ids=eligible, limit=limit, offset=offset)


def list_executors(machine_id, uow, *, principal, limit=100, offset=0):
    _page(limit, offset)
    with uow:
        person = _reader(principal, uow.context)
        machine = _machine_scope(machine_id, principal, person, uow.context)
        # No new technician role is inferred. These are active same-site identities,
        # not a promise of free time; command-time resource guards still apply.
        own_id = None if principal.full_access or person.site_master else person.actor.id
        return uow.context.executor_choices(site=machine.site, own_id=own_id, limit=limit, offset=offset)


def list_recipes(machine_id, uow, *, principal, limit=100, offset=0):
    _page(limit, offset)
    with uow:
        person = _reader(principal, uow.context)
        _machine_scope(machine_id, principal, person, uow.context)
        return uow.recipes.list_versions(machine_id, limit=limit, offset=offset)


def read_recipe(recipe_id, version, uow, *, principal):
    with uow:
        person = _reader(principal, uow.context)
        recipe = uow.recipes.get(recipe_id, version)
        if recipe is None:
            raise KeyError("Версия рецепта не найдена")
        _machine_scope(recipe.machine_id, principal, person, uow.context)
        return recipe


def list_trials(task_id, uow, *, principal, limit=100, offset=0):
    _page(limit, offset)
    with uow:
        person = _reader(principal, uow.context)
        task = uow.tasks.get(task_id)
        if task is None:
            raise KeyError("Задание наладки не найдено")
        _may_read(principal, person, task, uow.context)
        return uow.recipes.trials(task.id, limit=limit, offset=offset)

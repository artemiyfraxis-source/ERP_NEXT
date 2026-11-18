"""Publish manually recorded recipe versions and append trials without a QC verdict."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.http_auth import Principal

from .application import NOT_A_MACHINE, ConcurrentSetupUpdate, SetupPermissionError, _actor, _authorize, _site
from .domain import SetupStatus, SetupValidationError
from .recipes import (OB019_BY_KEY, ManualRecipeVersion, RecipeField, SetupTrialMeasurement, is_ob019, ob019_recipe_fields,
                      plain, plc_value)


@dataclass(frozen=True)
class PublishRecipeCommand:
    recipe_id: UUID
    expected_version: int
    machine_id: UUID
    title: str
    geometry: tuple[RecipeField, ...]
    panel_settings: tuple[RecipeField, ...]
    source: str


@dataclass(frozen=True)
class RecordTrialCommand:
    id: UUID
    task_id: UUID
    expected_version: int
    label: str
    value: Decimal
    unit: str


def _version(value):
    if type(value) is not int or value < 0:
        raise SetupValidationError("Неверная ожидаемая версия")


def publish_recipe(command, uow, *, principal, now: datetime) -> ManualRecipeVersion:
    _version(command.expected_version)
    with uow:
        person = _actor(principal, uow.context)
        machine = uow.context.machine(command.machine_id)
        if machine is None or machine.id != command.machine_id or machine.site not in {"ЦАР", "ЖУК"}:
            raise SetupValidationError("Физический станок с известной площадкой не найден")
        if not machine.operational:
            raise SetupValidationError(NOT_A_MACHINE)
        if not (principal.full_access or (person.site_master and person.site == machine.site)):
            raise SetupPermissionError("Публиковать рецепт может мастер площадки станка или администратор")
        recipe = ManualRecipeVersion(command.recipe_id, command.expected_version + 1, command.machine_id,
            command.title, command.geometry, command.panel_settings, command.source, person.actor.id, now)
        existing = uow.recipes.get(recipe.recipe_id, recipe.version)
        if existing is not None:
            # Immutable version PUT semantics: a retry returns that version, never creates another.
            if replace(recipe, created_at=existing.created_at) != existing:
                raise ConcurrentSetupUpdate("Версия рецепта уже существует с другими данными")
            return existing
        uow.recipes.append(recipe, expected_version=command.expected_version)
        uow.audit.add(AuditEvent(uuid4(), recipe.recipe_id, "equipment_setup.recipe_published", person.actor, now,
            {"version": str(recipe.version), "machine_id": str(recipe.machine_id),
             "inventory_number": machine.inventory_number, "source": recipe.source}))
        uow.commit()
        return recipe


def record_trial(command, uow, *, principal, now: datetime) -> SetupTrialMeasurement:
    _version(command.expected_version)
    with uow:
        person = _actor(principal, uow.context)
        task = uow.tasks.get(command.task_id)
        if task is None:
            raise SetupValidationError("Задание наладки не найдено")
        site = _site(uow.context, task)
        _authorize(principal, person, site, manager=False, task=task)
        trial = SetupTrialMeasurement(command.id, task.id, task.recipe.recipe_id, task.recipe.version,
            command.label, command.value, command.unit, person.actor.id, now)
        existing = uow.recipes.measurement(trial.id)
        if existing is not None:
            if replace(trial, measured_at=existing.measured_at) != existing:
                raise ConcurrentSetupUpdate("Измерение с этим номером уже записано иначе")
            return existing
        if task.status is not SetupStatus.IN_PROGRESS:
            raise SetupValidationError("Пробное измерение записывают во время наладки")
        if task.version != command.expected_version:
            raise ConcurrentSetupUpdate("Задание наладки уже изменилось — обновите данные")
        if now < task.updated_at:
            raise SetupValidationError("Измерение не может быть раньше изменения задания")
        uow.tasks.save(replace(task, version=task.version + 1, updated_at=now), expected_version=task.version)
        uow.recipes.add_measurement(trial)
        uow.audit.add(AuditEvent(uuid4(), task.id, "equipment_setup.trial_recorded", person.actor, now,
            {"measurement_id": str(trial.id), "recipe_id": str(trial.recipe_id), "recipe_version": str(trial.recipe_version)}))
        uow.commit()
        return trial


# --- ОБ.019 setup panel (slice 2): corrections are journaled, «Сохранить версию» publishes the machine's values ---

@dataclass(frozen=True)
class Ob019CorrectionCommand:
    machine_id: UUID
    parameter: str
    actual: Decimal
    proposed: Decimal | None
    live: Decimal | None
    wire: str
    context: str
    quantity: int | None = None


@dataclass(frozen=True)
class SaveOb019VersionCommand:
    recipe_id: UUID
    expected_version: int
    machine_id: UUID
    wire: str
    values: dict
    etk_strip: dict
    context: str


def _shown(value: Decimal | None) -> str:
    return "" if value is None else plain(value)


def _ob019_machine(uow, machine_id):
    machine = uow.context.machine(machine_id)
    if machine is None or machine.id != machine_id or machine.site not in {"ЦАР", "ЖУК"}:
        raise SetupValidationError("Физический станок с известной площадкой не найден")
    if not is_ob019(machine.inventory_number):
        raise SetupValidationError("Панель наладки есть только у ОБ.019")
    return machine


def ob019_machine(machine_id, uow, *, principal):
    """Read gate of the panel: a person of the machine's site (or full access) sees it."""
    if not isinstance(principal, Principal) or principal.service:
        raise SetupPermissionError("Нужна личная сессия сотрудника")
    with uow:
        person = uow.context.person(principal.actor.id)
        if person is None or not person.active or person.actor.id != principal.actor.id:
            raise SetupPermissionError("Сотрудник неактивен или не найден")
        machine = _ob019_machine(uow, machine_id)
        if not principal.full_access and person.site != machine.site:
            raise SetupPermissionError("Станок другой площадки недоступен")
        return machine


def record_ob019_correction(command: Ob019CorrectionCommand, uow, *, principal, now: datetime) -> dict:
    """One operator correction of one panel value, journaled before it is used (owner 18.11: ERP learns the setup)."""
    parameter = OB019_BY_KEY.get(command.parameter)
    if parameter is None:
        raise SetupValidationError("Неизвестный параметр ОБ.019")
    actual = plc_value(parameter.key, command.actual)
    if command.quantity is not None and (type(command.quantity) is not int or not 1 <= command.quantity <= 1_000_000):
        raise SetupValidationError("Количество: целое число от 1")
    wire, context = " ".join(str(command.wire or "").split()), " ".join(str(command.context or "").split())
    if len(wire) > 200 or len(context) > 300:
        raise SetupValidationError("Слишком длинное описание наладки")
    with uow:
        person = _actor(principal, uow.context)
        machine = _ob019_machine(uow, command.machine_id)
        if not (principal.full_access or person.site == machine.site):
            raise SetupPermissionError("Правки записывает сотрудник площадки станка")
        details = {"parameter": parameter.key, "label": parameter.label, "register": parameter.register,
                   "proposed": _shown(command.proposed), "live": _shown(command.live), "actual": plain(actual),
                   "wire": wire, "context": context, "quantity": "" if command.quantity is None else str(command.quantity),
                   "inventory_number": machine.inventory_number}
        event = AuditEvent(uuid4(), machine.id, "equipment_setup.ob019_correction", person.actor, now, details)
        uow.audit.add(event)
        uow.commit()
        return {"id": str(event.id), "occurred_at": now.isoformat(), "actor": person.actor.name, **details}


def save_ob019_version(command: SaveOb019VersionCommand, uow, *, principal, now: datetime) -> ManualRecipeVersion:
    """The values the machine actually runs with → the next version of this machine's recipe for that wire."""
    geometry, panel = ob019_recipe_fields(command.wire, command.values, command.etk_strip)
    context = " ".join(str(command.context or "").split())
    with uow:
        machine = _ob019_machine(uow, command.machine_id)
    source = f"Наладка {machine.inventory_number}: фактические значения станка" + (f" · {context}" if context else "")
    return publish_recipe(PublishRecipeCommand(command.recipe_id, command.expected_version, command.machine_id,
        f"{geometry[0].value} · {machine.inventory_number}", geometry, panel, source[:2000]),
        uow, principal=principal, now=now)

"""Publish manually recorded recipe versions and append trials without a QC verdict."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.shared.events import AuditEvent

from .application import ConcurrentSetupUpdate, SetupPermissionError, _actor, _authorize, _site
from .domain import SetupStatus, SetupValidationError
from .recipes import ManualRecipeVersion, RecipeField, SetupTrialMeasurement


@dataclass(frozen=True)
class PublishRecipeCommand:
    recipe_id: UUID
    expected_version: int
    machine_id: str
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
            {"version": str(recipe.version), "machine_id": recipe.machine_id, "source": recipe.source}))
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

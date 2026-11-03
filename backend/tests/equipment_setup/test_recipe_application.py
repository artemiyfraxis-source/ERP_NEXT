from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.equipment_setup.application import ConcurrentSetupUpdate, SetupAction, SetupPermissionError
from app.modules.equipment_setup.domain import SetupValidationError
from app.modules.equipment_setup.recipe_application import PublishRecipeCommand, RecordTrialCommand, publish_recipe, record_trial
from app.modules.equipment_setup.recipes import RecipeField
from tests.equipment_setup.test_application import NOW, assign, change, env, principal


class Recipes:
    def __init__(self):
        self.versions = {}
        self.measurements = {}

    def get(self, identifier, version):
        return self.versions.get((identifier, version))

    def append(self, recipe, *, expected_version):
        latest = max((version for identifier, version in self.versions if identifier == recipe.recipe_id), default=0)
        if latest != expected_version:
            raise ConcurrentSetupUpdate("version")
        self.versions[(recipe.recipe_id, recipe.version)] = recipe

    def measurement(self, identifier):
        return self.measurements.get(identifier)

    def add_measurement(self, measurement):
        assert measurement.id not in self.measurements
        self.measurements[measurement.id] = measurement


@pytest.fixture
def recipe_env(env):
    env[0].recipes = Recipes()
    env[0].PORTS = (*env[0].PORTS, "recipes")
    return env


def publish_command():
    return PublishRecipeCommand(uuid4(), 0, "ОБ.027.2", "Ручная настройка", (), (RecipeField("Панель", "12"),), "Записано с панели")


def test_publish_immutable_version_and_repeat_without_side_effects(recipe_env):
    uow, manager, *_ = recipe_env
    command = publish_command()
    first = publish_recipe(command, uow, principal=principal(manager), now=NOW)
    again = publish_recipe(command, uow, principal=principal(manager), now=NOW + timedelta(minutes=1))
    assert again == first
    assert len(uow.audit.items) == uow.commits == 1
    second = publish_recipe(replace(command, expected_version=1, panel_settings=(RecipeField("Панель", "13"),)),
        uow, principal=principal(manager), now=NOW + timedelta(minutes=2))
    assert second.version == 2
    assert uow.recipes.get(first.recipe_id, 1).panel_settings[0].value == "12"


def test_recipe_replay_checks_current_manager_rights(recipe_env):
    uow, manager, *_ = recipe_env
    command = publish_command()
    publish_recipe(command, uow, principal=principal(manager), now=NOW)
    uow.context.people[manager.actor.id] = replace(manager, site_master=False)
    with pytest.raises(SetupPermissionError):
        publish_recipe(command, uow, principal=principal(manager), now=NOW)


@pytest.mark.parametrize("index", [2, 3])
def test_other_site_master_and_technician_cannot_publish(recipe_env, index):
    with pytest.raises(SetupPermissionError):
        publish_recipe(publish_command(), recipe_env[0], principal=principal(recipe_env[index]), now=NOW)
    assert not recipe_env[0].recipes.versions


def trial_command(task):
    return RecordTrialCommand(uuid4(), task.id, task.version, "Диаметр", Decimal("2.0100"), "мм")


def test_trial_is_pinned_to_task_and_cas_idempotent_without_qc_verdict(recipe_env):
    uow = recipe_env[0]
    task = change(recipe_env, assign(recipe_env), SetupAction.START)
    command = trial_command(task)
    trial = record_trial(command, uow, principal=principal(recipe_env[2]), now=NOW + timedelta(minutes=3))
    assert (trial.recipe_id, trial.recipe_version) == (task.recipe.recipe_id, task.recipe.version)
    assert str(trial.value) == "2.0100"
    assert uow.tasks.get(task.id).version == task.version + 1
    before = uow.commits
    again = record_trial(command, uow, principal=principal(recipe_env[2]), now=NOW + timedelta(minutes=4))
    assert again == trial
    assert uow.commits == before
    assert not hasattr(trial, "passed")


def test_trial_cas_conflict_or_not_active_has_no_measurement(recipe_env):
    uow = recipe_env[0]
    task = assign(recipe_env)
    with pytest.raises(SetupValidationError):
        record_trial(trial_command(task), uow, principal=principal(recipe_env[2]), now=NOW + timedelta(minutes=3))
    task = change(recipe_env, task, SetupAction.START)
    with pytest.raises(ConcurrentSetupUpdate):
        record_trial(replace(trial_command(task), expected_version=1), uow, principal=principal(recipe_env[2]), now=NOW + timedelta(minutes=3))
    assert not uow.recipes.measurements


def test_trial_failed_commit_rolls_back_task_measurement_and_audit(recipe_env):
    uow = recipe_env[0]
    task = change(recipe_env, assign(recipe_env), SetupAction.START)
    before = len(uow.audit.items)
    uow.fail_commit = True
    with pytest.raises(RuntimeError):
        record_trial(trial_command(task), uow, principal=principal(recipe_env[2]), now=NOW + timedelta(minutes=3))
    assert uow.tasks.get(task.id) == task
    assert not uow.recipes.measurements
    assert len(uow.audit.items) == before

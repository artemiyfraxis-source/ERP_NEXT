"""Immutable manually recorded recipes: geometry is not a machine command or a trial verdict."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from .domain import SetupValidationError, _instant, _integer, _required, _uuid


@dataclass(frozen=True)
class RecipeField:
    label: str
    value: str
    unit: str = ""

    def __post_init__(self):
        for key, maximum in (("label", 160), ("value", 500), ("unit", 40)):
            value = getattr(self, key)
            if not isinstance(value, str) or len(value) > maximum:
                raise SetupValidationError("Поле рецепта слишком длинное или имеет неверный тип")
            object.__setattr__(self, key, value.strip())
        if not self.label or not self.value:
            raise SetupValidationError("Укажите название и значение поля рецепта")


def fields(values: tuple[RecipeField, ...]) -> None:
    if not isinstance(values, tuple) or len(values) > 100 or any(not isinstance(item, RecipeField) for item in values):
        raise SetupValidationError("Допустимо не более 100 полей в разделе рецепта")
    labels = [item.label.casefold() for item in values]
    if len(set(labels)) != len(labels):
        raise SetupValidationError("Названия полей раздела не должны повторяться")


@dataclass(frozen=True)
class ManualRecipeVersion:
    recipe_id: UUID
    version: int
    machine_id: str
    title: str
    geometry: tuple[RecipeField, ...]
    panel_settings: tuple[RecipeField, ...]
    source: str
    created_by: UUID
    created_at: datetime

    def __post_init__(self):
        _uuid(self.recipe_id, "Рецепт")
        _uuid(self.created_by, "Автор")
        _integer(self.version, "Версия рецепта")
        for key, maximum in (("machine_id", 128), ("title", 200), ("source", 2000)):
            value = _required(getattr(self, key), key)
            if len(value) > maximum:
                raise SetupValidationError("Слишком длинное поле рецепта")
            object.__setattr__(self, key, value)
        object.__setattr__(self, "created_at", _instant(self.created_at))
        fields(self.geometry)
        fields(self.panel_settings)
        if not self.panel_settings:
            raise SetupValidationError("Нужны фактически записанные настройки панели станка")


@dataclass(frozen=True)
class SetupTrialMeasurement:
    id: UUID
    task_id: UUID
    recipe_id: UUID
    recipe_version: int
    label: str
    value: Decimal
    unit: str
    measured_by: UUID
    measured_at: datetime

    def __post_init__(self):
        for key in ("id", "task_id", "recipe_id", "measured_by"):
            _uuid(getattr(self, key), key)
        _integer(self.recipe_version, "Версия рецепта")
        for key in ("label", "unit"):
            value = _required(getattr(self, key), key)
            if len(value) > 160:
                raise SetupValidationError("Слишком длинное поле измерения")
            object.__setattr__(self, key, value)
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise SetupValidationError("Нужно конечное числовое значение измерения")
        object.__setattr__(self, "measured_at", _instant(self.measured_at))

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
    machine_id: UUID  # the БД.ОБ unit (tech_operation_records.id)
    title: str
    geometry: tuple[RecipeField, ...]
    panel_settings: tuple[RecipeField, ...]
    source: str
    created_by: UUID
    created_at: datetime

    def __post_init__(self):
        _uuid(self.recipe_id, "Рецепт")
        _uuid(self.created_by, "Автор")
        _uuid(self.machine_id, "Физический станок")
        _integer(self.version, "Версия рецепта")
        for key, maximum in (("title", 200), ("source", 2000)):
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


# --- ОБ.019 (Eastontech EW-3185+L): proposal from the last recorded recipe, else the ETK nominal (owner 18.11.2026) ---
# Keys are the live snapshot keys of «Станки» (production/machines.py decode_ob019 `wire`); registers hold ×0,01 мм.

OB019_INVENTORY = "ОБ.019"
WIRE_LABEL = "Провод"
PLC_STEP = Decimal("0.01")
PLC_MAX = Decimal("655.35")  # u16 ×0,01 of D34–D43; D32 (length) is u32
LENGTH_MAX = Decimal("99999.99")
DIAMETER_OVER_CONDUCTOR = Decimal("0.10")  # cut diameter without a recipe: Ø жилы + 0,10 мм (owner 19.11.2026)
HALF_PULL = Decimal("0.5")  # «полустягивание»: the slug moves half the strip length and stays on the end (owner 19.11.2026)
FULL_PULL_EXTRA = Decimal("2.00")  # «стягивание»: the pull runs past the strip so the slug comes off (owner 19.11.2026: «должно быть больше»)


@dataclass(frozen=True)
class PanelParameter:
    key: str
    label: str
    register: str
    in_recipe: bool = True
    unit: str = "мм"


OB019_PARAMETERS = (
    PanelParameter("length_mm", "Длина провода", "D32", in_recipe=False),
    PanelParameter("strip_front_mm", "Зачистка начала", "D34"),
    PanelParameter("pull_front_mm", "Стягивание начала", "D36"),
    PanelParameter("strip_back_mm", "Зачистка конца", "D38"),
    PanelParameter("pull_back_mm", "Стягивание конца", "D40"),
    PanelParameter("diameter_mm", "Диаметр реза", "D42"),
    PanelParameter("knife_back_mm", "Отвод ножа", "D43"),
)
OB019_BY_KEY = {item.key: item for item in OB019_PARAMETERS}
RECIPE_PARAMETERS = tuple(item for item in OB019_PARAMETERS if item.in_recipe)
# The ETK strip a recipe was recorded against: a later job with another ETK strip keeps the machine's offset.
ETK_STRIP_LABELS = {"strip_front_mm": "Зачистка начала по ЭТК", "strip_back_mm": "Зачистка конца по ЭТК"}
PULL_SIDES = {"pull_front_mm": ("strip_front_mm", "partial_front"), "pull_back_mm": ("strip_back_mm", "partial_back")}


def is_ob019(inventory_number: str) -> bool:
    return "".join(str(inventory_number or "").split()).upper() == OB019_INVENTORY


def decimal_value(value) -> Decimal | None:
    """«5», «5,00», «0.80» → Decimal; empty, a range («3–4») or text → None."""
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    text = str(value if value is not None else "").strip().replace(",", ".").replace(" ", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except ArithmeticError:
        return None
    return number if number.is_finite() else None


def plc_value(key: str, value) -> Decimal:
    """A value the panel can hold: ≥ 0, step 0,01 мм, within the register."""
    parameter = OB019_BY_KEY.get(key)
    if parameter is None:
        raise SetupValidationError("Неизвестный параметр ОБ.019")
    number = decimal_value(value)
    if number is None:
        raise SetupValidationError(f"{parameter.label}: нужно число")
    if number < 0 or number > (LENGTH_MAX if key == "length_mm" else PLC_MAX):
        raise SetupValidationError(f"{parameter.label}: значение вне диапазона станка")
    if number != number.quantize(PLC_STEP):
        raise SetupValidationError(f"{parameter.label}: точность станка — 0,01 мм")
    return number.quantize(PLC_STEP)


def plain(value: Decimal) -> str:
    """0,01 мм as the panel shows it, with a decimal comma: «5,00»."""
    return format(value.quantize(PLC_STEP), "f").replace(".", ",")


def wire_key(text: str) -> str:
    return " ".join(str(text or "").split()).casefold().replace(".", ",")


def recipe_value(recipe: ManualRecipeVersion, label: str, *, geometry: bool = False) -> str | None:
    values = recipe.geometry if geometry else recipe.panel_settings
    return next((item.value for item in values if item.label.casefold() == label.casefold()), None)


def recipe_wire(recipe: ManualRecipeVersion) -> str:
    return recipe_value(recipe, WIRE_LABEL, geometry=True) or ""


def latest_for_wire(versions, wire: str) -> ManualRecipeVersion | None:
    """The newest recorded version for this machine and wire: what the operator last confirmed on the machine."""
    key = wire_key(wire)
    if not key:
        return None
    found = [item for item in versions if wire_key(recipe_wire(item)) == key]
    return max(found, key=lambda item: (item.created_at, item.version), default=None)


@dataclass(frozen=True)
class EtkNominal:
    """What the issued ETK (cutting group) or the wire reference gives; None — not given."""

    wire: str = ""
    length_mm: Decimal | None = None
    strip_front_mm: Decimal | None = None
    strip_back_mm: Decimal | None = None
    partial_front: bool = False
    partial_back: bool = False
    conductor_mm: Decimal | None = None


@dataclass(frozen=True)
class ProposedValue:
    key: str
    value: Decimal | None
    source: str


NO_DATA = "Нет данных"


def _signed(value: Decimal) -> str:
    return ("+" if value > 0 else "") + plain(value)


def ob019_proposal(recipe: ManualRecipeVersion | None, etk: EtkNominal | None,
                   length: Decimal | None = None) -> tuple[ProposedValue, ...]:
    """Start values: the recipe (shifted when the job's ETK strip differs from the one it was recorded against), else
    the ETK nominal; the length is the job's only, never a recipe's."""
    etk = etk or EtkNominal()
    saved = {item.key: decimal_value(recipe_value(recipe, item.label)) for item in RECIPE_PARAMETERS} if recipe else {}
    tag = f"Рецепт, версия {recipe.version}" if recipe else ""
    result: dict[str, ProposedValue] = {}
    job_length = length if length is not None else etk.length_mm
    result["length_mm"] = ProposedValue("length_mm", job_length,
        "Задание" if length is not None else ("ЭТК" if job_length is not None else NO_DATA))
    for key in ETK_STRIP_LABELS:
        nominal, base = getattr(etk, key), saved.get(key)
        if base is not None:
            recorded = decimal_value(recipe_value(recipe, ETK_STRIP_LABELS[key], geometry=True))
            if nominal is not None and recorded is not None and nominal != recorded:
                shift = base - recorded
                result[key] = ProposedValue(key, max(nominal + shift, Decimal(0)),
                    f"ЭТК, поправка рецепта {_signed(shift)}" if shift else "ЭТК")
            else:
                result[key] = ProposedValue(key, base, tag)
        else:
            result[key] = ProposedValue(key, nominal, "ЭТК" if nominal is not None else NO_DATA)
    for key, (strip, partial) in PULL_SIDES.items():
        base, proposed_strip = saved.get(key), result[strip].value
        if base is not None:
            shift = proposed_strip - saved[strip] if proposed_strip is not None and saved.get(strip) is not None else Decimal(0)
            result[key] = ProposedValue(key, max(base + shift, Decimal(0)), tag)
        elif proposed_strip is None:
            result[key] = ProposedValue(key, None, NO_DATA)
        elif proposed_strip == 0:
            result[key] = ProposedValue(key, Decimal(0), "ЭТК: без зачистки")
        elif getattr(etk, partial):
            # Three pull-off modes (owner 19.11.2026): full = the strip length, half = half of it, none = 0.
            result[key] = ProposedValue(key, (proposed_strip * HALF_PULL).quantize(PLC_STEP), "ЭТК: полустягивание")
        else:
            result[key] = ProposedValue(key, min(proposed_strip + FULL_PULL_EXTRA, PLC_MAX), "ЭТК: стягивание (зачистка + 2,00)")
    if saved.get("diameter_mm") is not None:
        result["diameter_mm"] = ProposedValue("diameter_mm", saved["diameter_mm"], tag)
    elif etk.conductor_mm is not None:
        # The knife closes just above the conductor (owner 19.11.2026): Ø жилы of the reference + 0,10 мм.
        diameter = (etk.conductor_mm + DIAMETER_OVER_CONDUCTOR).quantize(PLC_STEP)
        result["diameter_mm"] = ProposedValue("diameter_mm", diameter, "Ø жилы + 0,10")
    else:
        result["diameter_mm"] = ProposedValue("diameter_mm", None, NO_DATA)
    back = saved.get("knife_back_mm")
    result["knife_back_mm"] = ProposedValue("knife_back_mm", back, tag if back is not None else NO_DATA)
    return tuple(result[item.key] for item in OB019_PARAMETERS)


def recipe_holds(recipe: ManualRecipeVersion | None, key: str) -> bool:
    """The recipe has a panel value for this parameter: that value is what the machine held when it cut right, so it is
    final — the machine's offset is not added on top of it (owner 19.11.2026)."""
    parameter = OB019_BY_KEY.get(key)
    return recipe is not None and parameter is not None and parameter.in_recipe \
        and decimal_value(recipe_value(recipe, parameter.label)) is not None


def ob019_recipe_fields(wire: str, values: dict[str, object],
                        etk_strip: dict[str, Decimal | None]) -> tuple[tuple[RecipeField, ...], tuple[RecipeField, ...]]:
    """Geometry (wire + the ETK strip it was recorded against) and the panel values as the machine holds them."""
    name = " ".join(str(wire or "").split())
    if not name:
        raise SetupValidationError("Укажите провод")
    missing = [item.label for item in RECIPE_PARAMETERS if values.get(item.key) in (None, "")]
    if missing:
        raise SetupValidationError("Не заданы: " + ", ".join(missing))
    geometry = [RecipeField(WIRE_LABEL, name)]
    for key, label in ETK_STRIP_LABELS.items():
        nominal = etk_strip.get(key)
        if nominal is not None:
            geometry.append(RecipeField(label, plain(plc_value(key, nominal)), "мм"))
    panel = tuple(RecipeField(item.label, plain(plc_value(item.key, values[item.key])), item.unit)
                  for item in RECIPE_PARAMETERS)
    return tuple(geometry), panel

"""Setup model of БД.ОБ (owner 18–19.11.2026, screen «Станки + наладка» for the setup technician Слава) — pure rules.

- A **setup model** per equipment model: does it need setup (нет / простая / полная) and which parameters, each with the
  source its value comes from (ЭТК, контакт, провод, оснастка, ПЛК, вручную) and, for a bridged machine, the PLC
  register it is written to. A unit may override the class or single parameters (`effective`).
- The **current configuration** of a unit: what is installed and set now (tooling, blades, wire/contact, the reference
  it was set from, key values, who/when) — the base of «что перенастраивать» for the next request (`changeover`).
- **References (эталоны)** per unit + wire/contact, append-only versions: Слава's proven values.
- **Machine corrections**: an offset per parameter (optionally only for one wire), stored with the unit and applied
  on every write to the machine (`corrected`); the ETK stays unchanged. ETK corrections go through the ETK revision
  path with the reason «правка наладчика: …» (`etk_reason`).

Persistence lives in `setup_model_infrastructure.py`; the read API and the seed in `app/workflows/equipment_setup_model.py`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from .domain import SetupValidationError, _instant, _integer, _required, _uuid

REQUIRES_SETUP = ("none", "simple", "full")
REQUIRES_LABELS = {"none": "нет", "simple": "простая", "full": "полная"}
SOURCES = ("etk", "contact", "wire", "tooling", "plc", "manual")
SOURCE_LABELS = {"etk": "ЭТК", "contact": "контакт", "wire": "провод", "tooling": "оснастка", "plc": "ПЛК",
                 "manual": "вручную"}
CORRECTION_CASES = ("machine", "etk", "both")
CORRECTION_LABELS = {"machine": "станок режет не так — поправка станка", "etk": "размер в ЭТК неверный — правка ЭТК",
                     "both": "и станок, и ЭТК"}
ETK_REASON_PREFIX = "правка наладчика"
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
OFFSET_LIMIT = Decimal("1000")


def _text(value: str, label: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise SetupValidationError(f"{label}: нужен текст")
    text = " ".join(value.split())
    if required and not text:
        raise SetupValidationError(f"{label} обязательно")
    if len(text) > maximum:
        raise SetupValidationError(f"{label}: не длиннее {maximum} символов")
    return text


def _key(value: str) -> str:
    if not isinstance(value, str) or not KEY_PATTERN.match(value):
        raise SetupValidationError(f"Ключ параметра «{value}»: латиница, цифры и «_», с буквы")
    return value


def material_key(text: str) -> str:
    """«ПВАМ  0.35» and «пвам 0,35» are one wire (the same normalisation as the ОБ.019 recipes)."""
    return " ".join(str(text or "").split()).casefold().replace(".", ",")


def decimal_of(value: object) -> Decimal | None:
    """«5», «5,00», «0.80» → Decimal; empty or text → None."""
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    text = str(value if value is not None else "").strip().replace(",", ".").replace(" ", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


@dataclass(frozen=True)
class SetupParameter:
    key: str
    label: str
    unit: str = ""
    source: str = "manual"
    source_field: str = ""
    register: str = ""
    position: int = 0

    def __post_init__(self):
        _key(self.key)
        object.__setattr__(self, "label", _text(self.label, "Название параметра", 160, required=True))
        object.__setattr__(self, "unit", _text(self.unit, "Единица", 20))
        if self.source not in SOURCES:
            raise SetupValidationError(f"Источник «{self.source}» не из списка: {', '.join(SOURCE_LABELS.values())}")
        object.__setattr__(self, "source_field", _text(self.source_field, "Поле источника", 200))
        object.__setattr__(self, "register", _text(self.register, "Регистр", 40))
        _integer(self.position, "Порядок", minimum=0)

    @property
    def numeric(self) -> bool:
        """A parameter with a unit holds a number; one without (аппликатор, ножи, программа) — a name."""
        return bool(self.unit)


def _parameters(values: Iterable[SetupParameter]) -> tuple[SetupParameter, ...]:
    result = tuple(values)
    if any(not isinstance(item, SetupParameter) for item in result):
        raise SetupValidationError("Параметры наладки заданы неверно")
    keys = [item.key for item in result]
    if len(set(keys)) != len(keys):
        raise SetupValidationError("Ключи параметров наладки не должны повторяться")
    if len(result) > 60:
        raise SetupValidationError("Не более 60 параметров наладки")
    return tuple(sorted(result, key=lambda item: (item.position, item.key)))


@dataclass(frozen=True)
class SetupModel:
    """The setup model of an equipment model (or the effective one of a unit)."""
    requires_setup: str
    parameters: tuple[SetupParameter, ...] = ()
    reason: str = ""

    def __post_init__(self):
        if self.requires_setup not in REQUIRES_SETUP:
            raise SetupValidationError("Наладка: нет, простая или полная")
        object.__setattr__(self, "parameters", _parameters(self.parameters))
        object.__setattr__(self, "reason", _text(self.reason, "Причина", 500))
        if self.requires_setup == "none" and self.parameters:
            raise SetupValidationError("Оборудование без наладки не имеет параметров наладки")
        if self.requires_setup != "none" and not self.parameters:
            raise SetupValidationError("Для наладки нужен хотя бы один параметр")

    @property
    def label(self) -> str:
        return REQUIRES_LABELS[self.requires_setup]

    def parameter(self, key: str) -> SetupParameter | None:
        return next((item for item in self.parameters if item.key == key), None)


@dataclass(frozen=True)
class ParameterOverride:
    """A unit's change of one model parameter: `removed` hides it, otherwise `parameter` replaces or adds it."""
    key: str
    removed: bool = False
    parameter: SetupParameter | None = None

    def __post_init__(self):
        _key(self.key)
        if self.removed == (self.parameter is not None):
            raise SetupValidationError("Замена параметра: либо убрать, либо задать новый")
        if self.parameter is not None and self.parameter.key != self.key:
            raise SetupValidationError("Замена параметра должна иметь тот же ключ")


@dataclass(frozen=True)
class UnitOverride:
    requires_setup: str | None = None
    parameters: tuple[ParameterOverride, ...] = ()
    reason: str = ""

    def __post_init__(self):
        if self.requires_setup is not None and self.requires_setup not in REQUIRES_SETUP:
            raise SetupValidationError("Наладка: нет, простая или полная")
        keys = [item.key for item in self.parameters]
        if len(set(keys)) != len(keys):
            raise SetupValidationError("Ключи замен параметров не должны повторяться")
        object.__setattr__(self, "reason", _text(self.reason, "Причина", 500))


def effective(model: SetupModel | None, override: UnitOverride | None) -> SetupModel | None:
    """The unit's setup model: its own class and parameter changes over the model's; None — not classified yet."""
    if override is None:
        return model
    if model is None and override.requires_setup is None:
        return None
    requires = override.requires_setup or model.requires_setup
    parameters = {item.key: item for item in (model.parameters if model else ())}
    for change in override.parameters:
        if change.removed:
            parameters.pop(change.key, None)
        else:
            parameters[change.key] = change.parameter
    reason = override.reason or (model.reason if model else "")
    return SetupModel(requires, tuple(parameters.values()) if requires != "none" else (), reason)


def setup_values(model: SetupModel, values: Mapping[str, object]) -> dict[str, str]:
    """Values keyed by the model's parameters: numbers for parameters with a unit (normalised, «5,00» → «5.00»), a
    name otherwise. An unknown key or a non-number is refused — a reference / configuration never holds stray keys."""
    if not isinstance(values, Mapping):
        raise SetupValidationError("Значения наладки заданы неверно")
    result: dict[str, str] = {}
    for key, raw in values.items():
        parameter = model.parameter(key)
        if parameter is None:
            raise SetupValidationError(f"Параметра «{key}» нет в модели наладки")
        if parameter.numeric:
            number = decimal_of(raw)
            if number is None:
                raise SetupValidationError(f"{parameter.label}: нужно число")
            result[key] = str(number)
        else:
            result[key] = _text(str(raw if raw is not None else ""), parameter.label, 200, required=True)
    return dict(sorted(result.items()))


@dataclass(frozen=True)
class MachineCorrection:
    """Case (a): the ETK size is right, the machine cuts it differently — add `offset` to the written value. An empty
    `wire` applies to every wire; a wire's own correction wins over it."""
    parameter_key: str
    offset: Decimal
    wire: str = ""
    reason: str = ""

    def __post_init__(self):
        _key(self.parameter_key)
        if not isinstance(self.offset, Decimal) or not self.offset.is_finite() or abs(self.offset) >= OFFSET_LIMIT:
            raise SetupValidationError("Поправка станка: число по модулю меньше 1000")
        object.__setattr__(self, "wire", _text(self.wire, "Провод", 200))
        object.__setattr__(self, "reason", _text(self.reason, "Причина поправки", 500))


def corrected(values: Mapping[str, Decimal], corrections: Iterable[MachineCorrection], wire: str = "") -> dict[str, Decimal]:
    """What is written to the machine: each value plus its correction (the wire's own, else the general one). A
    negative result is refused: the correction or the value is wrong, never a silently clamped write."""
    wire_id = material_key(wire)
    general: dict[str, Decimal] = {}
    own: dict[str, Decimal] = {}
    for item in corrections:
        if not item.wire:
            general[item.parameter_key] = item.offset
        elif material_key(item.wire) == wire_id:
            own[item.parameter_key] = item.offset
    result = {}
    for key, value in values.items():
        written = value + own.get(key, general.get(key, Decimal(0)))
        if written < 0:
            raise SetupValidationError(f"«{key}»: с поправкой станка значение меньше нуля ({written})")
        result[key] = written
    return result


@dataclass(frozen=True)
class SetupReference:
    """An эталон: Слава's proven values for one unit + wire and/or contact; versions are append-only."""
    id: UUID
    unit_id: UUID
    wire: str
    contact: str
    version: int
    values: Mapping[str, str]
    tooling_unit_id: UUID | None
    note: str
    created_by_name: str
    created_at: datetime

    def __post_init__(self):
        _uuid(self.id, "Эталон")
        _uuid(self.unit_id, "Станок")
        if self.tooling_unit_id is not None:
            _uuid(self.tooling_unit_id, "Оснастка")
        object.__setattr__(self, "wire", _text(self.wire, "Провод", 200))
        object.__setattr__(self, "contact", _text(self.contact, "Контакт", 200))
        if not self.wire and not self.contact:
            raise SetupValidationError("Эталон: укажите провод или контакт")
        _integer(self.version, "Версия эталона")
        object.__setattr__(self, "note", _text(self.note, "Примечание", 500))
        object.__setattr__(self, "created_by_name", _required(self.created_by_name, "Кто сохранил"))
        object.__setattr__(self, "created_at", _instant(self.created_at))


def next_reference_version(versions: Iterable[int]) -> int:
    return max(versions, default=0) + 1


@dataclass(frozen=True)
class CurrentConfiguration:
    """What a unit holds now (one row per unit, replaced as a whole under its version)."""
    unit_id: UUID
    values: Mapping[str, str]
    tooling_unit_id: UUID | None = None
    blades: str = ""
    wire: str = ""
    contact: str = ""
    reference_id: UUID | None = None
    set_by_name: str = ""
    set_at: datetime | None = None
    version: int = 1


@dataclass(frozen=True)
class ChangeoverStep:
    key: str
    label: str
    now: str
    needed: str


def changeover(model: SetupModel, current: CurrentConfiguration | None, needed: Mapping[str, str], *,
               tooling: str = "", wire: str = "", contact: str = "", tooling_now: str = "") -> list[ChangeoverStep]:
    """«Что перенастраивать»: every parameter, tooling, wire or contact whose needed value differs from what the unit
    holds now (numbers compare by value: «5» = «5.00»). No current configuration — everything needed is a step."""
    held = dict(current.values) if current else {}
    steps = []
    if tooling and tooling != tooling_now:
        steps.append(ChangeoverStep("tooling", "Оснастка", tooling_now, tooling))
    for key, label, want, have in (("wire", "Провод", wire, current.wire if current else ""),
                                   ("contact", "Контакт", contact, current.contact if current else "")):
        if want and material_key(want) != material_key(have):
            steps.append(ChangeoverStep(key, label, have, want))
    for parameter in model.parameters:
        want = needed.get(parameter.key)
        if want is None:
            continue
        have = held.get(parameter.key, "")
        same = (decimal_of(want) == decimal_of(have) and decimal_of(want) is not None) if parameter.numeric \
            else material_key(want) == material_key(have)
        if not same:
            steps.append(ChangeoverStep(parameter.key, parameter.label, have, str(want)))
    return steps


def etk_reason(text: str) -> str:
    """Case (b): Слава corrects the ETK himself (owner 19.11) — a normal ETK revision whose reason says so."""
    detail = _text(text, "Что исправлено в ЭТК", 400, required=True)
    return f"{ETK_REASON_PREFIX}: {detail}"


def correction_case(value: str) -> str:
    if value not in CORRECTION_CASES:
        raise SetupValidationError("Правка: станок, ЭТК или и то и другое")
    return value


# --- CSV of the owner-reviewed proposal (docs/data/equipment-setup-model-proposal-*.csv) -------------------------------

CSV_REQUIRES = {label: code for code, label in REQUIRES_LABELS.items()}
CSV_SOURCES = {label: code for code, label in SOURCE_LABELS.items()}
CSV_COLUMNS = ("inv", "requires_setup", "param_key", "param_name", "unit", "source", "source_field", "register",
               "reason", "reviewed")
REVIEWED = {"да", "yes", "1", "+"}


@dataclass(frozen=True)
class ProposalUnit:
    inventory: str
    model: SetupModel


def read_proposal(rows: Iterable[Mapping[str, str]]) -> tuple[list[ProposalUnit], list[str]]:
    """Rows of the CSV (one per unit × parameter) → the reviewed units and the inventory numbers skipped as not reviewed.
    A unit is taken only when every one of its rows says «да» in `reviewed`; a contradiction refuses the whole file."""
    grouped: dict[str, list[Mapping[str, str]]] = {}
    for row in rows:
        missing = [name for name in CSV_COLUMNS if name not in row]
        if missing:
            raise SetupValidationError(f"В CSV нет колонок: {', '.join(missing)}")
        inventory = "".join(str(row["inv"] or "").split()).upper()
        if not inventory:
            raise SetupValidationError("Строка CSV без инвентарного номера")
        grouped.setdefault(inventory, []).append(row)
    units, skipped = [], []
    for inventory, items in grouped.items():
        if not all(str(item["reviewed"] or "").strip().casefold() in REVIEWED for item in items):
            skipped.append(inventory)
            continue
        classes = {str(item["requires_setup"]).strip() for item in items}
        reasons = {str(item["reason"]).strip() for item in items}
        if len(classes) != 1 or len(reasons) != 1:
            raise SetupValidationError(f"{inventory}: разные «требует наладки» или причины в строках одного ОБ")
        label = classes.pop()
        if label not in CSV_REQUIRES:
            raise SetupValidationError(f"{inventory}: «{label}» — нужно нет, простая или полная")
        parameters = []
        for position, item in enumerate(row for row in items if str(row["param_key"]).strip()):
            source = str(item["source"]).strip()
            if source not in CSV_SOURCES:
                raise SetupValidationError(f"{inventory}: источник «{source}» не из списка")
            try:
                parameters.append(SetupParameter(str(item["param_key"]).strip(), str(item["param_name"]),
                                                 str(item["unit"] or ""), CSV_SOURCES[source],
                                                 str(item["source_field"] or ""), str(item["register"] or ""), position))
            except SetupValidationError as error:
                raise SetupValidationError(f"{inventory}: {error}") from error
        try:
            units.append(ProposalUnit(inventory, SetupModel(CSV_REQUIRES[label], tuple(parameters), reasons.pop())))
        except SetupValidationError as error:
            raise SetupValidationError(f"{inventory}: {error}") from error
    return units, sorted(skipped)


def model_and_overrides(units: Mapping[str, SetupModel]) -> tuple[SetupModel, dict[str, UnitOverride]]:
    """The units of one equipment model → the model's setup model (the most common one, ties to the first inventory
    number) and an override for every unit that differs from it."""
    if not units:
        raise SetupValidationError("Нет единиц модели")
    ordered = sorted(units.items())
    counts: dict[SetupModel, int] = {}
    for _inventory, model in ordered:
        counts[model] = counts.get(model, 0) + 1
    base = max(counts, key=lambda model: counts[model])  # dicts keep insertion order: ties go to the first
    overrides = {}
    for inventory, model in ordered:
        if model == base:
            continue
        changes = [ParameterOverride(item.key, removed=True) for item in base.parameters if model.parameter(item.key) is None]
        changes += [ParameterOverride(item.key, parameter=item) for item in model.parameters if base.parameter(item.key) != item]
        overrides[inventory] = UnitOverride(model.requires_setup if model.requires_setup != base.requires_setup else None,
                                            tuple(changes), model.reason if model.reason != base.reason else "")
    return base, overrides

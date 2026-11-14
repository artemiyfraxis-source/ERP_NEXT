"""Станки планирования по ID модели оборудования (владелец 05.11.2026: «связка должна быть не через название, а через
идентификатор»; модели и роли — справочник БД.ОБ, миграция 0171).

Станок операции ЭТК — модель оборудования её строки «Оборудование»: явный equipment_model_id строки, иначе единица
БД.ОБ с этим инвентарным номером, иначе (номер модели «ОБ.027.X», «ОБ.027» или расчётная единица «.X» / «(R)») —
единственная операционная модель с физическими единицами под этим номером. Ключ станка — ID модели: переименование
модели не меняет ни ключ, ни единицы, ни план; подпись — «ОБ.027 <наименование модели сейчас>».
Единиц станка — физических единиц модели (номер «ОБ.027» или «ОБ.027.k», без «.X» и артикулов «(R)»).
Обеспечивающее оборудование (компрессор, чиллер…) станком операции не бывает и единиц не даёт: такую строку
«Оборудование» станок пропускает и берёт следующую.
Под одним номером несколько операционных моделей и строка ЭТК не называет ни одну (ни ID, ни номер единицы, ни артикул
в названии) — станок по номеру модели «ОБ.027», единиц — всех этих моделей вместе (так считалось до ID; такие строки
показывает отчёт несвязанного оборудования справочника). Номера нет в БД.ОБ — станок по номеру, единица одна.
«Машина» БД.ОП (запасная группа доски мастера) — по связи ячейки с моделью (equipment_operation_links), не по тексту."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.modules.references.domain import EQUIPMENT_TABS, TechOperationTab

SUPPORTING = "supporting"
# Типы БД.ОБ, что показываются станками в загрузке и без работы (ручной инструмент, матрицы — нет).
MACHINE_TYPES = frozenset({"оборудование"})
MACHINE_COLUMN = "Машина"
_INVENTORY = re.compile(r"([ОO]Б)\s*\.\s*(\d+)", re.IGNORECASE)
_UNIT = re.compile(r"^ОБ\.(\d+)(?:\.(\d+|[A-ZА-Я]))?$")


def _text(value: object) -> str:
    return " ".join(str(value if value is not None else "").split())


def inventory_prefix(value: object) -> str:
    """«ОБ.027.2» / «ОБ.027.X» / «ОБ.027» → «ОБ.027»: номер модели станка."""
    match = _INVENTORY.search(str(value or ""))
    return f"ОБ.{match.group(2)}" if match else ""


def _number(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper().replace("OБ", "ОБ")


def _fields(record: Any) -> dict[str, str]:
    return {" ".join(str(key).split()).casefold(): _text(value) for key, value in (getattr(record, "fields", None) or {}).items()}


def _pick(fields: Mapping[str, str], *keys: str) -> str:
    return next((fields[key] for key in keys if fields.get(key)), "")


def _site(value: str) -> str:
    site = value.upper()
    return site if site in {"ЦАР", "ЖУК"} else ""


def _order(code: str) -> tuple[int, str]:
    digits = re.sub(r"\D", "", code)
    return (int(digits) if digits else 10**9, code)


@dataclass(frozen=True)
class Unit:
    number: str  # «ОБ.027.1»
    prefix: str  # «ОБ.027»
    site: str  # ЦАР | ЖУК | ""
    machine_type: bool  # Тип «Оборудование»
    physical: bool  # не расчётная «.X» / «(R)»
    name: str


@dataclass(frozen=True)
class Model:
    key: str  # ID модели; у записи без модели (старый вход) — её номер модели
    name: str
    article: str = ""
    display: str = ""  # «Для базы»: артикул/наименование
    supporting: bool = False
    units: tuple[Unit, ...] = ()

    @property
    def physical(self) -> tuple[Unit, ...]:
        return tuple(unit for unit in self.units if unit.physical)

    @property
    def code(self) -> str:
        own = sorted(self.physical or self.units, key=lambda unit: (_order(unit.prefix), unit.number))
        return own[0].prefix if own else ""


@dataclass(frozen=True)
class Machine:
    """Станок: key — ID модели (или номер модели, если модель не определена); code — «ОБ.027»; name — наименование."""
    key: str
    code: str
    name: str
    model_id: str = ""

    @property
    def label(self) -> str:
        return f"{self.code} {self.name}".strip() or self.key


class EquipmentCatalog(Mapping[str, int]):
    """Модели БД.ОБ для планирования. Как Mapping — единиц станка по ключу (модели с физическими единицами и номера
    моделей, под которыми несколько операционных моделей)."""

    def __init__(self, models: Iterable[Model] = (), operation_models: Mapping[str, str] | None = None):
        self.models: dict[str, Model] = {model.key: model for model in models}
        self.operation_models = dict(operation_models or {})  # код БД.ОП → ID модели «Машины»
        self._numbers: dict[str, str] = {}
        families: dict[str, list[Model]] = {}
        for model in self.models.values():
            for unit in model.physical:
                self._numbers.setdefault(unit.number, model.key)
            for prefix in dict.fromkeys(unit.prefix for unit in model.units):
                families.setdefault(prefix, []).append(model)
        self._families = families
        self._units: dict[str, int] = {
            model.key: len({unit.number for unit in model.physical})
            for model in self.models.values() if not model.supporting and model.physical
        }
        for prefix in families:
            pool = self.pool(prefix)
            if len(pool) > 1:
                self._units[prefix] = sum(self._units.get(model.key, 0) for model in pool)

    # --- Mapping: единиц по ключу станка ---
    def __getitem__(self, key: str) -> int:
        return self._units[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._units)

    def __len__(self) -> int:
        return len(self._units)

    def pool(self, prefix: str) -> list[Model]:
        """Операционные модели с физическими единицами под номером модели."""
        return [model for model in self._families.get(prefix, ()) if not model.supporting and model.physical]

    def _machine(self, model: Model, prefix: str, tool_name: str) -> Machine:
        return Machine(model.key, model.code or prefix, model.name or tool_name, model.key if model.key in self.models else "")

    def machine(self, tool: Mapping[str, Any]) -> Machine | None:
        """Станок строки «Оборудование» оснастки ЭТК; None — не станок (нет номера ОБ, обеспечивающее)."""
        name = _text(tool.get("name"))
        explicit = self.models.get(_text(tool.get("equipment_model_id")))
        prefix = inventory_prefix(tool.get("inventory_number"))
        if explicit is not None:
            return None if explicit.supporting else self._machine(explicit, prefix, name)
        if not prefix:
            return None
        exact = self.models.get(self._numbers.get(_number(tool.get("inventory_number")), ""))
        if exact is not None:
            return None if exact.supporting else self._machine(exact, prefix, name)
        family = self._families.get(prefix, [])
        pool = self.pool(prefix)
        if not pool and family and all(model.supporting for model in family):
            return None
        if len(pool) == 1:
            return self._machine(pool[0], prefix, name)
        named = [model for model in pool if model.article and model.article.casefold() in name.casefold()]
        if len(named) == 1:
            return self._machine(named[0], prefix, name)
        return Machine(prefix, prefix, name)

    def operation_machine(self, code: str) -> Machine | None:
        """«Машина» БД.ОП по коду операции — модель по связи ячейки."""
        model = self.models.get(self.operation_models.get(_text(code), ""))
        if model is None or model.supporting:
            return None
        return Machine(model.key, model.code, model.name or model.display, model.key)

    def codes(self) -> dict[str, str]:
        """Номер модели для подписи и порядка строк по ключу станка."""
        return {**{prefix: prefix for prefix in self._families}, **{key: model.code for key, model in self.models.items() if model.code}}

    def idle_key(self, model: Model) -> str:
        """Ключ строки станка без работы: номер модели, если под ним несколько операционных моделей, иначе модель."""
        prefix = model.code
        return prefix if len(self.pool(prefix)) > 1 else model.key

    def site_units(self) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], str]]:
        """Единиц станка на площадке (по ключу станка) и подписи станков площадки без работы (только «Оборудование»)."""
        numbers: dict[tuple[str, str], set[str]] = {}
        labels: dict[tuple[str, str], str] = {}
        for model in sorted(self.models.values(), key=lambda item: (_order(item.code), item.key)):
            if model.supporting:
                continue
            key = self.idle_key(model)
            pooled = key != model.key
            for unit in model.physical:
                for machine in dict.fromkeys((model.key, key)):
                    numbers.setdefault((unit.site, machine), set()).add(unit.number)
                if unit.machine_type and (unit.site, key) not in labels:
                    labels[(unit.site, key)] = f"{unit.prefix if pooled else model.code} {model.name or unit.name}".strip()
        return {key: len(values) for key, values in numbers.items()}, labels


EMPTY = EquipmentCatalog()


def build_catalog(
    units: Sequence[Any], models: Sequence[Mapping[str, Any]] = (), operation_links: Mapping[str, str] | None = None,
) -> EquipmentCatalog:
    """Каталог из записей БД.ОБ (с equipment_model_id), моделей справочника (id, name, article, role, display) и
    связей «Машины» БД.ОП (код операции → ID модели). Запись без модели — своя группа по номеру модели."""
    about = {_text(item.get("id")): item for item in models}
    grouped: dict[str, list[Unit]] = {}
    for record in units:
        fields = _fields(record)
        number = _number(_pick(fields, "инв. номер", "inventorynumber"))
        match = _UNIT.match(number)
        if not match:
            continue
        article = _pick(fields, "артикул")
        name = _pick(fields, "наменование", "наименование") or _text(getattr(record, "display_text", ""))
        unit = Unit(
            number, f"ОБ.{match.group(1)}", _site(_pick(fields, "расположение")),
            _pick(fields, "тип", "obtype").casefold() in MACHINE_TYPES,
            not (match.group(2) or "").isalpha() and not article.upper().endswith("(R)") and int(match.group(1)) > 0
            and (match.group(2) or "1") != "0",
            name,
        )
        model_id = getattr(record, "equipment_model_id", None)
        grouped.setdefault(str(model_id) if model_id is not None else unit.prefix, []).append(unit)
    result = []
    for key, own in grouped.items():
        item = about.get(key, {})
        result.append(Model(
            key, _text(item.get("name")) if item else "", _text(item.get("article")), _text(item.get("display")),
            _text(item.get("role")) == SUPPORTING, tuple(own),
        ))
    return EquipmentCatalog(result, operation_links)


def load_catalog(factory: Callable[[], Any] | None, *, operations: bool = False) -> EquipmentCatalog:
    """Каталог из справочника (UoW references): БД.ОБ, модели и — для доски — связи «Машины» БД.ОП."""
    if factory is None:
        return EMPTY
    with factory() as references:
        units = references.tech_operations.list(tab=EQUIPMENT_TABS, query="", limit=100_000)
        equipment = getattr(references, "equipment", None)
        models = equipment.models() if equipment is not None else []
        links: dict[str, str] = {}
        if operations and equipment is not None:
            records = references.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=100_000)
            extras = equipment.record_extras(records) if records else {}
            for record in records:
                fields = getattr(record, "fields", None) or {}
                code = _text(fields.get("opNumber") or fields.get("Номер"))
                machine = next((link["model_id"] for link in (extras.get(record.id) or {}).get("equipment_links", [])
                                if link.get("column") == MACHINE_COLUMN), None)
                if code and machine and code not in links:
                    links[code] = str(machine)
    return build_catalog(units, models, links)

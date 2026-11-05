"""Stable equipment identity (owner 05.11.2026: «связка должна быть не через название, а через идентификатор»).

- A БД.ОБ unit is its record: the id never changes — a mirror reload / snapshot updates the matched row in place
  (`SqlAlchemyTechOperationRepository.replace`), an import or an edit saves it by id.
- A unit belongs to an equipment MODEL (`equipment_models`, `tech_operation_records.equipment_model_id`): a new unit
  joins the one model of its name + article + maker (the grouping the screen showed before), else gets a model of its
  own. The model's name / article / maker follow its units once they all agree (the screen renames every unit one by
  one), and the model has a role: «операционное» does operations, «обеспечивающее» (compressor, chiller…) serves
  machines and is never the machine of an operation.
- A БД.ОП cell «Машина» / «Инструмент» / «Апликаторы / модули» links to the model id (`equipment_operation_links`,
  one link per piece of the cell split by «;» / a line break); the cell text is a display copy: when the model is
  renamed and the old text names it no more, the text is rewritten to the model's «Для базы».
- «Для инструмента» of a matrix links to the tool unit, «Обслуживает» of a supporting unit to the units it serves
  (`equipment_unit_links`); «Для инструмента» stays the display copy of the tool's inventory number.
- A link is made only on an unambiguous match; an edited cell that matches nothing (or several) loses its link; a
  reload whose text matches nothing keeps the link and restores the display copy. Unresolved pieces are in
  `links_report` (CLI `equipment_links_report`, GET `/api/references/equipment-links/report`).
- A unit that another unit links to, or the last unit of a model operations link to, is never deleted — neither by
  «Удалить» nor by a reload (`LinkedEquipmentError`).
Migration 0171 holds literal copies of the matching rules for the first fill.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field as dc_field
from typing import Iterable, Mapping, Sequence
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .domain import ReferenceValidationError
from .equipment import ARTICLE_KEY, BASE_KEY, INV_KEY, TOOL_KEY, field, inv_key, name_of, parse_inventory, type_of
from .infrastructure import (
    EQUIPMENT_ROLES, OP_LINK_COLUMNS, EquipmentModelRow, EquipmentOperationLinkRow, EquipmentTemplateToolRow,
    EquipmentUnitLinkRow, TechOperationRecordRow,
)
from .technology import repoint_calc_unit

OB, OP = "ob", "op"
OPERATIONAL, SUPPORTING = EQUIPMENT_ROLES
MAKER_KEY = "Производитель"
TOOL, SERVES = "tool", "serves"
# The words that make a model «обеспечивающее» without doubt (its name or type); everything else is «операционное»
# until the owner confirms (migration 0171 holds a literal copy).
SUPPORTING_WORDS = re.compile(r"компрессор|чиллер|охладител", re.IGNORECASE)
_SEPARATORS = re.compile(r"([;\n])")
# The fields of a unit that its model and the links read: a change of one of them re-runs the links.
_IDENTITY_KEYS = ("Наменование", "Наименование", ARTICLE_KEY, MAKER_KEY, BASE_KEY, "baseValue", INV_KEY, TOOL_KEY,
                  "Тип", "obType")


class LinkedEquipmentError(ReferenceValidationError):
    """A unit something links to would be deleted: the API answers 422 with the list."""


# --- pure rules ---

def norm(text: object) -> str:
    return " ".join(str(text or "").casefold().split())


def ref_key(text: object) -> str:
    """A reference as the reader compares it: case, extra spaces and spaces around «/» do not matter."""
    return re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", str(text or ""))).strip().casefold()


def maker_of(fields: Mapping[str, str]) -> str:
    maker = field(fields, MAKER_KEY)
    return "" if re.fullmatch(r"-+", maker) else maker


def unit_attrs(fields: Mapping[str, str]) -> tuple[str, str, str]:
    return name_of(fields), field(fields, ARTICLE_KEY), maker_of(fields)


def model_key(name: str, article: str, maker: str) -> str | None:
    """One model = the same name + article + maker; a nameless unit is a model of its own (None)."""
    return f"{norm(name)}|{norm(article)}|{norm(maker)}" if norm(name) else None


def role_of(name: str, kind: str) -> str:
    return SUPPORTING if SUPPORTING_WORDS.search(name or "") or SUPPORTING_WORDS.search(kind or "") else OPERATIONAL


def unit_base(fields: Mapping[str, str], display_text: str) -> str:
    """«Для базы» of a unit; a unit imported without sheet columns carries it as its display text."""
    return field(fields, BASE_KEY, "baseValue") or str(display_text or "").strip()


def inventory_order(value: str) -> tuple:
    """Prefix, model, unit («ОБ.79.10» after «ОБ.79.9»); the unnumbered ones last (the screen's `inventoryOrder`)."""
    parsed = parse_inventory(value)
    if parsed is None:
        return (1, "", 0, 0, str(value or ""))
    unit = 0 if not parsed.suffix else int(parsed.suffix) if parsed.suffix.isdigit() else 9999
    return (0, parsed.prefix, parsed.base, unit, "")


def pieces(text: str) -> list[tuple[int, str]]:
    """(index in `split_cell`, stripped text) of every non-empty reference of a cell."""
    return [(index, part.strip()) for index, part in enumerate(split_cell(text)) if not index % 2 and part.strip()]


def split_cell(text: str) -> list[str]:
    return _SEPARATORS.split(str(text or ""))


def _search(display_text: str, fields: Mapping[str, str]) -> str:
    return " ".join(f"{display_text} {' '.join(str(value) for value in fields.values())}".casefold().split())


# --- one pass over the units and models ---

@dataclass
class _State:
    units: list[TechOperationRecordRow]
    models: dict[UUID, EquipmentModelRow]
    by_model: dict[UUID, list[TechOperationRecordRow]] = dc_field(default_factory=dict)
    texts: dict[str, set[UUID]] = dc_field(default_factory=dict)
    inventories: dict[str, list[TechOperationRecordRow]] = dc_field(default_factory=dict)
    families: dict[tuple[str, int], set[UUID]] = dc_field(default_factory=dict)

    @classmethod
    def load(cls, session: Session) -> _State:
        units = list(session.scalars(select(TechOperationRecordRow).where(TechOperationRecordRow.tab == OB)))
        models = {model.id: model for model in session.scalars(select(EquipmentModelRow))}
        state = cls(units, models)
        state.index()
        return state

    def index(self) -> None:
        by_model: dict[UUID, list[TechOperationRecordRow]] = defaultdict(list)
        for unit in self.units:
            if unit.equipment_model_id is not None:
                by_model[unit.equipment_model_id].append(unit)
        self.by_model = {key: sorted(items, key=lambda unit: inventory_order(field(unit.fields or {}, INV_KEY)))
                         for key, items in by_model.items()}
        texts: dict[str, set[UUID]] = defaultdict(set)
        inventories: dict[str, list[TechOperationRecordRow]] = defaultdict(list)
        families: dict[tuple[str, int], set[UUID]] = defaultdict(set)
        for unit in self.units:
            fields = unit.fields or {}
            if unit.equipment_model_id is None:
                continue
            if key := ref_key(unit_base(fields, unit.display_text)):
                texts[key].add(unit.equipment_model_id)
            number = field(fields, INV_KEY)
            if number:
                inventories[inv_key(number)].append(unit)
                parsed = parse_inventory(number)
                if parsed is not None:
                    families[(parsed.prefix, parsed.base)].add(unit.equipment_model_id)
        for model in self.models.values():
            if model.name and (key := ref_key(f"{model.article}/{model.name}")):
                texts[key].add(model.id)
        self.texts, self.inventories, self.families = dict(texts), dict(inventories), dict(families)

    def display(self, model_id: UUID) -> str:
        """The display copy of a model: «Для базы» of its first unit by number, else «артикул/наименование»."""
        units = self.by_model.get(model_id) or []
        if units:
            return unit_base(units[0].fields or {}, units[0].display_text)
        model = self.models.get(model_id)
        return f"{model.article}/{model.name}" if model is not None and model.name else ""

    def candidates(self, piece: str) -> set[UUID]:
        """The models a reference names, by the first form that names any: the text, the text without «(R)», the
        inventory number of a unit, the model number of a calculation stand-in («ОБ.071.X» → units ОБ.071.k)."""
        for found in (self.texts.get(ref_key(piece)), self.texts.get(ref_key(repoint_calc_unit(piece)))):
            if found:
                return set(found)
        units = self.inventories.get(inv_key(piece))
        if units:
            return {unit.equipment_model_id for unit in units}
        parsed = parse_inventory(piece)
        if parsed is not None and (found := self.families.get((parsed.prefix, parsed.base))):
            return set(found)
        return set()

    def operational(self, models: Iterable[UUID]) -> set[UUID]:
        return {item for item in models if item in self.models and self.models[item].role == OPERATIONAL}

    def inventory(self, unit_id: UUID) -> str:
        unit = next((item for item in self.units if item.id == unit_id), None)
        return field(unit.fields or {}, INV_KEY) if unit is not None else ""


# --- the model of a unit ---

class ModelAssigner:
    """Gives a new unit the one model of its name + article + maker, else a model of its own. Built once per write;
    a model it creates is flushed before the unit (the unit's FK)."""

    def __init__(self, session: Session):
        self.session = session
        self._index: dict[str, list[UUID]] | None = None

    def _models(self) -> dict[str, list[UUID]]:
        if self._index is None:
            index: dict[str, list[UUID]] = defaultdict(list)
            with self.session.no_autoflush:
                for model in self.session.scalars(select(EquipmentModelRow)):
                    if key := model_key(model.name, model.article, model.maker):
                        index[key].append(model.id)
            self._index = index
        return self._index

    def model_for(self, fields: Mapping[str, str]) -> UUID:
        name, article, maker = unit_attrs(fields)
        key = model_key(name, article, maker)
        found = self._models().get(key, []) if key else []
        if len(found) == 1:
            return found[0]
        model = EquipmentModelRow(id=uuid4(), name=name, article=article, maker=maker,
                                  role=role_of(name, type_of(fields)), role_confirmed=False)
        self.session.add(model)
        self.session.flush([model])
        if key and not found:
            self._models()[key] = [model.id]
        return model.id


# --- links ---

def reconcile(session: Session, *, operation_ids: Iterable[UUID] | None = None, units: bool = True,
              explicit_cells: Iterable[tuple[UUID, str]] = (), explicit_tools: Iterable[UUID] = (),
              reload: bool = False) -> None:
    """Brings the links in line with the cells after a write (inside its transaction). `operation_ids` — the БД.ОП
    rows to look at (None — every one); `units` — also the models of the units and the tool links; `explicit_*` —
    the cells the write changed (a cell edited to a text that names nothing loses its link). `reload` — a mirror /
    snapshot load: a text that names nothing keeps the link, and a rewritten display copy is no ERP edit."""
    state = _State.load(session)
    if units:
        _follow_models(state)
        state.index()
    _link_operations(session, state, None if operation_ids is None else set(operation_ids), set(explicit_cells),
                     reload)
    if units:
        _link_tools(session, state, set(explicit_tools), reload)


def _follow_models(state: _State) -> None:
    for model_id, units in state.by_model.items():
        model = state.models.get(model_id)
        attrs = {unit_attrs(unit.fields or {}) for unit in units}
        if model is not None and len(attrs) == 1:
            name, article, maker = next(iter(attrs))
            if (model.name, model.article, model.maker) != (name, article, maker):
                model.name, model.article, model.maker = name, article, maker


def _rewrite(row: TechOperationRecordRow, fields: dict[str, str], reload: bool) -> None:
    row.fields = fields
    row.normalized_search = _search(row.display_text, fields)
    if not reload:
        row.edited_in_erp = True


def _link_operations(session: Session, state: _State, operation_ids: set[UUID] | None,
                     explicit: set[tuple[UUID, str]], reload: bool) -> None:
    statement = select(TechOperationRecordRow).where(TechOperationRecordRow.tab == OP)
    link_statement = select(EquipmentOperationLinkRow)
    if operation_ids is not None:
        if not operation_ids:
            return
        statement = statement.where(TechOperationRecordRow.id.in_(operation_ids))
        link_statement = link_statement.where(EquipmentOperationLinkRow.operation_id.in_(operation_ids))
    operations = list(session.scalars(statement))
    links = {(link.operation_id, link.op_column, link.position): link for link in session.scalars(link_statement)}
    for operation in operations:
        fields = dict(operation.fields or {})
        changed = False
        for column in OP_LINK_COLUMNS:
            parts = split_cell(fields.get(column, ""))
            edited = (operation.id, column) in explicit
            for position, (index, piece) in enumerate(pieces(fields.get(column, ""))):
                link = links.pop((operation.id, column, position), None)
                named = state.candidates(piece)
                found = state.operational(named)
                if link is not None and link.model_id in found:
                    continue
                if len(found) == 1:
                    model_id = next(iter(found))
                    if link is None:
                        session.add(EquipmentOperationLinkRow(operation_id=operation.id, op_column=column,
                                                              position=position, model_id=model_id))
                    else:
                        link.model_id = model_id
                    continue
                if link is not None and not edited and link.model_id in state.operational([link.model_id]):
                    shown = state.display(link.model_id)  # the text names it no more: the display copy comes back
                    if shown and shown != piece:
                        parts[index] = parts[index].replace(piece, shown)
                        changed = True
                    continue
                if link is not None:
                    session.delete(link)
            if changed:
                fields[column] = "".join(parts)
        if changed:
            _rewrite(operation, fields, reload)
    for link in links.values():  # pieces the cells hold no more
        session.delete(link)


def _link_tools(session: Session, state: _State, explicit: set[UUID], reload: bool) -> None:
    links = {link.unit_id: link for link in session.scalars(
        select(EquipmentUnitLinkRow).where(EquipmentUnitLinkRow.kind == TOOL))}
    for unit in state.units:
        fields = unit.fields or {}
        text = field(fields, TOOL_KEY)
        link = links.get(unit.id)
        if not text:
            if link is not None:
                session.delete(link)
            continue
        found = {item.id for item in state.inventories.get(inv_key(text), []) if item.id != unit.id}
        if link is not None and link.target_id in found:
            continue
        if len(found) == 1:
            if link is not None:
                session.delete(link)
                session.flush([link])
            session.add(EquipmentUnitLinkRow(unit_id=unit.id, kind=TOOL, target_id=next(iter(found))))
            continue
        if link is not None and unit.id not in explicit and (number := state.inventory(link.target_id)):
            _rewrite(unit, {**fields, TOOL_KEY: number}, reload)  # the tool was renumbered: its number comes back
            continue
        if link is not None:
            session.delete(link)


# --- deleting ---

def linked_units(session: Session, ids: Iterable[UUID]) -> list[str]:
    """Why each unit of `ids` may not go (deleted together with `ids`): another unit links to it, or it is the last
    unit of a model operations / card templates link to."""
    gone = set(ids)
    if not gone:
        return []
    units = {unit.id: unit for unit in session.scalars(select(TechOperationRecordRow).where(
        TechOperationRecordRow.id.in_(gone), TechOperationRecordRow.tab == OB))}
    if not units:
        return []
    reasons: dict[UUID, list[str]] = defaultdict(list)
    for link in session.scalars(select(EquipmentUnitLinkRow).where(EquipmentUnitLinkRow.target_id.in_(units))):
        if link.unit_id in gone:
            continue
        source = session.get(TechOperationRecordRow, link.unit_id)
        number = field(source.fields or {}, INV_KEY) if source is not None else str(link.unit_id)
        reasons[link.target_id].append(
            f"{number} — «Для инструмента»" if link.kind == TOOL else f"{number} — «Обслуживает»")
    models = {unit.equipment_model_id for unit in units.values() if unit.equipment_model_id is not None}
    for model_id in models:
        left = session.scalar(select(func.count()).select_from(TechOperationRecordRow).where(
            TechOperationRecordRow.equipment_model_id == model_id, TechOperationRecordRow.id.not_in(gone))) or 0
        if left:
            continue
        operations = [operation for operation in session.scalars(
            select(TechOperationRecordRow).join(EquipmentOperationLinkRow,
                                                EquipmentOperationLinkRow.operation_id == TechOperationRecordRow.id)
            .where(EquipmentOperationLinkRow.model_id == model_id)) if operation.id not in gone]
        templates = session.scalar(select(func.count()).select_from(EquipmentTemplateToolRow).where(
            EquipmentTemplateToolRow.model_id == model_id)) or 0
        if operations or templates:
            last = max((unit for unit in units.values() if unit.equipment_model_id == model_id),
                       key=lambda unit: inventory_order(field(unit.fields or {}, INV_KEY)))
            named = sorted({field(item.fields or {}, "opNumber", "Номер") or item.display_text for item in operations})
            what = ", ".join([*(f"операция {name}" for name in named[:5]), *(["…"] if len(named) > 5 else []),
                              *([f"шаблонов карт: {templates}"] if templates else [])])
            reasons[last.id].append(f"последняя единица модели — {what}")
    return [f"{field(units[unit_id].fields or {}, INV_KEY) or units[unit_id].display_text}: {'; '.join(why)}"
            for unit_id, why in sorted(reasons.items(), key=lambda item: inventory_order(
                field(units[item[0]].fields or {}, INV_KEY)))]


def check_deletable(session: Session, ids: Iterable[UUID], *, reload: bool = False) -> None:
    reasons = linked_units(session, ids)
    if reasons:
        head = ("Загрузка не записана: она удалила бы оборудование, на которое есть связи" if reload
                else "Не удалено: на оборудование есть связи")
        raise LinkedEquipmentError(f"{head} — {'; '.join(reasons)}. Сначала снимите связи")


def forget(session: Session, ids: Iterable[UUID]) -> None:
    """The links of records about to be deleted (SQLite runs without FK cascades)."""
    gone = list(set(ids))
    if not gone:
        return
    session.execute(delete(EquipmentOperationLinkRow).where(EquipmentOperationLinkRow.operation_id.in_(gone)))
    session.execute(delete(EquipmentUnitLinkRow).where(EquipmentUnitLinkRow.unit_id.in_(gone)))


def prune_models(session: Session) -> int:
    """Models with no unit, no operation and no card template are dropped; one operations still link to stays
    (the report lists it)."""
    session.flush()
    used = select(TechOperationRecordRow.equipment_model_id).where(TechOperationRecordRow.equipment_model_id.is_not(None))
    linked = select(EquipmentOperationLinkRow.model_id)
    templates = select(EquipmentTemplateToolRow.model_id).where(EquipmentTemplateToolRow.model_id.is_not(None))
    empty = list(session.scalars(select(EquipmentModelRow).where(
        EquipmentModelRow.id.not_in(used), EquipmentModelRow.id.not_in(linked), EquipmentModelRow.id.not_in(templates))))
    for model in empty:
        session.delete(model)
    return len(empty)


def check_operation_cells(session: Session, fields: Mapping[str, str], before: Mapping[str, str] | None) -> None:
    """Before a БД.ОП row is written: a cell it changes may not name only supporting equipment (a compressor is no
    machine of an operation)."""
    changed = [column for column in OP_LINK_COLUMNS
               if before is None or str(before.get(column) or "") != str(fields.get(column) or "")]
    if not changed or not any(pieces(fields.get(column, "")) for column in changed):
        return
    with session.no_autoflush:
        state = _State.load(session)
    refused = [f"«{column}»: «{piece}» — обеспечивающее оборудование, машиной операции оно не бывает"
               for column in changed for _index, piece in pieces(fields.get(column, ""))
               if (named := state.candidates(piece)) and not state.operational(named)]
    if refused:
        raise ReferenceValidationError("; ".join(refused))


def after_save(session: Session, row: TechOperationRecordRow, before: Mapping[str, str] | None,
               before_display: str = "") -> None:
    """The links after one record was saved (`before` — its fields before, None for a new record)."""
    fields = row.fields or {}
    if row.tab == OP:
        edited = {(row.id, column) for column in OP_LINK_COLUMNS
                  if before is None or str(before.get(column) or "") != str(fields.get(column) or "")}
        if edited:
            reconcile(session, operation_ids={row.id}, units=False, explicit_cells=edited)
    elif row.tab == OB:
        if before is None or before_display != row.display_text or any(
                str(before.get(key) or "") != str(fields.get(key) or "") for key in _IDENTITY_KEYS):
            tool_edited = before is None or str(before.get(TOOL_KEY) or "") != str(fields.get(TOOL_KEY) or "")
            reconcile(session, explicit_tools={row.id} if tool_edited else ())
            prune_models(session)


# --- reading and the owner's changes (`uow.equipment`) ---

def _model_json(state: _State, model: EquipmentModelRow, operations: int) -> dict:
    units = state.by_model.get(model.id, [])
    return {
        "id": str(model.id), "name": model.name, "article": model.article, "maker": model.maker,
        "role": model.role, "role_confirmed": model.role_confirmed, "display": state.display(model.id),
        "unit_ids": [str(unit.id) for unit in units],
        "inventory_numbers": [field(unit.fields or {}, INV_KEY) for unit in units],
        "operations": operations,
    }


class SqlAlchemyEquipmentRepository:
    def __init__(self, session: Session):
        self.session = session

    def models(self) -> list[dict]:
        state = _State.load(self.session)
        counts = dict(self.session.execute(select(EquipmentOperationLinkRow.model_id, func.count(
            EquipmentOperationLinkRow.operation_id.distinct())).group_by(EquipmentOperationLinkRow.model_id)).all())
        return [_model_json(state, model, counts.get(model.id, 0)) for model in sorted(
            state.models.values(), key=lambda item: (inventory_order(state.inventory(state.by_model[item.id][0].id))
                                                     if state.by_model.get(item.id) else (2,), norm(item.name)))]

    def record_extras(self, records: Sequence) -> dict[UUID, dict]:
        """What a list row of БД.ОБ / БД.ОП carries besides its cells: the model (id, role) of a unit with its tool and
        the units it serves; the models a БД.ОП row's cells link to."""
        units = [item.id for item in records if item.tab.value == OB]
        operations = [item.id for item in records if item.tab.value == OP]
        extras: dict[UUID, dict] = {}
        if units:
            roles = dict(self.session.execute(select(TechOperationRecordRow.id, EquipmentModelRow.role).join(
                EquipmentModelRow, EquipmentModelRow.id == TechOperationRecordRow.equipment_model_id).where(
                TechOperationRecordRow.id.in_(units))).all())
            links: dict[UUID, dict] = defaultdict(lambda: {"tool_unit_id": None, "serves_unit_ids": []})
            for link in self.session.scalars(select(EquipmentUnitLinkRow).where(EquipmentUnitLinkRow.unit_id.in_(units))):
                if link.kind == TOOL:
                    links[link.unit_id]["tool_unit_id"] = str(link.target_id)
                else:
                    links[link.unit_id]["serves_unit_ids"].append(str(link.target_id))
            for item in records:
                if item.tab.value == OB:
                    extras[item.id] = {"equipment_role": roles.get(item.id), **links[item.id]}
        if operations:
            by_operation: dict[UUID, list[dict]] = defaultdict(list)
            for link in self.session.scalars(select(EquipmentOperationLinkRow).where(
                    EquipmentOperationLinkRow.operation_id.in_(operations)).order_by(
                    EquipmentOperationLinkRow.op_column, EquipmentOperationLinkRow.position)):
                by_operation[link.operation_id].append(
                    {"column": link.op_column, "position": link.position, "model_id": str(link.model_id)})
            for item in records:
                if item.tab.value == OP:
                    extras[item.id] = {"equipment_links": by_operation.get(item.id, [])}
        return extras

    def _unit(self, unit_id: UUID) -> TechOperationRecordRow:
        unit = self.session.get(TechOperationRecordRow, unit_id)
        if unit is None or unit.tab != OB:
            raise LookupError("Оборудование не найдено")
        return unit

    def set_serves(self, unit_id: UUID, target_ids: Sequence[UUID]) -> list[str]:
        """«Обслуживает»: the units a supporting unit (compressor, chiller…) serves, as a whole list."""
        unit = self._unit(unit_id)
        model = self.session.get(EquipmentModelRow, unit.equipment_model_id)
        targets = list(dict.fromkeys(target_ids))
        if targets and (model is None or model.role != SUPPORTING):
            raise ReferenceValidationError("«Обслуживает» бывает только у обеспечивающего оборудования (компрессор, "
                                           "чиллер…) — сначала отметьте модель обеспечивающей")
        for target in targets:
            if target == unit_id:
                raise ReferenceValidationError("Оборудование не обслуживает само себя")
            self._unit(target)
        self.session.execute(delete(EquipmentUnitLinkRow).where(
            EquipmentUnitLinkRow.unit_id == unit_id, EquipmentUnitLinkRow.kind == SERVES))
        self.session.add_all(EquipmentUnitLinkRow(unit_id=unit_id, kind=SERVES, target_id=target) for target in targets)
        self.session.flush()
        return [str(target) for target in targets]

    def set_role(self, model_id: UUID, role: str) -> dict:
        if role not in EQUIPMENT_ROLES:
            raise ReferenceValidationError("Роль оборудования — operational или supporting")
        model = self.session.get(EquipmentModelRow, model_id)
        if model is None:
            raise LookupError("Модель оборудования не найдена")
        units = [unit.id for unit in self.session.scalars(select(TechOperationRecordRow).where(
            TechOperationRecordRow.equipment_model_id == model_id))]
        if role == SUPPORTING:
            operations = self.session.scalar(select(func.count()).select_from(EquipmentOperationLinkRow).where(
                EquipmentOperationLinkRow.model_id == model_id)) or 0
            templates = self.session.scalar(select(func.count()).select_from(EquipmentTemplateToolRow).where(
                EquipmentTemplateToolRow.model_id == model_id)) or 0
            if operations or templates:
                raise ReferenceValidationError(
                    f"Модель — машина операций ({operations}) или карт ({templates}): обеспечивающим оборудование с "
                    "операциями не бывает — сначала уберите его из операций")
        elif units and self.session.scalar(select(func.count()).select_from(EquipmentUnitLinkRow).where(
                EquipmentUnitLinkRow.unit_id.in_(units), EquipmentUnitLinkRow.kind == SERVES)):
            raise ReferenceValidationError("У модели есть «Обслуживает» — сначала снимите эти связи")
        model.role, model.role_confirmed = role, True
        self.session.flush()
        state = _State.load(self.session)
        return _model_json(state, model, 0)

    def set_unit_model(self, unit_id: UUID, model_id: UUID | None) -> str:
        """Moves a unit to another model (None — a model of its own with the unit's name / article / maker)."""
        unit = self._unit(unit_id)
        if model_id is None:
            name, article, maker = unit_attrs(unit.fields or {})
            model = EquipmentModelRow(id=uuid4(), name=name, article=article, maker=maker,
                                      role=role_of(name, type_of(unit.fields or {})), role_confirmed=False)
            self.session.add(model)
            self.session.flush([model])
        else:
            model = self.session.get(EquipmentModelRow, model_id)
            if model is None:
                raise LookupError("Модель оборудования не найдена")
        unit.equipment_model_id = model.id
        unit.edited_in_erp = True
        prune_models(self.session)
        return str(model.id)

    def report(self) -> dict:
        return links_report(self.session)


# --- the unresolved-links report (read-only) ---

def links_report(session: Session) -> dict:
    """What is not linked by id and why: БД.ОП pieces (ambiguous / unmatched / supporting equipment), «Для
    инструмента» texts, card-template «Оборудование» rows, models without units, models whose units disagree on name /
    article / maker, roles the owner has not confirmed. Reads only."""
    with session.no_autoflush:
        state = _State.load(session)
        links = {(link.operation_id, link.op_column, link.position) for link in session.scalars(
            select(EquipmentOperationLinkRow))}
        operations = []
        for operation in session.scalars(select(TechOperationRecordRow).where(TechOperationRecordRow.tab == OP)
                                         .order_by(TechOperationRecordRow.sort_key)):
            for column in OP_LINK_COLUMNS:
                for position, (_index, piece) in enumerate(pieces((operation.fields or {}).get(column, ""))):
                    if (operation.id, column, position) in links:
                        continue
                    named = state.candidates(piece)
                    found = state.operational(named)
                    reason = "unmatched" if not named else "supporting" if not found else "ambiguous"
                    operations.append({
                        "operation_id": str(operation.id),
                        "number": field(operation.fields or {}, "opNumber", "Номер") or operation.display_text,
                        "column": column, "position": position, "text": piece, "reason": reason,
                        "candidates": [{"model_id": str(item), "display": state.display(item)}
                                       for item in sorted(named, key=str)],
                    })
        tools = []
        tool_links = {link.unit_id for link in session.scalars(
            select(EquipmentUnitLinkRow).where(EquipmentUnitLinkRow.kind == TOOL))}
        for unit in state.units:
            text = field(unit.fields or {}, TOOL_KEY)
            if text and unit.id not in tool_links:
                found = [item for item in state.inventories.get(inv_key(text), []) if item.id != unit.id]
                tools.append({"unit_id": str(unit.id), "inventory": field(unit.fields or {}, INV_KEY), "text": text,
                              "reason": "ambiguous" if found else "unmatched"})
        templates = [{
            "template_id": row.template_id, "position": row.position, "tool_name": row.tool_name,
            "inventory_number": row.inventory_number, "reason": "ambiguous" if row.candidates > 1 else "unmatched",
        } for row in session.scalars(select(EquipmentTemplateToolRow).where(EquipmentTemplateToolRow.model_id.is_(None))
                                     .order_by(EquipmentTemplateToolRow.template_id, EquipmentTemplateToolRow.position))]
        counts = dict(session.execute(select(EquipmentOperationLinkRow.model_id, func.count()).group_by(
            EquipmentOperationLinkRow.model_id)).all())
        models = sorted(state.models.values(), key=lambda item: norm(item.name))
        without_units = [_model_json(state, model, counts.get(model.id, 0)) for model in models
                         if not state.by_model.get(model.id)]
        disagree = [_model_json(state, model, counts.get(model.id, 0)) for model in models
                    if len({unit_attrs(unit.fields or {}) for unit in state.by_model.get(model.id, [])}) > 1]
        unconfirmed = [_model_json(state, model, counts.get(model.id, 0)) for model in models if not model.role_confirmed]
    return {
        "counts": {
            "units": len(state.units), "models": len(state.models), "operation_links": len(links),
            "operations_unresolved": len(operations), "tools_unresolved": len(tools),
            "templates_unresolved": len(templates), "models_without_units": len(without_units),
            "models_units_disagree": len(disagree), "roles_unconfirmed": len(unconfirmed),
        },
        "operations": operations, "tools": tools, "templates": templates, "models_without_units": without_units,
        "models_units_disagree": disagree, "roles_unconfirmed": unconfirmed,
    }

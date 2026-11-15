"""SQLAlchemy persistence for editable maps and technology snapshots."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Sequence
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, Uuid,
    delete, false as sa_false, func, select, text, union_all,
)
from sqlalchemy import event
from sqlalchemy.orm import Mapped, Session, declared_attr, mapped_column, sessionmaker
from sqlalchemy.ext.declarative import AbstractConcreteBase

from app.shared.persistence import Base, SqlAlchemyAuditRepository

from .cable_structure import (
    ALIAS_KINDS, LAYERS, PARAMS, CoaxAlias, CoaxAttenuation, CoaxCard, CoaxConnector, CoaxLayer, CoaxMarker, CoaxNames,
    HookupWire, WireGauge, same_card,
)
from .search_text import search_rows, search_words
from .domain import (
    EDITABLE_TABS, EQUIPMENT_TABS, TERMINAL_TABS, ReferenceEntry, ReferenceKind, StockArticle, StockLinkChange, StockLinkEntry,
    StockLinkEvent, TechOperationRecord, TechOperationTab, ReferenceValidationError, family_root,
)
from .equipment_catalogs import CATALOG_TABLES, equipment_tab


class ReferenceEntryRow(Base):
    __tablename__ = "reference_entries"
    __table_args__ = (
        Index("ux_reference_entries_kind_key", "kind", "normalized_key", unique=True),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(2048), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(512), nullable=False)
    value: Mapped[str] = mapped_column(String(512), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


def terminal_card_checks(dialect: str) -> dict[str, str]:
    """БД.ТЕР contact card CHECKs over the JSON `fields` (migration 0115 holds a literal copy; 0165 extended them to
    «Наконечники», a tab of its own since — `TER_CARD_OTHER`): gender Male / Female or empty; insulation Ø from / to a
    positive number (decimal point or comma), from ≤ to; datasheet a http(s) link.
    CASE keeps a text from being cast before it is known to be a number (PostgreSQL does not order AND / OR)."""
    def value(key: str) -> str:
        raw = f"json_extract(fields, '$.{key}')" if dialect == "sqlite" else f"(fields ->> '{key}')"
        return f"COALESCE({raw}, '')"

    def number(text: str) -> str:
        if dialect == "sqlite":
            return (f"({text} GLOB '[0-9]*' AND {text} NOT GLOB '*[^0-9.,]*' AND {text} NOT GLOB '*[.,]*[.,]*'"
                    f" AND {text} NOT GLOB '*[.,]')")
        return f"({text} ~ '^[0-9]+([.,][0-9]+)?$')"

    def cast(text: str) -> str:
        return f"CAST(REPLACE({text}, ',', '.') AS {'REAL' if dialect == 'sqlite' else 'NUMERIC'})"

    def positive(text: str) -> str:
        return f"CASE WHEN {text} = '' THEN TRUE WHEN {number(text)} THEN {cast(text)} > 0 ELSE FALSE END"

    gender, od_from, od_to, sheet = (value(key) for key in ("terGender", "terInsOdFrom", "terInsOdTo", "terDatasheet"))
    other = TER_CARD_OTHER
    return {
        "ck_tech_operation_records_ter_gender": f"{other} OR {gender} IN ('', 'Male', 'Female')",
        "ck_tech_operation_records_ter_ins_od": (
            f"{other} OR (({positive(od_from)}) AND ({positive(od_to)}) AND (CASE WHEN {number(od_from)} AND"
            f" {number(od_to)} THEN {cast(od_from)} <= {cast(od_to)} ELSE TRUE END))"
        ),
        "ck_tech_operation_records_ter_datasheet": (
            f"{other} OR {sheet} = '' OR {sheet} LIKE 'http://%' OR {sheet} LIKE 'https://%'"
        ),
    }


# A row of neither «Терминалы» nor «Наконечники»: the card CHECKs hold on both (migration 0165 holds a literal copy).
TER_CARD_OTHER = "tab NOT IN (" + ", ".join(f"'{tab.value}'" for tab in TERMINAL_TABS) + ")"
# Every record is in a known base, «ter» first (migration 0165 holds a literal copy; a new tab widens it in a migration).
TAB_CHECK = "tab IN (" + ", ".join(f"'{tab.value}'" for tab in sorted(
    (tab for tab in TechOperationTab if tab not in EQUIPMENT_TABS[1:]), key=lambda tab: (tab != TechOperationTab.TERMINAL, list(TechOperationTab).index(tab)))) + ")"


# --- Equipment identity (owner 05.11.2026: «связка должна быть не через название, а через идентификатор»; 0171) ---

EQUIPMENT_ROLES = ("operational", "supporting")  # «операционное» does operations, «обеспечивающее» serves machines
OP_LINK_COLUMNS = ("Машина", "Инструмент", "Апликаторы / модули")  # БД.ОП cells that name an equipment model
UNIT_LINK_KINDS = ("tool", "serves")  # matrix → its tool («Для инструмента»); compressor / chiller → a unit it serves
# A БД.ОБ unit always has a model, any other record never (migration 0171 holds a literal copy).
OB_MODEL_CHECK = ("(tab = 'ob' AND equipment_model_id IS NOT NULL) OR (tab <> 'ob' AND equipment_model_id IS NULL)")


class EquipmentModelRow(Base):
    """A model of equipment: the units of БД.ОБ that are one machine («ОБ.070.1…8»). Name, article and maker are
    editable attributes — links go to the id."""
    __tablename__ = "equipment_models"
    __table_args__ = (
        CheckConstraint("role IN ('operational', 'supporting')", name="ck_equipment_models_role"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    article: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    maker: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="operational", server_default="operational")
    # False — the role was given by default (or by a word of the name), the owner has not confirmed it (the report).
    role_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())


class EquipmentUnitRow(Base):
    """Stable physical identity. Deferred reciprocal FKs enforce exactly one owning catalog at COMMIT."""
    __tablename__ = "equipment_units"
    __table_args__ = (
        UniqueConstraint("id", "catalog", "model_id", name="uq_equipment_units_owner"),
        CheckConstraint(" OR ".join(
            "(" + " AND ".join([f"catalog = '{tab}'", f"{tab}_id IS NOT NULL", f"{tab}_id = id",
                                *(f"{other}_id IS NULL" for other in CATALOG_TABLES if other != tab)]) + ")"
            for tab in CATALOG_TABLES), name="ck_equipment_units_one_owner"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    model_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("equipment_models.id", ondelete="RESTRICT"), nullable=False)
    catalog: Mapped[str] = mapped_column(String(16), nullable=False)
    ob_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("equipment_machines.id", name="fk_equipment_units_ob", use_alter=True, deferrable=True, initially="DEFERRED"))
    ob_measuring_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("equipment_measuring_units.id", name="fk_equipment_units_ob_measuring", use_alter=True, deferrable=True, initially="DEFERRED"))
    ob_support_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("equipment_support_units.id", name="fk_equipment_units_ob_support", use_alter=True, deferrable=True, initially="DEFERRED"))
    ob_applicators_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("equipment_applicators.id", name="fk_equipment_units_ob_applicators", use_alter=True, deferrable=True, initially="DEFERRED"))
    ob_dies_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("equipment_dies.id", name="fk_equipment_units_ob_dies", use_alter=True, deferrable=True, initially="DEFERRED"))
    ob_tools_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("equipment_tools.id", name="fk_equipment_units_ob_tools", use_alter=True, deferrable=True, initially="DEFERRED"))


class EquipmentRecordRow(AbstractConcreteBase, Base):
    """Mapped UNION ALL reader; writes use the concrete owning catalog class."""
    strict_attrs = True
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tab: Mapped[str] = mapped_column(String(16), nullable=False)
    equipment_model_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    display_text: Mapped[str] = mapped_column(String(2000), nullable=False)
    normalized_search: Mapped[str] = mapped_column(Text, nullable=False)
    source_sheet: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_key: Mapped[str] = mapped_column(String(2000), nullable=False)
    fields: Mapped[dict] = mapped_column(JSON, nullable=False)
    mirror_key: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    edited_in_erp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())

    @declared_attr.directive
    def __table_args__(cls):
        if not hasattr(cls, "__tablename__"):
            return ()
        tab = cls.__mapper_args__["polymorphic_identity"]
        return (
            CheckConstraint(f"tab = '{tab}'", name=f"ck_{cls.__tablename__}_catalog"),
            ForeignKeyConstraint(["id", "tab", "equipment_model_id"],
                                 ["equipment_units.id", "equipment_units.catalog", "equipment_units.model_id"],
                                 name=f"fk_{cls.__tablename__}_owner", deferrable=True, initially="DEFERRED"),
            Index(f"ix_{cls.__tablename__}_sort", "sort_key", "id"),
            Index(f"ix_{cls.__tablename__}_model", "equipment_model_id"),
        )


class EquipmentMachineRow(EquipmentRecordRow):
    __tablename__ = "equipment_machines"
    __mapper_args__ = {"polymorphic_identity": "ob", "concrete": True, "exclude_properties": ["type"]}


class EquipmentMeasuringRow(EquipmentRecordRow):
    __tablename__ = "equipment_measuring_units"
    __mapper_args__ = {"polymorphic_identity": "ob_measuring", "concrete": True, "exclude_properties": ["type"]}


class EquipmentSupportRow(EquipmentRecordRow):
    __tablename__ = "equipment_support_units"
    __mapper_args__ = {"polymorphic_identity": "ob_support", "concrete": True, "exclude_properties": ["type"]}


class EquipmentApplicatorRow(EquipmentRecordRow):
    __tablename__ = "equipment_applicators"
    __mapper_args__ = {"polymorphic_identity": "ob_applicators", "concrete": True, "exclude_properties": ["type"]}


class EquipmentDieRow(EquipmentRecordRow):
    __tablename__ = "equipment_dies"
    __mapper_args__ = {"polymorphic_identity": "ob_dies", "concrete": True, "exclude_properties": ["type"]}


class EquipmentToolRow(EquipmentRecordRow):
    __tablename__ = "equipment_tools"
    __mapper_args__ = {"polymorphic_identity": "ob_tools", "concrete": True, "exclude_properties": ["type"]}


EQUIPMENT_ROWS = dict(zip(CATALOG_TABLES, (EquipmentMachineRow, EquipmentMeasuringRow, EquipmentSupportRow,
                                        EquipmentApplicatorRow, EquipmentDieRow, EquipmentToolRow)))
Base.registry.configure()


class EquipmentInstrumentBindingRow(Base):
    """A passport link, never verification evidence; unresolved instruments remain unbound."""
    __tablename__ = "equipment_instrument_bindings"
    unit_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT"), primary_key=True)
    instrument_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_instruments.id", ondelete="RESTRICT"), unique=True, nullable=False)


class TechOperationRecordRow(Base):
    __tablename__ = "tech_operation_records"
    __table_args__ = (
        Index("ix_tech_operation_records_tab_sort", "tab", "sort_key"),
        *(CheckConstraint(sql, name=name).ddl_if(dialect=dialect)
          for dialect in ("sqlite", "postgresql") for name, sql in terminal_card_checks(dialect).items()),
        CheckConstraint(TAB_CHECK, name="ck_tech_operation_records_tab"),
        CheckConstraint(OB_MODEL_CHECK, name="ck_tech_operation_records_ob_model"),
        Index("ix_tech_operation_records_equipment_model_id", "equipment_model_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    # The base of the record (`TechOperationTab`): «Наконечники» and each cable kind are bases of their own (0165).
    tab: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    display_text: Mapped[str] = mapped_column(String(2000), nullable=False)
    normalized_search: Mapped[str] = mapped_column(Text(), nullable=False)
    source_sheet: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_key: Mapped[str] = mapped_column(String(2000), nullable=False)
    fields: Mapped[dict] = mapped_column(JSON, nullable=False)
    mirror_key: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    edited_in_erp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    equipment_model_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("equipment_models.id", ondelete="RESTRICT", name="fk_tech_operation_records_equipment_model"),
        nullable=True)


class EquipmentOperationLinkRow(Base):
    """A БД.ОП cell («Машина» / «Инструмент» / «Апликаторы / модули») → the model it names; `position` — the piece of the
    cell (one cell may name several, split by «;» or a line break). The cell text is a display copy of the model."""
    __tablename__ = "equipment_operation_links"
    __table_args__ = (
        CheckConstraint("op_column IN ('Машина', 'Инструмент', 'Апликаторы / модули')", name="ck_equipment_operation_links_column"),
        CheckConstraint("position >= 0", name="ck_equipment_operation_links_position"),
        Index("ix_equipment_operation_links_model_id", "model_id"),
    )
    operation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tech_operation_records.id", ondelete="CASCADE"), primary_key=True)
    op_column: Mapped[str] = mapped_column(String(32), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("equipment_models.id", ondelete="RESTRICT"),
                                           nullable=False)


class EquipmentUnitLinkRow(Base):
    """A БД.ОБ unit → another unit: «tool» — the tool a matrix belongs to («Для инструмента» is its display copy, one per
    matrix); «serves» — a machine a compressor / chiller serves («Обслуживает»). The target is never deleted under it."""
    __tablename__ = "equipment_unit_links"
    __table_args__ = (
        CheckConstraint("kind IN ('tool', 'serves')", name="ck_equipment_unit_links_kind"),
        Index("ix_equipment_unit_links_target_id", "target_id"),
        Index("ux_equipment_unit_links_one_tool", "unit_id", unique=True,
              sqlite_where=text("kind = 'tool'"), postgresql_where=text("kind = 'tool'")),
    )
    unit_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("equipment_units.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("equipment_units.id", ondelete="RESTRICT"), primary_key=True)


class EquipmentTemplateToolRow(Base):
    """«Оборудование» rows of the operation-card templates (БД.ТК) → a model, filled by migration 0171 on a single
    match; `model_id` empty — not resolved (`candidates`: 0 — nothing matched, more — ambiguous), in the report.
    `position` — the row's index in the template's tooling, `tool_name` — its text then (a later edit of the template
    shows as a different name)."""
    __tablename__ = "equipment_template_tools"
    __table_args__ = (
        CheckConstraint("candidates >= 0", name="ck_equipment_template_tools_candidates"),
        Index("ix_equipment_template_tools_model_id", "model_id"),
    )
    template_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(2000), nullable=False)
    inventory_number: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    candidates: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    model_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("equipment_models.id", ondelete="RESTRICT"), nullable=True)


@event.listens_for(TechOperationRecordRow, "before_insert")
def _unit_gets_a_model(_mapper, connection, row: TechOperationRecordRow) -> None:
    """Any БД.ОБ row inserted without a model (a writer past the repository: a workflow, a fixture) gets the one
    model of its name + article + maker, else one of its own — the CHECK of 0171 holds for every writer."""
    if row.tab != TechOperationTab.EQUIPMENT.value or row.equipment_model_id is not None:
        return
    from .equipment_identity import role_of, model_key, unit_attrs
    from .equipment import type_of
    name, article, maker = unit_attrs(row.fields or {})
    key = model_key(name, article, maker)
    models = EquipmentModelRow.__table__
    found = [model.id for model in connection.execute(select(models.c.id, models.c.name, models.c.article, models.c.maker))
             if key and model_key(model.name, model.article, model.maker) == key]
    if len(found) == 1:
        row.equipment_model_id = found[0]
        return
    row.equipment_model_id = uuid4()
    connection.execute(models.insert().values(id=row.equipment_model_id, name=name, article=article, maker=maker,
                                              role=role_of(name, type_of(row.fields or {})), role_confirmed=False))


class TechOperationSnapshotRow(Base):
    __tablename__ = "tech_operation_snapshots"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)


class SqlAlchemyReferenceRepository:
    def __init__(self, session: Session): self.session = session
    def list(self) -> list[ReferenceEntry]:
        rows = self.session.scalars(select(ReferenceEntryRow).order_by(ReferenceEntryRow.kind, ReferenceEntryRow.position, ReferenceEntryRow.id))
        return [ReferenceEntry(row.id, ReferenceKind(row.kind), row.key, row.normalized_key, row.value, row.position) for row in rows]
    def replace(self, entries: tuple[ReferenceEntry, ...]) -> None:
        self.session.execute(delete(ReferenceEntryRow))
        self.session.add_all(ReferenceEntryRow(
            id=entry.id, kind=entry.kind.value, key=entry.key, normalized_key=entry.normalized_key,
            value=entry.value, position=entry.position,
        ) for entry in entries)


_STABLE_TABS = (*CATALOG_TABLES, TechOperationTab.OPERATION.value)
_RECORD_COLUMNS = ("id", "tab", "display_text", "normalized_search", "source_sheet", "sort_key", "fields",
                   "mirror_key", "edited_in_erp", "equipment_model_id")


def _write_row(row, record: TechOperationRecord, edited_in_erp: bool) -> None:
    for name in _RECORD_COLUMNS:
        if name not in ("id", "equipment_model_id"):
            setattr(row, name, record.tab.value if name == "tab" else getattr(record, name))
    row.edited_in_erp = edited_in_erp


def _record_union():
    sources = [select(*(getattr(TechOperationRecordRow, name) for name in _RECORD_COLUMNS))
               .where(TechOperationRecordRow.tab != "ob")]
    sources.extend(select(*(getattr(row, name) for name in _RECORD_COLUMNS)) for row in EQUIPMENT_ROWS.values())
    return union_all(*sources).subquery("live_reference_records")


def _natural_key(tab: str, fields: dict) -> str:
    key = "Инв. номер" if tab in CATALOG_TABLES else "opNumber"
    return "".join(str(fields.get(key) or "").split()).casefold()


def _match_stable(records, rows):
    """Match UUID, then unambiguous provenance, then unambiguous inventory across the entire family."""
    free = {row.id: row for row in rows}
    result = {}
    for index, record in enumerate(records):
        if record.tab.value in _STABLE_TABS and record.id in free:
            result[index] = free.pop(record.id)
    for key_of in (lambda r: r.mirror_key, lambda r: _natural_key(getattr(r.tab, "value", r.tab), r.fields)):
        given, stored = {}, {}
        for index, record in enumerate(records):
            if index not in result and record.tab.value in _STABLE_TABS and (key := key_of(record)):
                given.setdefault((family_root(record.tab).value, key), []).append(index)
        for row in free.values():
            if key := key_of(row):
                stored.setdefault((family_root(TechOperationTab(row.tab)).value, key), []).append(row)
        for key, indexes in given.items():
            candidates = stored.get(key, [])
            if key[0] == "ob" and candidates and (len(indexes) != 1 or len(candidates) != 1):
                raise ReferenceValidationError("Неоднозначная идентичность оборудования " + str(key[1]) +
                                               ": " + ", ".join(str(row.id) for row in candidates))
            if len(indexes) == len(candidates) == 1:
                result[indexes[0]] = free.pop(candidates[0].id)
    return result


class SqlAlchemyTechOperationRepository:
    def __init__(self, session: Session): self.session = session

    @staticmethod
    def _domain(row) -> TechOperationRecord:
        return TechOperationRecord(row.id, TechOperationTab(row.tab), row.display_text, row.normalized_search,
                                   row.source_sheet, row.sort_key, row.fields, row.mirror_key, row.edited_in_erp,
                                   row.equipment_model_id)

    def list(self, *, tab: TechOperationTab | Sequence[TechOperationTab] | None, query: str, limit: int):
        """One global UNION ordering and limit, including search; no archived equipment is visible."""
        rows = _record_union()
        statement = select(rows)
        if isinstance(tab, TechOperationTab):
            statement = statement.where(rows.c.tab == tab.value)
        elif tab is not None:
            statement = statement.where(rows.c.tab.in_([item.value for item in tab]))
        by_tab = (rows.c.tab,) if tab is None else ()
        statement = statement.order_by(*by_tab, rows.c.sort_key, rows.c.id)
        words = search_words(query)
        if not words:
            return [self._domain(row) for row in self.session.execute(statement.limit(limit))]
        found = search_rows(self.session.execute(statement), lambda row: (row.display_text, *(row.fields or {}).values()),
                            words, limit, extra=lambda row: (row.normalized_search,))
        return [self._domain(row) for row in found]

    def counts(self):
        rows = _record_union()
        return {TechOperationTab(tab): count for tab, count in self.session.execute(
            select(rows.c.tab, func.count()).group_by(rows.c.tab))}

    def _row(self, record_id):
        unit = self.session.get(EquipmentUnitRow, record_id)
        if unit is not None:
            return self.session.get(EQUIPMENT_ROWS[unit.catalog], record_id)
        row = self.session.get(TechOperationRecordRow, record_id)
        return row if row is not None and row.tab != "ob" else None

    def get(self, record_id):
        row = self._row(record_id)
        return None if row is None else self._domain(row)

    def _delete_row(self, row):
        self.session.delete(row)
        if row.tab in CATALOG_TABLES:
            self.session.delete(self.session.get(EquipmentUnitRow, row.id))

    def delete(self, record_id):
        from . import equipment_identity as identity
        row = self._row(record_id)
        if row is None:
            return
        if row.tab in _STABLE_TABS:
            identity.check_deletable(self.session, {row.id})
            identity.forget(self.session, {row.id})
        self._delete_row(row)
        self.session.flush()
        identity.prune_models(self.session)

    def _put(self, record, *, row=None):
        from . import equipment_identity as identity
        if record.tab in EQUIPMENT_TABS:
            # A storage move keeps both UUIDs and all relations. Fields are not renamed by this path.
            tab = equipment_tab(record.fields)
            binding = self.session.get(EquipmentInstrumentBindingRow, record.id)
            if binding is not None:
                if tab != TechOperationTab.MEASURING:
                    raise ReferenceValidationError("Сначала снимите связь с реестром средств измерений")
                if row is not None and any(record.fields.get(key) != row.fields.get(key)
                                           for key in ("Заводской номер", "Серийный номер", "Поверка", "Дата поверки")):
                    raise ReferenceValidationError("Паспортные данные связанного СИ меняются в реестре средств измерений")
            if row is not None and row.tab not in CATALOG_TABLES:
                raise ReferenceValidationError("Нельзя превращать запись другой базы в оборудование")
            model_id = (row.equipment_model_id if row is not None else record.equipment_model_id)
            if model_id is None:
                model_id = identity.ModelAssigner(self.session).model_for(record.fields)
            model = self.session.get(EquipmentModelRow, model_id)
            tab = equipment_tab(record.fields, role=model.role if model and model.role_confirmed else "")
            unit = self.session.get(EquipmentUnitRow, record.id)
            if unit is None:
                unit = EquipmentUnitRow(id=record.id, model_id=model_id, catalog=tab.value)
                self.session.add(unit)
            if row is not None and row.tab != tab.value:
                self.session.delete(row)
                self.session.flush()
                row = None
            unit.catalog, unit.model_id = tab.value, model_id
            for catalog in CATALOG_TABLES:
                setattr(unit, f"{catalog}_id", record.id if catalog == tab.value else None)
            if row is None:
                row = EQUIPMENT_ROWS[tab.value](id=record.id)
                self.session.add(row)
            row.equipment_model_id = model_id
            record = replace(record, tab=tab, equipment_model_id=model_id)
        else:
            if row is not None and row.tab in CATALOG_TABLES:
                raise ReferenceValidationError("Нельзя превращать оборудование в запись другой базы")
            if row is None:
                row = TechOperationRecordRow(id=record.id)
                self.session.add(row)
            row.equipment_model_id = None
        _write_row(row, record, record.edited_in_erp)
        return row

    def save(self, record):
        from . import equipment_identity as identity
        row = self._row(record.id)
        before = None if row is None else (dict(row.fields or {}), row.display_text)
        if record.tab in EQUIPMENT_TABS:
            from .equipment import DuplicateInventoryError, prepare_inventory
            others = [(item.id, item.fields) for item in self.session.scalars(select(EquipmentRecordRow))]
            try:
                fields = prepare_inventory(dict(record.fields), others, self_id=record.id, creating=row is None,
                                           previous=before[0] if before else None)
            except DuplicateInventoryError as error:
                raise ReferenceValidationError(str(error)) from error
            if fields != record.fields:
                search = " ".join(f"{record.display_text} {' '.join(fields.values())}".casefold().split())
                record = replace(record, fields=fields, normalized_search=search)
        if record.tab == TechOperationTab.OPERATION:
            identity.check_operation_cells(self.session, record.fields, before[0] if before else None)
        row = self._put(record, row=row)
        if row.tab in _STABLE_TABS:
            identity.after_save(self.session, row, before[0] if before else None, before[1] if before else "")

    def replace(self, records, *, schema_version, source_updated_at, imported_at, keep_tabs=(), preserve_erp_edits=False):
        from . import equipment_identity as identity
        rows = [*self.session.scalars(select(TechOperationRecordRow).where(TechOperationRecordRow.tab != "ob")),
                *self.session.scalars(select(EquipmentRecordRow))]
        matched = _match_stable(records, [row for row in rows if row.tab in _STABLE_TABS])
        protected = {row.id for row in rows if TechOperationTab(row.tab) in keep_tabs or
                     (preserve_erp_edits and row.edited_in_erp)}
        protected_keys = {(family_root(TechOperationTab(row.tab)), row.mirror_key) for row in rows
                          if row.id in protected and row.mirror_key}
        skipped, wanted = {}, []
        for index, record in enumerate(records):
            row = matched.get(index)
            if ((row is not None and row.id in protected) or
                    (preserve_erp_edits and (family_root(record.tab), record.mirror_key) in protected_keys)):
                key = family_root(record.tab).value
                skipped[key] = skipped.get(key, 0) + 1
                continue
            if record.tab in keep_tabs:
                continue
            wanted.append((record, row))
        retained = protected | {row.id for _, row in wanted if row is not None}
        gone = {row.id for row in rows if row.id not in retained}
        identity.check_deletable(self.session, gone, reload=True)
        identity.forget(self.session, gone)
        for row in rows:
            if row.id in gone:
                self._delete_row(row)
        self.session.flush()
        for record, row in wanted:
            if row is not None:
                record = replace(record, id=row.id, equipment_model_id=row.equipment_model_id)
            self._put(replace(record, edited_in_erp=False), row=row)
        self.session.flush()
        identity.reconcile(self.session, reload=True)
        identity.prune_models(self.session)
        meta = self.session.get(TechOperationSnapshotRow, "current")
        if meta is None:
            meta = TechOperationSnapshotRow(name="current", record_count=0)
            self.session.add(meta)
        meta.schema_version, meta.source_updated_at, meta.imported_at = schema_version, source_updated_at, imported_at
        meta.record_count = sum(self.counts().values())
        self.session.flush()
        return skipped

    def metadata(self):
        row = self.session.get(TechOperationSnapshotRow, "current")
        if row is None: return None
        return {"schema_version": row.schema_version, "source_updated_at": row.source_updated_at,
                "imported_at": row.imported_at, "record_count": row.record_count}


# --- structured cable reference (cable_structure.py; migration 0089) ---

class WireGaugeRow(Base):
    __tablename__ = "wire_gauges"
    __table_args__ = (
        UniqueConstraint("awg", name="ux_wire_gauges_awg"),
        UniqueConstraint("section_mm2", name="ux_wire_gauges_section"),
        CheckConstraint("section_mm2 > 0", name="ck_wire_gauges_section_positive"),
        CheckConstraint("conductor_d_mm IS NULL OR conductor_d_mm > 0", name="ck_wire_gauges_conductor_positive"),
        CheckConstraint("awg IS NULL OR awg <> ''", name="ck_wire_gauges_awg_not_blank"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    awg: Mapped[str | None] = mapped_column(String(8), nullable=True)
    section_mm2: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    conductor_d_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class HookupWireRow(Base):
    __tablename__ = "hookup_wires"
    __table_args__ = (
        UniqueConstraint("mark_key", name="ux_hookup_wires_mark_key"),
        CheckConstraint("series <> ''", name="ck_hookup_wires_series_not_blank"),
        CheckConstraint("outer_d_mm IS NULL OR outer_d_mm > 0", name="ck_hookup_wires_outer_positive"),
        CheckConstraint("voltage_v IS NULL OR voltage_v > 0", name="ck_hookup_wires_voltage_positive"),
        CheckConstraint("temp_min_c IS NULL OR temp_max_c IS NULL OR temp_min_c < temp_max_c", name="ck_hookup_wires_temp_range"),
        CheckConstraint("insulation_thickness_mm IS NULL OR insulation_thickness_mm > 0",
                        name="ck_hookup_wires_insulation_thickness_positive"),
        Index("ix_hookup_wires_gauge_id", "gauge_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    mark: Mapped[str] = mapped_column(String(512), nullable=False)
    mark_key: Mapped[str] = mapped_column(String(512), nullable=False)
    series: Mapped[str] = mapped_column(String(255), nullable=False)
    gauge_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("wire_gauges.id", ondelete="RESTRICT"), nullable=False)
    insulation: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    outer_d_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    voltage_v: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temp_min_c: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temp_max_c: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conductor_material: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    insulation_thickness_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CoaxCableRow(Base):
    __tablename__ = "coax_cables"
    __table_args__ = (
        UniqueConstraint("name_key", name="ux_coax_cables_name_key"),
        CheckConstraint("name <> ''", name="ck_coax_cables_name_not_blank"),
        CheckConstraint("span_from_mhz IS NULL OR span_from_mhz > 0", name="ck_coax_cables_span_from_positive"),
        CheckConstraint("span_to_ghz IS NULL OR span_to_ghz > 0", name="ck_coax_cables_span_to_positive"),
        CheckConstraint("span_from_mhz IS NULL OR span_to_ghz IS NULL OR span_from_mhz < span_to_ghz * 1000",
                        name="ck_coax_cables_span_order"),
        CheckConstraint("fc_ghz IS NULL OR fc_ghz > 0", name="ck_coax_cables_fc_positive"),
        CheckConstraint("max_operating_ghz IS NULL OR max_operating_ghz > 0", name="ck_coax_cables_operating_positive"),
        CheckConstraint("capacitance_pf_m IS NULL OR capacitance_pf_m > 0", name="ck_coax_cables_capacitance_positive"),
        CheckConstraint("max_power_w IS NULL OR max_power_w > 0", name="ck_coax_cables_power_positive"),
        CheckConstraint("max_power_at_mhz IS NULL OR max_power_at_mhz > 0", name="ck_coax_cables_power_freq_positive"),
        CheckConstraint("impedance_ohm IS NULL OR impedance_ohm > 0", name="ck_coax_cables_impedance_positive"),
        CheckConstraint("velocity_pct IS NULL OR (velocity_pct > 0 AND velocity_pct <= 100)", name="ck_coax_cables_velocity_pct"),
        CheckConstraint("shielding_db IS NULL OR shielding_db > 0", name="ck_coax_cables_shielding_positive"),
        CheckConstraint("bend_static_mm IS NULL OR bend_static_mm > 0", name="ck_coax_cables_bend_static_positive"),
        CheckConstraint("bend_dynamic_mm IS NULL OR bend_dynamic_mm > 0", name="ck_coax_cables_bend_dynamic_positive"),
        CheckConstraint("temp_min_c IS NULL OR temp_max_c IS NULL OR temp_min_c < temp_max_c", name="ck_coax_cables_temp_range"),
        CheckConstraint("voltage_max_v IS NULL OR voltage_max_v > 0", name="ck_coax_cables_voltage_positive"),
        CheckConstraint("weight_g_m IS NULL OR weight_g_m > 0", name="ck_coax_cables_weight_positive"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_key: Mapped[str] = mapped_column(String(255), nullable=False)
    family: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    full_name: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    span_from_mhz: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    span_to_ghz: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    span_note: Mapped[str] = mapped_column(String(2000), nullable=False, default="", server_default="")
    fc_ghz: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    fc_note: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    max_operating_ghz: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    max_operating_note: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    impedance_ohm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    velocity_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    capacitance_pf_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    max_power_w: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    max_power_at_mhz: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    shielding_db: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    bend_static_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    bend_dynamic_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    temp_min_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 1), nullable=True)
    temp_max_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 1), nullable=True)
    voltage_max_v: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    weight_g_m: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    notes: Mapped[str] = mapped_column(Text(), nullable=False, default="", server_default="")
    param_notes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    changes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CoaxCableLayerRow(Base):
    __tablename__ = "coax_cable_layers"
    __table_args__ = (
        UniqueConstraint("cable_id", "layer", name="ux_coax_cable_layers_cable_layer"),
        CheckConstraint("layer IN ('core', 'dielectric', 'shield', 'jacket')", name="ck_coax_cable_layers_layer"),
        CheckConstraint("diameter_mm IS NULL OR diameter_mm > 0", name="ck_coax_cable_layers_diameter_positive"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    cable_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("coax_cables.id", ondelete="CASCADE"), nullable=False)
    layer: Mapped[str] = mapped_column(String(16), nullable=False)
    material: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    diameter_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)


class CoaxCableConnectorRow(Base):
    __tablename__ = "coax_cable_connectors"
    __table_args__ = (
        UniqueConstraint("cable_id", "family", name="ux_coax_cable_connectors_cable_family"),
        CheckConstraint("family <> ''", name="ck_coax_cable_connectors_family_not_blank"),
        CheckConstraint("max_ghz IS NULL OR max_ghz > 0", name="ck_coax_cable_connectors_max_positive"),
        CheckConstraint("span_to_ghz IS NULL OR span_to_ghz > 0", name="ck_coax_cable_connectors_span_positive"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    cable_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("coax_cables.id", ondelete="CASCADE"), nullable=False)
    family: Mapped[str] = mapped_column(String(128), nullable=False)
    max_ghz: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    max_note: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    span_to_ghz: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class CoaxCableMarkerRow(Base):
    __tablename__ = "coax_cable_markers"
    __table_args__ = (
        UniqueConstraint("cable_id", "freq_mhz", name="ux_coax_cable_markers_cable_freq"),
        CheckConstraint("freq_mhz IS NULL OR freq_mhz > 0", name="ck_coax_cable_markers_freq_positive"),
        CheckConstraint("vswr_max IS NULL OR vswr_max >= 1", name="ck_coax_cable_markers_vswr"),
        CheckConstraint("vswr_ref_assembly IS NULL OR vswr_ref_assembly >= 1", name="ck_coax_cable_markers_vswr_ref"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    cable_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("coax_cables.id", ondelete="CASCADE"), nullable=False)
    freq_mhz: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)  # NULL — the «Peak» marker
    vswr_max: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    vswr_ref_assembly: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="", server_default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class CoaxCableAttenuationRow(Base):
    __tablename__ = "coax_cable_attenuation"
    __table_args__ = (
        UniqueConstraint("cable_id", "freq_mhz", "kind", "source", name="ux_coax_cable_attenuation_point"),
        CheckConstraint("freq_mhz > 0", name="ck_coax_cable_attenuation_freq_positive"),
        CheckConstraint("kind IN ('nominal', 'typical', 'max')", name="ck_coax_cable_attenuation_kind"),
        CheckConstraint("value > 0", name="ck_coax_cable_attenuation_value_positive"),
        CheckConstraint("unit IN ('dB/m', 'dB/100m', 'dB/km', 'dB/100ft', 'dB/ft')", name="ck_coax_cable_attenuation_unit"),
        CheckConstraint("ref_length_m > 0", name="ck_coax_cable_attenuation_length_positive"),
        CheckConstraint("loss_db_m > 0", name="ck_coax_cable_attenuation_loss_positive"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    cable_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("coax_cables.id", ondelete="CASCADE"), nullable=False)
    freq_mhz: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    ref_length_m: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    temp_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 1), nullable=True)
    loss_db_m: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    derived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    interpolated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    source: Mapped[str] = mapped_column(String(1000), nullable=False, default="", server_default="")
    basis: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")


class CoaxCableAliasRow(Base):
    """Another name of a coax card (migration 0108): the key is unique across all cables."""
    __tablename__ = "coax_cable_aliases"
    __table_args__ = (
        UniqueConstraint("alias_key", name="ux_coax_cable_aliases_key"),
        CheckConstraint("alias <> ''", name="ck_coax_cable_aliases_alias_not_blank"),
        CheckConstraint("alias_key <> ''", name="ck_coax_cable_aliases_key_not_blank"),
        CheckConstraint("kind IN (" + ", ".join(f"'{kind}'" for kind in ALIAS_KINDS) + ")", name="ck_coax_cable_aliases_kind"),
        CheckConstraint("kind <> 'supplier_article' OR supplier <> ''", name="ck_coax_cable_aliases_article_supplier"),
        Index("ix_coax_cable_aliases_cable", "cable_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    cable_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("coax_cables.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(String(500), nullable=False)
    alias_key: Mapped[str] = mapped_column(String(500), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    supplier: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    url: Mapped[str] = mapped_column(String(1000), nullable=False, default="", server_default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


def _gauge(row: WireGaugeRow) -> WireGauge:
    return WireGauge(row.id, row.awg, Decimal(row.section_mm2), None if row.conductor_d_mm is None else Decimal(row.conductor_d_mm),
                     position=row.position)


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class SqlAlchemyCableStructureRepository:
    def __init__(self, session: Session): self.session = session

    def gauges(self) -> list[WireGauge]:
        rows = self.session.scalars(select(WireGaugeRow).order_by(WireGaugeRow.position, WireGaugeRow.section_mm2))
        return [_gauge(row) for row in rows]

    def save_gauge(self, gauge: WireGauge) -> None:
        row = self.session.get(WireGaugeRow, gauge.id)
        if row is None: row = WireGaugeRow(id=gauge.id); self.session.add(row)
        row.awg = gauge.awg; row.section_mm2 = gauge.section_mm2; row.conductor_d_mm = gauge.conductor_d_mm
        row.position = gauge.position
        self.session.flush()

    def delete_gauge(self, gauge_id: UUID) -> None:
        self.session.execute(delete(WireGaugeRow).where(WireGaugeRow.id == gauge_id))
        self.session.flush()

    def gauge_usage(self) -> dict[UUID, int]:
        rows = self.session.execute(select(HookupWireRow.gauge_id, func.count()).group_by(HookupWireRow.gauge_id))
        return {gauge_id: count for gauge_id, count in rows}

    def hookups(self) -> list[HookupWire]:
        return [self._hookup(row) for row in self.session.scalars(select(HookupWireRow).order_by(HookupWireRow.mark_key))]

    def hookup(self, mark_key: str) -> HookupWire | None:
        row = self.session.scalar(select(HookupWireRow).where(HookupWireRow.mark_key == mark_key))
        return None if row is None else self._hookup(row)

    @staticmethod
    def _hookup(row: HookupWireRow) -> HookupWire:
        return HookupWire(row.id, row.mark, row.mark_key, row.series, row.gauge_id, row.insulation, _dec(row.outer_d_mm),
                          row.voltage_v, row.temp_min_c, row.temp_max_c, row.conductor_material or "",
                          _dec(row.insulation_thickness_mm))

    def save_hookup(self, wire: HookupWire, now: datetime) -> None:
        row = self.session.get(HookupWireRow, wire.id)
        if row is None: row = HookupWireRow(id=wire.id); self.session.add(row)
        row.mark = wire.mark; row.mark_key = wire.mark_key; row.series = wire.series; row.gauge_id = wire.gauge_id
        row.insulation = wire.insulation; row.outer_d_mm = wire.outer_d_mm; row.voltage_v = wire.voltage_v
        row.temp_min_c = wire.temp_min_c; row.temp_max_c = wire.temp_max_c; row.updated_at = now
        row.conductor_material = wire.conductor_material; row.insulation_thickness_mm = wire.insulation_thickness_mm
        self.session.flush()

    def cards(self) -> list[CoaxCard]:
        cables = list(self.session.scalars(select(CoaxCableRow).order_by(CoaxCableRow.position, CoaxCableRow.name)))
        ids = [row.id for row in cables]
        children: dict[UUID, dict[str, list]] = {cable_id: {"layers": [], "connectors": [], "markers": [], "attenuation": [], "aliases": []} for cable_id in ids}
        if ids:
            order = {layer: index for index, layer in enumerate(LAYERS)}
            for row in self.session.scalars(select(CoaxCableLayerRow).where(CoaxCableLayerRow.cable_id.in_(ids))):
                children[row.cable_id]["layers"].append(row)
            for row in self.session.scalars(select(CoaxCableConnectorRow).where(CoaxCableConnectorRow.cable_id.in_(ids)).order_by(CoaxCableConnectorRow.position)):
                children[row.cable_id]["connectors"].append(row)
            for row in self.session.scalars(select(CoaxCableMarkerRow).where(CoaxCableMarkerRow.cable_id.in_(ids)).order_by(CoaxCableMarkerRow.position)):
                children[row.cable_id]["markers"].append(row)
            for row in self.session.scalars(select(CoaxCableAttenuationRow).where(CoaxCableAttenuationRow.cable_id.in_(ids)).order_by(CoaxCableAttenuationRow.freq_mhz, CoaxCableAttenuationRow.kind)):
                children[row.cable_id]["attenuation"].append(row)
            for row in self.session.scalars(select(CoaxCableAliasRow).where(CoaxCableAliasRow.cable_id.in_(ids)).order_by(CoaxCableAliasRow.position)):
                children[row.cable_id]["aliases"].append(row)
        else:
            order = {}
        result = []
        for row in cables:
            own = children[row.id]
            result.append(CoaxCard(
                id=row.id, name=row.name, name_key=row.name_key, family=row.family,
                layers=tuple(CoaxLayer(item.layer, item.material, _dec(item.diameter_mm))
                             for item in sorted(own["layers"], key=lambda item: order.get(item.layer, 9))),
                connectors=tuple(CoaxConnector(item.family, _dec(item.max_ghz), item.max_note, _dec(item.span_to_ghz)) for item in own["connectors"]),
                markers=tuple(CoaxMarker(_dec(item.freq_mhz), _dec(item.vswr_max), item.note, _dec(item.vswr_ref_assembly))
                              for item in own["markers"]),
                attenuation=tuple(CoaxAttenuation(
                    _dec(item.freq_mhz), item.kind, _dec(item.value), item.unit, _dec(item.ref_length_m), _dec(item.temp_c),
                    _dec(item.loss_db_m), item.derived, item.interpolated, item.source, item.basis) for item in own["attenuation"]),
                span_from_mhz=_dec(row.span_from_mhz), span_to_ghz=_dec(row.span_to_ghz), span_note=row.span_note,
                fc_ghz=_dec(row.fc_ghz), notes=row.notes, sources=tuple(row.sources or ()), confidence=row.confidence,
                verified=row.verified, changes=tuple(row.changes or ()),
                params={key: _dec(getattr(row, key)) for key in PARAMS if getattr(row, key) is not None},
                position=row.position, fc_note=row.fc_note, max_operating_ghz=_dec(row.max_operating_ghz),
                max_operating_note=row.max_operating_note, param_notes=dict(row.param_notes or {}),
                full_name=row.full_name or "",
                aliases=tuple(CoaxAlias(item.alias, item.alias_key, item.kind, item.supplier, item.url) for item in own["aliases"]),
            ))
        return result

    def replace_cards(self, cards: Sequence[CoaxCard], now: datetime) -> dict[str, int]:
        """Idempotent seed: a card per name key — kept id, its rows and children replaced by the file's when they
        differ (an unchanged card is not written); cards the file no longer names are removed with their children and
        aliases. The full name and the aliases are not the card file's (`replace_names`)."""
        existing = {row.name_key: row for row in self.session.scalars(select(CoaxCableRow))}
        stored = {card.name_key: card for card in self.cards()}
        keys = {card.name_key for card in cards}
        removed = [row.id for key, row in existing.items() if key not in keys]
        if removed:
            # the FK cascades on PostgreSQL; SQLite (tests, preview) does not enforce it, and an orphan name would
            # still hold its unique key
            self.session.execute(delete(CoaxCableAliasRow).where(CoaxCableAliasRow.cable_id.in_(removed)))
            self.session.execute(delete(CoaxCableRow).where(CoaxCableRow.id.in_(removed)))
        created = updated = 0
        for card in cards:
            row = existing.get(card.name_key)
            if row is None:
                row = CoaxCableRow(id=card.id); self.session.add(row); created += 1
            elif same_card(stored[card.name_key], card):
                continue
            else:
                updated += 1
                for child in (CoaxCableLayerRow, CoaxCableConnectorRow, CoaxCableMarkerRow, CoaxCableAttenuationRow):
                    self.session.execute(delete(child).where(child.cable_id == row.id))
            row.name = card.name; row.name_key = card.name_key; row.family = card.family
            row.span_from_mhz = card.span_from_mhz; row.span_to_ghz = card.span_to_ghz; row.span_note = card.span_note
            row.fc_ghz = card.fc_ghz; row.fc_note = card.fc_note; row.max_operating_ghz = card.max_operating_ghz
            row.max_operating_note = card.max_operating_note; row.param_notes = dict(card.param_notes)
            row.notes = card.notes; row.sources = list(card.sources)
            row.confidence = card.confidence; row.verified = card.verified; row.changes = list(card.changes)
            row.position = card.position; row.imported_at = now
            for key in PARAMS:
                setattr(row, key, card.params.get(key))
            self.session.flush()
            self.session.add_all(CoaxCableLayerRow(id=uuid4(), cable_id=row.id, layer=item.layer, material=item.material,
                                                   diameter_mm=item.diameter_mm) for item in card.layers)
            self.session.add_all(CoaxCableConnectorRow(id=uuid4(), cable_id=row.id, family=item.family, max_ghz=item.max_ghz,
                                                       max_note=item.max_note, span_to_ghz=item.span_to_ghz, position=index)
                                 for index, item in enumerate(card.connectors))
            self.session.add_all(CoaxCableMarkerRow(id=uuid4(), cable_id=row.id, freq_mhz=item.freq_mhz, vswr_max=item.vswr_max,
                                                    vswr_ref_assembly=item.vswr_ref, note=item.note, position=index)
                                 for index, item in enumerate(card.markers))
            self.session.add_all(CoaxCableAttenuationRow(
                id=uuid4(), cable_id=row.id, freq_mhz=item.freq_mhz, kind=item.kind, value=item.value, unit=item.unit,
                ref_length_m=item.ref_length_m, temp_c=item.temp_c, loss_db_m=item.loss_db_m, derived=item.derived,
                interpolated=item.interpolated, source=item.source, basis=item.basis) for item in card.attenuation)
        self.session.flush()
        return {"created": created, "updated": updated, "removed": len(removed)}

    def replace_names(self, names: dict[str, CoaxNames]) -> dict[str, int]:
        """Full name and aliases of each card (card name key → names; a card not given keeps its own). Written only
        where they differ: a second load with the same file changes nothing. Aliases of every changed card go first,
        so a name moving from one card to another never meets itself on the unique key."""
        rows = {row.name_key: row for row in self.session.scalars(select(CoaxCableRow))}
        cards = {card.name_key: card for card in self.cards()}
        changed = [key for key, item in names.items() if key in cards and cards[key].aliases != item.aliases]
        renamed = [key for key, item in names.items() if key in cards and cards[key].full_name != item.full_name]
        if changed:
            self.session.execute(delete(CoaxCableAliasRow).where(CoaxCableAliasRow.cable_id.in_([rows[key].id for key in changed])))
            self.session.flush()
        for key in renamed:
            rows[key].full_name = names[key].full_name
        added = removed = 0
        for key in changed:
            before = {alias.alias_key for alias in cards[key].aliases}
            after = {alias.alias_key for alias in names[key].aliases}
            added += len(after - before); removed += len(before - after)
            self.session.add_all(CoaxCableAliasRow(
                id=uuid4(), cable_id=rows[key].id, alias=alias.alias, alias_key=alias.alias_key, kind=alias.kind,
                supplier=alias.supplier, url=alias.url, position=index) for index, alias in enumerate(names[key].aliases))
        self.session.flush()
        return {"cables_changed": len(set(changed) | set(renamed)), "aliases_added": added, "aliases_removed": removed,
                "full_names_changed": len(renamed)}


# --- «Склад → база данных» journal (migration 0116): append-only, the repository only adds and reads ---

STOCK_LINK_EVENTS = tuple(event.value for event in StockLinkEvent)
STOCK_LINK_TABS = tuple(sorted(tab.value for tab in EDITABLE_TABS))
# Migration 0165 holds a literal copy (it added «lug» and the cable bases).
STOCK_LINK_TAB_CHECK = "record_tab IN (" + ", ".join(f"'{tab}'" for tab in STOCK_LINK_TABS) + ")"


class ReferenceStockLinkRow(Base):
    __tablename__ = "reference_stock_links"
    __table_args__ = (
        CheckConstraint("event IN ('linked', 'created', 'updated')", name="ck_reference_stock_links_event"),
        CheckConstraint("record_id IS NULL OR equipment_unit_id IS NULL", name="ck_reference_stock_links_one_target"),
        CheckConstraint(STOCK_LINK_TAB_CHECK, name="ck_reference_stock_links_tab"),
        CheckConstraint("length(trim(grn)) > 0", name="ck_reference_stock_links_grn"),
        CheckConstraint("length(trim(record_text)) > 0", name="ck_reference_stock_links_record_text"),
        CheckConstraint("length(trim(actor_name)) > 0", name="ck_reference_stock_links_actor"),
        Index("ix_reference_stock_links_record_id", "record_id"),
        Index("ix_reference_stock_links_lot_id", "lot_id"),
        Index("ix_reference_stock_links_occurred_at", "occurred_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event: Mapped[str] = mapped_column(String(16), nullable=False)
    lot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("warehouse_lots.id", ondelete="RESTRICT", name="fk_reference_stock_links_lot_id"),
        nullable=False)
    warehouse: Mapped[str] = mapped_column(String(8), nullable=False)
    grn: Mapped[str] = mapped_column(String(32), nullable=False)
    package: Mapped[str] = mapped_column(String(32), nullable=False)
    article: Mapped[str] = mapped_column(String(1024), nullable=False)
    name: Mapped[str] = mapped_column(String(1024), nullable=False)
    record_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tech_operation_records.id", ondelete="SET NULL", name="fk_reference_stock_links_record_id"),
        nullable=True)
    record_tab: Mapped[str] = mapped_column(String(16), nullable=False)
    equipment_unit_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("equipment_units.id", ondelete="SET NULL", name="fk_reference_stock_links_equipment_unit"),
        nullable=True)
    record_text: Mapped[str] = mapped_column(String(2000), nullable=False)
    changes: Mapped[list] = mapped_column(JSON, nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(255), nullable=False)


class SqlAlchemyStockLinkRepository:
    def __init__(self, session: Session): self.session = session

    def add(self, entry: StockLinkEntry) -> None:
        article = entry.article
        self.session.add(ReferenceStockLinkRow(
            id=entry.id, occurred_at=entry.occurred_at, event=entry.event.value, lot_id=article.lot_id,
            warehouse=article.warehouse, grn=article.grn, package=article.package, article=article.article,
            name=article.name, record_id=None if entry.record_tab in EQUIPMENT_TABS else entry.record_id,
            equipment_unit_id=entry.record_id if entry.record_tab in EQUIPMENT_TABS else None,
            record_tab=entry.record_tab.value,
            record_text=entry.record_text,
            changes=[{"field": change.field, "old": change.old, "new": change.new} for change in entry.changes],
            actor_id=entry.actor_id, actor_name=entry.actor_name,
        ))

    def list(self, *, record_id: UUID | None = None, lot_id: UUID | None = None, limit: int = 500) -> list[StockLinkEntry]:
        """Newest first."""
        statement = select(ReferenceStockLinkRow)
        if record_id is not None:
            statement = statement.where((ReferenceStockLinkRow.record_id == record_id) |
                                        (ReferenceStockLinkRow.equipment_unit_id == record_id))
        if lot_id is not None: statement = statement.where(ReferenceStockLinkRow.lot_id == lot_id)
        rows = self.session.scalars(statement.order_by(
            ReferenceStockLinkRow.occurred_at.desc(), ReferenceStockLinkRow.id.desc()).limit(limit))
        return [StockLinkEntry(
            id=row.id, occurred_at=row.occurred_at, event=StockLinkEvent(row.event),
            article=StockArticle(lot_id=row.lot_id, warehouse=row.warehouse, grn=row.grn, package=row.package,
                                 article=row.article, name=row.name),
            record_id=row.record_id or row.equipment_unit_id, record_tab=TechOperationTab(row.record_tab), record_text=row.record_text,
            changes=tuple(StockLinkChange(str(item.get("field", "")), str(item.get("old", "")), str(item.get("new", "")))
                          for item in row.changes or []),
            actor_id=row.actor_id, actor_name=row.actor_name,
        ) for row in rows]


class SqlAlchemyReferencesUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory; self.session: Session | None = None; self.committed = False
    def __enter__(self):
        self.committed = False; self.session = self.session_factory()
        self.references = SqlAlchemyReferenceRepository(self.session)
        self.tech_operations = SqlAlchemyTechOperationRepository(self.session)
        self.cable_structure = SqlAlchemyCableStructureRepository(self.session)
        self.audit = SqlAlchemyAuditRepository(self.session)
        self.stock_links = SqlAlchemyStockLinkRepository(self.session)
        from .equipment_identity import SqlAlchemyEquipmentRepository
        self.equipment = SqlAlchemyEquipmentRepository(self.session)
        return self
    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.session is None: return
        if exc_type is not None or not self.committed: self.session.rollback()
        self.session.close()
    def commit(self) -> None:
        if self.session is None: raise RuntimeError("Unit of Work has not been entered")
        self.session.commit(); self.committed = True

"""Compose the production catalog with reference reads on the caller's transaction.

Equipment rows are frozen compatibility extensions. They supply only UUID/model links;
every displayed value, including an empty value, comes from the canonical aggregate.
"""

import re
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select

from app.modules.production.infrastructure import SqlAlchemyInstrumentRepository, SqlAlchemyInstrumentFileRepository
from app.modules.production.instrument_catalog import InstrumentCatalog
from app.modules.production.quality import UpdateInstrumentCommand, instrument_snapshot
from app.modules.production.traceability import InstrumentKind, InstrumentStatus
from app.modules.production.application import ConcurrentProductionUpdate
from app.modules.production.domain import ProductionValidationError
from app.modules.references.domain import (
    EQUIPMENT_TABS, ReferenceValidationError, StaleRecordError, TechOperationRecord, TechOperationTab,
)
from app.modules.references.infrastructure import (
    EquipmentInstrumentBindingRow, EquipmentMeasuringRow, SqlAlchemyTechOperationRepository,
)
from app.shared.persistence import SqlAlchemyAuditRepository
from app.modules.references.search_text import search_rows, search_words
from app.workflows.new_operation import SqlAlchemyReferencesOperationsUnitOfWork


FIELD_ATTRIBUTES = {
    "Инв. номер": "inventory_number", "Наименование": "name", "Артикул": "article",
    "Производитель": "manufacturer", "Расположение": "storage_location", "Примечание": "notes",
    "Заводской номер": "serial_number", "Категория измерений": "measurement_category",
    "Вид": "kind", "Тип СИ": "instrument_type", "Количество": "quantity", "Статус": "status",
    "Свидетельство о поверке": "verification_number", "Дата поверки": "verified_at",
    "Поверка до": "verification_due", "Обозначение документа": "doc_designation",
    "ГРН": "warehouse_grn", "Родитель": "parent_id",
}


def _cell(value):
    if value is None:
        return ""
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


class ReferenceInstrumentCatalog:
    reserved_columns = frozenset((*FIELD_ATTRIBUTES, "Тип", "Наменование", "obName", "obArticle", "obMaker",
                                 "inventoryNumber", "obType", "Для базы", "baseValue"))

    def __init__(self, session):
        self.session = session
        self.catalog = InstrumentCatalog(SimpleNamespace(
            instruments=SqlAlchemyInstrumentRepository(session), audit=SqlAlchemyAuditRepository(session)))
        self._bindings = None
        self._extensions = None
        self._read_items = {}
        self._files = None
        self._serial_contexts = None

    def _links(self):
        if self._bindings is None:
            self._bindings = {row.unit_id: row.instrument_id for row in self.session.scalars(select(EquipmentInstrumentBindingRow))}
        return self._bindings

    def list(self):
        rows = self.catalog.list()
        self._read_items = {item.id: item for item in rows}
        return rows

    def files(self, instrument_id):
        if self._files is None:
            self._files = {}
            for item in SqlAlchemyInstrumentFileRepository(self.session).list():
                self._files.setdefault(item.instrument_id, []).append(item)
        return self._files.get(instrument_id, [])

    def serial_contexts(self):
        if self._serial_contexts is None:
            rows = self.catalog.list()
            self._serial_contexts = self.catalog.uow.instruments.serial_contexts(
                [item.id for item in rows if not item.serial_number.strip()])
        return self._serial_contexts

    def resolve(self, record_id):
        instrument_id = self._links().get(record_id, record_id)
        return self._read_items.get(instrument_id) or self.catalog.get(instrument_id)

    def legacy_id(self, instrument_id):
        return next((unit_id for unit_id, bound in self._links().items() if bound == instrument_id), None)

    def record(self, item, *, record_id=None):
        legacy_id = self.legacy_id(item.id)
        if self._extensions is None:
            self._extensions = {row.id: row for row in self.session.scalars(select(EquipmentMeasuringRow))}
        legacy = self._extensions.get(legacy_id)
        fields = {**item.custom_fields, **{key: _cell(getattr(item, attr)) for key, attr in FIELD_ATTRIBUTES.items()},
                  "Тип": "Средства измерений", "Наменование": item.name,
                  "obName": item.name, "obArticle": item.article, "obMaker": item.manufacturer,
                  "inventoryNumber": item.inventory_number, "obType": "Средства измерений",
                  "Для базы": f"{item.article}/{item.name}".strip("/"),
                  "baseValue": f"{item.article}/{item.name}".strip("/")}
        return TechOperationRecord(
            id=record_id or item.id, tab=TechOperationTab.MEASURING,
            display_text=item.name, normalized_search=" ".join(fields.values()).casefold(),
            source_sheet="Реестр СИ", sort_key=item.inventory_number, fields=fields,
            equipment_model_id=legacy.equipment_model_id if legacy else None,
        )

    def metadata(self, record_id):
        item = self.resolve(record_id)
        if item is None:
            return {}
        legacy = self.legacy_id(item.id)
        return {"instrument_id": str(item.id), "version": item.version,
                "legacy_unit_id": str(legacy) if legacy else None, "instrument": instrument_snapshot(item)}

    def version(self, record_id):
        return self.resolve(record_id).version

    def update_custom(self, record_id, changes, actor, now, *, source="references", version=None):
        item = self.resolve(record_id)
        if item is None:
            raise LookupError("СИ не найдено")
        try:
            saved = self.catalog.update(UpdateInstrumentCommand(
                item.id, item.version if version is None else version, {"custom_fields": changes},
                actor, now, source, self.legacy_id(item.id)))
            self._read_items.pop(item.id, None)
            return saved
        except ConcurrentProductionUpdate as error:
            raise StaleRecordError(str(error)) from error
        except ProductionValidationError as error:
            raise ReferenceValidationError(str(error)) from error

    def edit_values(self, record_id, edits, actor, now, own_columns, *, source="references", version=None):
        item = self.resolve(record_id)
        if item is None:
            raise LookupError("СИ не найдено")
        if any(edit.field not in own_columns or edit.field in FIELD_ATTRIBUTES for edit in edits):
            raise StaleRecordError(f"Паспорт СИ меняется в реестре: /?section=references&tab=ob_measuring&instrument={item.id}")
        values = dict(item.custom_fields)
        for edit in edits:
            if values.get(edit.field, "") != str(edit.old or "").strip():
                raise StaleRecordError("СИ изменилось после загрузки формы")
            values[edit.field] = edit.value
        return self.record(self.update_custom(record_id, values, actor, now, source=source, version=version))

    def edit_excel(self, record_id, edits, actor, now, own_columns, *, quality=False, version):
        if not quality:
            return self.edit_values(record_id, edits, actor, now, own_columns, source="excel", version=version)
        item = self.resolve(record_id)
        changes, custom = {}, dict(item.custom_fields)
        for edit in edits:
            attr = FIELD_ATTRIBUTES.get(edit.field)
            if attr is None:
                if edit.field not in own_columns:
                    raise ReferenceValidationError(f"Поле «{edit.field}» недоступно для правки")
                custom[edit.field] = edit.value
                changes["custom_fields"] = custom
                continue
            value = edit.value
            try:
                if attr in {"verified_at", "verification_due"}:
                    value = datetime.fromisoformat(value) if value else None
                elif attr == "quantity":
                    value = int(value)
                elif attr == "kind":
                    value = InstrumentKind(value)
                elif attr == "status":
                    value = InstrumentStatus(value)
                elif attr == "parent_id":
                    value = UUID(value) if value else None
                elif attr == "verification_number":
                    value = value or None
            except ValueError as error:
                raise ReferenceValidationError(f"Некорректное значение «{edit.field}»") from error
            changes[attr] = value
        try:
            saved = self.catalog.update(UpdateInstrumentCommand(item.id, version, changes, actor, now,
                                                               "excel", self.legacy_id(item.id)))
            self._read_items.pop(item.id, None)
            return self.record(saved)
        except ConcurrentProductionUpdate as error:
            raise StaleRecordError(str(error)) from error
        except ProductionValidationError as error:
            raise ReferenceValidationError(str(error)) from error


class InstrumentReferenceRepository(SqlAlchemyTechOperationRepository):
    def __init__(self, session):
        super().__init__(session)
        self.instrument_catalog = ReferenceInstrumentCatalog(session)
        session.info["reference_instrument_catalog"] = self.instrument_catalog

    def get(self, record_id):
        item = self.instrument_catalog.resolve(record_id)
        return self.instrument_catalog.record(item, record_id=record_id) if item else super().get(record_id)

    def delete(self, record_id):
        item = self.instrument_catalog.resolve(record_id)
        if item is not None:
            raise ReferenceValidationError(f"СИ хранится в едином реестре: /?section=references&tab=ob_measuring&instrument={item.id}")
        return super().delete(record_id)

    def list(self, *, tab, query, limit):
        tabs = [tab] if isinstance(tab, TechOperationTab) else list(tab) if tab is not None else list(TechOperationTab)
        if TechOperationTab.MEASURING not in tabs:
            return super().list(tab=tab, query=query, limit=limit)
        other_tabs = [value for value in tabs if value != TechOperationTab.MEASURING]
        rows = super().list(tab=other_tabs, query=query, limit=limit) if other_tabs else []
        rows.extend(self.instrument_catalog.record(item) for item in self.instrument_catalog.list())
        def order(row):
            group = row.tab.value if tab is None else ""
            if row.tab not in EQUIPMENT_TABS:
                return (group, ((0, row.sort_key),), row.id)
            parts = re.split(r"(\d+)", row.fields.get("Инв. номер", row.sort_key).casefold())
            return (group, tuple((1, int(p)) if p.isdecimal() else (0, p) for p in parts), row.id)
        rows.sort(key=order)
        return list(search_rows(rows, lambda row: (row.display_text, *row.fields.values()), search_words(query), limit))

    def counts(self):
        counts = super().counts()
        counts[TechOperationTab.MEASURING] = len(self.instrument_catalog.list())
        return counts


class SqlAlchemyInstrumentReferencesUnitOfWork(SqlAlchemyReferencesOperationsUnitOfWork):
    def __enter__(self):
        super().__enter__()
        self.tech_operations = InstrumentReferenceRepository(self.session)
        self.instrument_catalog = self.tech_operations.instrument_catalog
        return self

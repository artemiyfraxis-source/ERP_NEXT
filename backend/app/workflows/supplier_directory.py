"""Procurement's SupplierDirectory port over the References module's «ссылка → поставщик» map.

PR-12: SUPPLYMAP sets ПОСТАВЩИК from the ССЫЛКА's domain when a request is transferred to procurement
(Pipeline.gs:78-92, `supplierMap_`) and teaches the map when a buyer first fills an empty supplier for a line
with a link (`learnSupplier_`). Procurement never reads References' tables directly; this adapter is the
only place the two modules meet, the same shape as ProcurementLineStageLookup.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from app.modules.references.application import UnitOfWork as ReferencesUnitOfWork
from app.modules.references.domain import (
    COUNTRY_BY_MANUFACTURER,
    SUPPLIER_BY_LINK,
    ReferenceEntry,
    ReferenceKind,
    ReferenceValidationError,
    normalized_manufacturer_key,
    normalized_supplier_key,
)
from app.shared.events import AuditEvent
from app.shared.identity import Actor

# The references maps are one aggregate (see references.application._audit).
_MAPS_AGGREGATE_ID = UUID("00000000-0000-0000-0000-000000000010")


class ReferencesSupplierDirectory:
    def __init__(self, references_uow_factory: Callable[[], ReferencesUnitOfWork]):
        self.references_uow_factory = references_uow_factory

    def supplier_map(self) -> dict[str, str]:
        """`supplierMap_` (References.gs:68): the saved map, otherwise the Config SUPPLIER_BY_LINK seed — the same
        list the «Соответствия» screen shows while the map was never saved (a fresh server has it empty)."""
        with self.references_uow_factory() as uow:
            entries = list(uow.references.list())
        saved = {
            entry.normalized_key: entry.value
            for entry in entries
            if entry.kind == ReferenceKind.SUPPLIER and entry.value.strip()
        }
        return saved or {normalized_supplier_key(key): value for key, value in SUPPLIER_BY_LINK}

    def country_for_manufacturer(self, manufacturer: str) -> str:
        """`countryForManufacturer_` (References.gs:101): СТРАНА by ИЗГОТОВИТЕЛЬ from the saved map, else the COUNTRY_BY_MANUFACTURER seed, otherwise ''."""
        try:
            key = normalized_manufacturer_key(manufacturer)
        except ReferenceValidationError:
            return ""
        with self.references_uow_factory() as uow:
            entries = list(uow.references.list())
        for entry in entries:
            if entry.kind == ReferenceKind.COUNTRY and entry.normalized_key == key and entry.value.strip():
                return entry.value.strip()
        # Nothing saved for this key: the built-in seed (saved wins per key, the seed fills the rest).
        for name, country in COUNTRY_BY_MANUFACTURER:
            if normalized_manufacturer_key(name) == key:
                return country
        return ""

    def learn(self, link: str | None, supplier: str | None, actor: Actor, now: datetime) -> bool:
        """`learnSupplier_`: remember «домен → поставщик»; no link — nothing to learn; an existing key is
        never overwritten (the map is shared, a one-off purchase must not retrain it for everyone). The key is
        `supplierKey_`: the link's domain. Free text that is not a link (old data, «Склад») is never learned (owner 2026-10-04)."""
        value = str(supplier or "").strip()
        if not value or not str(link or "").strip():
            return False
        try:
            key = normalized_supplier_key(str(link))
        except ReferenceValidationError:
            return False
        if " " in key or "." not in key:
            return False  # not a link/domain (old free text such as «Склад»): nothing to learn
        with self.references_uow_factory() as uow:
            entries = list(uow.references.list())
            suppliers = [entry for entry in entries if entry.kind == ReferenceKind.SUPPLIER]
            if any(entry.normalized_key == key for entry in suppliers):
                return False
            position = max((entry.position for entry in suppliers), default=-1) + 1
            learned = ReferenceEntry(uuid4(), ReferenceKind.SUPPLIER, key, key, value, position)
            uow.references.replace(tuple(entries) + (learned,))
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=_MAPS_AGGREGATE_ID,
                    event_type="references.supplier_learned",
                    actor=actor,
                    occurred_at=now,
                    details={"key": key, "supplier": value},
                )
            )
            uow.commit()
        return True

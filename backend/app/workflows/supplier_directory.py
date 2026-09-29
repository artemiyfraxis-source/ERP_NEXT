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
    ReferenceEntry,
    ReferenceKind,
    ReferenceValidationError,
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
        with self.references_uow_factory() as uow:
            entries = list(uow.references.list())
        return {
            entry.normalized_key: entry.value
            for entry in entries
            if entry.kind == ReferenceKind.SUPPLIER and entry.value.strip()
        }

    def learn(self, link: str | None, supplier: str | None, actor: Actor, now: datetime) -> bool:
        """`learnSupplier_`: remember «домен → поставщик»; no link — nothing to learn; an existing key is
        never overwritten (the map is shared, a one-off purchase must not retrain it for everyone)."""
        value = str(supplier or "").strip()
        if not value or not str(link or "").strip():
            return False
        try:
            key = normalized_supplier_key(str(link))
        except ReferenceValidationError:
            return False  # a literal without a domain: the ERP map stores domains only
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

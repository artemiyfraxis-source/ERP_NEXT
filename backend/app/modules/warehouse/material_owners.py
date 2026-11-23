"""Minimal stable owner registry for customer-supplied stock."""

import re
from uuid import uuid4

from sqlalchemy import select

from app.shared.events import AuditEvent
from app.shared.inn import inn_valid

from .domain import WarehouseValidationError
from .grn_registry import MaterialOwnerRow


def owner_dict(row):
    return {"id": str(row.id), "display_name": row.display_name, "legal_identity": row.legal_identity}


def list_owners(uow):
    with uow:
        return [owner_dict(row) for row in uow.session.scalars(
            select(MaterialOwnerRow).order_by(MaterialOwnerRow.display_name, MaterialOwnerRow.id))]


def create_owner(uow, display_name, legal_identity, actor, now):
    name = " ".join(display_name.split())
    key = re.sub(r"\s+", "", legal_identity).upper()
    if key.startswith("INN:"):
        key = "ИНН:" + key[4:]
    if not name or len(name) > 512 or len(key) > 512 or not re.fullmatch(r"[^:]+:[^:]+", key):
        raise WarehouseValidationError("Укажите владельца и юридический ключ вида ИНН:7707083893")
    if key.startswith("ИНН:") and not inn_valid(key[4:]):
        raise WarehouseValidationError("Проверьте ИНН владельца")
    with uow:
        existing = uow.session.scalar(select(MaterialOwnerRow).where(MaterialOwnerRow.legal_identity == key))
        if existing is not None:
            return owner_dict(existing)
        row = MaterialOwnerRow(id=uuid4(), display_name=name, legal_identity=key)
        uow.session.add(row)
        uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=row.id, event_type="grn.material_owner_created",
            actor=actor, occurred_at=now, details={"display_name": name, "legal_identity": key}))
        uow.commit()
        return owner_dict(row)

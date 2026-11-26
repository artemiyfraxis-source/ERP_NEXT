"""Receiving's StockBalanceLookup port over the Warehouse module's own lots.

RC-6: a supplier return writes the returned quantity off only when the lot of the line's ГРН + ПАКЕТ
really lies on stock (SupplierReturn.gs warehouseGrnInfo_ looks in ЦАР and ЖУК). Receiving never reads
Warehouse's tables, so this is a read-only port, the same shape as ProcurementLineStageLookup. The balance
it reports is what a write-off may take: ERP never writes off into the reserve (WH-2).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid5, NAMESPACE_URL

from sqlalchemy import select

from app.modules.receiving.domain import ReceivingValidationError
from app.modules.warehouse.domain import WarehouseValidationError
from app.modules.warehouse.grn_registry import MaterialOwnerRow
from app.shared.persistence import AuditEventRow
from app.shared.clock import as_utc
from .grn_allocation import GrnCategory, GrnContext, GrnRequest, fingerprint_body, grn_allocator
from .warehouse_grn_context import validate_warehouse_grn_context

from app.modules.receiving.application import StockLotBalance
from app.modules.warehouse.application import UnitOfWork as WarehouseUnitOfWork

WAREHOUSES = ("ЦАР", "ЖУК")

GRN_FIELDS = ("grn_id", "grn_category", "grn_context", "grn_contract_version", "material_owner_id",
              "quality_state", "quality_basis", "quality_decided_by", "quality_decided_at",
              "quality_version", "expiry_required")


def json_value(value):
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, (UUID, date, datetime)):
        return value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    return value


def receiving_command_identity(command, kind):
    body = asdict(command)
    body.pop("now", None)
    body.pop("actor", None)
    body.pop("quality_authorized", None)
    for inspection in ([body["inspection"]] if "inspection" in body else body.get("batches", [])):
        inspection.pop("inspection_date", None)
        inspection.pop("quality_authorized", None)
        if not inspection.get("historical_verdict"):
            inspection.pop("verdict_date", None)
    if kind == "delivery":
        keys = sorted(f"{item.line_id}:{item.expected_version}" for item in command.lines)
        for item in body["lines"]:
            item.pop("expected_version")
        body["supplier_aliases"] = sorted(body["supplier_aliases"])
    else:
        keys = [f"{command.line_id}:{command.expected_version}"]
        body.pop("expected_version")
    key = uuid5(NAMESPACE_URL, "receiving-command:" + kind + ":" + ",".join(keys))
    return key, fingerprint_body(json_value(body))


def receiving_command_details(session, key):
    return session.scalar(select(AuditEventRow.details).where(
        AuditEventRow.id == key, AuditEventRow.event_type == "receiving.command"))


def prepare_receiving_grns(uow, pairs, now):
    """One allocation per physical receipt; QC revisions and packages retain that identity."""
    requests, positions = [], []
    result = [line for line, _ in pairs]
    for index, (line, data) in enumerate(pairs):
        if line.grn:
            if data.grn_category is not None and data.grn_category != line.grn_category:
                raise ReceivingValidationError("Категория выданного ГРН не меняется")
            if data.grn_context is not None:
                try:
                    values = dict(data.grn_context)
                    if values.get("owner_id"):
                        values["owner_id"] = UUID(str(values["owner_id"]))
                    context = validate_warehouse_grn_context(uow, GrnCategory(line.grn_category), GrnContext(**values))
                except (ValueError, TypeError) as error:
                    raise ReceivingValidationError(str(error)) from error
                if context.payload() != line.grn_context:
                    raise ReceivingValidationError("Контекст выданного ГРН не меняется")
            continue
        try:
            category = GrnCategory(data.grn_category)
            values = dict(data.grn_context or {})
            if values.get("owner_id"):
                values["owner_id"] = UUID(str(values["owner_id"]))
            context = GrnContext(**values)
            context = validate_warehouse_grn_context(uow, category, context)
            if category == GrnCategory.CSM and (context.owner_id is None or uow.session.get(MaterialOwnerRow, context.owner_id) is None):
                raise ValueError("Укажите существующего владельца давальческого материала")
            # A receiving line belongs to its purchase order; external manufacturing orders cannot be invented.
            if category in {GrnCategory.SUB, GrnCategory.FRX}:
                if not line.order_number or context.production_order != line.order_number:
                    raise ValueError("Производственный заказ должен совпадать с заказом позиции")
            passport = asdict(data)
            passport.pop("inspection_date")
            passport.pop("quality_authorized")
            if not data.historical_verdict:
                passport.pop("verdict_date")
            request = GrnRequest(category=category, source_kind="receiving",
                source_op_id=f"{line.lineage_root_id}:r{line.supplier_returns}", context=context,
                body_fingerprint=fingerprint_body(json_value({
                    "line_id": line.lineage_root_id, "article": line.article, "name": line.name,
                    "unit": line.unit, "warehouse": line.warehouse, "package": line.package,
                    "supplier": line.supplier, "procurement_line_id": line.procurement_line_id,
                    "ordered_quantity": line.ordered_quantity, "passport": passport,
                })))
        except (ValueError, TypeError, WarehouseValidationError) as error:
            raise ReceivingValidationError(f"Категория и контекст ГРН: {error}") from error
        requests.append(request)
        positions.append(index)
    identities = grn_allocator(uow.session).allocate_many(tuple(requests), registered_at=as_utc(now)) if requests else ()
    for index, identity in zip(positions, identities):
        line, data = pairs[index]
        result[index] = replace(line, grn=identity.number, grn_id=identity.id,
            grn_category=identity.category.value, grn_context=identity.context.payload(), grn_contract_version=1,
            material_owner_id=identity.context.owner_id, quality_state="IQH", quality_version=1,
            expiry_required=data.expiry_required)
    return result


class WarehouseStockBalanceLookup:
    def __init__(self, warehouse_uow_factory: Callable[[], WarehouseUnitOfWork]):
        self.warehouse_uow_factory = warehouse_uow_factory

    def lot_balance(self, grn: str, package: str) -> StockLotBalance | None:
        with self.warehouse_uow_factory() as uow:
            for warehouse in WAREHOUSES:
                lot = uow.warehouse.by_key(warehouse, grn, package)
                if lot is not None:
                    return StockLotBalance(warehouse=lot.warehouse, balance=lot.available, unit=lot.unit)
        return None

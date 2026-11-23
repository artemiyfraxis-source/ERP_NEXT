"""Receiving cutover: physical identity, whole-place quality and transactional retries."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.modules.receiving.application import (
    AcceptDeliveryCommand, ConcurrentReceivingUpdate, DeliveryLineInput, InspectBatchesCommand,
    InspectReceivingCommand, OpenVkRevisionCommand, PlaceReceivingCommand, accept_delivery,
    consume_receiving_expected, inspect_receiving, inspect_receiving_batches, open_vk_revision,
    place_receiving, return_to_supplier,
)
from app.modules.receiving.domain import ReceivingStatus, ReceivingValidationError, SupplierReturnMode
from app.modules.warehouse.application import dispatch_next_warehouse_received
from app.modules.warehouse.grn_registry import GrnRegistryRow, GrnCounterRow
from app.shared.persistence import OutboxMessageRow
from tests.receiving.test_supplier_return import (
    NOW, RECEIVER, erp, expected_message, inspection, command as supplier_return,
)
from tests.receiving.test_api import receiving_api


def inspect_command(line, **changes):
    return InspectReceivingCommand(line.id, line.version, replace(inspection(), **changes), RECEIVER, NOW)


def counts(factory):
    with factory() as uow:
        return (uow.session.scalar(select(func.count()).select_from(GrnRegistryRow)),
                uow.session.scalar(select(func.count()).select_from(OutboxMessageRow)
                                   .where(OutboxMessageRow.topic == "warehouse.received")))


def test_new_receipt_requires_explicit_category_and_rolls_back(erp):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    with pytest.raises(ReceivingValidationError, match="Категория"):
        inspect_receiving(inspect_command(line, grn_category=None), receiving())
    assert counts(receiving) == (0, 0)


def test_receiver_gets_identity_but_only_qc_can_release_without_upd(erp):
    receiving, warehouse, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    held = inspect_receiving(inspect_command(line, quality_authorized=False, shipping_document_url=None), receiving())
    assert held.grn == "PPM.26.000001" and held.grn_id
    assert held.quality_state == "IQH" and held.status is ReceivingStatus.WAITING
    assert counts(receiving) == (1, 0)
    with pytest.raises(ReceivingValidationError):
        inspect_receiving(inspect_command(held, quality_authorized=False, quality_state="RELEASED", quality_basis="pass"), receiving())
    released = inspect_receiving(inspect_command(held, grn_context={}, shipping_document_url=None), receiving())
    assert released.quality_state == "RELEASED" and released.grn_id == held.grn_id
    assert released.shipping_document_pending
    place_receiving(PlaceReceivingCommand(released.id, released.version, "1F4", RECEIVER, NOW), receiving())
    lot = dispatch_next_warehouse_received(NOW, warehouse())
    assert lot.grn_id == held.grn_id and lot.quality_state == "RELEASED"
    assert lot.quality_decided_by == RECEIVER.id and lot.grn_contract_version == 1
    assert dispatch_next_warehouse_received(NOW, warehouse()) is None
    with receiving() as uow:
        uow.session.scalar(select(OutboxMessageRow).where(OutboxMessageRow.topic == "warehouse.received")).published_at = None
        uow.commit()
    replayed_lot = dispatch_next_warehouse_received(NOW, warehouse())
    assert replayed_lot.id == lot.id and replayed_lot.balance == Decimal(10)
    assert counts(receiving) == (1, 1)


def test_same_inspection_replays_after_midnight_but_changed_body_conflicts(erp):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    original = inspect_command(line)
    first = inspect_receiving(original, receiving())
    retry = replace(original, now=NOW + timedelta(days=1), inspection=replace(original.inspection,
        inspection_date=NOW.date() + timedelta(days=1), verdict_date=NOW.date() + timedelta(days=1)))
    assert inspect_receiving(retry, receiving()).grn_id == first.grn_id
    with pytest.raises(ConcurrentReceivingUpdate):
        inspect_receiving(replace(original, inspection=replace(original.inspection, fact_quantity=Decimal(9))), receiving())
    assert counts(receiving) == (1, 0)


def test_mixed_delivery_has_per_line_category_and_atomic_replay(erp):
    receiving, _, _ = erp
    lines = [consume_receiving_expected(expected_message(i), receiving()) for i in (1, 2)]
    command = AcceptDeliveryCommand(lines=(
        DeliveryLineInput(lines[0].id, 1, grn_category="PPM"),
        DeliveryLineInput(lines[1].id, 1, grn_category="GOS", grn_context={"purpose": "Уборка"},
                          quality_state="NOT_REQUIRED", quality_basis="Хозяйственные материалы"),
    ), document_number="", document_date=None, actor=RECEIVER, now=NOW)
    result = accept_delivery(command, receiving())
    assert [line.grn for line in result.lines] == ["PPM.26.000001", "GOS.26.000001"]
    assert [line.quality_state for line in result.lines] == ["IQH", "NOT_REQUIRED"]
    assert accept_delivery(replace(command, now=NOW + timedelta(days=1)), receiving()).delivery_id == result.delivery_id
    assert counts(receiving) == (2, 0)


def test_batch_categories_and_retry_preserve_physical_batch_ids(erp):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    command = InspectBatchesCommand(line.id, 1, (
        replace(inspection("5"), document_quantity=Decimal(5), series="00007"),
        replace(inspection("5"), document_quantity=Decimal(5), series="00008", grn_category="PAM",
                grn_context={"equipment": "Стенд"}, quality_state="NOT_REQUIRED", quality_basis="Оснастка"),
    ), RECEIVER, NOW)
    first = inspect_receiving_batches(command, receiving())
    again = inspect_receiving_batches(command, receiving())
    assert [line.grn for line in first] == ["PPM.26.000001", "PAM.26.000001"]
    assert [line.id for line in first] == [line.id for line in again]
    assert first[0].series == "00007" and counts(receiving) == (2, 0)


def test_replacement_revision_replacement_uses_three_physical_identities(erp):
    receiving, _, stock = erp
    line = consume_receiving_expected(expected_message(), receiving())
    first = inspect_receiving(inspect_command(line), receiving())
    waiting = return_to_supplier(supplier_return(first, mode=SupplierReturnMode.WAIT), receiving(), stock).lines[0]
    second = inspect_receiving(inspect_command(waiting), receiving())
    _, revision = open_vk_revision(OpenVkRevisionCommand(second.id, second.version, RECEIVER, NOW), receiving())
    assert revision.supplier_returns == 1 and revision.grn_id == second.grn_id
    revised = inspect_receiving(inspect_command(revision), receiving())
    waiting = return_to_supplier(supplier_return(revised, mode=SupplierReturnMode.WAIT), receiving(), stock).lines[0]
    third = inspect_receiving(inspect_command(waiting), receiving())
    assert len({first.grn_id, second.grn_id, third.grn_id}) == 3
    assert counts(receiving) == (3, 0)


@pytest.mark.parametrize("changes", [
    {"expiry_required": True, "expiry_date": None},
    {"expiry_date": date(2025, 1, 1)},
    {"manual_nonconformities": ("Механическое повреждение / брак",), "defect_quantity": Decimal(1)},
])
def test_no_release_of_expired_unknown_or_partially_defective_place(erp, changes):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    with pytest.raises(ReceivingValidationError):
        inspect_receiving(inspect_command(line, quality_state="RELEASED", quality_basis="ОТК", **changes), receiving())
    assert counts(receiving) == (0, 0)


def test_invalid_second_delivery_line_rolls_back_numbers_and_stock_events(erp):
    receiving, _, _ = erp
    lines = [consume_receiving_expected(expected_message(i), receiving()) for i in (1, 2)]
    cmd = AcceptDeliveryCommand(tuple(DeliveryLineInput(line.id, 1, grn_category="PPM",
        storage_location="1F4" if i == 0 else "33A1") for i, line in enumerate(lines)),
        "", None, RECEIVER, NOW, quality_authorized=True)
    with pytest.raises(ReceivingValidationError):
        accept_delivery(cmd, receiving())
    assert counts(receiving) == (0, 0)
    with receiving() as uow:
        assert uow.session.scalar(select(func.count()).select_from(GrnCounterRow)) == 0


def test_csm_context_owner_and_project_follow_receipt_to_stock(erp):
    from tests.warehouse.test_grn_acceptance import csm_context
    receiving, warehouse, _ = erp
    context = csm_context(receiving().session_factory)
    line = consume_receiving_expected(expected_message(), receiving())
    accepted = inspect_receiving(inspect_command(line, grn_category="CSM", grn_context=context), receiving())
    again = inspect_receiving(inspect_command(accepted, grn_category="CSM", grn_context=context), receiving())
    assert again.grn_id == accepted.grn_id
    place_receiving(PlaceReceivingCommand(again.id, again.version, "1F4", RECEIVER, NOW), receiving())
    lot = dispatch_next_warehouse_received(NOW, warehouse())
    assert str(lot.material_owner_id) == context["owner_id"]
    assert lot.csm_project_id == context["customer_order"]
    assert lot.grn == "CSM.26.000001"


@pytest.mark.parametrize("category", ["SUB", "FRX", "R&D"])
def test_remaining_receiving_categories_require_verified_origin(erp, category):
    from app.modules.production.infrastructure import ProductionPlanProjectRow
    receiving, _, _ = erp
    message = expected_message()
    line = consume_receiving_expected(replace(message, payload={**message.payload, "order_number": "ORDER-1"}), receiving())
    context = {"production_order": "ORDER-1", "acceptance_document": "ACT-1", "subcontractor": "Contractor"}
    if category == "R&D":
        with receiving() as uow:
            uow.session.add(ProductionPlanProjectRow(project="591К.3", customer="", status="", terminal=False, imported_at=NOW))
            uow.commit()
        context = {"project": "591К.3", "purpose": "Prototype"}
    result = inspect_receiving(inspect_command(line, grn_category=category, grn_context=context), receiving())
    assert result.grn == f"{category}.26.000001"


def test_http_receiver_cannot_forge_quality_capability_and_admin_can_decide(receiving_api):
    from app.main import app
    from app.shared.http_auth import Principal, authenticated_principal
    client, line = receiving_api
    principal = Principal(actor=RECEIVER, email="receiver@example.test", roles=frozenset({"receiver"}), permissions={"receiving": "edit"})
    app.dependency_overrides[authenticated_principal] = lambda: principal
    payload = {"version": line.version, "fact_quantity": "10", "grn_category": "PPM", "quality_authorized": True}
    held = client.post(f"/api/receiving/{line.id}/inspect", json=payload).json()["line"]
    assert held["quality_state"] == "IQH"
    payload.update(version=held["version"], quality_state="RELEASED", quality_basis="ОТК")
    assert client.post(f"/api/receiving/{line.id}/inspect", json=payload).status_code == 422
    principal = replace(principal, roles=frozenset({"admin"}), permissions={})
    released = client.post(f"/api/receiving/{line.id}/inspect", json=payload)
    assert released.status_code == 200 and released.json()["line"]["quality_state"] == "RELEASED"

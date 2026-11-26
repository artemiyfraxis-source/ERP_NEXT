"""Whole-place quality and customer ownership rules for contract 0218."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.warehouse.domain import WarehouseValidationError
from tests.warehouse.test_domain import lot


TODAY = date(2026, 11, 23)
DECIDED_AT = datetime(2026, 11, 23, 10, tzinfo=UTC)
INSPECTOR = uuid4()
OWNER = uuid4()


def new_lot(**changes):
    values = dict(
        grn="PPM.26.000001", grn_id=uuid4(), grn_category="PPM", grn_context={},
        grn_contract_version=1, quality_state="IQH", quality_version=1, expiry_required=False,
    )
    return replace(lot(), **(values | changes))


def released(**changes):
    return new_lot(**(dict(
        quality_state="RELEASED", quality_basis="ВК всего места: годен", quality_decided_by=INSPECTOR,
        quality_decided_at=DECIDED_AT,
    ) | changes))


@pytest.mark.parametrize("state", [None, "IQH", "QTN", "REJECTED", "unknown"])
@pytest.mark.parametrize("action", ["reserve", "write_off", "issue_reserved"])
def test_every_consuming_path_blocks_a_held_or_unknown_new_place(state, action):
    held = new_lot(quality_state=state, reserved=Decimal("2") if action == "issue_reserved" else Decimal("0"))
    with pytest.raises(WarehouseValidationError, match="решение ОТК"):
        getattr(held, action)(Decimal("1"), on_date=TODAY)


def test_release_then_reserve_then_hold_does_not_authorize_issue():
    reserved = released().reserve(Decimal("3"), on_date=TODAY)
    held = replace(reserved, quality_state="QTN", quality_version=2, version=reserved.version + 1)
    with pytest.raises(WarehouseValidationError, match="решение ОТК"):
        held.issue_reserved(Decimal("3"), on_date=TODAY)
    assert held.reserved == 3 and held.written_off == 0
    assert held.release_reserve(Decimal("3")).reserved == 0


@pytest.mark.parametrize("field,value", [
    ("quality_basis", " "), ("quality_decided_by", None), ("quality_decided_at", None),
    ("quality_version", None), ("quality_version", 0),
])
def test_release_requires_recorded_decision_evidence(field, value):
    with pytest.raises(WarehouseValidationError, match="основание, автор или дата"):
        released(**{field: value}).reserve(Decimal("1"), on_date=TODAY)


@pytest.mark.parametrize("category,context", [
    ("GOS", {"purpose": "Уборка цеха"}), ("PAM", {"equipment": "ОБ.099"}),
])
def test_nonrequired_qc_uses_a_recorded_basis_without_requiring_upd(category, context):
    stock = released(grn=f"{category}.26.000001", grn_category=category, grn_context=context,
                     quality_state="NOT_REQUIRED", quality_basis="ВК не требуется: утверждённый маршрут")
    assert stock.receipt_id is None
    assert stock.reserve(Decimal("2"), on_date=TODAY).issue_reserved(Decimal("2"), on_date=TODAY).balance == 8
    with pytest.raises(WarehouseValidationError, match="основание, автор или дата"):
        replace(stock, quality_basis="").reserve(Decimal("1"), on_date=TODAY)


@pytest.mark.parametrize("category,context", [
    ("PPM", {}),
    ("CSM", {"owner_id": str(OWNER), "customer_order": "order:1", "transfer_document": "Акт 1"}),
    ("SUB", {"subcontractor": "contractor:1", "production_order": "order:1", "acceptance_document": "Акт 1"}),
])
def test_required_routes_cannot_use_nonrequired_qc(category, context):
    stock = released(grn_category=category, grn_context=context, quality_state="NOT_REQUIRED")
    with pytest.raises(WarehouseValidationError, match="решение ОТК"):
        stock.reserve(Decimal("1"), on_date=TODAY)


@pytest.mark.parametrize("action", ["reserve", "write_off", "issue_reserved"])
@pytest.mark.parametrize("expiry_required,expiry_date", [(True, None), (True, TODAY - timedelta(days=1)),
                                                       (False, TODAY - timedelta(days=1))])
def test_unknown_required_or_expired_dates_block_consumption(action, expiry_required, expiry_date):
    stock = released(expiry_required=expiry_required, expiry_date=expiry_date,
                     reserved=Decimal("2") if action == "issue_reserved" else Decimal("0"))
    with pytest.raises(WarehouseValidationError, match="срок годности|Срок годности"):
        getattr(stock, action)(Decimal("1"), on_date=TODAY)


def test_expiry_day_is_inclusive_and_next_day_blocks_an_existing_reservation():
    reserved = released(expiry_required=True, expiry_date=TODAY).reserve(Decimal("2"), on_date=TODAY)
    assert reserved.issue_reserved(Decimal("1"), on_date=TODAY).balance == 9
    with pytest.raises(WarehouseValidationError, match="истёк"):
        reserved.issue_reserved(Decimal("1"), on_date=TODAY + timedelta(days=1))


@pytest.mark.parametrize("fields", [
    {"grn_id": None}, {"grn_contract_version": 2}, {"grn_category": None}, {"grn_context": None},
    {"expiry_required": None}, {"grn_category": "PAM", "grn_context": {}},
])
def test_incomplete_new_passport_is_never_issuable(fields):
    with pytest.raises(WarehouseValidationError):
        released(**fields).reserve(Decimal("1"), on_date=TODAY)


def customer_lot():
    return released(grn="CSM.26.000001", grn_category="CSM", material_owner_id=OWNER, csm_project_id="591К.3",
                    grn_context={"owner_id": str(OWNER), "customer_order": "591К.3", "transfer_document": "Акт 1"})


@pytest.mark.parametrize("action", ["reserve", "write_off", "issue_reserved"])
@pytest.mark.parametrize("owner_id,customer_order", [(None, None), (uuid4(), "order:1"),
                                                     (OWNER, "order:2"), (OWNER, None)])
def test_customer_material_cannot_be_consumed_by_another_owner_or_order(action, owner_id, customer_order):
    stock = customer_lot()
    if action == "issue_reserved":
        stock = replace(stock, reserved=Decimal("2"))
    with pytest.raises(WarehouseValidationError, match="владелец и заказ"):
        getattr(stock, action)(Decimal("1"), owner_id=owner_id, customer_order=customer_order, on_date=TODAY)


def test_customer_issue_return_and_transfer_keep_exact_identity_owner_and_quality():
    stock = customer_lot()
    reserved = stock.reserve(Decimal("2"), owner_id=OWNER, customer_order="591К.3", project_id="591К.3", on_date=TODAY)
    issued = reserved.issue_reserved(Decimal("2"), owner_id=OWNER, customer_order="591К.3", project_id="591К.3", on_date=TODAY)
    returned = issued.return_writeoff(Decimal("1"), owner_id=OWNER, customer_order="591К.3", project_id="591К.3")
    transferred = returned.transfer("ЖУК", "2A1")
    assert transferred.id == stock.id and transferred.grn_id == stock.grn_id
    assert transferred.material_owner_id == OWNER and transferred.grn_context == stock.grn_context
    assert transferred.quality_state == "RELEASED" and transferred.balance == 9


def test_hold_survives_physical_relocation():
    stock = new_lot()
    moved = stock.transfer("ЖУК", "2A1").move("2A2")
    assert moved.quality_state == "IQH" and moved.grn_id == stock.grn_id and moved.balance == stock.balance


@pytest.mark.parametrize("changes", [{"reserved": "1"}])
def test_manual_quantity_edit_cannot_bypass_hold(changes):
    with pytest.raises(WarehouseValidationError, match="решение ОТК"):
        new_lot().edit_row(changes)


@pytest.mark.parametrize("field", ["reserved"])
@pytest.mark.parametrize("expiry", [None, date(2000, 1, 1)])
def test_quantity_and_expiry_edit_cannot_consume_the_resulting_blocked_place(field, expiry):
    stock = released(expiry_required=True, expiry_date=date.today() + timedelta(days=30))
    with pytest.raises(WarehouseValidationError, match="срок годности|Срок годности"):
        stock.edit_row({field: "1", "expiry_date": expiry})


def test_customer_material_cannot_be_reserved_by_unscoped_manual_edit():
    with pytest.raises(WarehouseValidationError, match="владелец и заказ"):
        customer_lot().edit_row({"reserved": "1"})


def test_historical_stock_is_not_implicitly_reclassified_or_mass_blocked():
    historical = lot()
    reserved = historical.reserve(Decimal("2"))
    assert reserved.issue_reserved(Decimal("2")).balance == 8
    assert historical.grn_contract_version is None and historical.quality_state is None


@pytest.mark.parametrize("state", ["IQH", "QTN", "REJECTED"])
def test_scrap_and_supplier_return_can_take_a_held_place_out_but_issue_cannot(state):
    held = new_lot(quality_state=state)
    assert held.write_off(Decimal("1"), on_date=TODAY, disposal=True).written_off == 1
    with pytest.raises(WarehouseValidationError, match="решение ОТК"):
        held.write_off(Decimal("1"), on_date=TODAY)


def test_scrap_works_after_the_shelf_life_expired():
    expired = released(expiry_required=True, expiry_date=TODAY - timedelta(days=1))
    assert expired.write_off(Decimal("1"), on_date=TODAY, disposal=True).balance == 9
    with pytest.raises(WarehouseValidationError, match="Срок годности"):
        expired.write_off(Decimal("1"), on_date=TODAY)


CSM_CONTEXT = {"owner_id": str(OWNER), "customer_order": "order:1", "transfer_document": "Акт 1"}


def csm_lot(**changes):
    return released(**({"grn": "CSM.26.000001", "grn_category": "CSM", "grn_context": CSM_CONTEXT,
                        "material_owner_id": OWNER, "csm_project_id": "order:1"} | changes))


def test_csm_place_can_be_written_off_returned_and_edited_with_its_own_owner_checks():
    stock = csm_lot()
    off = stock.write_off(Decimal("1"), on_date=TODAY, **stock.owner_claim())
    assert off.return_writeoff(Decimal("1"), **off.owner_claim()).written_off == stock.written_off
    assert stock.edit_row({"written_off": Decimal("4")}).written_off == 4


def test_csm_place_with_a_broken_owner_pair_still_refuses():
    broken = csm_lot(material_owner_id=None)
    with pytest.raises(WarehouseValidationError, match="владелец"):
        broken.write_off(Decimal("1"), on_date=TODAY, **broken.owner_claim())


@pytest.mark.parametrize("category,context", [("FRX", {"production_order": "o:1", "acceptance_document": "A"}),
                                              ("R&D", {"project": "p:1", "purpose": "Испытания"})])
def test_frx_and_rd_may_use_the_nonrequired_qc_decision(category, context):
    stock = new_lot(grn=f"{category}.26.000001", grn_category=category, grn_context=context)
    decided = stock.decide_quality("NOT_REQUIRED", "Маршрут без ВК", INSPECTOR, DECIDED_AT)
    assert decided.reserve(Decimal("1"), on_date=TODAY).reserved == 1


def test_manual_writeoff_edit_may_take_a_held_place_out():
    assert new_lot().edit_row({"written_off": "1"}).written_off == 1

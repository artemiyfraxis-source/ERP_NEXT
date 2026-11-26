"""Registry allocation at own-production hand-in, including legacy coexistence and quality holds."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.modules.shipping.application import _good_lots, ConcurrentShippingUpdate
from app.modules.shipping.domain import FinishedGood, ShippingValidationError
from app.modules.shipping.infrastructure import FinishedGoodsActRow, FinishedGoodRow
from app.modules.purchasing.infrastructure import InternalProjectRow
from app.modules.warehouse.grn_registry import GrnCounterRow, GrnRegistryRow
from app.workflows.finished_goods import (
    AcceptOneCommand, AcceptBatchCommand, AcceptLine, accept_one, accept_batch, flow_uow_factory,
)
from tests.shipping.test_acceptance_acts import shop, NOW, STOREKEEPER, _register, _accept, _acts


def test_new_acts_share_first_identity_with_aggregate_and_retain_legacy_aggregate(shop):
    _, sessions, factory = shop
    good = _register(factory)
    first = _accept(factory, good.id, 3)
    assert first.grn == "FRX.26.000001" and first.grn_id == _acts(factory, good.id)[0].grn_id
    with sessions.begin() as session:
        legacy = FinishedGood.create(project="old", item_index="KB", customer="", article="A", name="old", ordered=Decimal(100), unit="pcs")
        from app.modules.shipping.infrastructure import SqlAlchemyFinishedGoodsRepository
        SqlAlchemyFinishedGoodsRepository(session).add(replace(legacy, grn="ГП.25.0000099"))
    accepted = _accept(factory, legacy.id, 2)
    assert accepted.grn == "ГП.25.0000099" and accepted.grn_contract_version is None
    act, = _acts(factory, legacy.id)
    assert act.grn == "FRX.26.000002" and act.quality_state == "RELEASED"
    assert act.quality_decided_by == STOREKEEPER.id


def test_registered_rd_project_uses_literal_ampersand_and_separate_counter(shop):
    _, sessions, factory = shop
    with sessions.begin() as session:
        session.add(InternalProjectRow(id=uuid4(), number="ВП-001", name="Prototype", purpose="RND",
            created_by=STOREKEEPER.id, created_at=NOW, updated_by=STOREKEEPER.id, updated_at=NOW))
    good = _register(factory)
    with sessions.begin() as session:
        session.get(FinishedGoodRow, good.id).project = "ВП-001"
    accepted = _accept(factory, good.id, 5)
    assert accepted.grn == "R&D.26.000001" and accepted.grn_context["project"] == "ВП-001"
    with sessions() as session:
        assert session.scalar(select(GrnCounterRow.category)) == "R&D"


def test_single_production_acceptance_replays_and_changed_quantity_conflicts(shop):
    _, sessions, factory = shop
    good = _register(factory)
    flow = flow_uow_factory(factory)
    cmd = AcceptOneCommand(good.id, good.version, Decimal(4), None, STOREKEEPER, NOW, confirm_beyond_tasks=True)
    first = accept_one(cmd, flow)
    assert accept_one(cmd, flow).accepted == Decimal(4)
    with pytest.raises(ConcurrentShippingUpdate):
        accept_one(replace(cmd, quantity=Decimal(5)), flow)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(GrnRegistryRow)) == 1
    assert len(_acts(factory, first.id)) == 1


def test_held_act_does_not_block_other_places_or_leak_through_fifo_fallback(shop):
    _, sessions, factory = shop
    good = _register(factory)
    _accept(factory, good.id, 3)
    current = _accept(factory, good.id, 5)
    first, second = _acts(factory, good.id)
    with sessions.begin() as session:
        session.get(FinishedGoodsActRow, second.id).quality_state = "QTN"
    with factory() as uow:
        available = _good_lots({good.id: current}, {}, uow)[good.id]
        assert available.available == 3
        with pytest.raises(ShippingValidationError):
            available.take(Decimal(4))
        chosen = _good_lots({good.id: current}, {good.id: first.grn}, uow)[good.id]
        assert chosen.take(Decimal(3))[0][0].grn == first.grn
        with pytest.raises(ShippingValidationError):
            _good_lots({good.id: current}, {good.id: second.grn}, uow)


def test_new_act_label_passes_category_and_quality_or_refuses_old_renderer(shop):
    from dataclasses import fields
    from app.workflows.finished_goods_label import act_label_data
    from app.modules.warehouse.labels.layout import GrnLabelData
    _, sessions, factory = shop
    good = _register(factory)
    accepted = _accept(factory, good.id, 2)
    label = act_label_data(sessions, accepted.grn)
    assert label.registry_fields["grn_id"] == accepted.grn_id
    assert label.registry_fields["category"] == "FRX" and label.registry_fields["quality_state"] == "RELEASED"
    if "category" not in {item.name for item in fields(GrnLabelData)}:
        with pytest.raises(ValueError, match="шаблонов категорий"):
            _ = label.content
    else:
        assert label.content.category == "FRX" and label.content.grn_id == accepted.grn_id


def test_production_capacity_is_actionable_422_and_rolls_back_acceptance(shop):
    from fastapi import HTTPException
    from app.workflows.finished_goods_api import _guarded
    _, sessions, factory = shop
    good = _register(factory)
    with sessions.begin() as session:
        session.add(GrnCounterRow(category="FRX", full_year=2026, last_serial=999999))
    cmd = AcceptOneCommand(good.id, good.version, Decimal(1), None, STOREKEEPER, NOW, confirm_beyond_tasks=True)
    with pytest.raises(HTTPException) as error:
        _guarded(lambda: accept_one(cmd, flow_uow_factory(factory)))
    assert error.value.status_code == 422
    with factory() as uow:
        assert uow.goods.get(good.id).accepted == 0
        assert uow.goods.acts([good.id]) == {}


def test_mixed_production_batch_preserves_response_order_and_replays_both_categories(shop):
    _, sessions, factory = shop
    frx = _register(factory, "КБ1")
    rd = _register(factory, "КБ2")
    with sessions.begin() as session:
        session.add(InternalProjectRow(id=uuid4(), number="ВП-001", name="Prototype", purpose="RND",
                                      created_at=NOW, created_by=STOREKEEPER.id))
        session.get(FinishedGoodRow, rd.id).project = "ВП-001"
    command = AcceptBatchCommand(idempotency_key="mixed-categories", lines=(
        AcceptLine("ВП-001", "КБ2", Decimal(2), confirm_beyond_tasks=True),
        AcceptLine(frx.project, "КБ1", Decimal(3), confirm_beyond_tasks=True),
    ), received_at=None, actor=STOREKEEPER, now=NOW)
    flow = flow_uow_factory(factory)
    first = accept_batch(command, flow)
    assert [good.grn for good in first.goods] == ["R&D.26.000001", "FRX.26.000001"]
    again = accept_batch(command, flow)
    assert again.replayed and [good.accepted for good in again.goods] == [2, 3]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(GrnRegistryRow)) == 2

from datetime import date
from decimal import Decimal

from types import SimpleNamespace

from app.modules.fulfillment.domain import FulfillmentStatus
from app.modules.production.plan import kit_stage_key, project_stage
from app.modules.shipping.domain import FinishedGood
from app.workflows.production_plan import kit_stage_facts


def _facts(*goods: FinishedGood) -> dict[str, set[str]]:
    facts, _marks = kit_stage_facts(cards=(), tasks=(), fulfillment=(), goods=goods, marks={})
    return facts


def test_kb_is_shipped_when_the_run_left_the_plant_not_when_it_was_packed():
    good = FinishedGood.create(project="591К.3", item_index="КБ1", customer="", article="", name="Жгут",
                               ordered=Decimal("10"), unit="шт").accept(Decimal("10"), date(2026, 9, 22), "ГП.26.0000001")
    key = kit_stage_key("591К.3", "КБ1")
    packed = good.take(Decimal("10"))

    assert key in _facts(packed)["stock"] and key not in _facts(packed)["shipped"]
    assert key in _facts(packed.dispatch(Decimal("10")))["shipped"]


def test_partial_shipment_before_erp_is_not_a_shipped_kb():
    good = FinishedGood.create(project="70К.26.13.2", item_index="КБ1", customer="", article="", name="Жгут",
                               ordered=Decimal("10270"), unit="шт")
    key = kit_stage_key("70К.26.13.2", "КБ1")

    assert key not in _facts(good.ship_before_erp(Decimal("154")))["shipped"]
    assert key in _facts(good.ship_before_erp(Decimal("10270")))["shipped"]


def _kit_facts(status: FulfillmentStatus) -> dict[str, set[str]]:
    request = SimpleNamespace(status=status, purpose="33МФ.2-КБ1 жгут")
    facts, _marks = kit_stage_facts(cards=(), tasks=(), fulfillment=(request,), goods=(), marks={})
    return facts


def test_issued_kit_means_work_started_picked_kit_does_not():
    key = kit_stage_key("33МФ.2", "КБ1")
    for status in (FulfillmentStatus.ISSUED, FulfillmentStatus.RETURNED, FulfillmentStatus.DONE):
        facts = _kit_facts(status)
        assert key in facts["kit"] and key in facts["work"]
        assert project_stage("33МФ.2", ("КБ1",), facts).status == "В работе"
    for status in (FulfillmentStatus.COMPLETE, FulfillmentStatus.READY):
        facts = _kit_facts(status)
        assert key in facts["kit"] and key not in facts["work"]
        assert project_stage("33МФ.2", ("КБ1",), facts).status == "КБ набрана"


def test_issued_kit_does_not_hold_back_later_stages():
    key = kit_stage_key("33МФ.2", "КБ1")
    facts = _kit_facts(FulfillmentStatus.ISSUED)
    facts["stock"].add(key)
    assert project_stage("33МФ.2", ("КБ1",), facts).status == "КБ на складе"

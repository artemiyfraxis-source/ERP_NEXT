from datetime import date
from decimal import Decimal

from app.modules.production.plan import kit_stage_key
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

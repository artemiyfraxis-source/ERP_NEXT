"""«Есть на складе, без ГРН» across the chain (06.10): a procurement cover without a lot is on the shelf but in no lot.

The project's «Снабжение» counts it once («Без ГРН»), so «Заказать недостающее» does not buy it again; bound to a ГРН
it moves into the lot's own reserve and leaves «Без ГРН» (never both). It holds no РЕЗЕРВ, so the techcard reserve
of a picked ГРН is not cut by it, and binding it later goes through the lot's ДОСТУПНО like any reserve: a lot the
techcard already holds is refused, the РЕЗЕРВ never goes over the balance."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.main import app
from app.modules.procurement.application import (
    BindStockCoverCommand,
    CoverFromStockCommand,
    bind_stock_cover,
    cover_from_stock,
)
from app.modules.procurement.domain import ProcurementStatus, ProcurementValidationError, create_line_from_requested
from app.shared.events import OutboxMessage
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.workflows.test_kitset_reserve import (  # noqa: F401 — fixture
    ART,
    PROJECT,
    assert_clean,
    env,
    lot,
    put,
    reserves,
    save,
    stored,
)

BUYER = Actor(id=UUID("40000000-0000-0000-0000-000000000007"), name="Петров", department="Закупки")
NOW = datetime(2026, 10, 6, 9, 0)


def buyer_line(quantity: str, *, article: str = ART, name: str = "Корпус Molex 43025-0400"):
    """A procurement line of the project on the buyer's desk («В работе», facts filled)."""
    line = create_line_from_requested(OutboxMessage(
        id=uuid4(), topic="procurement.requested", idempotency_key=f"request:{uuid4()}:procurement:v2",
        created_at=NOW,
        payload={
            "source_request_id": str(uuid4()), "legacy_id": "SM-20261006-01", "request_date": "2026-10-06",
            "initiator": "Технолог", "department": "Производство", "task_type": "Закупка",
            "designation": f"{PROJECT}.001", "project_reference": PROJECT, "priority": "Срочно",
            "delivery_point": "Москва", "planned_date": "2026-10-20", "item_type": "Комплектующие",
            "design_article": article, "category": "Общепром", "calculated_name": name,
            "calculated_quantity": quantity, "calculated_unit": "шт",
        },
    ))
    line = replace(
        line, status=ProcurementStatus.IN_WORK, buyer=BUYER, supplier="ООО Поставка", actual_unit="шт",
        actual_quantity=Decimal(quantity),
    )
    with app.state.procurement_uow_factory() as uow:
        uow.procurement.add(line)
        uow.commit()
    return line


def cover_without_lot(line, quantity: str):
    return cover_from_stock(CoverFromStockCommand(
        line_id=line.id, expected_version=line.version, lot_id=None, quantity=Decimal(quantity), actor=BUYER,
        now=NOW,
    ), app.state.procurement_uow_factory())


def bind(line, cover, item):
    return bind_stock_cover(BindStockCoverCommand(
        line_id=line.id, expected_version=line.version, cover_id=cover.id, lot_id=item.id, actor=BUYER, now=NOW,
    ), app.state.procurement_uow_factory())


def supply(client) -> dict:
    lines = client.get(f"/api/techcards/kitsets/{PROJECT}/supply").json()["lines"]
    return {line["art"]: line for line in lines}


def picked(line: dict) -> tuple:
    return line["need"], line["free"], line["off_book"], line["ordered"], line["short"]


def test_partial_cover_without_grn_counts_once_and_the_rest_stays_in_procurement(env):
    client, _engine = env
    line = buyer_line("20")

    cover_without_lot(line, "12")

    molex = supply(client)[ART]
    # need 20 = 12 on the shelf without a ГРН + 8 still to buy; nothing short (before: «Не хватает 12»)
    assert picked(molex) == ("20", "0", "12", "8", "0")
    assert molex["stage"] == "Закупка · Готов к запросу"


def test_full_cover_without_grn_is_not_accepted_and_order_missing_does_not_buy_it_again(env):
    client, _engine = env
    line = buyer_line("20")

    updated, _cover = cover_without_lot(line, "20")

    assert updated.status is ProcurementStatus.DONE
    molex = supply(client)[ART]
    assert picked(molex) == ("20", "0", "20", "0", "0")
    assert (molex["stage"], molex["received"]) == ("Закупка · Готово · без ГРН", False)

    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name="Технолог", department="Производство"),
        email="engineer@example.test", roles=frozenset({"engineer"}),
        permissions={"projects": "edit", "newrequest": "edit"},
    )
    ordered = client.post(f"/api/techcards/kitsets/{PROJECT}/order-missing", json={
        "articles": [ART], "deadline": "2026-10-20",
    })
    assert ordered.status_code == 200, ordered.text
    assert ordered.json()["created"] == []
    assert ordered.json()["skipped"] == [
        {"art": ART, "kind": "есть на складе без ГРН", "reason": "на складе без ГРН: 20"},
    ]


def test_binding_a_grn_moves_the_cover_from_without_grn_to_the_lot_not_twice(env):
    client, _engine = env
    item = lot("FRX.26.0000001", "30")
    put(item)
    line = buyer_line("20")
    updated, cover = cover_without_lot(line, "20")
    assert picked(supply(client)[ART]) == ("20", "30", "20", "0", "0")  # 30 free in the lot + 20 unbooked

    bind(updated, cover, item)

    assert (stored(item).reserved, stored(item).available) == (Decimal("20"), Decimal("10"))
    molex = supply(client)[ART]
    # the 20 now sit in the lot as the project's own reserve: «Свободно» 10 + 20, «Без ГРН» 0
    assert picked(molex) == ("20", "30", "0", "0", "0")
    assert (molex["stage"], molex["received"]) == ("Приёмка · Готово", True)
    assert_clean(_engine)


def test_techcard_reserve_holds_its_grn_and_a_later_bind_cannot_over_reserve_the_lot(env):
    client, engine = env
    item = lot("FRX.26.0000001", "25")
    put(item)
    line = buyer_line("20")
    updated, cover = cover_without_lot(line, "20")
    assert stored(item).reserved == 0  # without a ГРН nothing is reserved anywhere

    assert save(client, item).status_code == 200  # КБ1 picks the lot: «на партию» 20

    [held] = reserves(engine)
    assert held.quantity == Decimal("20")  # the cover without a lot sits on no lot: it does not cut this reserve
    assert (stored(item).reserved, stored(item).available) == (Decimal("20"), Decimal("5"))

    with pytest.raises(ProcurementValidationError, match="доступно 5"):
        bind(updated, cover, item)  # 20 more on a lot with 5 available: refused, nothing written

    assert (stored(item).reserved, stored(item).available) == (Decimal("20"), Decimal("5"))
    with app.state.procurement_uow_factory() as uow:
        assert uow.stock_covers.get(cover.id).lot_id is None
        assert [found.id for found in uow.stock_covers.active_without_lot([line.id])] == [cover.id]
    assert_clean(engine)

    assert save(client, None).status_code == 200  # the ГРН cleared in the spec gives the 20 back
    assert stored(item).reserved == 0
    bind(updated, cover, item)
    assert (stored(item).reserved, stored(item).available) == (Decimal("20"), Decimal("5"))
    assert stored(item).reserved <= stored(item).balance
    with app.state.procurement_uow_factory() as uow:
        assert uow.stock_covers.active_without_lot([line.id]) == []
    assert_clean(engine)

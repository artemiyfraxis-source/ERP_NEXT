"""Приёмка с производства (20.11.2026, 708К. КБ20: приняли 1 по заданиям и 14 «до тиража вне заданий» при одной
сданной — откатывали скриптом): сверх сданного цехом по заданиям — только с подтверждением; «Откатить приёмку» акта —
руководитель, с причиной, ходом отката «Принято складом → В производстве»."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.shipping.domain import FinishedGood, ShippingValidationError
from app.modules.shipping.infrastructure import FinishedGoodMoveRow, SqlAlchemyShippingUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from app.workflows.finished_goods import OUTSIDE_TASKS_REASON, KbProduction, handed_over
from tests.factories import Actor
from tests.shipping.test_api import permit_fixture_goods
from tests.workflows.test_finished_goods_register import NOW, STOREKEEPER, flow_api  # noqa: F401 — фикстура

HEAD = Actor(id=UUID("10000000-0000-0000-0000-000000000001"), name="Руководитель", department="ERP")
LATER = NOW + timedelta(minutes=10)


def _line(item: str, quantity: str, confirm: bool = False) -> dict:
    return {"project": "708К.4", "item_index": item, "quantity": quantity, **({"confirm_beyond_tasks": True} if confirm else {})}


def _accept(client, *lines: dict, key: str):
    return client.post("/api/shipping/finished-goods/accept", headers={"Idempotency-Key": key}, json={"lines": list(lines)})


def _moves(sessions, kind: str | None = None) -> list[tuple[str, str, str, Decimal, str]]:
    with sessions() as session:
        rows = session.execute(select(
            FinishedGoodMoveRow.kind, FinishedGoodMoveRow.from_state, FinishedGoodMoveRow.to_state,
            FinishedGoodMoveRow.quantity, FinishedGoodMoveRow.reason,
        ).order_by(FinishedGoodMoveRow.occurred_at, FinishedGoodMoveRow.kind)).all()
    return [(k, a, b, Decimal(q), r) for k, a, b, q, r in rows if kind is None or k == kind]


def _audit(sessions, event_type: str) -> list[dict]:
    with sessions() as session:
        return [dict(details) for details in session.scalars(
            select(AuditEventRow.details).where(AuditEventRow.event_type == event_type).order_by(AuditEventRow.occurred_at)
        )]


def _as(principal: Principal) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: principal


def _head() -> None:
    _as(Principal(actor=HEAD, email="head@example.test", roles=frozenset({"admin"}), permissions={}))


def _good(**counters) -> FinishedGood:
    good = FinishedGood.create(project="708К.4", item_index="КБ1", customer="", article="", name="Жгут", ordered=Decimal(50), unit="шт")
    return good.__class__(**{**good.__dict__, **{key: Decimal(value) for key, value in counters.items()}})


# ── N: сдано цехом по заданиям и ещё не принято ─────────────────────────────────────────────────────────────────


def test_handed_over_is_shop_delivered_by_tasks_minus_accepted_and_zero_without_tasks():
    kb = KbProduction(key="k", project="708К.4", item_index="КБ1", name="Жгут", run=Decimal(50), delivered=Decimal(30))
    assert handed_over(kb, _good(accepted=12)) == 18
    assert handed_over(kb, _good(accepted=31)) == 0
    assert handed_over(None, _good(accepted=0)) == 0


# ── Подтверждение сверх сданного ────────────────────────────────────────────────────────────────────────────────


def test_batch_above_handed_over_needs_the_confirm_and_records_who_confirmed_n_and_m(flow_api):  # noqa: F811
    client, sessions = flow_api
    # КБ1: цех сдал по заданиям 30, принято 0 — 30 принимаются без вопросов, 31 — только с подтверждением.
    refused = _accept(client, _line("КБ1", "31"), key="g-1")
    assert refused.status_code == 409
    body = refused.json()
    assert body["detail"] == "708К.4 КБ1: Цех сдал по заданиям 30, принимаете 31 — подтвердите приёмку сверх сданного"
    assert body["code"] == "beyond_tasks"
    good_id = client.get("/api/shipping/goods").json()["goods"][0]["id"]
    assert body["rows"] == [{"project": "708К.4", "item_index": "КБ1", "good_id": good_id, "handed_over": "30",
                             "quantity": "31", "beyond": "1", "has_tasks": True}]
    assert _moves(sessions) == []

    confirmed = _accept(client, _line("КБ1", "31", confirm=True), key="g-1")  # тот же ключ: отказ ничего не записал
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["goods"][0]["accepted"] == "31"
    assert _moves(sessions) == [("accept", "production", "accepted", Decimal(31), OUTSIDE_TASKS_REASON)]
    [details] = _audit(sessions, "shipping.good_accepted")
    assert details["reason"] == OUTSIDE_TASKS_REASON
    assert details["beyond_tasks"] == {
        "confirmed_by": "Кладовщик", "confirmed_by_id": str(STOREKEEPER.id), "handed_over": "30", "accepted": "31", "beyond": "1",
        "has_tasks": True,
    }


def test_n_counts_only_what_is_handed_over_and_not_yet_accepted(flow_api):  # noqa: F811
    client, sessions = flow_api
    assert _accept(client, _line("КБ1", "20"), key="n-1").status_code == 200
    assert _accept(client, _line("КБ1", "10"), key="n-2").status_code == 200  # N = 30 − 20
    after = _accept(client, _line("КБ1", "1"), key="n-3")
    assert after.status_code == 409 and "Цех сдал по заданиям 0, принимаете 1" in after.json()["detail"]
    assert "beyond_tasks" not in str(_audit(sessions, "shipping.good_accepted"))


def test_mixed_batch_asks_only_for_the_rows_above_handed_over_and_is_all_or_nothing(flow_api):  # noqa: F811
    client, sessions = flow_api
    # КБ2: площадка сдала 10 — в пределах; КБ1: сдано 30, принимают 31; КБ3: задание запущено, сдано 0 — обе сверх.
    refused = _accept(client, _line("КБ1", "31"), _line("КБ2", "10"), _line("КБ3", "5"), key="m-1")
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "708К.4 КБ1: Цех сдал по заданиям 30, принимаете 31 — подтвердите приёмку сверх сданного; "
        "708К.4 КБ3: Цех сдал по заданиям 0, принимаете 5 — подтвердите приёмку сверх сданного"
    )
    assert [(row["item_index"], row["handed_over"], row["quantity"], row["beyond"]) for row in refused.json()["rows"]] == [
        ("КБ1", "30", "31", "1"), ("КБ3", "0", "5", "5"),
    ]
    assert _moves(sessions) == []
    half = _accept(client, _line("КБ1", "31", confirm=True), _line("КБ2", "10"), _line("КБ3", "5"), key="m-1")
    assert half.status_code == 409 and [row["item_index"] for row in half.json()["rows"]] == ["КБ3"]
    done = _accept(client, _line("КБ1", "31", confirm=True), _line("КБ2", "10"), _line("КБ3", "5", confirm=True), key="m-1")
    assert done.status_code == 200, done.text
    assert len(_audit(sessions, "shipping.good_accepted")) == 3
    beyond = [item["beyond_tasks"] for item in _audit(sessions, "shipping.good_accepted") if "beyond_tasks" in item]
    assert sorted(item["accepted"] for item in beyond) == ["31", "5"]
    assert {"confirmed_by": "Кладовщик", "confirmed_by_id": str(STOREKEEPER.id), "handed_over": "0", "accepted": "5",
            "beyond": "5", "has_tasks": True} in beyond


def test_single_accept_without_tasks_asks_too_and_over_the_order_stays_422(flow_api):  # noqa: F811
    client, _ = flow_api
    _head()
    good = client.post("/api/shipping/goods", json={"project": "708К.4", "item_index": "КБ9", "name": "Жгут", "ordered": "5"}).json()["good"]
    plain = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "2"})
    assert plain.status_code == 409
    assert plain.json()["detail"] == "708К.4 КБ9: У КБ нет заданий в ERP — принимаете 2 — подтвердите приёмку сверх сданного"
    assert plain.json()["rows"][0]["has_tasks"] is False and plain.json()["rows"][0]["good_id"] == good["id"]
    over = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "6"})
    assert over.status_code == 422  # сверх тиража — прежний отказ, без вопроса о подтверждении
    ok = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "2", "confirm_beyond_tasks": True})
    assert ok.status_code == 200 and ok.json()["good"]["accepted"] == "2"


# ── «Откатить приёмку» ──────────────────────────────────────────────────────────────────────────────────────────


def _rollback(client, grn: str, reason: str = "приняли больше, чем сдал цех", key: str = "r-1"):
    return client.post(f"/api/shipping/acts/{grn}/rollback", headers={"Idempotency-Key": key}, json={"reason": reason})


@pytest.fixture()
def two_acts(flow_api):  # noqa: F811
    """Как 20.11: по заданиям приняли сданное (30), следом — 14 сверх сданного; второй акт ошибочный."""
    client, sessions = flow_api
    assert _accept(client, _line("КБ1", "30"), key="a-1").status_code == 200
    assert _accept(client, _line("КБ1", "14", confirm=True), key="a-2").status_code == 200
    app.dependency_overrides[moscow_now] = lambda: LATER
    return client, sessions


def _acts(sessions):
    with SqlAlchemyShippingUnitOfWork(sessions) as uow:
        good = next(item for item in uow.goods.list() if item.item_index == "КБ1")
        return good, uow.goods.acts([good.id])[good.id]


def test_rollback_of_an_act_writes_one_correction_back_to_production_and_shows_it_rolled_back(two_acts):
    client, sessions = two_acts
    _, (first, second) = _acts(sessions)
    _head()
    rolled = _rollback(client, second.grn)
    assert rolled.status_code == 200, rolled.text
    assert (rolled.json()["grn"], rolled.json()["quantity"], rolled.json()["replayed"]) == (second.grn, "14", False)
    assert rolled.json()["good"]["accepted"] == "30"
    assert _moves(sessions, "correction") == [("correction", "accepted", "production", Decimal(14), "приняли больше, чем сдал цех")]
    good, (first_after, second_after) = _acts(sessions)
    assert good.accepted == 30 and good.register.in_production == 20
    assert (first_after.net, second_after.net, second_after.rolled_back) == (30, 0, 14)
    [details] = _audit(sessions, "shipping.accept_rolled_back")
    assert details["grn"] == second.grn and details["quantity"] == "14" and details["reason"] == "приняли больше, чем сдал цех"
    assert details["act_id"] == str(second.id) and details["item_index"] == "КБ1"

    replay = _rollback(client, second.grn)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    other_reason = _rollback(client, second.grn, reason="другая причина")  # тот же ключ, другая причина — не повтор
    assert other_reason.status_code == 409 and "уже откачена" in other_reason.json()["detail"]
    assert len(_moves(sessions, "correction")) == 1
    again = _rollback(client, second.grn, key="r-2")
    assert again.status_code == 409 and "уже откачена" in again.json()["detail"]
    assert len(_moves(sessions, "correction")) == 1


def test_rollback_starts_from_the_latest_act_and_refuses_unknown_acts(two_acts):
    client, sessions = two_acts
    _, (first, second) = _acts(sessions)
    _head()
    older = _rollback(client, first.grn)
    assert older.status_code == 409 and f"После {first.grn} есть приёмка {second.grn}" in older.json()["detail"]
    assert _rollback(client, "ГП.26.9999999").status_code == 404
    assert _rollback(client, second.grn).status_code == 200
    assert _rollback(client, first.grn, key="r-2").status_code == 200  # теперь он последний
    good, acts = _acts(sessions)
    assert good.accepted == 0 and [act.net for act in acts] == [0, 0]


def test_rollback_needs_the_head_and_a_reason(two_acts):
    client, sessions = two_acts
    _, (_, second) = _acts(sessions)
    _as(Principal(actor=STOREKEEPER, email="sklad@example.test", roles=frozenset({"foreman"}),
                  permissions={"shipping_goods": "edit", "shipping": "edit", "warehouse": "edit"}))
    refused = _rollback(client, second.grn)
    assert refused.status_code == 403 and "руководитель" in refused.json()["detail"]
    _head()
    assert _rollback(client, second.grn, reason="   ").status_code == 422
    assert client.post(f"/api/shipping/acts/{second.grn}/rollback", json={"reason": "x"}).status_code == 422  # без ключа повтора
    assert _moves(sessions, "correction") == []


def test_rollback_refuses_an_act_whose_pieces_are_packed_or_shipped(two_acts):
    client, sessions = two_acts
    good, (first, second) = _acts(sessions)
    _head()
    assert _rollback(client, second.grn).status_code == 200
    permit_fixture_goods(client)
    made = client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "shipping_point": "Москва", "phone": "+7 900", "terms": "САМОВЫВОЗ", "upd_type": "ЭДО",
        "upd_number": "1", "lines": [{"good_id": str(good.id), "quantity": "5"}],
    })
    assert made.status_code == 200, made.text
    packed = _rollback(client, first.grn, key="r-2")
    assert packed.status_code == 409 and "сначала верните их на склад" in packed.json()["detail"]
    shipment = client.get("/api/shipping/shipments").json()["shipments"][0]
    shipped = client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": shipment["version"]})
    assert shipped.status_code == 200, shipped.text
    assert _rollback(client, first.grn, key="r-3").status_code == 409
    assert len(_moves(sessions, "correction")) == 1


def test_unaccept_takes_only_unpacked_pieces_accepted_in_erp():
    good = _good(accepted=10, shipped=4)
    assert good.unaccept(Decimal(6)).accepted == 4
    with pytest.raises(ShippingValidationError):
        good.unaccept(Decimal(7))
    with pytest.raises(ShippingValidationError):
        _good(accepted=10, before_erp=8, shipped=8, sent=8).unaccept(Decimal(3))

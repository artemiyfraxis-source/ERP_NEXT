"""Automatic packing depends on the entire order, never on the visible/selected KBs."""

from dataclasses import replace
from decimal import Decimal
from uuid import UUID

import pytest

from app.main import app
from app.modules.shipping.domain import MoveContext
from app.modules.techcards.kitset_infrastructure import SqlAlchemyKitsetRepository
from tests.shipping.test_api import shipping_api  # noqa: F401
from tests.shipping.test_pack_decision_no_upd import ADMIN, PROJECT, STOREKEEPER, _as, _good
from tests.shipping.test_pack_permissions import NOW, accept, grant, manager_request, pack, permission_events, permission_view, pick
from tests.workflows.test_production_intake import _kitset


def catalog(*runs):
    with app.state.shipping_uow_factory() as uow:
        SqlAlchemyKitsetRepository(uow.session).save(_kitset(PROJECT, *runs))
        uow.commit()


def test_all_kbs_accepted_pack_without_permission_and_keep_stock_cap(shipping_api):
    client = shipping_api
    first = _good(client, "КБ1", "40")
    _good(client, "КБ2", "40")
    _as(STOREKEEPER)
    assert pack(client, first, "41").status_code == 422
    response = pack(client, first, "25")
    assert response.status_code == 200, response.text
    assert pack(client, first, "16").status_code == 422
    assert pack(client, first, "15").status_code == 200
    assert pack(client, first, "1").status_code == 422
    assert permission_events() == []
    assert permission_view(client)["version"] == 0


@pytest.mark.parametrize("accepted", ["39", ""])
def test_one_short_or_unaccepted_kb_keeps_explicit_permission(shipping_api, accepted):
    client = shipping_api
    first = _good(client, "КБ1", "40")
    _good(client, "КБ2", accepted)
    assert pack(client, first, "1").status_code == 422
    assert grant(client, "3").status_code == 200
    _as(STOREKEEPER)
    assert pack(client, first, "3").status_code == 200
    assert pack(client, first, "1").status_code == 422


def test_catalog_kb_with_no_finished_goods_prevents_auto_permission(shipping_api):
    first = _good(shipping_api, accepted="40")
    catalog(("КБ1", 40), ("КБ2", 5))
    _as(STOREKEEPER)
    assert pack(shipping_api, first, "1").status_code == 422


def test_order_run_wins_over_partial_production_task_snapshot(shipping_api):
    first = _good(shipping_api, accepted="40")
    catalog(("КБ1", 50))
    _as(STOREKEEPER)
    assert pack(shipping_api, first, "1").status_code == 422


def test_unknown_order_run_keeps_permission_required(shipping_api):
    first = _good(shipping_api, accepted="40")
    catalog(("КБ1", 0))
    _as(STOREKEEPER)
    assert pack(shipping_api, first, "1").status_code == 422


def test_correction_of_sibling_acceptance_revokes_only_auto_allowance(shipping_api):
    first = _good(shipping_api, accepted="40")
    second = _good(shipping_api, "КБ2", "40")
    assert grant(shipping_api, "4").status_code == 200
    _as(STOREKEEPER)
    assert pack(shipping_api, first, "2").status_code == 200
    with app.state.shipping_uow_factory() as uow:
        sibling = uow.goods.get(UUID(second["id"]))
        uow.goods.add(replace(sibling, accepted=Decimal("39"), version=sibling.version + 1), MoveContext(
            actor="Руководитель", occurred_at=NOW, reason="Пересорт", correction=True,
        ))
        uow.commit()
    assert pack(shipping_api, first, "3").status_code == 422
    assert pack(shipping_api, first, "2").status_code == 200
    assert permission_view(shipping_api)["quantity"] == "4"


def test_direct_packing_use_case_enforces_whole_order_rule(shipping_api):
    from app.modules.shipping.application import PackProjectCommand, RequestedGood, pack_project
    first = _good(shipping_api, accepted="40")
    result = pack_project(PackProjectCommand(
        project=PROJECT, lines=(RequestedGood(UUID(first["id"]), Decimal("40")),), actor=STOREKEEPER.actor, now=NOW,
    ), app.state.shipping_uow_factory())
    assert result.lines[0].picked == 40


def test_complete_order_ignores_smaller_explicit_cap_without_rewriting_it(shipping_api):
    first = _good(shipping_api, accepted="40")
    assert grant(shipping_api, "2").status_code == 200
    _as(STOREKEEPER)
    assert pack(shipping_api, first, "5").status_code == 200
    view = permission_view(shipping_api)
    assert view["quantity"] == "2" and view["version"] == 1
    assert len(permission_events()) == 1


def test_project_ship_also_accepts_whole_order_without_permission(shipping_api):
    first = _good(shipping_api, accepted="40")
    _as(STOREKEEPER)
    response = shipping_api.post("/api/shipping/project-ship", json={
        "project": PROJECT, "addressee_version": 0, "ship": False,
        "lines": [{"good_id": first["id"], "version": first["version"], "quantity": "40"}],
    }, headers={"Idempotency-Key": "whole-order"})
    assert response.status_code == 200, response.text
    assert Decimal(response.json()["shipment"]["lines"][0]["picked"]) == 40


@pytest.mark.parametrize("endpoint", ["project-pack", "project-ship"])
def test_whole_order_allows_canonical_project_alias(shipping_api, endpoint):
    first = _good(shipping_api, accepted="40")
    _as(STOREKEEPER)
    response = shipping_api.post(f"/api/shipping/{endpoint}", json={
        "project": PROJECT.replace("К", "K"), "addressee_version": 0, "ship": False,
        "lines": [{"good_id": first["id"], "version": first["version"], "quantity": "40"}],
    }, headers={"Idempotency-Key": "whole-order-alias"})
    assert response.status_code == 200, response.text
    assert Decimal(response.json()["shipment"]["lines"][0]["picked"]) == 40
    assert permission_events() == []


def test_live_kb_without_order_quantity_prevents_auto_permission(shipping_api):
    from app.modules.production.infrastructure import SqlAlchemyProductionRepository
    from tests.workflows.test_production_intake import _task

    first = _good(shipping_api, accepted="40")
    with app.state.shipping_uow_factory() as uow:
        SqlAlchemyProductionRepository(uow.session).add(_task(PROJECT, "КБ2", 5, number="ПЗ-UNKNOWN"))
        uow.commit()
    _as(STOREKEEPER)
    assert pack(shipping_api, first, "1").status_code == 422


@pytest.mark.parametrize("restore_path", ["command", "rollback"])
def test_restoring_legacy_task_revokes_whole_order_packing(shipping_api, restore_path):
    from app.modules.production.application import SetProductionCancelledCommand, set_production_cancelled
    from app.modules.production.infrastructure import SqlAlchemyProductionRepository, SqlAlchemyProductionUnitOfWork
    from tests.workflows.test_production_intake import _task

    first = _good(shipping_api, accepted="40")
    cancelled = _task(PROJECT, "КБ2", 5, number="ПЗ-RESTORE").set_cancelled(True)
    with app.state.shipping_uow_factory() as uow:
        sessions = uow.session_factory
        SqlAlchemyProductionRepository(uow.session).add(cancelled)
        uow.commit()
    _as(STOREKEEPER)
    assert pack(shipping_api, first, "1").status_code == 200

    if restore_path == "command":
        set_production_cancelled(SetProductionCancelledCommand(
            cancelled.id, cancelled.version, False, STOREKEEPER.actor, NOW,
        ), SqlAlchemyProductionUnitOfWork(sessions))
    else:
        from datetime import timedelta
        from app.modules.production.domain import ProductionStatus
        from app.workflows.test_rollback import Plan, Scope, execute

        with sessions() as session:
            execute(session, Plan(
                tasks={cancelled.id}, task_status={cancelled.id: ProductionStatus.RUNNING.value},
                lines=["Восстановить задание"],
            ), Scope(NOW, NOW + timedelta(hours=1), frozenset({STOREKEEPER.actor.id}), STOREKEEPER.actor.name), NOW)
            session.commit()
    assert pack(shipping_api, first, "1").status_code == 422
    _as(ADMIN)
    assert grant(shipping_api, "2").status_code == 200
    _as(STOREKEEPER)
    assert pack(shipping_api, first, "1").status_code == 200


def test_pick_keeps_acceptance_correction_seen_when_stock_is_locked(shipping_api, monkeypatch):
    from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork

    first = _good(shipping_api, accepted="")
    shipment = manager_request(shipping_api, first, "5")
    accept(shipping_api, "40")
    original = SqlAlchemyShippingUnitOfWork.packing_order

    def corrected_order(uow, project):
        # Model a correction committed between discovery reads and the packing stock lock.
        good = uow.goods.get(UUID(first["id"]))
        uow.goods.add(replace(good, accepted=Decimal("39"), version=good.version + 1), MoveContext(
            actor="Руководитель", occurred_at=NOW, reason="Пересорт", correction=True,
        ))
        uow.session.flush()
        return original(uow, project)

    monkeypatch.setattr(SqlAlchemyShippingUnitOfWork, "packing_order", corrected_order)
    _as(STOREKEEPER)
    response = pick(shipping_api, shipment)
    assert response.status_code == 200, response.text
    with app.state.shipping_uow_factory() as uow:
        good = uow.goods.get(UUID(first["id"]))
        assert (good.accepted, good.shipped) == (Decimal("39"), Decimal("5"))

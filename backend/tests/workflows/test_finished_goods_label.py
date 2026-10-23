"""Этикетка ГРН акта приёмки ГП (владелец 22.10.2026): `POST /api/shipping/intake/{grn}/label` ставит этикетку акта в
ту же очередь печати склада, что и партии; права — «Склад» на правку, как у печати этикеток склада."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.shipping.application import AcceptGoodCommand, accept_good
from app.modules.shipping.domain import FinishedGood
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.modules.warehouse.label_jobs import LabelJobRow
from app.modules.warehouse.labels.layout import GrnLabelData
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from app.workflows.finished_goods_label import act_label_data
from tests.factories import Actor
from tests.view_access import as_viewer

NOW = datetime(2026, 10, 22, 12, 0)
STOREKEEPER = Actor(name="Кладовщик", department="Склад")


@pytest.fixture()
def labels():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    shipping = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    app.state.shipping_uow_factory = shipping
    app.state.warehouse_uow_factory = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=STOREKEEPER, email="sklad@example.test", roles=frozenset({"foreman"}), permissions={"warehouse": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    good = FinishedGood.create(
        project="533К.12", item_index="КБ1", customer="Заказчик", article="", name="CCW-0,2 жгут", ordered=Decimal(1000), unit="шт",
    )
    with shipping() as uow:
        uow.goods.add(good)
        uow.commit()
    for quantity, place in ((100, "А-1"), (350, "")):
        with shipping() as uow:
            current = uow.goods.get(good.id)
        accept_good(AcceptGoodCommand(
            good_id=good.id, expected_version=current.version, quantity=Decimal(quantity), received_at=NOW.date(),
            actor=STOREKEEPER, now=NOW, location=place,
        ), shipping())
    with shipping() as uow:
        acts = uow.goods.acts([good.id])[good.id]
    yield TestClient(app), sessions, acts
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.shipping_uow_factory, app.state.warehouse_uow_factory
    engine.dispose()


def _jobs(sessions) -> list[LabelJobRow]:
    with sessions() as session:
        rows = list(session.scalars(select(LabelJobRow).order_by(LabelJobRow.created_at)))
        session.expunge_all()
    return rows


def test_an_act_grn_goes_to_the_label_queue_with_the_kbs_fields(labels):
    client, sessions, acts = labels
    second = acts[1]
    done = client.post(f"/api/shipping/intake/{second.grn}/label", json={"copies": 2})
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["status"], body["copies"]) == ("queued", 2) and body["file"] == body["job_id"]
    [job] = _jobs(sessions)
    assert (str(job.id), job.status, job.kind, job.lot_id, job.copies, job.created_by) == (
        body["job_id"], "pending", "lot", second.id, 2, "Кладовщик")
    assert job.tspl.startswith(b"SIZE 58 mm,40 mm")  # та же 58×40, что у партий склада

    data = act_label_data(sessions, second.grn).data
    assert data == GrnLabelData(
        grn=second.grn, sku="533К.12 КБ1", item_name="CCW-0,2 жгут", quantity="350", unit="шт", manufacturer_batch="",
        received_on=NOW.date(), place="А-1", warehouse="ГП",  # у второй приёмки места нет — место КБ
    )
    first = act_label_data(sessions, acts[0].grn).data
    assert (first.quantity, first.place) == ("100", "А-1")  # каждый ГРН — свои штуки, а не всей КБ


def test_one_copy_by_default_and_an_unknown_grn_is_404(labels):
    client, sessions, acts = labels
    assert client.post(f"/api/shipping/intake/{acts[0].grn}/label").json()["copies"] == 1
    missing = client.post("/api/shipping/intake/ГП.26.9999999/label")
    assert missing.status_code == 404 and "не найден" in missing.json()["detail"]
    assert len(_jobs(sessions)) == 1  # на неизвестный ГРН задания нет
    assert client.post(f"/api/shipping/intake/{acts[0].grn}/label", json={"copies": 0}).status_code == 422


def test_print_needs_warehouse_edit(labels):
    client, sessions, acts = labels
    for role, section, mode in (("master", "warehouse", "view"), ("foreman", "shipping_goods", "edit")):
        as_viewer(role, section, mode)
        assert client.post(f"/api/shipping/intake/{acts[0].grn}/label").status_code == 403, (section, mode)
    assert not _jobs(sessions)


def test_the_act_label_prints_the_saved_intake_template_not_the_lots(labels):
    client, sessions, acts = labels
    grn = acts[0].grn
    label = act_label_data(sessions, grn)
    assert (label.project, label.item_index) == ("533К.12", "КБ1")
    assert dict(label.content.values) == {
        "grn": grn, "quantity": "100 шт", "name": "CCW-0,2 жгут", "project": "533К.12", "item_index": "КБ1",
        "project_item": "533К.12 КБ1", "place": "А-1", "warehouse": "ГП", "place_warehouse": "А-1 · ГП",
        "received": "поступило 22.10.2026",
    }
    assert label.content.kind == "intake"
    client.post(f"/api/shipping/intake/{grn}/label")
    # A lot template does not reach the act's label; the intake one does.
    lot = {"elements": [{"id": "x", "kind": "text", "text": "ПАРТИЯ", "x_mm": 1, "y_mm": 1, "w_mm": 50, "h_mm": 20,
                         "font_mm": 8}]}
    assert client.put("/api/stock/labels/templates/lot/58x40", json={"template": lot}).status_code == 200
    client.post(f"/api/shipping/intake/{grn}/label")
    intake = {"elements": [
        {"id": "kb", "kind": "text", "text": "{project_item}", "x_mm": 1, "y_mm": 1, "w_mm": 56, "h_mm": 10, "font_mm": 4},
        {"id": "qr", "kind": "qr", "x_mm": 1, "y_mm": 12, "w_mm": 27, "h_mm": 27},
    ]}
    assert client.put("/api/stock/labels/templates/intake/58x40", json={"template": intake}).status_code == 200
    client.post(f"/api/shipping/intake/{grn}/label")
    built_in, under_lot_template, designed = (job.tspl for job in _jobs(sessions))
    assert built_in == under_lot_template
    assert designed != built_in and designed.startswith(b"SIZE 58 mm,40 mm")

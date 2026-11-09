"""Historical headers stay snapshots; explicit corrections are bounded and audited."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.main import app
from app.modules.shipping import application
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork, SqlAlchemyShipmentRepository
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import Base, AuditEventRow, create_schema
from tests.factories import Actor
from tests.shipping.test_api import shipping_api  # noqa: F401
from tests.identity.test_view_as import view_api, _login, _view, _csrf, ADMIN2_ID  # noqa: F401
from app.modules.identity.middleware import VIEW_ONLY_DETAIL

from app.modules.shipping.domain import (
    ADDRESSEE_CARD_FIELDS, BoxItem, LineLot, MoveContext, ProjectAddressee, ShipmentBox, ShipmentStatus, ShippingValidationError, UpdType,
)
from tests.shipping.test_domain import good, shipment


def historical(status=ShipmentStatus.SHIPPED, item=None):
    return replace(shipment(item or good()), status=status, inherited=frozenset(ADDRESSEE_CARD_FIELDS))


def current_card():
    return ProjectAddressee(project="591К.3", receiver="Сегодняшний получатель", shipping_point="Новый адрес",
                            phone="Новый телефон", contact="Новый контакт", carrier="Новая ТК", upd_type=UpdType.PAPER)


@pytest.mark.parametrize("status", [ShipmentStatus.SHIPPED, ShipmentStatus.CLOSED, ShipmentStatus.CANCELLED])
def test_historical_follow_does_not_rewrite_snapshot(status):
    before = historical(status)
    assert before.follow(current_card()) is before
    assert before._followed(current_card()) is before


def test_shipped_head_edit_does_not_refresh_inherited_header():
    before = historical()
    with pytest.raises(ShippingValidationError):
        before.edit_head({"note": "Примечание"}, current_card())


@pytest.mark.parametrize("upd_type", [None, UpdType.PAPER])
def test_shipped_documents_preserve_snapshot_and_allow_upd_completion(upd_type):
    before = historical()
    after = before.update_documents(upd_type=upd_type, upd_number="УПД-2", upd_date=None,
                                    upd_url="/upd.pdf", track_number="", card=current_card())
    assert after.upd_type == (upd_type or before.upd_type)
    assert after.upd_number == "УПД-2" and after.upd_url == "/upd.pdf"
    assert after.inherited == (before.inherited if upd_type is None else before.inherited - {"upd_type"})
    for field in set(ADDRESSEE_CARD_FIELDS) - {"upd_type"}:
        assert getattr(after, field) == getattr(before, field)


@pytest.mark.parametrize("status", [ShipmentStatus.NEW, ShipmentStatus.ASSEMBLED, ShipmentStatus.PACKED])
def test_active_inheritance_still_follows_project(status):
    before = historical(status)
    card = current_card()
    for after in (before.follow(card), before.edit_head({"note": "note"}, card),
                  before.update_documents(upd_type=None, upd_number="", upd_date=None, upd_url="", track_number="", card=card)):
        assert after.receiver == card.receiver
        assert after.upd_type == card.upd_type
        assert after.version == before.version + 1


@pytest.fixture()
def database(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'history.db'}")
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 9, 22, 9)
    item = good().accept(Decimal("10"), now.date(), "ГП.26.0000001").take(Decimal("10")).dispatch(Decimal("10"))
    before = historical(item=item)
    before = replace(before, lines=(replace(before.lines[0], picked=Decimal("10"), grn=item.grn, lots=(LineLot(item.grn, Decimal("10")),)),),
                     shipped_at=now.date(), packed_at=now.date(), pl_number="PL-1", dn_number="DN-1",
                     boxes=(ShipmentBox(place=1, gross="1,5", items=(BoxItem(item.item_index, Decimal("10")),)),))
    with SqlAlchemyShippingUnitOfWork(sessions) as uow:
        uow.goods.add(item, MoveContext(actor="Администратор", occurred_at=now, shipment_id=before.id))
        uow.shipments.add(before)
        uow.commit()
    yield sessions, before
    engine.dispose()


def correct(sessions, before, **overrides):
    values = dict(shipment_id=before.id, expected_version=before.version,
                  actor=Actor(name="Администратор", department="ERP"), now=datetime(2026, 11, 2, 10), allowed=True,
                  reason="Ошибка переноса", source="Архив накладной 42", fields={"phone": "Исправленный телефон"})
    values.update(overrides)
    return application.correct_shipment_history(application.HistoryCorrectionCommand(**values),
                                                SqlAlchemyShippingUnitOfWork(sessions))


def snapshot(sessions):
    with sessions() as session:
        return {table.name: session.execute(select(table)).all() for table in Base.metadata.sorted_tables}


def test_correction_has_exact_audit_and_changes_no_other_data(database):
    sessions, before = database
    old = snapshot(sessions)
    actor = Actor(name="Администратор", department="ERP")
    after = correct(sessions, before, actor=actor)
    assert after == replace(before, phone="Исправленный телефон", inherited=before.inherited - {"phone"}, version=2)
    with sessions() as session:
        [audit] = list(session.scalars(select(AuditEventRow)))
        assert audit.event_type == "shipping.history_corrected"
        assert audit.aggregate_id == before.id and audit.actor_id == actor.id
        assert audit.actor_name == actor.name and audit.occurred_at == datetime(2026, 11, 2, 10)
        details = {"reason": "Ошибка переноса", "source": "Архив накладной 42", "before_version": "1", "after_version": "2",
                   "before_inherited": ",".join(sorted(before.inherited)), "after_inherited": ",".join(sorted(after.inherited))}
        for field in ("receiver", "shipping_point", "contact", "phone", "carrier", "track_number"):
            details[f"before_{field}"] = getattr(before, field)
            details[f"after_{field}"] = getattr(after, field)
        assert audit.details == details
    new = snapshot(sessions)
    for table in old.keys() - {"shipments", "audit_events"}:
        assert new[table] == old[table], table


def test_noop_has_no_write_audit_or_outbox(database):
    sessions, before = database
    old = snapshot(sessions)
    assert correct(sessions, before, fields={"phone": before.phone}) == before
    assert correct(sessions, before, fields={}) == before
    assert snapshot(sessions) == old


def test_audit_failure_rolls_back_entire_correction(database, monkeypatch):
    sessions, before = database
    old = snapshot(sessions)
    def failed(*args):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(application, "_audit", failed)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        correct(sessions, before)
    assert snapshot(sessions) == old


def test_stale_version_does_not_change_anything(database):
    sessions, before = database
    correct(sessions, before)
    old = snapshot(sessions)
    with pytest.raises(application.ConcurrentShippingUpdate):
        correct(sessions, before, fields={"receiver": "stale"})
    assert snapshot(sessions) == old


def test_two_sessions_cannot_overwrite_a_committed_shipment_snapshot(database):
    sessions, before = database
    with sessions() as first, sessions() as second:
        first_repo = SqlAlchemyShipmentRepository(first)
        second_repo = SqlAlchemyShipmentRepository(second)
        stale = first_repo.get(before.id)
        fresh = second_repo.get(before.id)
        second_repo.add(replace(fresh, phone="winner", version=fresh.version + 1))
        second.commit()
        with pytest.raises(StaleDataError):
            first_repo.add(replace(stale, phone="loser", version=stale.version + 1))
            first.commit()
        first.rollback()
    with sessions() as session:
        assert SqlAlchemyShipmentRepository(session).get(before.id).phone == "winner"


@pytest.mark.parametrize("overrides", [
    {"allowed": False}, {"reason": " "}, {"source": "\t"}, {"reason": "x" * 2001}, {"source": "x" * 2001},
    {"fields": {"phone": None}}, {"fields": {"phone": 42}}, {"fields": {"phone": "x" * 129}},
    {"fields": {"terms": "САМОВЫВОЗ"}}, {"fields": {"shipped_at": None}}, {"fields": {"lines": []}},
])
def test_invalid_application_commands_do_not_write(database, overrides):
    sessions, before = database
    old = snapshot(sessions)
    with pytest.raises((ShippingValidationError, application.ShippingPermissionError)):
        correct(sessions, before, **overrides)
    assert snapshot(sessions) == old


@pytest.mark.parametrize("status", list(ShipmentStatus))
def test_only_shipped_and_closed_accept_corrections(status):
    before = historical(status)
    if status in {ShipmentStatus.SHIPPED, ShipmentStatus.CLOSED}:
        after = before.correct_history({"phone": ""})
        assert after.phone == "" and "phone" not in after.inherited
        assert after.follow(current_card()) == after
        assert after._followed(current_card()) == after
    else:
        with pytest.raises(ShippingValidationError):
            before.correct_history({"phone": ""})


@pytest.mark.parametrize("track", ["", "new"])
def test_shipped_documents_cannot_change_historical_track(track):
    before = replace(historical(), track_number="old")
    with pytest.raises(ShippingValidationError):
        before.update_documents(upd_type=None, upd_number="УПД-2", upd_date=None,
                                upd_url="/upd.pdf", track_number=track, card=current_card())


def stored_history(status=ShipmentStatus.SHIPPED):
    before = historical(status)
    with app.state.shipping_uow_factory() as uow:
        uow.shipments.add(before)
        uow.commit()
    return before


def body(before, **fields):
    return dict(version=before.version, reason="Ошибка переноса", source="Накладная 42", **fields)


@pytest.mark.parametrize("role,service,status", [("admin", False, 200), ("superadmin", False, 200),
                                                 ("employee", False, 403), ("admin", True, 403)])
def test_history_api_permissions(shipping_api, role, service, status):
    before = stored_history()
    actor = Actor(name="Редактор", department="ERP")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor, email="editor@example.test", roles=frozenset({role}),
        permissions={"shipping": "edit", "shipping_goods": "edit"}, service=service,
    )
    response = shipping_api.post(f"/api/shipping/shipments/{before.id}/history-correction", json=body(before, phone="new"))
    assert response.status_code == status, response.text
    with app.state.shipping_uow_factory() as uow:
        current = uow.shipments.get(before.id)
        assert current.phone == ("new" if status == 200 else before.phone)
        assert len(list(uow.session.scalars(select(AuditEventRow)))) == (1 if status == 200 else 0)


@pytest.mark.parametrize("fields", [
    {"receiver": None}, {"shipping_point": None}, {"contact": None}, {"phone": None}, {"carrier": None}, {"track_number": None},
    {"terms": "САМОВЫВОЗ"}, {"shipped_at": "2026-11-02"}, {"upd_type": "ЭДО"}, {"lines": []},
    {"reason": " "}, {"source": "\t"}, {"reason": "x" * 2001}, {"source": "x" * 2001}, {"version": 0},
])
def test_history_api_rejects_invalid_payload_without_writes(shipping_api, fields):
    before = stored_history()
    payload = body(before)
    payload.update(fields)
    response = shipping_api.post(f"/api/shipping/shipments/{before.id}/history-correction", json=payload)
    assert response.status_code == 422, response.text
    with app.state.shipping_uow_factory() as uow:
        assert uow.shipments.get(before.id) == before
        assert list(uow.session.scalars(select(AuditEventRow))) == []


@pytest.mark.parametrize("field", ["version", "reason", "source"])
def test_history_api_requires_version_and_correction_evidence(shipping_api, field):
    before = stored_history()
    payload = body(before, phone="new")
    del payload[field]
    response = shipping_api.post(f"/api/shipping/shipments/{before.id}/history-correction", json=payload)
    assert response.status_code == 422, response.text


def test_history_api_clear_omission_noop_stale_and_documents(shipping_api):
    before = stored_history()
    url = f"/api/shipping/shipments/{before.id}"
    cleared = shipping_api.post(url + "/history-correction", json=body(before, phone=""))
    assert cleared.status_code == 200, cleared.text
    data = cleared.json()["shipment"]
    assert data["phone"] == "" and data["receiver"] == before.receiver
    stale = shipping_api.post(url + "/history-correction", json=body(before, phone="stale"))
    assert stale.status_code == 409
    noop = shipping_api.post(url + "/history-correction", json={**body(before, phone=""), "version": data["version"]})
    assert noop.status_code == 200 and noop.json()["shipment"]["version"] == data["version"]
    with app.state.shipping_uow_factory() as uow:
        assert len(list(uow.session.scalars(select(AuditEventRow)))) == 1
        card = replace(current_card(), version=1)
        uow.addressees.add(card)
        uow.commit()
    docs = shipping_api.put(url + "/documents", json={"version": data["version"], "upd_number": "УПД-2", "upd_url": "/upd.pdf"})
    assert docs.status_code == 200, docs.text
    updated = docs.json()["shipment"]
    assert updated["phone"] == "" and updated["receiver"] == before.receiver and updated["upd_type"] == before.upd_type.value
    refused = shipping_api.put(url + "/head", json={"version": updated["version"], "phone": "ordinary"})
    assert refused.status_code == 422


def test_shipped_upd_completion_preserves_omitted_track_but_rejects_explicit_clear(shipping_api):
    before = replace(historical(), track_number="TRK-42")
    with app.state.shipping_uow_factory() as uow:
        uow.shipments.add(before)
        uow.commit()
    url = f"/api/shipping/shipments/{before.id}/documents"
    response = shipping_api.put(url, json={"version": before.version, "upd_number": "УПД-42", "upd_url": "/upd.pdf"})
    assert response.status_code == 200, response.text
    updated = response.json()["shipment"]
    assert updated["track_number"] == "TRK-42" and updated["upd_type"] == before.upd_type.value
    for track in ("", "changed"):
        refused = shipping_api.put(url, json={"version": updated["version"], "track_number": track})
        assert refused.status_code == 422
    with app.state.shipping_uow_factory() as uow:
        assert uow.shipments.get(before.id).version == updated["version"]
        assert uow.shipments.get(before.id).track_number == "TRK-42"


def test_active_documents_keep_existing_omitted_track_behavior(shipping_api):
    before = replace(historical(ShipmentStatus.PACKED), track_number="TRK-42")
    with app.state.shipping_uow_factory() as uow:
        uow.shipments.add(before)
        uow.commit()
    response = shipping_api.put(f"/api/shipping/shipments/{before.id}/documents", json={"version": before.version})
    assert response.status_code == 200, response.text
    assert response.json()["shipment"]["track_number"] == ""


@pytest.mark.parametrize("status", list(ShipmentStatus))
def test_history_api_status_contract(shipping_api, status):
    before = stored_history(status)
    response = shipping_api.post(f"/api/shipping/shipments/{before.id}/history-correction", json=body(before, contact="new"))
    assert response.status_code == (200 if status in {ShipmentStatus.SHIPPED, ShipmentStatus.CLOSED} else 422), response.text


def test_history_correction_refused_by_real_view_as_middleware(view_api):
    admin = _login("admin")
    assert _view(admin, ADMIN2_ID).status_code == 200
    response = admin.post(f"/api/shipping/shipments/{ADMIN2_ID}/history-correction", json={}, headers=_csrf(admin))
    assert response.status_code == 403
    assert response.json() == {"detail": VIEW_ONLY_DETAIL}


def test_project_card_update_leaves_historical_rows_and_audit_unchanged(shipping_api):
    before = [replace(historical(status), number=f"HISTORY-{index}") for index, status in enumerate(ShipmentStatus)]
    with app.state.shipping_uow_factory() as uow:
        for item in before:
            uow.shipments.add(item)
        uow.commit()
    response = shipping_api.put("/api/shipping/project-addressee", json={
        "project": "591К.3", "receiver": "Сегодняшний получатель", "shipping_point": "Сегодняшний адрес",
        "phone": "Сегодняшний телефон", "contact": "Сегодняшний контакт", "carrier": "Сегодняшняя ТК", "upd_type": "БУМАГА",
    })
    assert response.status_code == 200, response.text
    with app.state.shipping_uow_factory() as uow:
        for item in before:
            after = uow.shipments.get(item.id)
            if item.status in {ShipmentStatus.SHIPPED, ShipmentStatus.CLOSED, ShipmentStatus.CANCELLED}:
                assert after == item
                assert list(uow.session.scalars(select(AuditEventRow).where(AuditEventRow.aggregate_id == item.id))) == []
            else:
                assert after.receiver == "Сегодняшний получатель" and after.version == item.version + 1


def test_competing_command_wins_once_and_loser_leaves_no_audit(database, monkeypatch):
    sessions, before = database
    original = SqlAlchemyShipmentRepository.add
    interleaved = False
    def competing(repo, updated):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            correct(sessions, before, fields={"phone": "winner"})
        original(repo, updated)
    monkeypatch.setattr(SqlAlchemyShipmentRepository, "add", competing)
    with pytest.raises(StaleDataError):
        correct(sessions, before, fields={"phone": "loser"})
    with sessions() as session:
        assert SqlAlchemyShipmentRepository(session).get(before.id).phone == "winner"
        [audit] = list(session.scalars(select(AuditEventRow)))
        assert audit.details["after_phone"] == "winner"

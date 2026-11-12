"""Автозакрытие отгруженной заявки после заполнения последних реквизитов."""

from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.shipping.domain import DeliveryTerms, ShipmentStatus
from app.shared.persistence import AuditEventRow, OutboxMessageRow
from tests.shipping.test_api import shipping_api  # noqa: F401
from tests.shipping.test_history_correction import historical
from tests.shipping.test_upd_files import _upload


def _stored(**fields):
    before = replace(historical(), terms=DeliveryTerms.CARRIER, carrier="СДЭК",
                     upd_number="УПД-42", upd_url="/upd.pdf", track_number="TRK-42",
                     shipped_at=date(2026, 11, 12))
    before = replace(before, **fields)
    with app.state.shipping_uow_factory() as uow:
        uow.shipments.add(before)
        uow.commit()
    return before


def _documents(client, before, **fields):
    payload = {"version": before.version, "upd_number": before.upd_number,
               "upd_url": before.upd_url}
    payload.update(fields)
    return client.put(f"/api/shipping/shipments/{before.id}/documents", json=payload)


def _assert_result(response, before, *, closed):
    assert response.status_code == 200, response.text
    shipment = response.json()["shipment"]
    assert shipment["status"] == ("Закрыта" if closed else before.status.value)
    assert shipment["version"] == before.version + (2 if closed else 1)
    with app.state.shipping_uow_factory() as uow:
        stored = uow.shipments.get(UUID(shipment["id"]))
        assert stored.status.value == shipment["status"]
        assert stored.version == shipment["version"]
        events = list(uow.session.scalars(select(AuditEventRow).where(
            AuditEventRow.aggregate_id == before.id, AuditEventRow.event_type == "shipping.closed",
        )))
        assert len(events) == int(closed)
        if closed:
            assert events[0].details == {"auto": True}
    return shipment


def test_attaching_last_upd_file_closes_and_keeps_notification(shipping_api):
    before = _stored(upd_url="")
    updated = _assert_result(_upload(shipping_api, {"id": str(before.id), "version": before.version}),
                             before, closed=True)
    assert shipping_api.get(updated["upd_url"]).content == b"%PDF-1.4 upd"
    with app.state.shipping_uow_factory() as uow:
        messages = list(uow.session.scalars(select(OutboxMessageRow)))
        assert any("УПД к отправке" in str(row.payload) for row in messages)


def test_adding_last_upd_number_closes_without_receiver_phone_or_address(shipping_api):
    before = _stored(upd_number="", receiver="", phone="", shipping_point="")
    _assert_result(_documents(shipping_api, before, upd_number="УПД-42"), before, closed=True)


@pytest.mark.parametrize("track", ["", "—"])
def test_adding_missing_required_track_closes(shipping_api, track):
    before = _stored(track_number=track)
    _assert_result(_documents(shipping_api, before, track_number="TRK-42"), before, closed=True)
    with app.state.shipping_uow_factory() as uow:
        messages = list(uow.session.scalars(select(OutboxMessageRow)))
        assert any("Трек по отправке" in str(row.payload) for row in messages)


@pytest.mark.parametrize("fields", [{"upd_url": ""}, {"upd_number": ""},
                                         {"track_number": ""}, {"shipped_at": None}])
def test_incomplete_documents_stay_shipped_without_error(shipping_api, fields):
    before = _stored(**fields)
    _assert_result(_documents(shipping_api, before), before, closed=False)


def test_file_upload_stays_shipped_when_number_is_missing(shipping_api):
    before = _stored(upd_number="", upd_url="")
    _assert_result(_upload(shipping_api, {"id": str(before.id), "version": before.version}),
                   before, closed=False)


def test_packed_documents_do_not_close(shipping_api):
    before = _stored(status=ShipmentStatus.PACKED, shipped_at=None)
    _assert_result(_documents(shipping_api, before), before, closed=False)


def test_history_track_correction_closes(shipping_api):
    before = _stored(track_number="")
    response = shipping_api.post(f"/api/shipping/shipments/{before.id}/history-correction", json={
        "version": before.version, "reason": "Дополнение трека", "source": "Транспортная накладная",
        "track_number": "TRK-42",
    })
    _assert_result(response, before, closed=True)


def test_existing_shipped_track_still_requires_history_correction(shipping_api):
    before = _stored(upd_url="")
    response = _documents(shipping_api, before, track_number="NEW")
    assert response.status_code == 422
    assert "исправление истории" in response.json()["detail"]

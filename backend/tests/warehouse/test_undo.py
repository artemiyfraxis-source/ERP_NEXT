"""«Отменить последнее»: GET/POST /api/stock/batches/{id}/undo — own write-off, return or move of the last
10 minutes is taken back by a compensating entry; everything else is refused with the reason."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.warehouse.domain import WriteOffEntry
from app.modules.warehouse.infrastructure import WriteOffEntryRow
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture

T0 = datetime(2026, 9, 21, 12, 0)


@pytest.fixture()
def undo_api(warehouse_api):  # noqa: F811
    client, lot = warehouse_api
    clock = {"now": T0}
    app.dependency_overrides[moscow_now] = lambda: clock["now"]

    def at(minutes: float):
        clock["now"] = T0 + timedelta(minutes=minutes)

    return client, lot, at


def _as(name: str, permissions=None):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name=name, department="Склад"), email=f"{name}@example.test",
        roles=frozenset({"receiver"}), permissions=permissions or {"warehouse": "edit"},
    )


def _ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def _writeoff(client, lot_id, version, quantity="3", purpose="FRX.001"):
    return _ok(client.post(f"/api/stock/batches/{lot_id}/writeoff",
                           json={"version": version, "quantity": quantity, "purpose": purpose}))["lot"]


def _preview(client, lot_id):
    return _ok(client.get(f"/api/stock/batches/{lot_id}/undo"))


def _undo(client, lot_id, version):
    return client.post(f"/api/stock/batches/{lot_id}/undo", json={"version": version})


def _journal(lot_id):
    with app.state.warehouse_uow_factory() as uow:
        entries = uow.session.scalars(
            select(WriteOffEntryRow).where(WriteOffEntryRow.lot_id == lot_id).order_by(WriteOffEntryRow.occurred_at)
        ).all()
        events = uow.session.scalars(
            select(AuditEventRow).where(AuditEventRow.aggregate_id == lot_id).order_by(AuditEventRow.occurred_at)
        ).all()
        return ([(e.kind, format(e.quantity.normalize(), "f"), e.reason, e.purpose) for e in entries],
                [(e.event_type, dict(e.details)) for e in events])


def test_own_writeoff_is_taken_back_by_a_return_and_both_rows_stay(undo_api):
    client, lot, at = undo_api
    written = _writeoff(client, lot.id, lot.version)
    at(3)
    assert _preview(client, lot.id) == {
        "undo": {"label": "Списание 3 шт · FRX.001", "occurred_at": T0.isoformat()}, "reason": "",
    }
    body = _ok(_undo(client, lot.id, written["version"]))
    assert body["undone"] == "Списание 3 шт · FRX.001"
    assert (body["lot"]["balance"], body["lot"]["written_off"]) == ("10", "0")
    entries, events = _journal(lot.id)
    assert entries == [("writeoff", "3", "Выдача", "FRX.001"), ("return", "3", "Возврат: отмена списания", "FRX.001")]
    assert [event_type for event_type, _ in events] == ["warehouse.written_off", "warehouse.writeoff_returned"]
    assert events[1][1]["undo_of"]
    # an undo is not undone again
    at(4)
    assert _preview(client, lot.id)["reason"] == "Последнее движение — уже отмена. Если нужно, сделай действие заново."
    again = _undo(client, lot.id, body["lot"]["version"])
    assert again.status_code == 422
    assert again.json()["detail"].startswith("Последнее движение — уже отмена")


def test_own_return_is_taken_back_by_a_writeoff(undo_api):
    client, lot, at = undo_api
    written = _writeoff(client, lot.id, lot.version, quantity="4")
    at(1)
    returned = _ok(client.post(f"/api/stock/batches/{lot.id}/return",
                               json={"version": written["version"], "quantity": "2", "source": "FRX.001"}))["lot"]
    assert returned["balance"] == "8"
    at(2)
    assert _preview(client, lot.id)["undo"]["label"] == "Возврат 2 шт · FRX.001"
    body = _ok(_undo(client, lot.id, returned["version"]))
    assert (body["lot"]["balance"], body["lot"]["written_off"]) == ("6", "4")
    entries, _ = _journal(lot.id)
    assert entries[-1] == ("writeoff", "2", "Отмена возврата", "FRX.001")


def test_a_move_and_a_transfer_go_back_where_they_came_from(undo_api):
    client, lot, at = undo_api
    moved = _ok(client.post(f"/api/stock/batches/{lot.id}/move", json={"version": lot.version, "storage_location": "2A1"}))["lot"]
    at(1)
    assert _preview(client, lot.id)["undo"]["label"] == "Перемещение 1F4 → 2A1"
    back = _ok(_undo(client, lot.id, moved["version"]))["lot"]
    assert (back["warehouse"], back["storage_location"]) == ("ЦАР", "1F4")
    at(2)
    sent = _ok(client.post(f"/api/stock/batches/{lot.id}/move",
                           json={"version": back["version"], "storage_location": "3B2", "warehouse": "ЖУК"}))["lot"]
    assert (sent["warehouse"], sent["storage_location"]) == ("ЖУК", "3B2")
    at(3)
    body = _ok(_undo(client, lot.id, sent["version"]))
    assert body["undone"] == "Перемещение ЦАР → ЖУК, место 1F4 → 3B2"
    assert (body["lot"]["warehouse"], body["lot"]["storage_location"], body["lot"]["balance"]) == ("ЦАР", "1F4", "10")
    _, events = _journal(lot.id)
    assert [event_type for event_type, _ in events] == [
        "warehouse.moved", "warehouse.moved", "warehouse.transferred", "warehouse.transferred",
    ]


def test_older_than_ten_minutes_is_refused(undo_api):
    client, lot, at = undo_api
    written = _writeoff(client, lot.id, lot.version)
    at(11)
    message = "Отменить можно в течение 10 минут — прошло 11 мин. Дальше — обычным списанием, возвратом или перемещением."
    assert _preview(client, lot.id) == {"undo": None, "reason": message}
    response = _undo(client, lot.id, written["version"])
    assert (response.status_code, response.json()["detail"]) == (422, message)


def test_another_user_may_not_undo(undo_api):
    client, lot, at = undo_api
    written = _writeoff(client, lot.id, lot.version)
    _as("Сменщик")
    response = _undo(client, lot.id, written["version"])
    assert (response.status_code, response.json()["detail"]) == (
        422, "Отменить может только тот, кто сделал движение: Кладовщик.")


def test_nothing_or_a_wrong_kind_is_refused(undo_api):
    client, lot, at = undo_api
    assert _preview(client, lot.id)["reason"] == "По этой партии нечего отменять."
    edited = _ok(client.put(f"/api/stock/batches/{lot.id}/row", json={"version": lot.version, "manufacturer": "Molex"}))["lot"]
    reason = _preview(client, lot.id)["reason"]
    assert reason.startswith("Последнее по партии — «Правка строки") and reason.endswith(
        "отменить можно только списание, возврат или перемещение, сделанные в ERP.")
    assert _undo(client, lot.id, edited["version"]).status_code == 422


def test_a_later_issue_without_an_event_blocks_the_undo(undo_api):
    client, lot, at = undo_api
    written = _writeoff(client, lot.id, lot.version)
    with app.state.warehouse_uow_factory() as uow:
        current = uow.warehouse.get(lot.id)
        uow.writeoffs.add(WriteOffEntry.for_lot(
            current, kind="issue", quantity=Decimal("1"), reason="Выдача КБ-7", purpose="", author="выдача по заявке",
            actor_id=Actor(name="Кладовщик", department="Склад").id, actor_name="Кладовщик",
            occurred_at=T0 + timedelta(minutes=1), remaining_after=None,
        ))
        uow.commit()
    at(2)
    response = _undo(client, lot.id, written["version"])
    assert (response.status_code, response.json()["detail"]) == (
        422, "После этого движения по партии уже была строка «Выдача» — отмена невозможна.")


def test_a_stale_version_is_409(undo_api):
    client, lot, at = undo_api
    _writeoff(client, lot.id, lot.version)
    assert _undo(client, lot.id, lot.version).status_code == 409


def test_undo_is_always_open_preview_on_view_undo_on_edit(undo_api, monkeypatch):
    client, lot, at = undo_api
    monkeypatch.delenv("ERP_STAGE_OPTIONS", raising=False)  # production: no stage options file
    assert _preview(client, lot.id)["undo"] is None
    _as("Мастер", {"warehouse": "view"})
    # The preview is a read: open on view (ID-6 view-mode contract); the undo itself needs edit.
    assert _preview(client, lot.id)["undo"] is None
    assert _undo(client, lot.id, lot.version).status_code == 403

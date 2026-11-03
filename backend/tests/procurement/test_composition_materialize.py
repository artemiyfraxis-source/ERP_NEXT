from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.requests.infrastructure import RequestRow, CalcCompositionRow
from tests.procurement.test_requests_composition_api import api, db, BASE, BODY, _counts, _access
from tests.identity.test_view_as import view_api, _login, _view, _csrf, ADMIN2_ID


def prepare(client):
    assert client.post(BASE + "/refresh", json=BODY).status_code == 200
    value = client.get(BASE, params={"project": BODY["project"]}).json()
    return {
        "project": BODY["project"], "snapshot_id": value["snapshot_id"], "expected_version": value["version"],
        "content_hash": value["source"]["content_hash"], "source_rows": [value["rows"][0]["source_row"]],
        "purpose": "Обеспечение производства", "delivery_point": "г. Москва, ул. 6-я Радиальная, д. 17",
        "procurement_deadline": "2026-11-20", "planned_date": "2026-11-19",
    }


def test_materialize_preserves_source_and_repeat_does_not_duplicate(api):
    client, db = api
    payload = prepare(client)
    response = client.post(BASE + "/materialize", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 2 and len(body["rows"][0]["procurement_ids"]) == 1
    before = _counts(db)
    _access("edit")  # another actor retry must still resolve the same source identity
    assert client.post(BASE + "/materialize", json=payload).json() == body
    assert _counts(db) == before
    with db.sessions() as session:
        request = session.scalars(select(RequestRow)).one()
        line = session.scalars(select(ProcurementLineRow)).one()
        assert request.quantity == line.calculated_quantity == Decimal("12.5")
        assert request.price_with_vat == line.calculated_price_with_vat == Decimal("1200.60")
        assert request.link == line.link == "https://x.test/a"
        assert request.unit == line.calculated_unit == "м"
        assert request.project_reference == "708К.4" and line.stock_covered == 0


@pytest.mark.parametrize("change", [
    {"expected_version": 88}, {"content_hash": "0" * 64}, {"source_rows": [999]},
    {"source_rows": [2, 2]}, {"source_rows": [3]}, {"purpose": None}, {"planned_date": None},
])
def test_invalid_or_stale_materialize_writes_nothing(api, change):
    client, db = api
    payload = prepare(client)
    before = _counts(db)
    result = client.post(BASE + "/materialize", json={**payload, **change})
    assert result.status_code in (409, 422), result.text
    assert _counts(db) == before


def test_materialize_viewer_cannot_write(api):
    client, db = api
    payload = prepare(client)
    before = _counts(db)
    _access("view")
    assert client.post(BASE + "/materialize", json=payload).status_code == 403
    assert _counts(db) == before


def test_source_changed_is_not_guessed(api):
    client, db = api
    payload = prepare(client)
    with db.sessions() as session:
        snapshot = session.scalars(select(CalcCompositionRow)).one()
        snapshot.rows = [{**row, "request_link_state": "source_changed"} for row in snapshot.rows]
        session.commit()
    before = _counts(db)
    assert client.post(BASE + "/materialize", json=payload).status_code == 409
    assert _counts(db) == before


def test_snapshot_cas_failure_rolls_back_requests_events_and_procurement(api, monkeypatch):
    from app.modules.requests.composition import CompositionConflict
    from app.modules.requests.infrastructure import SqlAlchemyCompositionRepository
    client, db = api
    payload = prepare(client)
    before = _counts(db)
    def conflict(*args):
        raise CompositionConflict("Состав изменён другим закупщиком")
    monkeypatch.setattr(SqlAlchemyCompositionRepository, "save", conflict)
    assert client.post(BASE + "/materialize", json=payload).status_code == 409
    assert _counts(db) == before
    with db.sessions() as session:
        assert session.scalars(select(ProcurementLineRow)).all() == []


def test_second_selection_preserves_first_link_and_dispatch_is_idempotent(api):
    from tests.workflows.test_kitset_requests_to_procurement import drain
    client, db = api
    payload = prepare(client)
    # Synthetic valid second source row, with its independently proven unit.
    with db.sessions() as session:
        snapshot = session.scalars(select(CalcCompositionRow)).one()
        row = {**snapshot.rows[0], "source_row": 44, "name": "Другая позиция"}
        snapshot.rows = [*snapshot.rows, row]
        session.commit()
    first = client.post(BASE + "/materialize", json=payload).json()
    second = client.post(BASE + "/materialize", json={**payload, "expected_version": 2, "source_rows": [44]})
    assert second.status_code == 200, second.text
    current = client.get(BASE, params={"project": BODY["project"]}).json()
    assert current["rows"][0]["request_id"] == first["rows"][0]["request_id"]
    assert current["rows"][-1]["request_id"] == second.json()["rows"][0]["request_id"]
    drain(db.procurement)
    with db.sessions() as session:
        assert len(session.scalars(select(ProcurementLineRow)).all()) == 2


def test_missing_linked_request_blocks_repeat(api):
    client, db = api
    payload = prepare(client)
    with db.sessions() as session:
        snapshot = session.scalars(select(CalcCompositionRow)).one()
        from uuid import uuid4
        snapshot.rows = [{**row, "request_id": str(uuid4()), "request_link_state": "recorded"}
                         if row["source_row"] in payload["source_rows"] else row for row in snapshot.rows]
        session.commit()
    before = _counts(db)
    assert client.post(BASE + "/materialize", json=payload).status_code == 409
    assert _counts(db) == before


def test_snapshot_without_purchase_intent_is_discoverable(api):
    client, db = api
    prepare(client)
    before = _counts(db)
    response = client.get("/api/procurement/requests-view")
    assert response.status_code == 200, response.text
    assert response.json()["projects"] == [{"key": "708К.4", "kind": "project", "value": "708К.4",
                                            "lines": 0, "counts": {}}]
    assert response.json()["batches"] == [] and _counts(db) == before


@pytest.mark.parametrize("change", [{"name": "X" * 513}, {"quantity": "0.0000001"},
                                  {"price_with_vat": "1.001"}, {"quantity": "999999999999", "price_with_vat": "999999999999"}])
def test_source_values_are_not_silently_truncated_to_request_columns(api, change):
    client, db = api
    payload = prepare(client)
    with db.sessions() as session:
        snapshot = session.scalars(select(CalcCompositionRow)).one()
        snapshot.rows = [{**row, **change} if row["source_row"] in payload["source_rows"] else row
                         for row in snapshot.rows]
        session.commit()
    before = _counts(db)
    assert client.post(BASE + "/materialize", json=payload).status_code == 422
    assert _counts(db) == before


@pytest.mark.parametrize("always", [False, True])
def test_concurrent_global_request_number_retries_whole_transaction(api, monkeypatch, always):
    from sqlalchemy.exc import IntegrityError
    from app.modules.requests.infrastructure import SqlAlchemyRequestRepository
    client, db = api
    payload = prepare(client)
    before = _counts(db)
    original = SqlAlchemyRequestRepository.add
    attempts = []
    def racing_add(self, request):
        attempts.append(self.session)
        original(self, request)
        if always or len(attempts) == 1:
            raise IntegrityError("insert requests", {}, Exception("unique legacy_id"))
    monkeypatch.setattr(SqlAlchemyRequestRepository, "add", racing_add)
    response = client.post(BASE + "/materialize", json=payload)
    assert len(attempts) == 2 and attempts[0] is not attempts[1]
    assert response.status_code == (409 if always else 200), response.text
    if always:
        assert _counts(db) == before
    else:
        with db.sessions() as session:
            assert len(session.scalars(select(RequestRow)).all()) == 1
            assert len(session.scalars(select(ProcurementLineRow)).all()) == 1


def test_service_principal_cannot_materialize(api):
    from app.main import app
    from app.shared.http_auth import Principal, authenticated_principal
    from tests.factories import Actor
    client, db = api
    payload = prepare(client)
    before = _counts(db)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Сервис", department="API"), email="service@example.test", roles=frozenset({"admin"}),
        permissions={"procurement": "edit"}, service=True,
    )
    assert client.post(BASE + "/materialize", json=payload).status_code == 403
    assert _counts(db) == before


def test_materialize_is_refused_by_real_view_as_middleware(view_api):
    from app.modules.identity.middleware import VIEW_ONLY_DETAIL
    admin = _login("admin")
    assert _view(admin, ADMIN2_ID).status_code == 200
    response = admin.post(BASE + "/materialize", json={}, headers=_csrf(admin))
    assert response.status_code == 403 and response.json() == {"detail": VIEW_ONLY_DETAIL}

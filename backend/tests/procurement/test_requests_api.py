"""«Заявки» закупщика over HTTP (option procurement.requests_rb): the view with RB statuses, «Разделить» by quantity
with the request for quotes copied, «Отменить выбранные» with a reason and skips."""

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.main import app
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.workflows.procurement_stock import SqlAlchemyProcurementStockUnitOfWork
from app.modules.purchasing.infrastructure import QuoteLineRow, RfqLineRow, RfqSupplierLineRow
from app.shared import stage_options
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.purchasing.test_rfq_api import BASE, _as, _quote, _rfq, _supplier, rfq_api  # noqa: F401 — fixture

VIEW = "/api/procurement/requests-view"


@pytest.fixture()
def api(rfq_api):  # noqa: F811
    client, lines, sessions = rfq_api
    app.state.procurement_stock_uow_factory = lambda: SqlAlchemyProcurementStockUnitOfWork(sessions)
    yield client, lines, sessions
    del app.state.procurement_stock_uow_factory


def _options(monkeypatch, tmp_path, on: bool = True) -> None:
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"procurement.rfq": True, "procurement.history": True, "procurement.requests_rb": on}), encoding="utf-8")
    monkeypatch.setenv(stage_options.ENV, str(path))


def _view(client, **params) -> dict:
    response = client.get(VIEW, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _lines(client, batch: str, **params) -> dict[str, dict]:
    return {row["legacy_id"]: row for row in _view(client, batch=batch, **params)["lines"]}


def _quantities(sessions, lines, value: str) -> None:
    with sessions() as session:
        for line in lines:
            row = session.get(ProcurementLineRow, line.id)
            row.calculated_quantity = row.actual_quantity = Decimal(value)
            row.version += 1
        session.commit()


def _version(sessions, line_id) -> int:
    with sessions() as session:
        return session.get(ProcurementLineRow, line_id).version


def test_option_off_answers_404(api, monkeypatch, tmp_path):
    client, lines, _ = api
    _options(monkeypatch, tmp_path, on=False)
    assert client.get(VIEW).status_code == 404
    assert client.post(f"/api/procurement/{lines[0].id}/split-qty", json={"version": 1, "quantity": "1"}).status_code == 404
    body = {"lines": [{"id": str(lines[0].id), "version": 1}], "reason": "не закупаем"}
    assert client.post("/api/procurement/cancel-many", json=body).status_code == 404


def test_view_groups_by_project_and_batch_with_rb_statuses(api, monkeypatch, tmp_path):
    client, lines, sessions = api
    _options(monkeypatch, tmp_path)
    summary = _view(client)
    # No project number and no purpose lookup wired in the test app: the purpose is НАЗНАЧЕНИЕ without «.NNN».
    assert [(p["key"], p["kind"], p["lines"], p["counts"]) for p in summary["projects"]] == [
        ("RND", "purpose", 2, {"new": 2})]
    assert sorted(batch["designation"] for batch in summary["batches"]) == ["RND.001", "RND.002"]
    first = next(batch for batch in summary["batches"] if batch["designation"] == "RND.001")
    assert first["selectable"] == 1 and first["mine"] == 1

    rfq = _rfq(client, lines)
    rfq_line = next(item for item in client.get(f"{BASE}/{rfq['id']}").json()["lines"]
                    if item["procurement_line_id"] == str(lines[0].id))
    supplier = _supplier(client, rfq["id"], "ООО Ответил")
    _quote(client, rfq["id"], supplier, rfq_line["id"], unit_price="100")
    assert _view(client)["projects"][0]["counts"] == {"in_rfq": 1, "quoted": 1}
    rows = _lines(client, first["key"])
    row = rows["SM-20260915-01"]
    assert row["rb_status"] == "quoted" and row["rfq"] == {"id": rfq["id"], "number": "КП-2609-001"}
    assert row["plan"]["price_with_vat"] == "110" and not row["selectable"] and row["can_split"]

    assert client.post(f"{BASE}/{rfq['id']}/close").status_code == 200
    assert _view(client)["projects"][0]["counts"] == {"requote": 2}
    row = _lines(client, first["key"])["SM-20260915-01"]
    assert row["requote_from"] == "КП-2609-001" and row["selectable"]

    # «Все» is read only, and the filter leaves other projects' batches out.
    everything = _view(client, scope="all", project="RND", batch=first["key"])
    assert not any(item["selectable"] or item["can_split"] for item in everything["lines"])
    assert _view(client, project="708К.4")["batches"] == []
    focus = _view(client, line=str(lines[1].id))["focus"]
    assert focus == {"project": "RND", "batch": "RND|rnd.002"}


def test_another_buyer_sees_only_his_lines_and_unclaimed_new_ones(api, monkeypatch, tmp_path):
    client, lines, sessions = api
    _options(monkeypatch, tmp_path)
    _as("Сидоров")
    assert _view(client)["batches"] == []
    assert len(_view(client, scope="all")["batches"]) == 2
    _as("Петров")


def test_split_by_quantity_copies_the_request_line_and_its_quotes(api, monkeypatch, tmp_path):
    client, lines, sessions = api
    _options(monkeypatch, tmp_path)
    _quantities(sessions, lines[:1], "10")
    rfq = _rfq(client, lines)
    rfq_line = next(item for item in client.get(f"{BASE}/{rfq['id']}").json()["lines"]
                    if item["procurement_line_id"] == str(lines[0].id))
    asked = _supplier(client, rfq["id"], "ООО Спросили")
    _supplier(client, rfq["id"], "ООО Другой")
    subset = client.put(f"{BASE}/{rfq['id']}/suppliers/{asked}/lines", json={"line_ids": [rfq_line["id"]]})
    assert subset.status_code == 200, subset.text
    _quote(client, rfq["id"], asked, rfq_line["id"], unit_price="100", moq="5")
    version = _version(sessions, lines[0].id)

    stale = client.post(f"/api/procurement/{lines[0].id}/split-qty", json={"version": version - 1, "quantity": "3"})
    assert stale.status_code == 409
    too_much = client.post(f"/api/procurement/{lines[0].id}/split-qty", json={"version": version, "quantity": "10"})
    assert too_much.status_code == 422 and "меньше 10 шт" in too_much.json()["detail"]

    split = client.post(f"/api/procurement/{lines[0].id}/split-qty", json={"version": version, "quantity": "3"})
    assert split.status_code == 200, split.text
    body = split.json()
    assert (body["original"]["calculated_quantity"], body["original"]["version"]) == ("7", version + 1)
    assert body["created"]["calculated_quantity"] == "3" and body["created"]["legacy_id"].startswith("SM-")
    assert body["created"]["supplier"] is None and body["rfq"]["rfq_number"] == "КП-2609-001"
    assert body["rfq"]["quotes"] == 1
    created_id = body["created"]["id"]
    with sessions() as session:
        live = {str(row.procurement_line_id): row for row in session.scalars(
            select(RfqLineRow).where(RfqLineRow.removed_at.is_(None)))}
        assert (live[str(lines[0].id)].quantity, live[created_id].quantity) == (Decimal("7"), Decimal("3"))
        copied = session.scalars(select(QuoteLineRow).where(QuoteLineRow.rfq_line_id == live[created_id].id)).all()
        assert [(quote.unit_price, quote.moq) for quote in copied] == [(Decimal("100"), Decimal("5"))]
        subsets = session.scalar(select(func.count()).select_from(RfqSupplierLineRow).where(
            RfqSupplierLineRow.rfq_line_id == live[created_id].id))
        assert subsets == 1
        events = {row.event_type for row in session.scalars(select(AuditEventRow))}
        assert {"procurement.split_quantity", "purchasing.rfq_line_split"} <= events
    # Both parts are in the request, with the quote: «Есть КП».
    batch = _view(client)["batches"]
    counts = next(item for item in batch if item["designation"] == "RND.001")["counts"]
    assert counts == {"quoted": 2}
    history = client.get(f"/api/procurement/{lines[0].id}/history").json()["events"]
    split_event = next(event for event in history if event["title"] == "Строка разделена")
    assert "отделено 3" in split_event["details"] and "КП из КП-2609-001" in split_event["details"]
    created_history = client.get(f"/api/procurement/{created_id}/history").json()["events"]
    assert any(event["title"] == "Отделена от строки" for event in created_history)


def test_split_outside_a_request_keeps_the_supplier_and_only_the_owner_splits(api, monkeypatch, tmp_path):
    client, lines, sessions = api
    _options(monkeypatch, tmp_path)
    _quantities(sessions, lines[1:], "4")
    with sessions() as session:
        session.get(ProcurementLineRow, lines[1].id).supplier = "ООО Своё"
        session.commit()
    version = _version(sessions, lines[1].id)
    _as("Сидоров")
    foreign = client.post(f"/api/procurement/{lines[1].id}/split-qty", json={"version": version, "quantity": "1"})
    assert foreign.status_code == 422 and "свои строки" in foreign.json()["detail"]
    _as("Петров")
    done = client.post(f"/api/procurement/{lines[1].id}/split-qty", json={"version": version, "quantity": "1.5"})
    assert done.status_code == 200, done.text
    assert done.json()["created"]["supplier"] == "ООО Своё" and done.json()["rfq"] is None
    assert done.json()["created"]["status"] == "Готов к запросу"


def test_cancel_many_keeps_the_reason_and_skips_what_cannot_go(api, monkeypatch, tmp_path):
    client, lines, sessions = api
    _options(monkeypatch, tmp_path)
    first, second = (_version(sessions, line.id) for line in lines)
    with sessions() as session:
        session.get(ProcurementLineRow, lines[1].id).status = "В пути"
        session.commit()
    empty = client.post("/api/procurement/cancel-many",
                        json={"lines": [{"id": str(lines[0].id), "version": first}], "reason": "  "})
    assert empty.status_code == 422 and "причину" in empty.json()["detail"]

    missing = str(uuid4())
    body = {"lines": [
        {"id": str(lines[0].id), "version": first}, {"id": str(lines[1].id), "version": second},
        {"id": missing, "version": 1},
    ], "reason": "не закупаем"}
    result = client.post("/api/procurement/cancel-many", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["cancelled"] == [str(lines[0].id)]
    skipped = {item["id"]: item["reason"] for item in result.json()["skipped"]}
    assert skipped[missing] == "строка не найдена" and "оплачена" in skipped[str(lines[1].id)]

    again = client.post("/api/procurement/cancel-many", json={**body, "lines": body["lines"][:1]})
    assert again.json()["skipped"] == [{"id": str(lines[0].id), "reason": "строка изменилась — обновите список"}]
    with sessions() as session:
        row = session.get(ProcurementLineRow, lines[0].id)
        assert row.status == "Отменено"
        event = session.scalar(select(AuditEventRow).where(
            AuditEventRow.aggregate_id == lines[0].id, AuditEventRow.event_type == "procurement.cancelled"))
        assert event.details["reason"] == "не закупаем"
    summary = _view(client)
    batch = next(item for item in summary["batches"] if item["designation"] == "RND.001")
    row = _lines(client, batch["key"])["SM-20260915-01"]
    assert row["rb_status"] == "cancelled" and row["cancel_reason"] == "не закупаем" and row["can_resend"]
    history = client.get(f"/api/procurement/{lines[0].id}/history").json()["events"]
    assert any(event["title"] == "Отменена" and "причина: не закупаем" in event["details"] for event in history)


def test_cancel_many_skips_another_buyers_line(api, monkeypatch, tmp_path):
    client, lines, sessions = api
    _options(monkeypatch, tmp_path)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Сидоров", department="Закупки"), email="s@example.test", roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )
    body = {"lines": [{"id": str(lines[0].id), "version": _version(sessions, lines[0].id)}], "reason": "дубль"}
    result = client.post("/api/procurement/cancel-many", json=body).json()
    assert result == {"cancelled": [], "skipped": [{"id": str(lines[0].id), "reason": "строка другого закупщика"}]}
    _as("Петров")

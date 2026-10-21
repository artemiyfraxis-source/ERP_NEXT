"""«Запросы КП» over HTTP: candidates → request → suppliers → quotes → matrix → decision → the procurement line."""

import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.procurement.application import consume_procurement_requested
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementUnitOfWork
from app.modules.purchasing.infrastructure import (
    DecisionRow,
    QuoteRow,
    RfqLineRow,
    SqlAlchemyPurchasingUnitOfWork,
    SupplierRow,
)
from app.modules.requests.api import moscow_now
from app.shared import stage_options
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor
from tests.procurement.test_api import message

BASE = "/api/purchasing/rfqs"
NOW = datetime(2026, 9, 15, 11, 30)


def _as(name: str) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name=name, department="Закупки"), email=f"{name}@example.test", roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )


def _options(monkeypatch, tmp_path, on: bool) -> None:
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"procurement.rfq": on}), encoding="utf-8")
    monkeypatch.setenv(stage_options.ENV, str(path))


@pytest.fixture()
def rfq_api(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.procurement_uow_factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    _as("Петров")
    app.dependency_overrides[moscow_now] = lambda: NOW
    _options(monkeypatch, tmp_path, True)
    client = TestClient(app)
    lines = [consume_procurement_requested(message(n), app.state.procurement_uow_factory()) for n in (1, 2)]
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id) for line in lines]})
    assert taken.status_code == 200, taken.text
    with sessions() as session:
        row = session.get(ProcurementLineRow, lines[0].id)
        row.calculated_price_with_vat = Decimal("110")
        row.design_article = "SHR-02V-S-B"
        session.commit()
    yield client, lines, sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.procurement_uow_factory
    del app.state.purchasing_uow_factory
    engine.dispose()


def _rfq(client, lines) -> dict:
    created = client.post(BASE, json={"line_ids": [str(line.id) for line in lines], "title": "Разъёмы"})
    assert created.status_code == 201, created.text
    return created.json()


def _supplier(client, rfq_id: str, name: str, sessions=None, key: bool = False) -> str:
    added = client.post(f"{BASE}/{rfq_id}/suppliers", json={"name": name})
    assert added.status_code == 201, added.text
    supplier_id = added.json()["supplier_id"]
    if key:
        with sessions() as session:
            session.get(SupplierRow, UUID(supplier_id)).is_key = True
            session.commit()
    return supplier_id


def _quote(client, rfq_id, supplier_id, rfq_line_id, **line) -> str:
    body = {"supplier_id": supplier_id, "lines": [{"rfq_line_id": rfq_line_id, **line}]}
    response = client.post(f"{BASE}/{rfq_id}/quotes", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_option_off_means_404(rfq_api, monkeypatch, tmp_path):
    client, _, _ = rfq_api
    _options(monkeypatch, tmp_path, False)
    assert client.get(BASE).status_code == 404
    assert client.get(f"{BASE}/candidates").status_code == 404
    assert client.post(BASE, json={"line_ids": [str(uuid4())]}).status_code == 404


def test_full_flow_from_candidates_to_the_procurement_line(rfq_api):
    client, lines, sessions = rfq_api
    candidates = client.get(f"{BASE}/candidates").json()["lines"]
    assert {line["id"] for line in candidates} == {str(line.id) for line in lines}
    first = next(line for line in candidates if line["id"] == str(lines[0].id))
    assert first["budget_unit_gross"] == "110" and first["quantity"] == "2" and first["article"] == "SHR-02V-S-B"

    rfq = _rfq(client, lines)
    assert rfq["number"] == "КП-2609-001"
    assert client.get(f"{BASE}/candidates").json()["lines"] == []
    again = client.post(BASE, json={"line_ids": [str(lines[0].id)]})
    assert again.status_code == 409 and "уже в запросе" in again.json()["detail"]

    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    line_a = next(line for line in matrix["lines"] if line["procurement_line_id"] == str(lines[0].id))
    key = _supplier(client, rfq["id"], "ООО Ключевой", sessions, key=True)
    cheap = _supplier(client, rfq["id"], "ООО Дёшево")
    bulk = _supplier(client, rfq["id"], "  ооо дёшево ")  # same name key → same supplier, no duplicate
    assert bulk == cheap
    bulk = _supplier(client, rfq["id"], "ООО Опт")
    assert client.post(f"{BASE}/{rfq['id']}/suppliers/{key}/sent").status_code == 200

    _quote(client, rfq["id"], key, line_a["id"], unit_price="120")  # 2 × 120 = 240
    cheap_quote = _quote(client, rfq["id"], cheap, line_a["id"], unit_price="90", vat_included=False, vat_rate="22",
                         pack="3")  # 3 × 109.8 = 329.40
    _quote(client, rfq["id"], bulk, line_a["id"], unit_price="50", moq="100")  # 100 × 50 = 5000

    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    line_a = next(line for line in matrix["lines"] if line["id"] == line_a["id"])
    assert line_a["gate"] == {"priced": 3, "required": 3, "has_non_key": True, "require_non_key": True, "passes": True}
    cells = line_a["cells"]
    assert cells[key]["cheapest"] and cells[key]["total"] == "240"
    assert cells[cheap]["gross_unit"] == "109.8" and cells[cheap]["cover_qty"] == "3" and cells[cheap]["total"] == "329.4"
    assert cells[bulk]["cover_qty"] == "100" and cells[bulk]["requires"] == ["not_cheapest_reason"]
    assert line_a["delta_pct"] == "9.1"  # 120 vs budget 110
    head = {supplier["supplier_id"]: supplier for supplier in matrix["suppliers"]}
    assert head[key]["is_key"] and head[key]["sent_at"] and head[key]["answered"]
    # «всё у него»: the quote names no delivery — the settings' 1 500 ₽ is counted (RB rfq-matrix).
    assert head[key]["basket_total"] == "1740" and head[key]["delivery_known"] is False
    # Suppliers are ordered key first.
    assert matrix["suppliers"][0]["supplier_id"] == key

    decision_url = f"{BASE}/{rfq['id']}/decisions"
    chosen = {"rfq_line_id": line_a["id"], "quote_line_id": cells[cheap]["quote_line_id"]}
    refused = client.post(decision_url, json=chosen)
    assert refused.status_code == 422
    assert refused.json()["detail"]["violations"][0]["field"] == "not_cheapest_reason"
    assert "не самое дешёвое" in refused.json()["detail"]["message"]

    decided = client.post(decision_url, json={**chosen, "not_cheapest_reason": "Срок", "gate_override_reason": "лишнее"})
    assert decided.status_code == 201, decided.text
    body = decided.json()
    assert body["cover_quantity"] == "3" and body["total_gross"] == "329.4" and body["applied_at"]
    assert body["gate_override_reason"] is None and body["not_cheapest_reason"] == "Срок"
    assert body["baseline_unit_gross"] == "120" and body["cheapest_unit_gross"] == "120" and body["priced_quotes"] == 3

    with sessions() as session:
        row = session.get(ProcurementLineRow, lines[0].id)
        assert (row.supplier, row.actual_quantity, row.actual_unit, row.status) == (
            "ООО Дёшево", Decimal("3"), "шт", "Готов к запросу")

    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    line_a = next(line for line in matrix["lines"] if line["id"] == line_a["id"])
    assert line_a["cells"][cheap]["decided"] and line_a["decision"]["supplier_id"] == cheap
    assert next(s for s in matrix["suppliers"] if s["supplier_id"] == cheap)["chosen_total"] == "329.4"

    assert client.post(decision_url, json={**chosen, "not_cheapest_reason": "Срок"}).status_code == 409
    assert client.delete(f"{BASE}/quotes/{cheap_quote}").status_code == 409
    assert client.delete(f"{decision_url}/{body['id']}").status_code == 204
    assert client.delete(f"{BASE}/quotes/{cheap_quote}").status_code == 204
    with sessions() as session:
        row = session.get(ProcurementLineRow, lines[0].id)
        assert row.supplier == "ООО Дёшево"  # an undone decision leaves the line's facts as they are

    listed = client.get(BASE).json()["rfqs"]
    assert listed[0]["number"] == "КП-2609-001" and listed[0]["lines"] == 2 and listed[0]["quotes"] == 2

    assert client.post(f"{BASE}/{rfq['id']}/close").status_code == 200
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(RfqLineRow).where(RfqLineRow.removed_at.is_(None))) == 0
    assert len(client.get(f"{BASE}/candidates").json()["lines"]) == 2
    assert client.post(f"{BASE}/{rfq['id']}/suppliers", json={"name": "Ещё"}).status_code == 422


def test_a_stale_line_version_is_a_conflict_and_nothing_is_saved(rfq_api):
    client, lines, sessions = rfq_api
    rfq = _rfq(client, lines[:1])
    line_id = client.get(f"{BASE}/{rfq['id']}").json()["lines"][0]["id"]
    supplier = _supplier(client, rfq["id"], "ООО Один")
    _quote(client, rfq["id"], supplier, line_id, unit_price="100")
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    line = matrix["lines"][0]
    assert line["gate"]["passes"] is False and line["cells"][supplier]["requires"] == ["gate_override_reason"]
    patch = client.patch(f"/api/procurement/{lines[0].id}/facts", json={"version": line["version"], "site_name": "x"})
    assert patch.status_code == 200, patch.text
    decision = {
        "rfq_line_id": line_id, "quote_line_id": line["cells"][supplier]["quote_line_id"],
        "expected_version": line["version"], "gate_override_reason": "Срочно",
    }
    conflict = client.post(f"{BASE}/{rfq['id']}/decisions", json=decision)
    assert conflict.status_code == 409 and "обновите" in conflict.json()["detail"]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DecisionRow)) == 0
        assert session.get(ProcurementLineRow, lines[0].id).supplier is None
    fresh = client.post(f"{BASE}/{rfq['id']}/decisions", json={**decision, "expected_version": None})
    assert fresh.status_code == 201, fresh.text


def test_only_the_lines_buyer_puts_it_into_a_request_or_decides(rfq_api):
    client, lines, _ = rfq_api
    rfq = _rfq(client, lines[:1])
    line_id = client.get(f"{BASE}/{rfq['id']}").json()["lines"][0]["id"]
    supplier = _supplier(client, rfq["id"], "ООО Один")
    _quote(client, rfq["id"], supplier, line_id, unit_price="100")
    quote_line = client.get(f"{BASE}/{rfq['id']}").json()["lines"][0]["cells"][supplier]["quote_line_id"]

    _as("Иванов")
    assert client.get(f"{BASE}/candidates").json()["lines"] == []
    assert client.post(BASE, json={"line_ids": [str(lines[1].id)]}).status_code == 403
    assert client.get(BASE).json()["rfqs"] == []
    assert client.get(f"{BASE}?scope=all").json()["rfqs"][0]["mine"] is False
    assert client.get(f"{BASE}/{rfq['id']}").json()["rfq"]["mine"] is False  # a viewer sees the matrix
    decision = {"rfq_line_id": line_id, "quote_line_id": quote_line, "gate_override_reason": "Срочно"}
    assert client.post(f"{BASE}/{rfq['id']}/decisions", json=decision).status_code == 403
    assert client.post(f"{BASE}/{rfq['id']}/suppliers", json={"name": "Чужой"}).status_code == 403
    assert client.get(f"{BASE}/{uuid4()}").status_code == 404
    assert client.post(f"{BASE}/{uuid4()}/close").status_code == 404


def test_paste_preview_matches_rows_to_request_lines(rfq_api):
    client, lines, _ = rfq_api
    rfq = _rfq(client, lines)
    text = "Наименование\tАртикул\tКол-во\tЦена без НДС\tСрок\nРазъём\tshr 02v-s-b\t2\t1 200,50\t5 дней\nНеизвестное\t\t1\t10\t\n"
    preview = client.post(f"{BASE}/{rfq['id']}/quotes/paste", json={"text": text})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["vat_included"] is False and body["columns"]["price"] == 3
    first, second = body["rows"]
    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    line_a = next(line for line in matrix["lines"] if line["procurement_line_id"] == str(lines[0].id))
    assert (first["rfq_line_id"], first["matched_by"], first["price"], first["lead_days"]) == (
        line_a["id"], "article", "1200.5", 5)
    assert second["rfq_line_id"] is None
    assert client.post(f"{BASE}/{rfq['id']}/quotes/paste", json={"text": "\n\n"}).status_code == 422


def test_suggestions_key_rotation_and_the_lines_own_supplier(rfq_api):
    client, lines, sessions = rfq_api
    with sessions() as session:
        def add(name, is_key, categories):
            session.add(SupplierRow(id=uuid4(), name=name, name_key=name.casefold(), categories=categories,
                                    is_key=is_key, domains=[], created_at=NOW, version=1))
        add("Ключ-разъёмы", True, ["connector"])
        add("Ключ-кабель", True, ["cable"])
        add("Новый", False, ["Разъёмы"])
        add("Старый", False, ["connector"])
        add("План", False, [])
        session.commit()
        line = session.get(ProcurementLineRow, lines[0].id)
        line.supplier = "план"
        session.commit()
    rfq = _rfq(client, lines)  # both lines are «Разъём» → connector
    suggestion = client.get(f"{BASE}/suggest-suppliers", params={"rfq_id": rfq["id"]}).json()
    reasons = {item["name"]: (item["reason"], item["preselect"]) for item in suggestion["suggestions"]}
    assert suggestion["categories"] == ["connector"]
    assert reasons["Ключ-разъёмы"] == ("key", True) and "Ключ-кабель" not in reasons
    assert reasons["План"] == ("plan", True)
    assert {reasons["Новый"], reasons["Старый"]} == {("rotation", True), ("category", False)}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(QuoteRow)) == 0

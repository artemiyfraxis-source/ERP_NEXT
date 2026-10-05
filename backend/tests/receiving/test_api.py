from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.receiving.application import consume_receiving_expected
from app.modules.receiving.infrastructure import SqlAlchemyReceivingUnitOfWork
from app.shared.clock import moscow_now
from app.shared.events import OutboxMessage
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor


def expected_message(suffix: int = 1) -> OutboxMessage:
    return OutboxMessage(
        id=UUID(f"10000000-0000-0000-0000-{suffix:012d}"),
        topic="receiving.expected",
        idempotency_key=f"procurement:{suffix}:receiving:v1",
        created_at=datetime(2026, 9, 21, 10, 0),
        payload={
            "procurement_line_id": f"20000000-0000-0000-0000-{suffix:012d}",
            "legacy_id": f"SM-20260921-{suffix:02d}",
            "warehouse": "ЦАР",
            "article": "CON-001",
            "name": "Разъём",
            "item_type": "Комплектующие",
            "purpose": "FRX.001",
            "goal": "",
            "source_link": "",
            "supplier": "ООО Поставка",
            "ordered_quantity": "10",
            "unit": "шт",
            "quality_document_required": False,
        },
    )


@pytest.fixture()
def receiving_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyReceivingUnitOfWork(sessions)
    app.state.receiving_uow_factory = factory
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Приёмщик", department="ОТК"),
        email="receiver@example.test",
        roles=frozenset({"receiver"}),
        permissions={"receiving": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 21, 11, 0)
    line = consume_receiving_expected(expected_message(), factory())
    yield TestClient(app), line
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.receiving_uow_factory
    engine.dispose()


def test_receiving_requires_the_receiving_section(receiving_api):
    # Access levels: the matrix decides — «Приёмка» closed, refused whatever else is open.
    client, _ = receiving_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Закупщик", department="Закупки"),
        email="estimator@example.test",
        roles=frozenset({"commercial"}),
        permissions={"procurement": "edit", "warehouse": "edit"},
    )

    assert client.get("/api/receiving").status_code == 403


def test_receiving_queue_has_stable_cursor_pages(receiving_api):
    client, first_line = receiving_api
    factory = app.state.receiving_uow_factory
    second_line = consume_receiving_expected(expected_message(2), factory())

    first_page = client.get("/api/receiving?limit=1")
    cursor = first_page.json()["next_cursor"]
    second_page = client.get(f"/api/receiving?limit=1&after={cursor}")

    assert first_page.status_code == 200
    assert cursor is not None
    assert second_page.status_code == 200
    assert second_page.json()["next_cursor"] is None
    assert {
        first_page.json()["lines"][0]["id"],
        second_page.json()["lines"][0]["id"],
    } == {str(first_line.id), str(second_line.id)}


def test_documents_are_derived_from_urls_and_accepted_line_is_placed(receiving_api):
    client, line = receiving_api
    base = {
        "fact_quantity": "10",
        "document_quantity": "10",
        "expiry_date": "2027-01-01",
    }

    without_upd = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={**base, "version": line.version, "has_shipping_document": True},
    )
    accepted = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={
            **base,
            "version": without_upd.json()["line"]["version"],
            "shipping_document_url": "https://files.test/upd.pdf",
            "series": "LOT-2409",
        },
    )
    placed = client.post(
        f"/api/receiving/{line.id}/place",
        json={
            "version": accepted.json()["line"]["version"],
            "storage_location": "1F4",
        },
    )

    # Owner 05.10.2026: no УПД file → still «Принято» with a ГРН, only a non-blocking note.
    assert without_upd.status_code == 200, without_upd.text
    assert without_upd.json()["line"]["status"] == "Принято"
    assert without_upd.json()["line"]["grn"]
    assert without_upd.json()["line"]["nonconformities"] == []
    assert without_upd.json()["line"]["shipping_document_note"] == "УПД не приложен — придёт по ЭДО"
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["line"]["status"] == "Принято"
    assert accepted.json()["line"]["shipping_document_note"] is None
    assert accepted.json()["line"]["article"] == "CON-001"
    assert accepted.json()["line"]["series"] == "LOT-2409"
    assert placed.status_code == 200, placed.text
    assert placed.json()["line"]["warehouse_event_emitted"] is True
    assert placed.json()["line"]["storage_location"] == "1F4"

    reinspection = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={
            **base,
            "version": placed.json()["line"]["version"],
            "shipping_document_url": "https://files.test/replacement.pdf",
        },
    )
    relocation = client.post(
        f"/api/receiving/{line.id}/place",
        json={
            "version": placed.json()["line"]["version"],
            "storage_location": "1F5",
        },
    )

    assert reinspection.status_code == 422
    assert relocation.status_code == 422


@pytest.mark.parametrize(
    "document_url",
    ["x", "javascript:alert(1)", "file:///tmp/upd.pdf"],
)
def test_receiving_rejects_non_http_document_urls(receiving_api, document_url):
    client, line = receiving_api

    response = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={
            "version": line.version,
            "fact_quantity": "10",
            "document_quantity": "10",
            "shipping_document_url": document_url,
        },
    )

    assert response.status_code == 422


def test_new_vk_archives_line_and_queue_shows_both_rows(receiving_api):
    client, line = receiving_api
    base = {"fact_quantity": "10", "document_quantity": "10", "expiry_date": "2027-01-01"}
    early = client.post(f"/api/receiving/{line.id}/vk-revision", json={"version": line.version})
    assert early.status_code == 422
    assert "ещё не проведён ВК" in early.json()["detail"]

    waiting = client.post(f"/api/receiving/{line.id}/inspect", json={**base, "version": line.version})
    revised = client.post(
        f"/api/receiving/{line.id}/vk-revision", json={"version": waiting.json()["line"]["version"]}
    )

    assert revised.status_code == 200, revised.text
    body = revised.json()
    assert body["archived"]["status"] == "Архив ВК"
    assert body["line"]["status"] == "В пути"
    assert body["line"]["legacy_id"] == "SM-20260921-01-В2"
    assert body["line"]["revision_of_id"] == str(line.id)
    assert body["line"]["grn"] is None

    again = client.post(
        f"/api/receiving/{line.id}/vk-revision", json={"version": body["archived"]["version"]}
    )
    stale = client.post(f"/api/receiving/{line.id}/vk-revision", json={"version": line.version})
    assert again.status_code == 422
    assert stale.status_code == 409

    queue = client.get("/api/receiving").json()["lines"]
    assert [(row["legacy_id"], row["status"]) for row in queue] == [
        ("SM-20260921-01", "Архив ВК"),
        ("SM-20260921-01-В2", "В пути"),
    ]


def test_historical_receipt_uses_document_date_for_verdict_but_today_for_grn(receiving_api):
    client, line = receiving_api
    base = {
        "version": line.version,
        "fact_quantity": "10",
        "document_quantity": "10",
        "expiry_date": "2026-03-01",
        "shipping_document_url": "https://files.test/upd.pdf",
    }

    historical = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={**base, "historical": True, "document_date": "2026-02-10"},
    )

    assert historical.status_code == 200, historical.text
    body = historical.json()["line"]
    assert body["status"] == "Принято"
    assert body["verdict"] == "Годен"
    assert body["grn"].split(".")[1] == "26"


def test_document_date_without_historical_flag_keeps_today(receiving_api):
    client, line = receiving_api
    response = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={
            "version": line.version,
            "fact_quantity": "10",
            "document_quantity": "10",
            "expiry_date": "2026-03-01",
            "shipping_document_url": "https://files.test/upd.pdf",
            "document_date": "2026-02-10",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["line"]["status"] == "Возврат"


def test_putaway_sheet_shows_accepted_unplaced_lines_only(receiving_api):
    """WH-6: печатный лист перемещения — только «Принято» без места; поставленное на место пропадает.
    Тот же печатный механизм, что и FF-8 (app.shared.print_sheet + frontend/src/print.ts)."""
    client, line = receiving_api
    base = {"fact_quantity": "10", "document_quantity": "10", "expiry_date": "2027-01-01"}

    empty = client.get("/api/receiving/putaway-sheet")
    assert empty.status_code == 422, empty.text

    accepted = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={**base, "version": line.version, "shipping_document_url": "https://files.test/upd.pdf"},
    )
    assert accepted.json()["line"]["status"] == "Принято"

    sheet = client.get("/api/receiving/putaway-sheet")
    assert sheet.status_code == 200, sheet.text
    body = sheet.json()["sheet"]
    assert body["title"] == "ЛИСТ ПЕРЕМЕЩЕНИЯ НА СКЛАД"
    assert len(body["rows"]) == 1
    assert body["rows"][0][1] == line.name

    placed = client.post(
        f"/api/receiving/{line.id}/place",
        json={"version": accepted.json()["line"]["version"], "storage_location": "1F4"},
    )
    assert placed.status_code == 200, placed.text

    after_placement = client.get("/api/receiving/putaway-sheet")
    assert after_placement.status_code == 422, after_placement.text


def test_putaway_sheet_requires_the_receiving_section(receiving_api):
    client, _ = receiving_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Сметчик", department="КБ"),
        email="estimator@example.test",
        roles=frozenset({"commercial"}),
        permissions={"warehouse": "edit"},
    )

    assert client.get("/api/receiving/putaway-sheet").status_code == 403


def test_upd_file_is_stored_in_erp_and_its_link_passes_the_inspection(receiving_api):
    import base64

    client, line = receiving_api
    pdf = b"%PDF-1.4 scan of the UPD"
    uploaded = client.post(
        "/api/receiving/files",
        json={
            "line_id": str(line.id),
            "kind": "upd",
            "file_name": "скан.PDF",
            "content_type": "application/pdf",
            "content_base64": base64.b64encode(pdf).decode(),
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    url = uploaded.json()["url"]
    assert url.startswith("/api/receiving/files/")
    assert uploaded.json()["file_name"] == "УПД SM-20260921-01.pdf"

    opened = client.get(url)
    assert opened.status_code == 200
    assert opened.content == pdf
    assert opened.headers["content-type"] == "application/pdf"
    assert "inline" in opened.headers["content-disposition"]

    accepted = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={"version": line.version, "fact_quantity": "10", "document_quantity": "10", "shipping_document_url": url},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["line"]["status"] == "Принято"
    assert accepted.json()["line"]["shipping_document_url"] == url
    assert accepted.json()["line"]["grn_prefix"] == "FRX"


@pytest.mark.parametrize(
    ("file_name", "size", "detail"),
    [("upd.docx", 3, "нужен PDF или фото JPG/PNG"), ("upd.pdf", 20 * 1024 * 1024 + 1, "больше 20 МБ")],
    ids=["word", "over-20-mb"],
)
def test_receiving_file_refuses_other_types_and_large_files(receiving_api, file_name, size, detail):
    import base64

    client, line = receiving_api
    response = client.post(
        "/api/receiving/files",
        json={
            "line_id": str(line.id),
            "kind": "upd",
            "file_name": file_name,
            "content_base64": base64.b64encode(b"x" * size).decode(),
        },
    )
    assert response.status_code == 422
    assert detail in response.json()["detail"]


def test_receiving_file_needs_a_known_line_and_an_editor(receiving_api):
    import base64

    client, line = receiving_api
    body = {"kind": "quality", "file_name": "cert.png", "content_base64": base64.b64encode(b"png").decode()}
    missing = {**body, "line_id": "00000000-0000-0000-0000-000000000009"}
    assert client.post("/api/receiving/files", json=missing).status_code == 404
    assert client.get("/api/receiving/files/00000000-0000-0000-0000-000000000009").status_code == 404

    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Мастер", department="Цех"),
        email="master@example.test",
        roles=frozenset({"receiver"}),
        permissions={"receiving": "view"},
    )
    assert client.post("/api/receiving/files", json={**body, "line_id": str(line.id)}).status_code == 403


def _second_line():
    return consume_receiving_expected(expected_message(2), app.state.receiving_uow_factory())


def _documents(client, lines, **fields):
    return client.post(
        "/api/receiving/documents",
        json={"lines": [{"id": str(line.id), "version": line.version} for line in lines], **fields},
    )


def test_bulk_upd_is_attached_to_selected_lines_without_status_change(receiving_api):
    client, first = receiving_api
    second = _second_line()

    response = _documents(
        client, [first, second], document_number="УПД-77", document_date="2026-09-20",
        shipping_document_url="https://files.test/upd.pdf",
    )

    assert response.status_code == 200, response.text
    lines = response.json()["lines"]
    assert [item["document_number"] for item in lines] == ["УПД-77", "УПД-77"]
    assert all(item["version"] == first.version + 1 for item in lines)
    assert all(item["status"] == first.status.value for item in lines)
    with app.state.receiving_uow_factory() as uow:
        stored = uow.receiving.get(first.id)
    assert stored.shipping_document_url == "https://files.test/upd.pdf"
    assert str(stored.document_date) == "2026-09-20"


def test_bulk_upd_blank_fields_keep_existing_values(receiving_api):
    client, first = receiving_api
    done = _documents(client, [first], document_number="УПД-1", document_date="2026-09-20").json()["lines"][0]
    again = client.post(
        "/api/receiving/documents",
        json={"lines": [{"id": done["id"], "version": done["version"]}], "shipping_document_url": "https://files.test/a.pdf"},
    )

    assert again.status_code == 200, again.text
    line = again.json()["lines"][0]
    assert line["document_number"] == "УПД-1"
    assert line["document_date"] == "2026-09-20"


def test_bulk_upd_is_attached_to_accepted_line_on_stock_and_clears_the_note(receiving_api):
    client, first = receiving_api
    base = {"fact_quantity": "10", "document_quantity": "10", "expiry_date": "2027-01-01"}
    accepted = client.post(
        f"/api/receiving/{first.id}/inspect", json={**base, "version": first.version}
    ).json()["line"]
    on_stock = client.post(
        f"/api/receiving/{first.id}/place", json={"version": accepted["version"], "storage_location": "1F4"}
    ).json()["line"]
    assert on_stock["warehouse_event_emitted"] is True
    assert on_stock["shipping_document_note"] == "УПД не приложен — придёт по ЭДО"

    response = client.post(
        "/api/receiving/documents",
        json={"lines": [{"id": on_stock["id"], "version": on_stock["version"]}], "document_number": "ЭДО-9"},
    )

    assert response.status_code == 200, response.text
    line = response.json()["lines"][0]
    assert line["status"] == "Принято"
    assert line["document_number"] == "ЭДО-9"
    assert line["shipping_document_note"] is None
    assert line["warehouse_event_emitted"] is True
    assert (line["grn"], line["accepted_quantity"]) == (on_stock["grn"], on_stock["accepted_quantity"])


def test_bulk_upd_rejects_archived_vk_line_and_applies_nothing(receiving_api):
    client, first = receiving_api
    second = _second_line()
    base = {"fact_quantity": "10", "document_quantity": "10", "expiry_date": "2027-01-01"}
    accepted = client.post(
        f"/api/receiving/{second.id}/inspect",
        json={**base, "version": second.version, "shipping_document_url": "https://files.test/x.pdf"},
    ).json()["line"]
    archived = client.post(
        f"/api/receiving/{second.id}/vk-revision", json={"version": accepted["version"]}
    ).json()["archived"]
    assert archived["status"] == "Архив ВК"

    response = client.post(
        "/api/receiving/documents",
        json={
            "lines": [{"id": str(first.id), "version": first.version}, {"id": archived["id"], "version": archived["version"]}],
            "document_number": "УПД-9",
        },
    )

    assert response.status_code == 422
    assert "Архив ВК" in response.json()["detail"]
    with app.state.receiving_uow_factory() as uow:
        assert uow.receiving.get(first.id).document_number == ""
        assert uow.receiving.get(first.id).version == first.version


def test_bulk_upd_version_conflict_is_409(receiving_api):
    client, first = receiving_api

    response = client.post(
        "/api/receiving/documents",
        json={"lines": [{"id": str(first.id), "version": first.version + 5}], "document_number": "УПД-1"},
    )

    assert response.status_code == 409


def test_bulk_upd_requires_lines_and_some_value(receiving_api):
    client, first = receiving_api

    assert client.post("/api/receiving/documents", json={"lines": [], "document_number": "1"}).status_code == 422
    empty = _documents(client, [first])
    assert empty.status_code == 422
    assert "Укажите файл, № или дату УПД" in empty.json()["detail"]

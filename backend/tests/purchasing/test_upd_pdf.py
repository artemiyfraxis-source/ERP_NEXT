"""The registry's PDF parser at work: upload → queue → one claim → the answer written, the buyer's word kept
(purchasing.upd_pdf, app.workflows.upd_pdf_parse, /api/purchasing/upd/import-pdf and /{id}/reparse)."""

from __future__ import annotations

import base64
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.purchasing import upd_parse, upd_pdf
from app.modules.purchasing import upd_registry as repo
from app.modules.purchasing.infrastructure import SqlAlchemyPurchasingUnitOfWork, UpdDocumentRow, UpdLineRow
from app.modules.warehouse import receipt_llm
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows import upd_pdf_parse as flow
from tests.factories import Actor

BASE = "/api/purchasing/upd"
NOW = datetime(2026, 10, 26, 12, 0)
PDF = b"%PDF-1.4\n% a scanned UPD\n"
READY = receipt_llm.LlmConfig(api_key="test-key")
ANSWER = {
    "docKind": "UPD", "number": "№ 916", "date": "19.10.2026", "sellerName": "ООО «Сафетин»", "sellerInn": "7707083893",
    "basis": "Счет на оплату № 77 от 15.10.2026", "paymentDoc": "№ 916 от 19.10.2026", "totalGross": 1220,
    "lines": [{"seq": 1, "name": "Разъём XLR ACG-3F-BK", "article": "ACG-3F-BK", "unit": "шт", "qty": 10,
               "price": 100, "net": 1000, "vatRate": "22%", "vat": 220, "gross": 1220}],
}


def fake_reader(answer: dict = ANSWER, model: str = "gpt-test"):
    calls: list[str] = []

    def read(content: bytes, file_name: str, tag: str, config) -> tuple[upd_parse.ParsedDoc, str]:
        calls.append(file_name)
        return upd_parse.normalize(answer, tag, file_name), model

    read.calls = calls
    return read


@pytest.fixture()
def env():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"), email="p@example.test",
        roles=frozenset({"buyer"}), permissions={"procurement": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.purchasing_uow_factory
    engine.dispose()


def factory(sessions):
    return lambda: SqlAlchemyPurchasingUnitOfWork(sessions)


def upload(client, *files: tuple[str, bytes]) -> dict:
    response = client.post(f"{BASE}/import-pdf", json={"files": [
        {"file_name": name, "content_base64": base64.b64encode(content).decode()} for name, content in files]})
    assert response.status_code == 200, response.text
    return response.json()


def test_upload_without_a_model_key_queues_the_documents(env, monkeypatch):
    client, sessions = env
    monkeypatch.setattr(flow, "llm_config", lambda warehouse: receipt_llm.LlmConfig())
    result = upload(client, ("Сафетин 1 220.pdf", PDF), ("Сафетин копия.pdf", PDF), ("письмо.txt", b"hello"))
    assert [item["status"] for item in result["files"]] == ["created", "unchanged", "error"]
    assert result["model_ready"] is False
    key = result["queued"][0]
    assert key == upd_pdf.pdf_document_id(PDF)
    with sessions() as session:
        row = session.get(UpdDocumentRow, key)
        assert (row.origin, row.status, row.folder, row.file_amount) == ("google", "В очереди", "Загрузка в ERP", 122_000)
    card = client.get(f"{BASE}/{key}").json()
    assert card["status"] == "waiting"
    assert client.get(f"{BASE}/{key}/file").content == PDF


def test_upload_with_a_key_parses_in_the_background(env, monkeypatch):
    client, sessions = env
    reader = fake_reader()
    monkeypatch.setattr(flow, "llm_config", lambda warehouse: READY)
    monkeypatch.setattr(flow, "read_pdf", reader)
    key = upload(client, ("Сафетин 1 220.pdf", PDF))["queued"][0]
    assert reader.calls == ["Сафетин 1 220.pdf"]
    with sessions() as session:
        row = session.get(UpdDocumentRow, key)
        assert (row.status, row.kind, row.number, row.doc_date, row.seller_inn, row.total_gross, row.model) == (
            "Разобран", "УПД", "916", date(2026, 10, 19), "7707083893", 122_000, "gpt-test")
        assert row.parser_updated.startswith("erp|gpt-test|")
        line = session.scalars(select(UpdLineRow).where(UpdLineRow.document_id == key)).one()
        assert (line.source, line.article, line.quantity, line.price, line.gross, line.vat_rate) == (
            "google", "ACG-3F-BK", Decimal("10"), Decimal("100"), 122_000, "22")
        events = [event.event_type for event in session.scalars(select(AuditEventRow))]
        assert "purchasing.upd_pdf_uploaded" in events and "purchasing.upd_pdf_parsed" in events
    assert client.get(f"{BASE}/{key}").json()["status"] == "parsed"


def test_a_pdf_imported_from_roller_buyer_is_not_uploaded_twice(env, monkeypatch):
    client, sessions = env
    monkeypatch.setattr(flow, "llm_config", lambda warehouse: receipt_llm.LlmConfig())
    with sessions() as session:
        session.add(UpdDocumentRow(id="1-2BEdkWE", origin="google", file_name="Дарт 500.pdf", status="Разобран",
                                   md5=__import__("hashlib").md5(PDF).hexdigest(), synced_at=NOW, created_at=NOW))
        session.commit()
    item = upload(client, ("скан.pdf", PDF))["files"][0]
    assert (item["status"], item["document_id"]) == ("unchanged", "1-2BEdkWE")


def test_one_claim_per_document(env):
    _, sessions = env
    make = factory(sessions)
    uow = make()
    with uow:
        key = upd_pdf.add_pdfs(uow, [("a.pdf", PDF)], Actor(name="П", department="З"), NOW)["queued"][0]
        uow.commit()
    first, second = make(), make()
    with first:
        assert upd_pdf.claim(first, key, NOW) is not None
        first.commit()
    with second:
        assert upd_pdf.claim(second, key, NOW) is None  # another process got it: no second paid call
    assert flow.parse_document(make, key, READY, reader=fake_reader()) == "skipped"


def test_reparse_keeps_the_buyers_word_and_marks_the_parse_newer(env, monkeypatch):
    client, sessions = env
    with sessions() as session:
        session.add(UpdDocumentRow(
            id="1-drive", origin="google", file_name="Сафетин.pdf", status="Требует проверки", kind="УПД", number="9I6",
            seller_inn="7707083893", line_count=1, parser_updated="2026-09-23T20:00:00", synced_at=NOW, created_at=NOW,
            file_id=repo._stored_file(session, "Сафетин.pdf", PDF, NOW).id,
            manual_header={"number": "916"}, manual_based_on="2026-09-23T20:00:00", verified_at=NOW,
        ))
        session.add(repo._line_row("1-drive", "google", seq=1, name="Разьём XLR", quantity=Decimal("1")))
        session.add(repo._line_row("1-drive", "manual", seq=1, name="Разъём XLR ACG-3F-BK", quantity=Decimal("10")))
        session.commit()
    monkeypatch.setattr(flow, "llm_config", lambda warehouse: READY)
    monkeypatch.setattr(flow, "read_pdf", fake_reader())
    response = client.post(f"{BASE}/1-drive/reparse")
    assert response.status_code == 200, response.text
    with sessions() as session:
        row = session.get(UpdDocumentRow, "1-drive")
        assert (row.status, row.number, row.manual_header, row.verified_at) == ("Разобран", "916", {"number": "916"}, NOW)
        assert row.parser_updated != row.manual_based_on  # the card shows the buyer a newer parse
        lines = {line.source: line.name for line in session.scalars(select(UpdLineRow).where(UpdLineRow.document_id == "1-drive"))}
        assert lines == {"google": "Разъём XLR ACG-3F-BK", "manual": "Разъём XLR ACG-3F-BK"}


def test_reparse_refusals(env, monkeypatch):
    client, sessions = env
    with sessions() as session:
        session.add(UpdDocumentRow(id="edo-1", origin="edo", file_name="a.xml", status="Разобран", synced_at=NOW,
                                   created_at=NOW))
        session.add(UpdDocumentRow(id="1-nofile", origin="google", file_name="b.pdf", status="Разобран", synced_at=NOW,
                                   created_at=NOW))
        session.commit()
    monkeypatch.setattr(flow, "llm_config", lambda warehouse: receipt_llm.LlmConfig())
    assert client.post(f"{BASE}/1-nofile/reparse").status_code == 409  # no key
    monkeypatch.setattr(flow, "llm_config", lambda warehouse: READY)
    assert client.post(f"{BASE}/edo-1/reparse").status_code == 409
    assert client.post(f"{BASE}/1-nofile/reparse").status_code == 409
    assert client.post(f"{BASE}/nothing/reparse").status_code == 404


def test_a_failed_parse_says_why_and_leaves_the_previous_reading(env):
    _, sessions = env
    make = factory(sessions)
    with sessions() as session:
        session.add(UpdDocumentRow(
            id="1-drive", origin="google", file_name="a.pdf", status="В очереди", number="916", line_count=1,
            file_id=repo._stored_file(session, "a.pdf", PDF, NOW).id, synced_at=NOW, created_at=NOW))
        session.add(repo._line_row("1-drive", "google", seq=1, name="Разъём"))
        session.commit()

    def broken(*_):
        raise receipt_llm.LlmError("HTTP 401: invalid key")

    assert flow.parse_document(make, "1-drive", READY, reader=broken) == "error"
    with sessions() as session:
        row = session.get(UpdDocumentRow, "1-drive")
        assert row.status == "Ошибка" and "HTTP 401" in row.issues and "оставлен прежний разбор" in row.issues
        assert session.scalars(select(UpdLineRow.name).where(UpdLineRow.document_id == "1-drive")).all() == ["Разъём"]
    assert repo.registry_status("Ошибка", None, None) == "needs_review"
    assert repo.registry_status("Разбирается", None, None) == "waiting"


def test_read_pdf_sends_every_page_and_the_text_layer_in_one_call(monkeypatch):
    sent: list[dict] = []

    def complete(config, model, messages, options, timeout):
        sent.append({"model": model, "messages": messages, "options": options})
        return {"choices": [{"message": {"content": "```json\n" + __import__("json").dumps(ANSWER) + "\n```"}}]}

    monkeypatch.setattr(flow, "page_pictures", lambda content: [b"page-1", b"page-2"])
    monkeypatch.setattr(flow, "text_layer", lambda content: "Счет-фактура № 916 от 19.10.2026")
    monkeypatch.setattr(receipt_llm, "complete", complete)
    doc, model = flow.read_pdf(PDF, "Сафетин 1 220.pdf", "", receipt_llm.LlmConfig(api_key="k", model="strong"))
    assert (model, doc.status, doc.number) == ("strong", "Разобран", "916")
    parts = sent[0]["messages"][0]["content"]
    assert [part["type"] for part in parts] == ["text", "image_url", "image_url"]
    assert "Счет-фактура № 916" in parts[0]["text"]
    assert sent[0]["options"]["response_format"]["type"] == "json_schema"


def test_read_pdf_falls_back_to_the_second_model_and_refuses_without_a_key(monkeypatch):
    models: list[str] = []

    def complete(config, model, messages, options, timeout):
        models.append(model)
        if model == "main":
            raise receipt_llm.LlmError("HTTP 400: bad image", status=400)
        return {"choices": [{"message": {"content": __import__("json").dumps(ANSWER)}}]}

    monkeypatch.setattr(flow, "page_pictures", lambda content: [b"page"])
    monkeypatch.setattr(flow, "text_layer", lambda content: "")
    monkeypatch.setattr(receipt_llm, "complete", complete)
    _, model = flow.read_pdf(PDF, "a.pdf", "", receipt_llm.LlmConfig(api_key="k", model="main", fallback_model="spare"))
    assert (models, model) == (["main", "spare"], "spare")
    with pytest.raises(flow.ParseFailed, match="ключ"):
        flow.read_pdf(PDF, "a.pdf", "", receipt_llm.LlmConfig())
    monkeypatch.setattr(flow, "page_pictures", lambda content: [])
    with pytest.raises(flow.ParseFailed, match="pdftoppm"):
        flow.read_pdf(PDF, "a.pdf", "", READY)


def test_benchmark_counts_errors_against_the_edo_twin(env, monkeypatch, capsys):
    _, sessions = env
    with sessions() as session:
        session.add(UpdDocumentRow(
            id="edo-x", origin="edo", file_name="x.xml", status="Разобран", number="916", doc_date=date(2026, 10, 19),
            seller_inn="7707083893", total_gross=122_000, payment_doc="№ 916 от 19.10.2026", line_count=1,
            synced_at=NOW, created_at=NOW))
        session.add(UpdDocumentRow(
            id="1-drive", origin="google", file_name="Сафетин.pdf", status="Разобран", number="916",
            doc_date=date(2026, 10, 19), seller_inn="7719117191", total_gross=100_000, payment_doc="№ 386 от 19.10.2026",
            line_count=1, twin_of="edo-x", file_id=repo._stored_file(session, "s.pdf", PDF, NOW).id, synced_at=NOW,
            created_at=NOW))
        session.add(repo._line_row("edo-x", "google", seq=1, name="Разъём XLR ACG-3F-BK", quantity=Decimal("10"),
                                   price=Decimal("100"), gross=122_000))
        session.add(repo._line_row("1-drive", "google", seq=1, name="Разьем XLR", quantity=Decimal("100"),
                                   price=Decimal("10"), gross=122_000))
        session.commit()
    monkeypatch.setattr(flow, "read_pdf", fake_reader())
    report = flow.benchmark(sessions, READY, None)
    assert report["pairs"] == 1 and report["failed"] == 0
    assert {name: report["stored"][name] for name in ("seller_inn", "total_gross", "payment_doc", "name", "quantity")} == {
        "seller_inn": 1, "total_gross": 1, "payment_doc": 1, "name": 1, "quantity": 1}
    assert sum(value for name, value in report["fresh"].items() if name != "строк сверено") == 0
    assert "Пар PDF ↔ ЭДО: 1" in capsys.readouterr().out

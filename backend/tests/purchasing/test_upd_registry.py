"""«Реестр УПД» over HTTP and relink_documents (cases from Roller_Buyer test/upd.test.ts, test/upd-sellers.test.ts and
the EDO part of test/doc-chain.test.ts): list and filters, the card, the buyer's version, «проверено», suppliers by the
corrected INN (never our own), the choice by hand, EDO upload with dry run and twins, sellers without a supplier."""

from __future__ import annotations

import base64
import io
import json
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.purchasing import suppliers as directory
from app.modules.purchasing import upd_registry as repo
from app.modules.purchasing.edo_xml import edo_document_id, parse_edo_xml
from app.modules.purchasing.items import alias_key, part_key
from app.modules.purchasing.infrastructure import (
    DocumentFileRow,
    SqlAlchemyPurchasingUnitOfWork,
    SupplierRow,
    UpdDocumentRow,
    UpdLineRow,
)
from app.shared import stage_options
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor

BASE = "/api/purchasing/upd"
NOW = datetime(2026, 10, 13, 12, 0)
KROKS = "3662192701"
OWN = "5040191181"
FIXTURES = Path(__file__).parent / "fixtures" / "edo"
UPD_XML = (FIXTURES / "upd-503.xml").read_bytes()
UPD_DOC = parse_edo_xml(UPD_XML.decode("cp1251"), "upd.xml")
EDO_ID = edo_document_id(UPD_DOC.id_file)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"documents.upd": True}), encoding="utf-8")
    monkeypatch.setenv(stage_options.ENV, str(path))
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"), email="p@example.test",
        roles=frozenset({"buyer"}), permissions={"procurement": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), sessions, path
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.purchasing_uow_factory
    engine.dispose()


def upd(sessions, document_id: str, lines: list[dict] | None = None, **over) -> None:
    values = {
        "origin": "google", "file_name": f"{document_id}.pdf", "status": "Разобран", "kind": "УПД", "number": document_id,
        "doc_date": date(2026, 3, 17), "seller_name": 'ООО "Крокс Плюс"', "seller_inn": KROKS, "buyer_inn": OWN,
        "total_gross": 1_473_000, "parser_updated": "2026-09-23T20:00:00", "synced_at": NOW, "created_at": NOW,
        "line_count": len(lines or []),
    }
    values.update(over)
    with sessions() as session:
        session.add(UpdDocumentRow(id=document_id, **values))
        for line in lines or []:
            session.add(repo._line_row(document_id, "google", **line))
        session.commit()


def supplier(sessions, name: str, inn: str | None) -> UUID:
    with sessions() as session:
        if inn in (OWN,):
            # The directory refuses nothing about our INN; an old import made such a card (Roller_Buyer supplier 54).
            row = SupplierRow(id=directory.uuid4(), name=name, name_key=directory.supplier_key(name), inn=inn,
                              categories=[], is_key=False, domains=[], version=1, created_at=NOW)
            session.add(row)
        else:
            row = directory.create(session, {"name": name, "inn": inn}, None, NOW)
        session.commit()
        return row.id


def relink(sessions) -> dict:
    with sessions() as session:
        result = repo.relink_documents(session, NOW)
        session.commit()
        return result


def cards(client, query: str = "") -> dict:
    response = client.get(f"{BASE}{query}")
    assert response.status_code == 200, response.text
    return response.json()


def by_id(listing: dict) -> dict[str, dict]:
    return {card["id"]: card for card in listing["items"]}


def b64(content: bytes) -> str:
    return base64.b64encode(content).decode()


def upload(client, files: list[tuple[str, bytes]], dry_run: bool = False):
    return client.post(f"{BASE}/import-edo", json={
        "files": [{"file_name": name, "content_base64": b64(content)} for name, content in files], "dry_run": dry_run,
    })


DART_LINES = [
    {"seq": 1, "name": "Разъём SMA-J", "article": "SMA-J", "unit": "шт", "quantity": Decimal(1), "price": Decimal("327.87"),
     "net": 32_787, "vat_rate": "22", "vat": 7_213, "gross": 40_000},
    {"seq": 2, "name": "Доставка", "unit": "усл", "quantity": Decimal(1), "price": Decimal("81.97"), "net": 8_197,
     "vat_rate": "22", "vat": 1_803, "gross": 10_000, "is_service": True},
]


# ---- gate ----------------------------------------------------------------------------------------------------------

def test_option_off_means_the_registry_does_not_exist(env):
    client, _, path = env
    path.write_text(json.dumps({"documents.upd": False}), encoding="utf-8")
    assert client.get(BASE).status_code == 404
    assert client.post(f"{BASE}/import-edo", json={"files": [{"file_name": "a.xml", "content_base64": "eA=="}]}).status_code == 404


def test_a_viewer_reads_but_does_not_write(env):
    client, sessions, _ = env
    upd(sessions, "file-dart-0001")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Смотрит", department="Склад"), email="v@example.test",
        roles=frozenset({"foreman"}), permissions={"procurement": "view"},
    )
    assert client.get(BASE).status_code == 200
    assert client.put(f"{BASE}/file-dart-0001/verified", json={"verified": True}).status_code == 403


# ---- list ----------------------------------------------------------------------------------------------------------

def test_list_filters_counts_and_order(env):
    client, sessions, _ = env
    upd(sessions, "file-dart-0001", DART_LINES, seller_name="ООО «Дарт»", seller_inn="7707083893", total_gross=50_000,
        doc_date=date(2026, 4, 21), number="2937")
    upd(sessions, "file-krox-0002", status="Требует проверки", doc_date=date(2026, 5, 2), number="77",
        issues="ИНН продавца не проходит проверку: 3862192701", seller_inn="3862192701")
    upd(sessions, "file-new-00003", status="В очереди", doc_date=None, number="", seller_name="", seller_inn="")
    upd(sessions, "file-dup-00004", status="Дубликат", dup_of="file-dart-0001", seller_name="ООО «Дарт»",
        seller_inn="7707083893")
    upd(sessions, "file-other-005", buyer_inn="7726465440", doc_date=date(2026, 6, 1), number="24")
    relink(sessions)

    listing = cards(client)
    assert [card["id"] for card in listing["items"]] == ["file-other-005", "file-krox-0002", "file-dart-0001", "file-new-00003"]
    assert listing["counts"] == {"waiting": 1, "parsed": 2, "needs_review": 1, "verified": 0, "duplicate": 1,
                                 "not_purchase": 0}
    assert listing["origins"] == {"edo": 0, "google": 4}
    assert listing["buyers"] == [{"key": "ours", "count": 3}, {"key": "7726465440", "count": 1}]
    assert listing["sum_gross"] == 50_000 + 1_473_000 * 3
    dart = by_id(listing)["file-dart-0001"]
    assert (dart["status"], dart["line_count"], dart["total_gross"], dart["has_file"]) == ("parsed", 2, 50_000, False)

    assert [c["id"] for c in cards(client, "?status=needs_review")["items"]] == ["file-krox-0002"]
    assert [c["id"] for c in cards(client, "?review=true")["items"]] == ["file-krox-0002"]
    assert [c["id"] for c in cards(client, "?duplicates=true")["items"]] == ["file-dup-00004"]
    assert {c["id"] for c in cards(client, "?q=%D0%94%D0%B0%D1%80%D1%82")["items"]} == {"file-dart-0001"}  # «Дарт»
    assert [c["id"] for c in cards(client, "?buyer=other")["items"]] == ["file-other-005"]
    assert [c["id"] for c in cards(client, "?buyer=7726465440")["items"]] == ["file-other-005"]
    assert [c["id"] for c in cards(client, "?date_from=2026-04-01&date_to=2026-05-31")["items"]] == [
        "file-krox-0002", "file-dart-0001",
    ]
    assert cards(client, "?origin=edo")["items"] == []
    assert client.get(f"{BASE}?origin=fax").status_code == 422
    assert client.get(f"{BASE}?buyer=всех").status_code == 422
    assert client.get(f"{BASE}?status=lost").status_code == 422


def test_a_misread_seller_inn_keeps_the_supplier_the_seller_and_the_twin(env):
    """Крокс from the live registry: three readings of one INN, № 1320 scanned twice."""
    client, sessions, _ = env
    kroks = supplier(sessions, 'ООО "КРОКС ПЛЮС"', KROKS)
    upd(sessions, "kroks-0001", number="1320")
    upd(sessions, "kroks-0002", number="650", doc_date=date(2026, 2, 12), seller_inn="3662152701", status="Требует проверки")
    upd(sessions, "kroks-0003", number="1320", seller_inn="3862192701", status="Требует проверки")
    result = relink(sessions)
    assert result["suppliers_changed"] == 3 and result["twins"] == 1

    everything = by_id(cards(client, "?duplicates=true")) | by_id(cards(client))
    assert {key: (card["supplier_id"], card["seller_org_inn"]) for key, card in everything.items()} == {
        key: (str(kroks), KROKS) for key in ("kroks-0001", "kroks-0002", "kroks-0003")
    }
    assert everything["kroks-0002"]["seller_note"] == "ИНН продавца прочитан с ошибкой: 3662152701"
    assert (everything["kroks-0003"]["status"], everything["kroks-0003"]["twin_of"]) == ("duplicate", "kroks-0001")
    assert everything["kroks-0001"]["copies"] == 1
    listing = cards(client)
    assert listing["suppliers"] == [{"id": str(kroks), "name": 'ООО "КРОКС ПЛЮС"', "count": 2}]
    assert listing["no_supplier"] == 0 and cards(client, "?no_supplier=true")["items"] == []
    assert [c["id"] for c in cards(client, f"?supplier_id={kroks}")["items"]] == ["kroks-0001", "kroks-0002"]
    detail = client.get(f"{BASE}/kroks-0001").json()
    assert [copy["id"] for copy in detail["twins"]["copies"]] == ["kroks-0003"]
    assert client.get(f"{BASE}/kroks-0003").json()["twins"]["main"]["id"] == "kroks-0001"
    assert relink(sessions)["suppliers_changed"] == 0  # nothing left to do


def test_a_supplier_carrying_our_own_inn_is_never_linked(env):
    client, sessions, _ = env
    bogus = supplier(sessions, 'ООО "ЭЗП "ФРАКСИС"', OWN)
    dart = supplier(sessions, "ООО «Дарт»", "7707083893")
    # A scan with the parties swapped: our company read as the seller.
    upd(sessions, "swap-00001", seller_name='ООО "ЗЭП "ФРАКСИС"', seller_inn=OWN, buyer_inn="7707083893")
    relink(sessions)
    card = by_id(cards(client))["swap-00001"]
    assert card["supplier_id"] == str(dart) and card["seller_org_inn"] == "7707083893"
    assert "из графы покупателя" in card["seller_note"]
    refused = client.put(f"{BASE}/swap-00001/supplier", json={"supplier_id": str(bogus)})
    assert refused.status_code == 422 and "нашей организации" in refused.json()["detail"]


def test_the_supplier_chosen_by_hand_stays_through_relinks_and_null_hands_it_back(env):
    client, sessions, _ = env
    dart = supplier(sessions, "ООО «Дарт»", "7707083893")
    other = supplier(sessions, "ООО Маркетплейс", None)
    upd(sessions, "file-dart-0001", seller_name="ООО «Дарт»", seller_inn="7707083893")
    relink(sessions)
    chosen = client.put(f"{BASE}/file-dart-0001/supplier", json={"supplier_id": str(other)})
    assert chosen.status_code == 200, chosen.text
    assert (chosen.json()["supplier_id"], chosen.json()["supplier_manual"]) == (str(other), True)
    relink(sessions)
    assert by_id(cards(client))["file-dart-0001"]["supplier_id"] == str(other)
    back = client.put(f"{BASE}/file-dart-0001/supplier", json={"supplier_id": None}).json()
    assert (back["supplier_id"], back["supplier_manual"]) == (str(dart), False)
    assert client.put(f"{BASE}/file-dart-0001/supplier", json={"supplier_id": "00000000-0000-0000-0000-000000000001"}).status_code == 404
    with sessions() as session:
        events = session.scalars(select(AuditEventRow.event_type).where(AuditEventRow.event_type == repo.SUPPLIER_EVENT)).all()
    assert len(events) == 2


# ---- the card and the buyer's word ---------------------------------------------------------------------------------

def test_the_buyers_version_wins_on_read_keeps_the_parsers_and_is_refused_when_stale(env):
    client, sessions, _ = env
    upd(sessions, "file-krox-0002", [{"seq": 1, "name": "Кабель РК 50-2-11", "unit": "м", "quantity": Decimal(100),
                                       "price": Decimal("120.74"), "net": 1_207_400, "vat_rate": "22", "vat": 265_600,
                                       "gross": 1_473_000}],
        status="Требует проверки", seller_inn="3862192701", number="77", issues="ИНН продавца не проходит проверку: 3862192701")
    detail = client.get(f"{BASE}/file-krox-0002").json()
    assert detail["issues"] == ["ИНН продавца не проходит проверку: 3862192701"]
    assert detail["lines"][0]["quantity"] == "100" and detail["lines"][0]["price"] == "120.74"
    header = {"number": "77", "date": "2026-05-02", "seller_name": "ООО Крокс", "seller_inn": "7707083893",
              "seller_kpp": "", "buyer_inn": "", "total_net": 1_207_400, "total_vat": 265_600, "total_gross": 1_473_000}
    lines = [{"seq": 1, "name": "Кабель РК 50-2-11", "article": "RK-50-2-11", "unit": "м", "quantity": "100",
              "price": "120.74", "net": 1_207_400, "vat_rate": "22", "vat": 265_600, "gross": 1_473_000}]
    body = {"header": header, "lines": lines, "parser_updated": detail["parser_updated"], "manual_saved_at": None}
    saved = client.put(f"{BASE}/file-krox-0002/manual", json=body)
    assert saved.status_code == 200, saved.text
    saved = saved.json()
    assert (saved["manual"], saved["issues"], saved["seller_inn"]) == (True, [], "7707083893")
    assert saved["parser_header"]["seller_inn"] == "3862192701" and saved["header"]["date"] == "2026-05-02"
    assert saved["lines"][0]["article"] == "RK-50-2-11" and saved["parser_lines"][0]["article"] == ""
    with sessions() as session:
        own = session.scalar(select(UpdLineRow).where(UpdLineRow.source == "manual"))
        assert (own.article_key, own.name_key) == (part_key("RK-50-2-11"), alias_key("Кабель РК 50-2-11"))

    # The card was opened before this save: refused, nothing lost.
    assert client.put(f"{BASE}/file-krox-0002/manual", json=body).status_code == 409
    stale = {**body, "manual_saved_at": saved["manual_saved_at"], "parser_updated": "older"}
    assert client.put(f"{BASE}/file-krox-0002/manual", json=stale).status_code == 409
    again = {**body, "manual_saved_at": saved["manual_saved_at"],
             "lines": [{**lines[0], "gross": 1_500_000}]}
    checked = client.put(f"{BASE}/file-krox-0002/manual", json=again).json()
    assert checked["lines"][0]["issues"] == ["без НДС + НДС ≠ с НДС"]
    assert "строк с замечаниями: 1" in checked["issues"]

    reverted = client.delete(f"{BASE}/file-krox-0002/manual").json()
    assert (reverted["manual"], reverted["seller_inn"], len(reverted["lines"])) == (False, "3862192701", 1)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(UpdLineRow).where(UpdLineRow.source == "manual")) == 0
        kinds = session.scalars(select(AuditEventRow.event_type)).all()
    assert kinds.count("purchasing.upd_manual_saved") == 2 and "purchasing.upd_manual_dropped" in kinds


def test_manual_header_and_lines_are_validated_in_words(env):
    client, sessions, _ = env
    upd(sessions, "file-dart-0001")
    base = {"number": "1", "date": "2026-05-02", "seller_name": "Дарт", "seller_inn": "7707083893"}
    line = {"seq": 1, "name": "Разъём"}

    def put(header=None, lines=None):
        return client.put(f"{BASE}/file-dart-0001/manual", json={
            "header": header or base, "lines": lines if lines is not None else [line],
            "parser_updated": "2026-09-23T20:00:00",
        })

    assert put({**base, "seller_inn": "77070838"}).json()["detail"] == "ИНН продавца — 10 или 12 цифр"
    assert put({**base, "seller_kpp": "12"}).json()["detail"] == "КПП — 9 цифр"
    assert put({**base, "date": "02.05.2026"}).json()["detail"] == "Дата документа — ГГГГ-ММ-ДД"
    assert put(lines=[line, line]).json()["detail"] == "№ п/п 1 повторяется"
    assert put(lines=[{**line, "quantity": "-1"}]).json()["detail"] == "«Строка 1: кол-во»: число не меньше нуля"
    assert put(lines=[{**line, "price": "abc"}]).status_code == 422
    assert put().status_code == 200


def test_verified_needs_a_parsed_document_and_a_new_parse_is_flagged(env):
    client, sessions, _ = env
    upd(sessions, "file-new-00003", status="В очереди")
    upd(sessions, "file-dart-0001")
    early = client.put(f"{BASE}/file-new-00003/verified", json={"verified": True})
    assert early.status_code == 422 and "не разобран" in early.json()["detail"]
    done = client.put(f"{BASE}/file-dart-0001/verified", json={"verified": True}).json()
    assert (done["status"], done["verified"], done["changed_in_parser"]) == ("verified", True, False)
    with sessions() as session:
        session.get(UpdDocumentRow, "file-dart-0001").parser_updated = "2026-10-01T09:00:00"
        session.commit()
    assert client.get(f"{BASE}/file-dart-0001").json()["changed_in_parser"] is True
    assert [c["id"] for c in cards(client, "?review=true")["items"]] == ["file-dart-0001"]
    undone = client.put(f"{BASE}/file-dart-0001/verified", json={"verified": False}).json()
    assert (undone["status"], undone["verified"]) == ("parsed", False)


def test_project_reference_is_set_and_cleared(env):
    client, sessions, _ = env
    upd(sessions, "file-dart-0001")
    assert client.put(f"{BASE}/file-dart-0001/project", json={"project_reference": "  591К.3 "}).json()["project_reference"] == "591К.3"
    assert client.put(f"{BASE}/file-dart-0001/project", json={"project_reference": ""}).json()["project_reference"] is None
    assert client.put(f"{BASE}/nope-000001/project", json={"project_reference": "ВП-001"}).status_code == 404


def test_the_file_opens_a_pdf_in_the_tab(env):
    client, sessions, _ = env
    with sessions() as session:
        stored = DocumentFileRow(id=directory.uuid4(), sha256="a" * 64, file_name="Дарт 500.pdf", size=9,
                                 content=b"%PDF-1.4\n", created_at=NOW)
        session.add(stored)
        session.commit()
        file_id = stored.id
    upd(sessions, "file-dart-0001", file_id=file_id)
    upd(sessions, "file-none-0002")
    opened = client.get(f"{BASE}/file-dart-0001/file")
    assert opened.status_code == 200 and opened.content.startswith(b"%PDF")
    assert opened.headers["content-disposition"].startswith("inline")
    assert client.get(f"{BASE}/file-dart-0001").json()["file"] == {"name": "Дарт 500.pdf", "size": 9, "kind": "pdf"}
    assert client.get(f"{BASE}/file-none-0002/file").status_code == 404


# ---- EDO upload ----------------------------------------------------------------------------------------------------

def test_edo_xml_dry_run_writes_nothing_apply_imports_once_exact_and_keeps_the_original(env):
    client, sessions, _ = env
    dry = upload(client, [("upd.xml", UPD_XML)], dry_run=True)
    assert dry.status_code == 200, dry.text
    assert [(f["status"], f["document_id"], f["number"]) for f in dry.json()["files"]] == [("created", EDO_ID, "РЭ-0915")]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(UpdDocumentRow)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentFileRow)) == 0

    applied = upload(client, [("upd.xml", UPD_XML)])
    assert [f["status"] for f in applied.json()["files"]] == ["created"]
    card = by_id(cards(client))[EDO_ID]
    assert (card["origin"], card["status"], card["kind"], card["has_file"]) == ("edo", "parsed", "УПД", True)
    assert card["total_gross"] == UPD_DOC.header.total_gross
    detail = client.get(f"{BASE}/{EDO_ID}").json()
    assert len(detail["lines"]) == 3 and detail["basis"] == UPD_DOC.header.basis
    assert detail["model"] == "ЭДО XML 5.03" and detail["parser_updated"].startswith("2026-09-15T10:15:00|")
    assert "TESTMARKCODE" not in json.dumps(detail, ensure_ascii=False)
    with sessions() as session:
        keys = session.execute(
            select(UpdLineRow.article_key, UpdLineRow.name_key).where(UpdLineRow.document_id == EDO_ID).order_by(UpdLineRow.seq)
        ).all()
    assert keys[0] == (part_key("RT-2@TEST"), alias_key("Разъём тестовый РТ-2, 2 контакта"))
    assert keys[2][0] is None  # the delivery line has no code

    original = client.get(f"{BASE}/{EDO_ID}/file")
    assert original.status_code == 200 and original.content == UPD_XML
    assert original.headers["content-disposition"].startswith("attachment")
    assert [f["status"] for f in upload(client, [("upd (1).xml", UPD_XML)]).json()["files"]] == ["unchanged"]


def test_edo_zip_takes_only_documents_and_junk_is_refused_politely(env):
    client, _, _ = env
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("docs/ON_NSCHFDOPPR_test.xml", UPD_XML)
        archive.writestr("docs/ON_NSCHFDOPPR_test.xml.sig", b"\x01\x02")
        archive.writestr("card.xml", b"<card/>")
        archive.writestr("Счет.pdf", b"%PDF")
    result = upload(client, [
        ("Diadoc.Documents.zip", buffer.getvalue()),
        ("doc.xml", b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>'),
        ("photo.png", b"\x89PNG"),
    ]).json()["files"]
    assert [f["status"] for f in result] == ["created", "skipped", "error"]
    assert result[1]["message"] == "XML с DOCTYPE не принимается"
    assert client.post(f"{BASE}/import-edo", json={"files": [{"file_name": "x.xml", "content_base64": "%%%"}]}).status_code == 422


def test_twins_the_google_pdf_gives_way_to_the_edo_xml_whichever_came_first(env):
    client, sessions, _ = env
    h = UPD_DOC.header
    upd(sessions, "g-twin-00001", number=h.number, doc_date=date.fromisoformat(h.date), seller_inn=h.seller_inn,
        seller_name=h.seller_name, total_gross=h.total_gross)
    files = upload(client, [("upd.xml", UPD_XML)]).json()["files"]
    # The exact XML is in force and the PDF read by Google becomes its copy.
    assert [f["status"] for f in files] == ["created"]
    pdf = by_id(cards(client, "?duplicates=true"))["g-twin-00001"]
    assert (pdf["status"], pdf["twin_of"]) == ("duplicate", EDO_ID)

    # A later PDF of the same document becomes the twin too.
    upd(sessions, "g-twin-00002", number=h.number, doc_date=date.fromisoformat(h.date), seller_inn=h.seller_inn,
        total_gross=1)
    relink(sessions)
    assert by_id(cards(client, "?duplicates=true"))["g-twin-00002"]["twin_of"] == EDO_ID

    # The seller sends the same XML again under another ИдФайл: the later formed one stays in force.
    formed = "ВремИнфПр".encode("cp1251")
    resent = UPD_XML.replace(b"_0_0_0_0_0_00", b"_0_0_0_0_0_01").replace(formed + b'="10.15.00"', formed + b'="11.15.00"')
    files = upload(client, [("upd-again.xml", resent)]).json()["files"]
    assert files[0]["status"] == "created"
    older = by_id(cards(client, "?duplicates=true"))[EDO_ID]
    assert older["twin_of"] == files[0]["document_id"]
    # An earlier formed resend arriving late is the copy.
    late = UPD_XML.replace(b"_0_0_0_0_0_00", b"_0_0_0_0_0_02").replace(formed + b'="10.15.00"', formed + b'="09.15.00"')
    dry = upload(client, [("upd-third.xml", late)], dry_run=True).json()
    assert dry["files"][0]["status"] == "twin" and "повторная отправка" in dry["files"][0]["message"]


def test_a_changed_xml_under_the_same_id_file_updates_the_document_and_keeps_the_buyers_word(env):
    client, sessions, _ = env
    upload(client, [("upd.xml", UPD_XML)])
    detail = client.get(f"{BASE}/{EDO_ID}").json()
    client.put(f"{BASE}/{EDO_ID}/verified", json={"verified": True})
    header = {**detail["header"], "number": "РЭ-0915/1"}
    client.put(f"{BASE}/{EDO_ID}/manual", json={"header": header, "lines": [], "parser_updated": detail["parser_updated"]})
    changed = UPD_XML.replace(b'="12.5000"', b'="12.50"')  # the seller re-formed the file: same document, other bytes
    assert changed != UPD_XML
    files = upload(client, [("upd.xml", changed)]).json()["files"]
    assert files[0]["status"] == "updated"
    after = client.get(f"{BASE}/{EDO_ID}").json()
    assert (after["manual"], after["verified"], after["number"]) == (True, True, "РЭ-0915/1")
    assert after["changed_in_parser"] is True  # the buyer's word was given on the older file
    assert len(after["parser_lines"]) == 3


# ---- sellers without a supplier ------------------------------------------------------------------------------------

def test_sellers_without_a_supplier_are_listed_and_imported_then_linked(env):
    client, sessions, _ = env
    supplier(sessions, 'ООО "КРОКС ПЛЮС"', KROKS)
    upd(sessions, "kroks-0001")
    upd(sessions, "dart-0001", seller_name="ООО «Дарт»", seller_inn="7707083893", total_gross=50_000, doc_date=date(2026, 4, 21))
    upd(sessions, "dart-0002", seller_name="ООО «Дарт»", seller_inn="7707083893", total_gross=60_000, doc_date=date(2026, 5, 1),
        status="Требует проверки")
    upd(sessions, "bad-0001", seller_name="ООО Нечитаемое", seller_inn="971038484")
    upd(sessions, "m15-0001", kind="М-15", seller_name="АО Заказчик", seller_inn="7701000019")
    upd(sessions, "queue-0001", status="В очереди", seller_name="ООО Мицар", seller_inn="7707083893")
    relink(sessions)

    listed = client.get(f"{BASE}/sellers").json()
    assert listed["skipped_invalid_inn"] == 1
    assert [(s["inn"], s["name"], s["documents"], s["total_gross"], s["needs_review"], s["last_date"]) for s in listed["sellers"]] == [
        ("7707083893", "ООО «Дарт»", 2, 110_000, 1, "2026-05-01"),
    ]
    dry = client.post(f"{BASE}/sellers/import", json={"dry_run": True}).json()
    assert [c["name"] for c in dry["created"]] == ["ООО «Дарт»"] and dry["dry_run"] is True
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SupplierRow)) == 1
    applied = client.post(f"{BASE}/sellers/import", json={"dry_run": False, "inns": ["7707083893"]}).json()
    assert applied["suppliers_changed"] == 3  # both Дарт documents and the queued one with the same INN
    card = by_id(cards(client))["dart-0001"]
    assert card["supplier_name"] == "ООО «Дарт»"
    assert client.get(f"{BASE}/sellers").json()["sellers"] == []

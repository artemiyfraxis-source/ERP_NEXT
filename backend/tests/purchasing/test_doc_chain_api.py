"""«Цепочка документов» over HTTP (Roller_Buyer apps/api/src/test/doc-chain.test.ts) and its ERP continuation into
receiving; the buyer's decisions; option gate; the list of 1000 × 700 documents in time."""

import time
from urllib.parse import quote
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.purchasing import doc_chain
from app.modules.purchasing.infrastructure import (
    InvoiceFileRow,
    InvoiceRow,
    SqlAlchemyPurchasingUnitOfWork,
    SupplierRow,
    UpdDocumentRow,
    UpdInvoiceLinkRow,
    UpdLineRow,
)
from app.modules.receiving.infrastructure import SqlAlchemyReceivingUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor
from tests.purchasing.test_items_stock import add_receiving

NOW = datetime(2026, 10, 9, 12, 0)
ROMASHKA = "7701000019"
OUR_INN = "5040191181"


def _as(permissions: dict) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"), email="p@example.test", roles=frozenset({"buyer"}),
        permissions=permissions,
    )


@pytest.fixture()
def env():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    app.state.receiving_uow_factory = lambda: SqlAlchemyReceivingUnitOfWork(sessions)
    _as({"procurement": "edit"})
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.purchasing_uow_factory
    del app.state.receiving_uow_factory
    engine.dispose()


_card = iter(range(1, 1_000_000))


def invoice(sessions, number=None, pays=(), total=100000, day="2026-09-01", status=None, files=(), **over) -> UUID:
    """A Kaiten card: its payment orders are files named after them (pays = (number, «dd.mm.yyyy», roubles))."""
    card = next(_card)
    iid = uuid4()
    values = dict(
        id=iid, source="kaiten", external_id=str(card), title=f"Счет № {number or card}",
        status=status or ("paid" if pays else "to_pay"), card_created=NOW, card_updated=NOW, number=number or str(card),
        doc_date=date.fromisoformat(day) if day else None, seller_name='ООО "Ромашка Электро"', seller_inn=ROMASHKA,
        total=total, synced_at=NOW, lines=[],
    )
    values.update(over)
    with sessions() as s:
        s.add(InvoiceRow(**values))
        s.flush()
        for k, (n, d, rub) in enumerate(pays):
            s.add(InvoiceFileRow(
                id=uuid4(), invoice_id=iid, external_id=f"{card}0{k}", kind="payment", size=1, seq=k,
                name=f"Платежное_поручение_№{n}_от_{d}_на_сумму_{rub}.pdf",
            ))
        for k, f in enumerate(files):
            s.add(InvoiceFileRow(invoice_id=iid, external_id=f"{card}9{k}", seq=10 + k, size=1, **{"id": uuid4(), **f}))
        s.commit()
    return iid


def upd(sessions, upd_id: str, **over) -> str:
    values = dict(
        id=upd_id, origin="google", file_name=f"{upd_id}.pdf", status="Разобран", kind="УПД", number="1",
        doc_date=date(2026, 9, 5), seller_name='ООО "Ромашка Электро"', seller_inn=ROMASHKA, buyer_inn=OUR_INN,
        total_gross=100000, parser_updated="2026-09-05T10:00:00", synced_at=NOW, created_at=NOW,
    )
    values.update(over)
    with sessions() as s:
        s.add(UpdDocumentRow(**values))
        s.commit()
    return upd_id


def chains(client, view="all", **params) -> dict:
    res = client.get("/api/purchasing/doc-chain", params={"view": view, **params})
    assert res.status_code == 200, res.text
    return res.json()


def by_key(listing: dict, key: str) -> dict | None:
    return next((c for c in listing["chains"] if c["key"] == key), None)


def codes(chain: dict | None) -> list[str]:
    return [f"{x['level']}:{x['code']}" for x in (chain or {}).get("checks", [])]


def test_states_closed_partial_over_missing_waiting_unpaid(env):
    client, sessions = env
    closed = invoice(sessions, "А-1", pays=[("801", "01.09.2026", 1000)])
    upd(sessions, "g-closed-0001", basis="Счет № А-1 от 01.09.2026")
    partial = invoice(sessions, "А-2", pays=[("802", "01.09.2026", 1000)])
    upd(sessions, "g-partial-001", basis="Счет № А-2 от 01.09.2026", total_gross=40000)
    over = invoice(sessions, "А-3", pays=[("803", "01.09.2026", 1000)])
    upd(sessions, "g-over-00001", basis="Счет № А-3 от 01.09.2026", total_gross=150000)
    missing = invoice(sessions, "А-4", pays=[("804", "01.09.2026", 1000)])
    waiting = invoice(sessions, "А-5", day="2026-10-05", pays=[("805", "05.10.2026", 1000)])
    unpaid = invoice(sessions, "А-6")

    listing = chains(client)
    state = lambda iid: by_key(listing, f"invoice:{iid}")["state"]  # noqa: E731
    assert [state(i) for i in (closed, partial, over, missing, waiting, unpaid)] == [
        "closed", "partial", "over", "missing", "waiting", "unpaid",
    ]
    assert codes(by_key(listing, f"invoice:{missing}")) == ["error:no_closing"]
    assert by_key(listing, f"invoice:{partial}")["closed_gross"] == 40000

    problems = chains(client, "problems")
    assert sorted(c["invoice"]["number"] for c in problems["chains"]) == ["А-2", "А-3", "А-4"]
    assert problems["summary"]["problems"] == 3
    assert problems["summary"]["by_state"]["closed"] == 1


def test_a_upd_splits_between_the_invoices_it_closes_twin_torg12_and_a_correction_counted_once(env):
    client, sessions = env
    a = invoice(sessions, "Б-1", total=30000, pays=[("811", "01.09.2026", 300)])
    b = invoice(sessions, "Б-2", total=70000, pays=[("812", "01.09.2026", 700)])
    upd(sessions, "g-both-00001", basis="Счет № Б-1 от 01.09.2026; Счет № Б-2 от 01.09.2026", total_gross=100000)
    c = invoice(sessions, "Б-3", total=50000, pays=[("813", "01.09.2026", 500)])
    upd(sessions, "g-sf-0000001", kind="Счёт-фактура", number="77", basis="Счет № Б-3 от 01.09.2026", total_gross=50000)
    upd(sessions, "g-torg-00001", kind="Товарная накладная", number="77", basis="Счет № Б-3 от 01.09.2026", total_gross=50000)
    upd(sessions, "g-corr-00001", kind="Корректировочный СФ", number="78", basis="Счет № Б-3 от 01.09.2026", total_gross=-1000)

    listing = chains(client)
    assert by_key(listing, f"invoice:{a}")["state"] == "closed"
    assert by_key(listing, f"invoice:{b}")["state"] == "closed"
    third = by_key(listing, f"invoice:{c}")
    assert len(third["upds"]) == 3
    assert third["closed_gross"] == 50000
    assert third["state"] == "closed"
    assert "info:correction" in codes(third)


def test_checks_a_foreign_payment_order_another_buyer_ocr_slips_of_our_inn(env):
    client, sessions = env
    inv = invoice(sessions, "В-1", pays=[("820", "01.09.2026", 1000)])
    upd(sessions, "g-pay-000001", basis="Счет № В-1 от 01.09.2026", payment_doc="№ 999 от 01.09.2026")
    upd(sessions, "g-buyer-0001", number="2", basis="Счет № В-1 от 01.09.2026", total_gross=0, buyer_inn="7726465440")
    upd(sessions, "g-ocr-000001", number="3", basis="Счет № В-1 от 01.09.2026", total_gross=0, buyer_inn="5040191161")
    chain = by_key(chains(client), f"invoice:{inv}")
    assert sorted(codes(chain)) == ["info:buyer_not_us", "warning:payment_not_in_invoice"]
    # The seller dated our payment a day later: the same payment.
    with sessions() as s:
        for row in s.scalars(select(UpdDocumentRow)):
            s.delete(row)
        s.commit()
    upd(sessions, "g-pay-000002", basis="Счет № В-1 от 01.09.2026", payment_doc="№ 00000820 от 02.09.2026")
    assert codes(by_key(chains(client), f"invoice:{inv}")) == []


def test_documents_without_an_invoice_a_problem_only_for_a_seller_we_buy_from(env):
    client, sessions = env
    invoice(sessions, "Г-1", day="2026-08-01", total=5000)
    upd(sessions, "g-orphan-001", number="10", basis="Счет № Г-9 от 01.09.2026")
    upd(sessions, "g-rent-00001", number="11", seller_inn="7702000017", seller_name='ООО "Арендодатель"')
    upd(sessions, "g-old-000001", number="12", doc_date=date(2026, 7, 1))
    listing = chains(client)
    assert codes(by_key(listing, "upd:g-orphan-001")) == ["warning:no_invoice"]
    assert "Г-9" in by_key(listing, "upd:g-orphan-001")["checks"][0]["text"]
    assert codes(by_key(listing, "upd:g-rent-00001")) == ["info:no_invoice"]
    assert codes(by_key(listing, "upd:g-old-000001")) == ["info:no_invoice"]
    assert [c["key"] for c in chains(client, "problems")["chains"]] == ["upd:g-orphan-001"]


def test_the_buyer_links_rejects_and_undoes_the_upd_card_shows_linked_candidates_and_rejected(env):
    client, sessions = env
    named = invoice(sessions, "Д-1", pays=[("830", "01.09.2026", 1000)])
    other = invoice(sessions, "Д-2", total=55500, day="2026-09-02")
    upd(sessions, "g-card-00001", basis="Счет № Д-1 от 01.09.2026", payment_doc="830 от 01.09.2026")
    url = "/api/purchasing/upd/g-card-00001/invoices"
    body = client.get(url).json()
    assert [(i["id"], i["method"]) for i in body["linked"]] == [(str(named), "basis_date")]
    assert [i["id"] for i in body["candidates"]] == [str(other)]
    assert [r["number"] for r in body["basis_refs"]] == ["Д-1"]
    assert body["payment_refs"] == [{"number": "830", "date": "2026-09-01"}]

    res = client.put(f"{url}/{named}", json={"decision": "rejected"})
    assert res.status_code == 200, res.text
    assert res.json()["linked"] == []
    assert [i["id"] for i in res.json()["rejected"]] == [str(named)]
    res = client.put(f"{url}/{other}", json={"decision": "confirmed"})
    assert [(i["id"], i["method"], i["decision"]) for i in res.json()["linked"]] == [(str(other), "manual", "confirmed")]
    res = client.put(f"{url}/{named}", json={"decision": None})
    assert sorted(i["id"] for i in res.json()["linked"]) == sorted([str(named), str(other)])

    assert client.put(f"{url}/{uuid4()}", json={"decision": "confirmed"}).status_code == 404
    assert client.put("/api/purchasing/upd/нет-такого/invoices/" + str(named), json={"decision": "confirmed"}).status_code in (404, 422)
    assert client.put(f"{url}/{named}", json={"decision": "maybe"}).status_code == 422
    assert client.get("/api/purchasing/upd/g-none-00001/invoices").status_code == 404
    card = client.get(f"/api/purchasing/invoices/{other}").json()
    assert [u["method"] for u in card["chain"]["upds"]] == ["manual"]

    with sessions() as s:
        stored = {(row.invoice_id, row.decision) for row in s.scalars(select(UpdInvoiceLinkRow))}
        events = [(e.event_type, e.aggregate_id, e.details["decision"], e.details["previous"]) for e in s.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "purchasing.upd_invoice_decided").order_by(AuditEventRow.occurred_at)
        )]
    assert stored == {(other, "confirmed")}
    assert sorted(events, key=lambda e: (str(e[1]), e[2])) == sorted([
        ("purchasing.upd_invoice_decided", named, "rejected", ""),
        ("purchasing.upd_invoice_decided", other, "confirmed", ""),
        ("purchasing.upd_invoice_decided", named, "", "rejected"),
    ], key=lambda e: (str(e[1]), e[2]))


def test_edo_and_paper_apart_closed_by_in_the_chain_list(env):
    client, sessions = env
    paper = invoice(sessions, "Е-1", pays=[("840", "01.09.2026", 1000)])
    upd(sessions, "g-paper-0001", basis="Счет № Е-1 от 01.09.2026")
    none = invoice(sessions, "Е-2", pays=[("841", "01.09.2026", 1000)])
    edo = invoice(sessions, "Е-3", pays=[("842", "01.09.2026", 1000)])
    upd(sessions, "edo-0000000001", origin="edo", number="РЭ-1", basis="Счет № Е-3 от 01.09.2026")

    by_paper = chains(client, closing="paper")
    assert [c["invoice"]["id"] for c in by_paper["chains"]] == [str(paper)]
    by_none = chains(client, closing="none")
    assert any(c["invoice"] and c["invoice"]["id"] == str(none) for c in by_none["chains"])
    assert all(not c["upds"] for c in by_none["chains"])
    by_edo = chains(client, closing="edo")
    assert [c["invoice"]["id"] for c in by_edo["chains"]] == [str(edo)]
    # The switch's own counts do not depend on the switch.
    assert by_paper["summary"]["by_closing"] == by_edo["summary"]["by_closing"]
    assert by_paper["summary"]["by_closing"]["paper"] == 1
    assert client.get("/api/purchasing/doc-chain", params={"closing": "fax"}).status_code == 422


def test_documents_by_the_buyer_ours_another_organisation_unknown(env):
    client, sessions = env
    upd(sessions, "g-ours-00001", number="21", buyer_inn=OUR_INN)
    upd(sessions, "g-slip-00001", number="22", buyer_inn="5040191161")
    upd(sessions, "g-other-0001", number="24", buyer_inn="7726465440")
    upd(sessions, "g-other-0002", number="25", buyer_inn="7726165440")
    upd(sessions, "g-none-00001", number="26", buyer_inn="")
    keys = lambda buyer: sorted(c["upds"][0]["id"] for c in chains(client, buyer=buyer)["chains"])  # noqa: E731
    assert keys("ours") == ["g-ours-00001", "g-slip-00001"]
    assert keys("7726465440") == ["g-other-0001", "g-other-0002"]
    assert keys("other") == ["g-other-0001", "g-other-0002"]
    assert keys("unknown") == ["g-none-00001"]
    other = chains(client, buyer="other")
    assert all(any(x["code"] == "buyer_not_us" and "7726465440" in x["text"] for x in c["checks"]) for c in other["chains"])
    assert client.get("/api/purchasing/doc-chain", params={"buyer": "всех"}).status_code == 422


def test_a_scan_without_the_seller_inn_its_invoices_are_found_by_the_seller_name(env):
    client, sessions = env
    inv = invoice(sessions, "Ж-1", seller_name='ООО "Галерея Коннект"', seller_inn="7707083893", day="2026-08-05")
    upd(
        sessions, "g-noinn-0001", kind="Акт", number="26002614", doc_date=date(2026, 8, 11),
        seller_name='Общество с ограниченной ответственностью "Галерея Коннект"', seller_inn="", total_gross=81300,
    )
    body = client.get("/api/purchasing/upd/g-noinn-0001/invoices").json()
    assert [i["id"] for i in body["candidates"]] == [str(inv)]
    # A seller we buy from: the missing invoice is a warning, not «not a Kaiten purchase».
    assert [f"{c['level']}:{c['code']}" for c in body["checks"]] == ["warning:no_invoice"]


def test_the_amount_suggestion_comes_first_among_the_candidates_and_shows_on_the_chain(env):
    client, sessions = env
    near = invoice(sessions, "Н-1", total=70000, day="2026-09-04")
    same_sum = invoice(sessions, "Н-2", total=251050, day="2026-08-01")
    upd(sessions, "g-sum-000001", number="41", total_gross=251000)
    body = client.get("/api/purchasing/upd/g-sum-000001/invoices").json()
    assert [(i["id"], i["suggested"]) for i in body["candidates"]] == [(str(same_sum), True), (str(near), False)]
    assert "info:maybe_invoice" in codes(body)
    chain = by_key(chains(client), f"invoice:{same_sum}")
    assert [u["id"] for u in chain["suggestions"]] == ["g-sum-000001"]
    assert chain["upds"] == []


def test_the_chain_filters_by_supplier_period_and_words(env):
    client, sessions = env
    supplier = uuid4()
    with sessions() as s:
        s.add(SupplierRow(
            id=supplier, name="Ромашка Электро", name_key="ромашкаэлектро", inn=ROMASHKA, categories=[], is_key=False,
            domains=[], version=1, created_at=NOW,
        ))
        s.commit()
    own = invoice(sessions, "З-1", supplier_id=supplier, pays=[("850", "01.09.2026", 1000)])
    invoice(sessions, "З-2", seller_inn="7707083893", day="2026-07-01")
    upd(sessions, "g-own-000001", number="31", basis="Счет № З-9 от 01.09.2026")
    assert {c["key"] for c in chains(client, supplier=str(supplier))["chains"]} == {f"invoice:{own}", "upd:g-own-000001"}
    assert [c["key"] for c in chains(client, **{"from": "2026-08-01", "to": "2026-08-31"})["chains"]] == []
    assert [c["invoice"]["number"] for c in chains(client, q="з-2")["chains"]] == ["З-2"]
    assert [c["key"] for c in chains(client, q="850")["chains"]] == [f"invoice:{own}"]


# ---- ERP: the chain continues into receiving -----------------------------------------------------------------------

def test_receiving_lines_by_the_upd_number_and_date_supplier_breaks_ties(env):
    client, sessions = env
    inv = invoice(sessions, "К-1", pays=[("860", "01.09.2026", 1000)])
    upd(sessions, "g-recv-00001", number="УТ-1714", doc_date=date(2026, 9, 5), basis="Счет № К-1 от 01.09.2026")
    line = uuid4()
    add_receiving(sessions, line, document_number="ут 1714", document_date=date(2026, 9, 5), supplier="Ромашка Электро", grn="FRX.26.0000101", status="Принято")
    add_receiving(sessions, uuid4(), document_number="YT1714", document_date=date(2026, 9, 7), supplier="ООО Ромашка", grn="FRX.26.0000102", status="Карантин")
    # Near date, another supplier: not this document.
    add_receiving(sessions, uuid4(), document_number="УТ-1714", document_date=date(2026, 9, 6), supplier="ООО Лютик", grn="FRX.26.0000103")
    # Archived revisions are not receipts; another number is not this document.
    add_receiving(sessions, uuid4(), document_number="УТ-1714", document_date=date(2026, 9, 5), status="Архив ВК", grn="FRX.26.0000104")
    add_receiving(sessions, uuid4(), document_number="УТ-1715", document_date=date(2026, 9, 5), grn="FRX.26.0000105")

    chain = by_key(chains(client), f"invoice:{inv}")
    receiving = chain["upds"][0]["receiving"]
    assert receiving["lines"] == 2
    assert receiving["grns"] == ["FRX.26.0000101", "FRX.26.0000102"]
    assert sorted((s["status"], s["count"]) for s in receiving["statuses"]) == [("Карантин", 1), ("Принято", 1)]

    card = client.get(f"/api/purchasing/invoices/{inv}").json()
    items = card["chain"]["upds"][0]["receiving"]["items"]
    assert [i["grn"] for i in items] == ["FRX.26.0000101", "FRX.26.0000102"]
    assert items[0]["procurement_line_id"] == str(line)
    assert card["receiving_available"] is True
    upd_card = client.get("/api/purchasing/upd/g-recv-00001/invoices").json()
    assert upd_card["receiving"]["lines"] == 2


def test_a_short_number_never_goes_to_a_document_of_another_seller_the_exact_one_wins(env):
    client, sessions = env
    upd(sessions, "g-short-0001", number="15", doc_date=date(2026, 9, 5), seller_name='ООО "Ромашка Электро"')
    upd(sessions, "g-short-0002", number="15", doc_date=date(2026, 9, 5), seller_name='ООО "Лютик Плюс"', seller_inn="7707083893")
    add_receiving(sessions, uuid4(), document_number="15", document_date=date(2026, 9, 5), supplier="Лютик Плюс", grn="FRX.26.0000201")
    add_receiving(sessions, uuid4(), document_number="15", document_date=date(2026, 9, 5), supplier="ООО Кактус", grn="FRX.26.0000202")
    listing = chains(client)
    assert by_key(listing, "upd:g-short-0001")["upds"][0]["receiving"]["grns"] == []
    assert by_key(listing, "upd:g-short-0002")["upds"][0]["receiving"]["grns"] == ["FRX.26.0000201"]


def test_a_stand_without_receiving_still_shows_the_chain(env):
    client, sessions = env
    inv = invoice(sessions, "Л-1", pays=[("870", "01.09.2026", 1000)])
    upd(sessions, "g-norecv-001", basis="Счет № Л-1 от 01.09.2026")
    del app.state.receiving_uow_factory
    try:
        listing = chains(client)
        assert listing["receiving_available"] is False
        assert by_key(listing, f"invoice:{inv}")["upds"][0]["receiving"] is None
        assert client.get(f"/api/purchasing/invoices/{inv}").json()["receiving_available"] is False
        assert client.get("/api/purchasing/upd/g-norecv-001/invoices").json()["receiving"] is None
    finally:
        app.state.receiving_uow_factory = lambda: SqlAlchemyReceivingUnitOfWork(sessions)


# ---- invoices ------------------------------------------------------------------------------------------------------

def test_invoice_list_filters_counts_and_the_closing_column(env):
    client, sessions = env
    paid = invoice(sessions, "43851", pays=[("880", "16.09.2025", 4815)], total=481500, day="2025-09-15",
                   lines=[{"seq": 1, "name": "Кабель РК 50-3-11", "article": "", "qty": 15, "unit": "м", "price": 32100, "sum": 481500}])
    to_pay = invoice(sessions, "5", day="2025-09-20", total=None)
    returned = invoice(sessions, "6", day="2025-09-21", status="return")
    upd(sessions, "g-inv-000001", number="9", doc_date=date(2025, 9, 18), basis="Счет № 43851 от 15.09.2025", total_gross=481500)

    body = client.get("/api/purchasing/invoices").json()
    assert [i["id"] for i in body["items"]] == [str(returned), str(to_pay), str(paid)]
    assert body["counts"] == {"to_pay": 1, "paid": 1, "return": 1, "other": 0, "cancelled": 0, "replaced": 0}
    first = next(i for i in body["items"] if i["id"] == str(paid))
    assert (first["payment_numbers"], first["payment_date"], first["payment_sum"], first["payment_matches"]) == ("880", "2025-09-16", 481500, True)
    assert first["line_count"] == 1
    assert first["chain"]["state"] == "closed" and first["chain"]["upds"] == 1
    assert [i["id"] for i in client.get("/api/purchasing/invoices", params={"status": "to_pay"}).json()["items"]] == [str(to_pay)]
    assert [i["id"] for i in client.get("/api/purchasing/invoices", params={"closing": "none"}).json()["items"]] == [str(returned), str(to_pay)]
    assert [i["id"] for i in client.get("/api/purchasing/invoices", params={"q": "880"}).json()["items"]] == [str(paid)]
    assert [i["id"] for i in client.get("/api/purchasing/invoices", params={"from": "2025-09-19", "to": "2025-09-20"}).json()["items"]] == [str(to_pay)]
    assert client.get("/api/purchasing/invoices", params={"status": "lost"}).status_code == 422


def test_invoice_card_positions_payments_files_and_the_file_itself(env):
    client, sessions = env
    from app.modules.purchasing.infrastructure import DocumentFileRow

    stored = uuid4()
    with sessions() as s:
        s.add(DocumentFileRow(id=stored, sha256="a" * 64, file_name="Счет 43851.pdf", size=8, content=b"%PDF-1.4\n%", created_at=datetime.now(UTC)))
        s.commit()
    inv = invoice(
        sessions, "43851", pays=[("880", "16.09.2025", 4815)], total=481500,
        lines=[{"seq": 1, "name": "Кабель РК 50-3-11", "article": "", "qty": 15, "unit": "м", "price": 32100, "sum": 481500}],
        files=[{"name": "Счет 43851.pdf", "kind": "invoice", "file_id": stored}, {"name": "Спецификация.xlsx", "kind": "other"}],
    )
    card = client.get(f"/api/purchasing/invoices/{inv}").json()
    assert card["lines"][0]["name"] == "Кабель РК 50-3-11"
    assert card["payments"] == [{"number": "880", "date": "2025-09-16", "sum": 481500}]
    assert [(f["name"], f["kind"], f["downloaded"]) for f in card["files"]] == [
        ("Платежное_поручение_№880_от_16.09.2025_на_сумму_4815.pdf", "payment", False),
        ("Счёт № 43851 от 01.09.2026.pdf", "invoice", True),
        ("Спецификация.xlsx", "other", False),
    ]
    pdf = next(f for f in card["files"] if f["kind"] == "invoice")
    assert card["invoice_file_id"] == pdf["id"]
    res = client.get(f"/api/purchasing/invoices/{inv}/files/{pdf['id']}")
    assert res.status_code == 200 and res.content.startswith(b"%PDF")
    assert res.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''" + quote("Счёт № 43851 от 01.09.2026.pdf") in res.headers["content-disposition"]
    missing = next(f for f in card["files"] if f["kind"] == "other")
    assert client.get(f"/api/purchasing/invoices/{inv}/files/{missing['id']}").status_code == 404
    assert client.get(f"/api/purchasing/invoices/{uuid4()}/files/{pdf['id']}").status_code == 404
    assert client.get(f"/api/purchasing/invoices/{uuid4()}").status_code == 404


# ---- gates ---------------------------------------------------------------------------------------------------------

def test_view_only_cannot_decide(env):
    client, sessions = env
    inv = invoice(sessions, "М-1")
    upd(sessions, "g-gate-00001")
    _as({"procurement": "view"})
    assert client.get("/api/purchasing/doc-chain").status_code == 200
    assert client.put(f"/api/purchasing/upd/g-gate-00001/invoices/{inv}", json={"decision": "confirmed"}).status_code == 403


# ---- performance ---------------------------------------------------------------------------------------------------

def _valid_inn(nine: str) -> str:
    """Nine digits + the check digit of a company INN."""
    weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
    return nine + str(sum(w * int(d) for w, d in zip(weights, nine)) % 11 % 10)


def test_the_chain_list_of_1000_upds_and_700_invoices_is_computed_in_time(env):
    client, sessions = env
    # Real INNs pass the checksum; every tenth document carries an OCR slip in it.
    sellers = [(_valid_inn(f"77{k:07d}"), f'ООО "Поставщик {k:03d}"') for k in range(60)]
    with sessions() as s:
        for k in range(700):
            inn, name = sellers[k % len(sellers)]
            iid = uuid4()
            s.add(InvoiceRow(
                id=iid, source="kaiten", external_id=f"p{k}", title=f"Счет № С-{k}", status="paid", card_created=NOW,
                card_updated=NOW, number=f"С-{k}", doc_date=date(2026, 1 + k % 9, 1 + k % 27), seller_name=name, seller_inn=inn,
                total=100000 + k, synced_at=NOW, lines=[],
            ))
            s.add(InvoiceFileRow(
                id=uuid4(), invoice_id=iid, external_id=f"pf{k}", kind="payment", size=1, seq=0,
                name=f"Платежное_поручение_№{1000 + k}_от_{1 + k % 27:02d}.{1 + k % 9:02d}.2026_на_сумму_{1000 + k // 100}.pdf",
            ))
        for k in range(1000):
            inn, name = sellers[k % len(sellers)]
            if k % 10 == 0:
                inn = inn[:-1] + str((int(inn[-1]) + 1) % 10)
            target = k % 700
            s.add(UpdDocumentRow(
                id=f"g-perf-{k:06d}", origin="google" if k % 3 else "edo", file_name=f"{k}.pdf", status="Разобран", kind="УПД",
                number=f"{k}", doc_date=date(2026, 1 + target % 9, 1 + target % 27), seller_name=name, seller_inn=inn,
                buyer_inn=OUR_INN, total_gross=100000 + target, synced_at=NOW, created_at=NOW,
                basis=f"Счет № С-{target} от {1 + target % 27:02d}.{1 + target % 9:02d}.2026" if k % 4 else "Договор поставки № 1",
                payment_doc=f"№ {1000 + target} от {1 + target % 27:02d}.{1 + target % 9:02d}.2026" if k % 5 == 0 else "",
            ))
            s.add(UpdLineRow(id=uuid4(), document_id=f"g-perf-{k:06d}", source="google", seq=1, name="Разъём", is_service=False))
        s.commit()
    def timed() -> tuple[float, dict]:
        started = time.perf_counter()
        res = client.get("/api/purchasing/doc-chain", params={"view": "all"})
        assert res.status_code == 200
        return time.perf_counter() - started, res.json()

    timed()  # the process's first read: statement caches and the like
    doc_chain._seller_memo = None
    changed, body = timed()  # the registry changed: the sellers' INNs are compared again
    again, _ = timed()  # nothing changed: that answer is reused
    assert body["total"] >= 700
    assert body["summary"]["by_state"]["closed"] > 100, body["summary"]
    # Budgets ×3 over the stage machine: CI runners jitter by tens of percent; an order-of-magnitude slowdown still fails.
    assert changed < 4.5, f"цепочка после изменения реестра считалась {changed:.2f} с"
    assert again < 3.0, f"цепочка считалась {again:.2f} с"

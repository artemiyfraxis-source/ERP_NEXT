"""«Принять по УПД» (owner 26.10.2026): the recognised УПД laid over the waiting list — by its invoice, by its seller
(INN / aliases), the same item of several orders shared out, rows summed, units compared, nothing false ticked — and
the API: the receiving edit right, the scan file kept as the delivery's УПД, all or nothing, «Реестр УПД»."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.main import app
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.purchasing.infrastructure import SupplierNameRow, SupplierRow, UpdDocumentRow
from app.modules.purchasing.suppliers import supplier_key
from app.modules.receiving.domain import ReceivingLine, ReceivingStatus
from app.modules.receiving.infrastructure import ReceivingLineRow
from app.modules.warehouse.infrastructure import WarehouseReceiptFileRow
from app.shared.http_auth import Principal, authenticated_principal
from app.workflows.receiving_by_upd import (
    SupplierBook,
    UpdHeader,
    UpdRow,
    Waiting,
    propose,
    supplier_aliases,
)
from tests.factories import Actor
from tests.warehouse.test_receipts import NOW, stand  # noqa: F401 — fixture

ELITAN = UUID("00000000-0000-0000-0000-0000000000e1")
ROMASHKA = UUID("00000000-0000-0000-0000-0000000000a2")
BOOK = SupplierBook(
    by_inn={"7801234567": ELITAN},
    by_key={supplier_key("ООО «Элитан»"): ELITAN, supplier_key("Элитан СПб"): ELITAN,
            supplier_key("Ромашка"): ROMASHKA},
)
HEADER = UpdHeader(supplier="ООО «Элитан»", inn="7801234567")


def line(number: int, *, article="XH-2P", name="Разъём XH 2 конт.", ordered="10", unit="шт", supplier="Элитан СПб",
         order="З-1", arrival: date | None = date(2026, 10, 20), warehouse="ЦАР",
         status=ReceivingStatus.IN_TRANSIT) -> ReceivingLine:
    return ReceivingLine(
        id=UUID(int=number), source_event_id=uuid4(), source_idempotency_key=f"k{number}", procurement_line_id=uuid4(),
        legacy_id=f"SM-{number:02d}", status=status, version=1, created_at=datetime(2026, 10, 1, 10, number),
        warehouse=warehouse, article=article, series="", name=name, item_type="", purpose="", goal="", source_link="",
        supplier=supplier, ordered_quantity=Decimal(ordered), unit=unit, quality_document_required=False,
        order_number=order, expected_arrival=arrival,
    )


def waiting(*lines: ReceivingLine, invoice: str = "", invoice_date: date | None = None) -> list[Waiting]:
    return [Waiting(item, invoice_number=invoice, invoice_date=invoice_date) for item in lines]


def row(index: int, article="XH-2P", quantity="10", unit="шт", name: str | None = None, **extra) -> UpdRow:
    name = f"Разъём {article}" if name is None else name
    return UpdRow(index=index, article=article, name=name, upd_name=name, quantity=Decimal(quantity) if quantity else None,
                  unit=unit, **extra)


def by_id(proposal):
    return {item.line.legacy_id: item for item in proposal.lines}


# --- candidates -------------------------------------------------------------------------------------------------


def test_the_invoice_of_the_upd_names_its_lines_and_another_supplier_with_the_same_number_is_not_taken():
    ours = line(1, order="З-1")
    other_invoice = line(2, article="PH-3", name="Разъём PH 3 конт.", order="З-2")
    foreign = line(3, supplier="ООО Ромашка", order="З-9")
    typed = line(4, article="KK-7", name="Клемма KK-7", supplier="Эл.", order="З-4")  # the buyer typed the supplier short
    pool = [*waiting(ours, invoice="СЧ-45", invoice_date=date(2026, 10, 1)), *waiting(other_invoice, invoice="СЧ-50"),
            *waiting(foreign, invoice="сч 45", invoice_date=date(2026, 10, 3)),
            *waiting(typed, invoice="СЧ 45", invoice_date=date(2026, 10, 1))]
    header = UpdHeader(supplier="ООО «Элитан»", inn="7801234567", invoice_number="СЧ-45", invoice_date=date(2026, 10, 1))

    proposal = propose(header, [row(0)], pool, BOOK)

    found = by_id(proposal)
    assert set(found) == {"SM-01", "SM-02", "SM-04"}, "Ромашка has an invoice «45» too — of another day and seller"
    assert found["SM-04"].candidate.by_invoice, "the same number and day: the order's own supplier text does not matter"
    assert (found["SM-01"].confidence, found["SM-01"].ticked, found["SM-01"].document_quantity) == (
        "invoice", True, Decimal("10"))
    assert found["SM-02"].rows == [] and not found["SM-02"].ticked, "waiting, not on this УПД — stays unticked"
    assert proposal.unmatched == []


def test_the_invoice_finds_lines_whose_supplier_text_is_unknown_to_the_book_and_skips_a_year_old_number():
    typed = line(1, supplier="Элит")  # neither an alias nor the same seller key
    stale = line(2, supplier="Элит", order="З-0")
    pool = [*waiting(typed, invoice="123", invoice_date=date(2026, 10, 2)),
            *waiting(stale, invoice="123", invoice_date=date(2025, 10, 2))]
    header = UpdHeader(supplier="", inn="", invoice_number="Счёт № 123", invoice_date=date(2026, 10, 2))

    proposal = propose(UpdHeader(invoice_number="123", invoice_date=date(2026, 10, 2)), [row(0)], pool, BOOK)

    assert [item.line.legacy_id for item in proposal.lines] == ["SM-01"]
    assert proposal.lines[0].confidence == "invoice"
    assert header.invoice_numbers == ["Счёт № 123"]


def test_the_seller_by_inn_and_aliases_brings_every_open_line_of_the_supplier_across_orders():
    lines = [line(1, supplier="Элитан СПб", order="З-1"), line(2, supplier="ООО «Элитан»", order="З-2",
                                                              article="PH-3", name="Разъём PH 3"),
             line(3, supplier="Ромашка", order="З-3"), line(4, supplier="Элитан", status=ReceivingStatus.ACCEPTED)]

    proposal = propose(UpdHeader(supplier="Элитан ООО", inn="7801234567"), [row(0), row(1, article="PH-3", quantity="5")],
                       waiting(*lines), BOOK)

    assert sorted(item.line.legacy_id for item in proposal.lines) == ["SM-01", "SM-02"]
    found = by_id(proposal)
    assert (found["SM-01"].confidence, found["SM-02"].confidence) == ("article", "article")
    assert found["SM-02"].document_quantity == Decimal("5") and found["SM-02"].ticked


def test_without_a_seller_the_group_the_scan_was_started_from_is_the_supplier():
    lines = [line(1, supplier="Электра"), line(2, supplier="Другой")]

    proposal = propose(UpdHeader(), [row(0)], waiting(*lines), BOOK, hint_supplier="электра")

    assert [item.line.legacy_id for item in proposal.lines] == ["SM-01"]


# --- matching and share-out -------------------------------------------------------------------------------------


def test_one_row_over_four_orders_fills_the_oldest_first_and_only_the_last_one_comes_short():
    lines = [line(index, order=f"З-{index}", arrival=date(2026, 10, 10 + index)) for index in (4, 2, 3, 1)]

    proposal = propose(HEADER, [row(0, quantity="35")], waiting(*lines), BOOK)

    found = by_id(proposal)
    assert [(key, found[key].document_quantity) for key in ("SM-01", "SM-02", "SM-03", "SM-04")] == [
        ("SM-01", Decimal("10")), ("SM-02", Decimal("10")), ("SM-03", Decimal("10")), ("SM-04", Decimal("5"))]
    assert all(found[key].ticked for key in found)
    assert proposal.row_lines[0] == [UUID(int=1), UUID(int=2), UUID(int=3), UUID(int=4)]


def test_a_row_that_equals_one_order_exactly_goes_to_that_order():
    lines = [line(1, ordered="50", order="З-1", arrival=date(2026, 10, 1)),
             line(2, ordered="100", order="З-2", arrival=date(2026, 10, 5))]

    proposal = propose(HEADER, [row(0, quantity="100")], waiting(*lines), BOOK)

    found = by_id(proposal)
    assert found["SM-02"].document_quantity == Decimal("100")
    assert found["SM-01"].rows == [] and not found["SM-01"].ticked


def test_more_than_ordered_lands_on_the_last_line_with_a_surplus_warning():
    lines = [line(1, order="З-1", arrival=date(2026, 10, 1)), line(2, order="З-2", arrival=date(2026, 10, 2))]

    proposal = propose(HEADER, [row(0, quantity="25")], waiting(*lines), BOOK)

    found = by_id(proposal)
    assert (found["SM-01"].document_quantity, found["SM-02"].document_quantity) == (Decimal("10"), Decimal("15"))
    assert found["SM-02"].warnings == ["По УПД больше заказа на 5 шт"]


def test_two_rows_of_one_item_add_up_on_its_line():
    proposal = propose(HEADER, [row(0, quantity="6"), row(1, quantity="4", name="Разъём XH-2P (доп.)")],
                       waiting(line(1)), BOOK)

    (only,) = proposal.lines
    assert only.rows == [(0, Decimal("6")), (1, Decimal("4"))] and only.document_quantity == Decimal("10")


def test_names_match_without_an_article_and_a_weak_match_is_offered_unticked():
    lines = [line(1, article="", name="Термоусадочная трубка 6,4/3,2 чёрная"),
             line(2, article="", name="Хомут нейлоновый 2,5х100 белый", order="З-2")]
    rows = [row(0, article="", name="Трубка термоусадочная 6,4/3,2 чёрная (1 м)", quantity="10"),
            row(1, article="", name="Хомут нейлоновый 3,6х200", quantity="10")]

    proposal = propose(HEADER, rows, waiting(*lines), BOOK)

    found = by_id(proposal)
    assert (found["SM-01"].confidence, found["SM-01"].ticked) == ("name", True)
    assert (found["SM-02"].confidence, found["SM-02"].ticked) == ("weak", False)


def test_part_numbers_that_disagree_are_never_matched_by_words():
    proposal = propose(HEADER, [row(0, article="XH-3P", name="Разъём XH-3P")], waiting(line(1)), BOOK)

    assert proposal.unmatched == [0] and proposal.lines[0].rows == []


def test_a_higher_tier_keeps_its_line_from_a_row_matching_it_by_words_only():
    rows = [row(0), row(1, article="", name="Разъём XH 2 конт. другой", quantity="3")]

    proposal = propose(HEADER, rows, waiting(line(1)), BOOK)

    assert proposal.lines[0].rows == [(0, Decimal("10"))]
    assert proposal.unmatched == [1]


def test_unmatched_rows_are_listed_and_waiting_lines_off_the_upd_keep_no_quantity():
    lines = [line(1), line(2, article="PH-3", name="Разъём PH 3", order="З-2")]

    proposal = propose(HEADER, [row(0), row(1, article="ZZ-999", name="Кабель ZZ-999")], waiting(*lines), BOOK)

    assert proposal.unmatched == [1]
    off = by_id(proposal)["SM-02"]
    assert (off.ticked, off.document_quantity, off.confidence) == (False, None, "")


def test_quantities_are_compared_in_the_unit_of_the_order():
    rolls = line(1, article="МТ-2К 6,4/3,2", name="Маркировочная трубка МТ-2К 6,4/3,2", unit="рул", ordered="3")
    metres = line(2, article="МТ-2К 4,8/2,4", name="Маркировочная трубка МТ-2К 4,8/2,4", unit="м", ordered="300",
                  order="З-2")
    pieces = line(3, article="PH-3", name="Разъём PH 3", unit="шт", ordered="5", order="З-3")
    rows = [
        UpdRow(index=0, article="МТ-2К 6,4/3,2", name="Маркировочная трубка", quantity=Decimal("300"), unit="м",
               invoice_quantity=Decimal("3"), invoice_unit="рул."),
        UpdRow(index=1, article="МТ-2К 4,8/2,4", name="Маркировочная трубка", quantity=Decimal("300"), unit="м",
               invoice_quantity=Decimal("3"), invoice_unit="рул"),
        UpdRow(index=2, article="PH-3", name="Разъём PH 3", quantity=Decimal("0.2"), unit="кг"),
    ]

    proposal = propose(HEADER, rows, waiting(rolls, metres, pieces), BOOK)

    found = by_id(proposal)
    assert (found["SM-01"].document_quantity, found["SM-01"].ticked) == (Decimal("3"), True)
    assert (found["SM-02"].document_quantity, found["SM-02"].ticked) == (Decimal("300"), True)
    assert not found["SM-03"].ticked and found["SM-03"].warnings == ["В УПД 0.2 кг, в заказе шт — проверьте количество"]


def test_an_unread_quantity_is_matched_but_not_ticked():
    proposal = propose(HEADER, [row(0, quantity="")], waiting(line(1)), BOOK)

    (only,) = proposal.lines
    assert only.rows and not only.ticked and only.document_quantity is None
    assert only.warnings == ["Количество в УПД не прочитано (строка 1) — укажите по УПД"]


def test_one_warehouse_per_delivery_the_one_most_rows_went_to_unless_asked():
    lines = [line(1, warehouse="ЖУК"), line(2, article="PH-3", name="Разъём PH 3", warehouse="ЖУК", order="З-2"),
             line(3, article="ZH-4", name="Разъём ZH 4", warehouse="ЦАР", order="З-3")]
    rows = [row(0), row(1, article="PH-3", name="Разъём PH-3"), row(2, article="ZH-4", name="Разъём ZH-4")]

    proposal = propose(HEADER, rows, waiting(*lines), BOOK)
    assert proposal.warehouse == "ЖУК" and proposal.other_warehouses == {"ЦАР": 1}
    assert proposal.unmatched == [2]

    asked = propose(HEADER, rows, waiting(*lines), BOOK, warehouse="ЦАР")
    assert [item.line.legacy_id for item in asked.lines] == ["SM-03"] and asked.other_warehouses == {"ЖУК": 2}


def test_supplier_texts_go_together_only_as_one_purchasing_supplier():
    assert supplier_aliases(["Элитан СПб", "ООО «Элитан»"], HEADER, BOOK) == {"элитан спб", "ооо «элитан»"}
    assert supplier_aliases(["Элитан СПб", "Ромашка"], HEADER, BOOK) == frozenset()
    assert supplier_aliases(["Элитан СПб", "элитан спб "], HEADER, BOOK) == frozenset(), "one text — the usual rule"
    assert supplier_aliases(["Элитан СПб", "ООО «Элитан»"], UpdHeader(), BOOK) == {"элитан спб", "ооо «элитан»"}


# --- API ----------------------------------------------------------------------------------------------------------

RECOGNISED = {"supplier": "ООО «Элитан»", "inn": "7801234567", "upd_number": "771", "upd_date": "2026-10-20",
              "invoice_number": "СЧ-45", "invoice_date": "2026-10-01"}
PDF = b"%PDF-1.4\n% scan of a paper UPD\n"


def _as(permission: str) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Приёмщик", department="ОТК"), email="receiver@example.test", roles=frozenset({"receiver"}),
        permissions={"receiving": permission},
    )


def _supplier(sessions, name: str, inn: str | None, aliases=()) -> UUID:
    supplier_id = uuid4()
    with sessions() as session:
        session.add(SupplierRow(id=supplier_id, name=name, name_key=supplier_key(name), inn=inn, categories=[],
                                domains=[], created_at=NOW))
        session.flush()
        for alias in aliases:
            session.add(SupplierNameRow(name_key=supplier_key(alias), supplier_id=supplier_id, created_at=NOW))
        session.commit()
    return supplier_id


def _waiting(sessions, number: int, *, article="XH-2P", supplier="Элитан СПб", order="З-1", ordered="10",
             invoice: str | None = None, arrival=date(2026, 10, 20)) -> UUID:
    line_id, procurement_id = UUID(int=number), uuid4()
    with sessions() as session:
        if invoice is not None:
            session.add(ProcurementLineRow(
                id=procurement_id, source_event_id=uuid4(), source_idempotency_key=str(uuid4()),
                source_request_id=uuid4(), legacy_id=f"SM-{number:02d}", revision="1", status="Заказано", version=1,
                created_at=NOW, request_date=date(2026, 10, 1), initiator="", department="", task_type="",
                designation="591К.3", priority="", delivery_point="", planned_date=date(2026, 10, 30),
                item_type="Разъём", category="other", calculated_name=f"Разъём {article}",
                calculated_quantity=Decimal(ordered), calculated_unit="шт", design_article=article,
                invoice_number=invoice, invoice_date=date(2026, 10, 1), supplier=supplier,
            ))
        session.add(ReceivingLineRow(
            id=line_id, source_event_id=uuid4(), source_idempotency_key=str(uuid4()), procurement_line_id=procurement_id,
            legacy_id=f"SM-{number:02d}", status="В пути", version=1, created_at=NOW + timedelta(minutes=number),
            warehouse="ЦАР", article=article, series="", name=f"Разъём {article}", item_type="Разъём", purpose="FRX.001",
            goal="", source_link="", supplier=supplier, ordered_quantity=Decimal(ordered), unit="шт",
            quality_document_required=False, nonconformities=[], backorder_quantity=Decimal("0"), package="",
            warehouse_event_emitted=False, order_number=order, expected_arrival=arrival,
        ))
        session.commit()
    return line_id


def _scan(sessions) -> str:
    file_id = uuid4()
    with sessions() as session:
        session.add(WarehouseReceiptFileRow(id=file_id, file_name="УПД.pdf", content_type="application/pdf",
                                            size=len(PDF), content=PDF, uploaded_by="Кладовщик", uploaded_at=NOW,
                                            recognition=""))
        session.commit()
    return f"/api/stock/receipts/files/{file_id}"


def _lines(sessions) -> dict[str, ReceivingLineRow]:
    with sessions() as session:
        return {row.legacy_id: row for row in session.scalars(select(ReceivingLineRow))}


UPD_ROWS = [
    {"article": "XH-2P", "name": "Разъём XH-2P", "quantity": "10", "unit": "шт"},
    {"article": "PH-3", "name": "Разъём PH-3", "quantity": "6", "unit": "шт"},
    {"article": "ZZ-999", "name": "Кабель ZZ-999", "quantity": "1", "unit": "шт"},
]


def test_a_view_only_receiving_is_refused_every_route(stand):  # noqa: F811
    client, _ = stand
    _as("view")
    routes = [("POST", "/api/receiving/upd/scan-jobs"), ("GET", f"/api/receiving/upd/scan-jobs/{uuid4()}"),
              ("DELETE", f"/api/receiving/upd/scan-jobs/{uuid4()}"), ("POST", "/api/receiving/upd/scan"),
              ("POST", "/api/receiving/upd/scan-merge"), ("POST", "/api/receiving/upd/proposal"),
              ("POST", "/api/receiving/upd/accept")]
    for method, path in routes:
        assert client.request(method, path, json={}).status_code == 403, (method, path)


def test_the_receiving_edit_right_starts_a_scanner_job_on_the_warehouse_queue(stand):  # noqa: F811
    client, _ = stand
    _as("edit")

    started = client.post("/api/receiving/upd/scan-jobs")
    assert started.status_code == 200, started.text
    polled = client.get(f"/api/receiving/upd/scan-jobs/{started.json()['id']}").json()
    assert polled["status"] == "pending" and polled["result"] is None
    assert client.delete(f"/api/receiving/upd/scan-jobs/{started.json()['id']}").json()["status"] == "failed"


def test_proposal_and_acceptance_of_a_upd_over_two_orders_keep_the_scan_and_reach_the_registry(stand):  # noqa: F811
    client, sessions = stand
    _as("edit")
    _supplier(sessions, "ООО «Элитан»", "7801234567", aliases=["Элитан СПб"])
    _waiting(sessions, 1, invoice="СЧ-45")
    _waiting(sessions, 2, article="PH-3", supplier="ООО «Элитан»", order="З-2", invoice="СЧ-45")
    _waiting(sessions, 3, article="KK-7", order="З-3")  # Элитан's, not on this УПД

    proposal = client.post("/api/receiving/upd/proposal", json={"recognised": RECOGNISED, "lines": UPD_ROWS})
    assert proposal.status_code == 200, proposal.text
    body = proposal.json()
    assert body["warehouse"] == "ЦАР" and body["unmatched"] == [2] and body["matched"] == 2
    assert [(item["line"]["legacy_id"], item["ticked"], item["document_quantity"], item["confidence"])
            for item in body["lines"]] == [("SM-01", True, "10", "invoice"), ("SM-02", True, "6", "invoice"),
                                           ("SM-03", False, "", "")]
    assert body["orders"] == ["З-1", "З-2"] and body["invoice_numbers"] == ["СЧ-45"]

    scan = _scan(sessions)
    ticked = [item for item in body["lines"] if item["ticked"]]
    accepted = client.post("/api/receiving/upd/accept", json={
        "lines": [{"id": item["line"]["id"], "version": item["line"]["version"],
                   "document_quantity": item["document_quantity"], "storage_location": "1F4"} for item in ticked],
        "document_number": "771", "document_date": "2026-10-20", "warehouse": "ЦАР", "scan_file": scan,
        "recognised": RECOGNISED,
    })
    assert accepted.status_code == 200, accepted.text
    answer = accepted.json()
    assert answer["upd_file"].startswith("/api/receiving/files/")
    assert answer["registry"]["status"] == "created" and answer["registry"]["origin"] == "scan"

    rows = _lines(sessions)
    assert (rows["SM-01"].status, rows["SM-01"].backorder_quantity) == ("Принято", Decimal("0"))
    assert (rows["SM-02"].status, rows["SM-02"].backorder_quantity) == ("Принято", Decimal("4")), "short: Недовоз"
    assert rows["SM-03"].status == "В пути" and rows["SM-03"].backorder_quantity == Decimal("0"), "off the УПД"
    assert {rows["SM-01"].shipping_document_url, rows["SM-02"].shipping_document_url} == {answer["upd_file"]}
    assert {rows["SM-01"].document_number, rows["SM-02"].document_number} == {"771"}
    assert client.get(answer["upd_file"]).content == PDF
    with sessions() as session:
        document = session.get(UpdDocumentRow, f"wh-{scan.rsplit('/', 1)[1]}")
        assert document is not None and document.number == "771" and document.origin == "scan"
        assert "СЧ-45" in document.basis


def test_acceptance_is_all_or_nothing_and_strangers_on_one_upd_are_refused(stand):  # noqa: F811
    client, sessions = stand
    _as("edit")
    _supplier(sessions, "ООО «Элитан»", "7801234567", aliases=["Элитан СПб"])
    first = _waiting(sessions, 1)
    other = _waiting(sessions, 2, article="PH-3", supplier="ООО Ромашка", order="З-2")
    scan = _scan(sessions)

    stale = client.post("/api/receiving/upd/accept", json={
        "lines": [{"id": str(first), "version": 1}, {"id": str(other), "version": 7}], "scan_file": scan,
        "recognised": RECOGNISED, "document_number": "771"})
    assert stale.status_code == 409
    mixed = client.post("/api/receiving/upd/accept", json={
        "lines": [{"id": str(first), "version": 1}, {"id": str(other), "version": 1}], "scan_file": scan,
        "recognised": RECOGNISED, "document_number": "771"})
    assert mixed.status_code == 422 and "разных поставщиков" in mixed.json()["detail"]
    assert {row.status for row in _lines(sessions).values()} == {"В пути"}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(UpdDocumentRow)) == 0

    missing = client.post("/api/receiving/upd/accept", json={
        "lines": [{"id": str(first), "version": 1}], "scan_file": "/api/stock/receipts/files/" + str(uuid4()),
        "recognised": RECOGNISED})
    assert missing.status_code == 422 and "Скан УПД не найден" in missing.json()["detail"]


def test_two_texts_of_one_supplier_are_accepted_together(stand):  # noqa: F811
    client, sessions = stand
    _as("edit")
    _supplier(sessions, "ООО «Элитан»", "7801234567", aliases=["Элитан СПб"])
    first = _waiting(sessions, 1, supplier="Элитан СПб")
    second = _waiting(sessions, 2, article="PH-3", supplier="ООО «Элитан»", order="З-2")

    accepted = client.post("/api/receiving/upd/accept", json={
        "lines": [{"id": str(first), "version": 1}, {"id": str(second), "version": 1}], "recognised": RECOGNISED,
        "document_number": "771", "document_date": "2026-10-20"})

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["upd_file"] == "" and accepted.json()["registry"] is None
    plain = client.post("/api/receiving/deliveries", json={"lines": [{"id": str(uuid4()), "version": 1}]})
    assert plain.status_code == 404

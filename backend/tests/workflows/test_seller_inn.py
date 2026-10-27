"""A misread seller ИНН is never matched by as is (owner 27.10.2026): the real model read «5256004951» and «5256084951»
off two scans of ООО «Промышленный Провод» («5256084961») — both fail the check digits. It is put right by the
document's own text, else by the one directory supplier of that name it is a slip of; else left out with a remark.
The warehouse screen, the УПД registry and «Принять по УПД» check it the same way. No real model is asked."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.modules.purchasing.infrastructure import (
    SqlAlchemyPurchasingUnitOfWork,
    SupplierNameRow,
    SupplierRow,
    UpdDocumentRow,
)
from app.modules.purchasing.suppliers import supplier_key
from app.modules.warehouse import scan_reading
from app.modules.warehouse.scan_reading import model_view, reading_json
from app.modules.warehouse.infrastructure import WarehouseReceiptFileRow
from app.modules.warehouse.scan_recognition import ScanSources, scan_answer
from app.shared.inn import checked_inn
from app.workflows.receiving_by_upd import SupplierBook, UpdHeader
from app.workflows.seller_inn import seller_inn
from tests.warehouse.test_receipts import HEADER, LINE, NOW, stand  # noqa: F401 — fixture

TRUE_INN = "5256084961"
MISREADS = ("5256004951", "5256084951")  # two digits / one digit off, both failing the check digits
SELLER = "ООО «Промышленный Провод»"
PAGE = {
    "docKind": "UPD", "number": "1187", "date": "2026-10-21", "sellerName": SELLER, "sellerInn": MISREADS[0],
    "sellerKpp": "525601001", "buyerInn": "5040191181", "basis": "Счет № 512 от 15.10.2026", "totalGross": 1220,
    "lines": [{"seq": 1, "article": "ПВ3-1", "name": "Провод ПуГВ 1х1,5 красный", "unit": "м", "qty": 100,
               "price": 10, "net": 1000, "vatRate": "22%", "vat": 220, "gross": 1220}],
}
FIXED = f"ИНН исправлен по справочнику: было {MISREADS[0]}"
FAILED = f"ИНН продавца не прошёл проверку: {MISREADS[0]}"


def _supplier(sessions, name: str = "Промышленный Провод", inn: str = TRUE_INN):
    supplier_id = uuid4()
    with sessions() as session:
        session.add(SupplierRow(id=supplier_id, name=name, name_key=supplier_key(name), inn=inn, categories=[],
                                is_key=False, domains=[], version=1, created_at=NOW))
        session.commit()
    return supplier_id


def test_the_real_misreads_are_put_right_by_the_known_supplier_a_valid_inn_never_changes():
    for misread in MISREADS:
        check = checked_inn(misread, known=[TRUE_INN])
        assert (check.inn, check.remark) == (TRUE_INN, f"ИНН исправлен по справочнику: было {misread}")
    assert checked_inn(MISREADS[1], known=[]) == checked_inn(MISREADS[1])
    assert (checked_inn(MISREADS[1]).inn, checked_inn(MISREADS[1]).remark) == ("", f"ИНН продавца не прошёл проверку: {MISREADS[1]}")
    assert checked_inn("5256084916", known=[TRUE_INN]).inn == TRUE_INN, "an adjacent transposition is one slip"
    valid = checked_inn("7707083893", known=["7707083894", TRUE_INN])
    assert (valid.inn, valid.remark) == ("7707083893", ""), "a valid ИНН is never changed"
    two = checked_inn(MISREADS[1], known=[TRUE_INN, "5256084954"])
    assert (two.inn, two.remark) == ("", f"ИНН продавца не прошёл проверку: {MISREADS[1]}"), "two candidates — no guess"
    assert checked_inn("").remark == "" and checked_inn(None).inn == ""


def test_the_directory_check_only_takes_suppliers_named_as_the_seller(stand):  # noqa: F811
    _, sessions = stand
    factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)  # noqa: E731
    assert (seller_inn(factory, MISREADS[0], SELLER).inn, seller_inn(factory, MISREADS[0], SELLER).remark) == (
        "", FAILED), "no such supplier: kept out of matching"
    other = _supplier(sessions, name="ООО Другой Завод")
    assert seller_inn(factory, MISREADS[0], SELLER).inn == "", "the ИНН of a supplier under another name is not taken"
    _supplier(sessions, name="ООО «Промышленный Провод»", inn="7707083893")
    assert seller_inn(factory, MISREADS[0], SELLER).inn == "", "the seller's name with a far ИНН is no candidate"
    with sessions() as session:
        session.add(SupplierNameRow(name_key=supplier_key("ПП «Промышленный Провод» Нижний"), supplier_id=other,
                                    created_at=NOW))
        session.commit()
    check = seller_inn(factory, MISREADS[0], SELLER)
    assert (check.inn, check.remark) == (TRUE_INN, FIXED), "found through the alias of the supplier"
    assert seller_inn(factory, "7707083893", SELLER).remark == "" and seller_inn(None, MISREADS[0], SELLER).inn == ""


def test_the_warehouse_screen_gets_the_corrected_inn_or_none_with_a_note(stand):  # noqa: F811
    _, sessions = stand
    factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)  # noqa: E731
    reading = model_view([PAGE], engine="gpt-test")
    assert reading.found.inn == MISREADS[0], "the reading keeps what was read for the directory check"
    answer = scan_answer(ScanSources(purchasing=factory), "/scan/1", reading)
    assert answer["recognised"]["inn"] == "" and FAILED in answer["note"]
    _supplier(sessions)
    answer = scan_answer(ScanSources(purchasing=factory), "/scan/1", reading)
    assert answer["recognised"]["inn"] == TRUE_INN and FIXED in answer["note"]
    valid = scan_answer(ScanSources(purchasing=factory), "/scan/1", model_view([{**PAGE, "sellerInn": TRUE_INN}]))
    assert valid["recognised"]["inn"] == TRUE_INN and "ИНН" not in valid["note"]


def test_tesseract_reads_the_inn_again_only_on_a_misread_page(monkeypatch):
    read = []

    def page_text(image: Path) -> str:
        read.append(image.name)
        return f"Продавец {SELLER} ИНН/КПП продавца {TRUE_INN}/525601001 Покупатель ИНН/КПП 5040191181/504001001"

    monkeypatch.setattr(scan_reading.receipt_ocr, "page_text", page_text)
    pages = scan_reading._with_ocr_inns((PAGE, {**PAGE, "sellerInn": TRUE_INN}, None),
                                        [Path("p1.png"), Path("p2.png"), Path("p3.png")])
    assert read == ["p1.png"], "only the page whose ИНН fails the check"
    reading = model_view(pages)
    assert reading.found.inn == TRUE_INN


def _file(sessions, pages) -> str:
    file_id = uuid4()
    with sessions() as session:
        session.add(WarehouseReceiptFileRow(
            id=file_id, file_name="УПД.pdf", content_type="application/pdf", size=10, content=b"%PDF-1.4\n%x\n",
            uploaded_by="Кладовщик", uploaded_at=NOW,
            recognition=reading_json(model_view(pages, engine="gpt-test")),
        ))
        session.commit()
    return f"/api/stock/receipts/files/{file_id}"


def _registry_doc(client, sessions, link) -> UpdDocumentRow:
    receipt = client.post("/api/stock/receipts", json={**HEADER, "supplier": SELLER, "scan_file": link,
                                                       "invoice_number": "512", "upd_number": "1187"})
    assert receipt.status_code == 200, receipt.text
    accepted = client.post(f"/api/stock/receipts/{receipt.json()['id']}/accept", json=LINE)
    assert accepted.status_code == 200, accepted.text
    with sessions() as session:
        return session.get(UpdDocumentRow, f"wh-{link.rsplit('/', 1)[1]}")


def test_the_registry_writes_the_corrected_inn_or_none_with_a_remark(stand):  # noqa: F811
    client, sessions = stand
    doc = _registry_doc(client, sessions, _file(sessions, [PAGE]))
    assert doc.seller_inn == "" and FAILED in doc.issues and doc.status == "Требует проверки"
    _supplier(sessions)
    doc = _registry_doc(client, sessions, _file(sessions, [PAGE]))
    assert doc.seller_inn == TRUE_INN and FIXED in doc.issues and doc.status == "Требует проверки"
    assert "не прошёл проверку" not in doc.issues


def test_receiving_by_upd_never_matches_by_a_misread_inn():
    provod, other = uuid4(), uuid4()
    book = SupplierBook(by_inn={TRUE_INN: provod, "5256084954": other},
                        by_key={supplier_key("Промышленный Провод"): provod})
    assert book.usable_inn(SELLER, MISREADS[1]) == TRUE_INN, "the named supplier's ИНН it is a slip of"
    assert book.usable_inn("ООО Неизвестный", MISREADS[1]) == "", "no name to vouch — by nothing"
    assert book.resolve(SELLER, MISREADS[0]) == provod
    assert book.usable_inn(SELLER, "7707083893") == "7707083893", "a valid ИНН is never changed"
    assert not UpdHeader(inn=MISREADS[0]).seller_known, "a misread ИНН alone does not name the seller"

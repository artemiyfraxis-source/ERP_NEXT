"""Supplier-scoped, opt-in learning and transactional scan acceptance."""

import json
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.modules.warehouse import receipt_learning as learning
from app.modules.warehouse.application import ManualStockItem
from app.modules.warehouse.domain import ReceiptHeader
from app.modules.warehouse.infrastructure import (
    ReceiptLineConfirmationRow, SqlAlchemyWarehouseUnitOfWork, StockLotRow,
    SupplierItemRuleRow, WarehouseReceiptFileRow,
)
from app.modules.warehouse.label_jobs import LabelJobRow
from app.modules.warehouse.receipts_application import AcceptLineCommand, accept_receipt_line, create_receipt
from app.modules.warehouse.scan_jobs import ScanJobRow, add_done_job
from app.shared.persistence import create_schema
from tests.factories import Actor
from tests.warehouse.test_receipts import NOW, _accept, _receipt, stand  # noqa: F401

INN = "7707083893"
OTHER_INN = "7736050003"


def source(sessions, *, inn=INN, supplier="Поставщик", quantity="2", unit="упак", code="S-1", name="Контакт белый 2 мм, уп. 100 шт", parsed="200", parsed_unit="шт"):
    file_id = uuid4()
    answer = learning.with_provenance({
        "scan_file": f"/api/stock/receipts/files/{file_id}", "recognised": {"inn": inn, "supplier": supplier}, "note": "",
        "lines": [{"article": "RAW", "name": name, "upd_name": name, "manufacturer": "",
                   "supplier_code": code, "quantity": parsed, "unit": parsed_unit, "invoice_quantity": quantity,
                   "invoice_unit": unit, "number": "7"}],
    })
    with sessions() as session:
        session.add(WarehouseReceiptFileRow(id=file_id, file_name="upd.pdf", content_type="application/pdf", size=1,
                                           content=b"x", uploaded_by="test", uploaded_at=NOW, recognition="raw evidence"))
        session.flush()
        job = add_done_job(session, file_id=file_id, requested_by="test", now=NOW)
        session.get(ScanJobRow, job.id).result = json.dumps(answer)
        session.commit()
    return answer["lines"][0]


def choice(row, **values):
    return {"file_id": row["file_id"], "source_line_key": row["source_line_key"], **values}


def accept(client, receipt, row, **values):
    return _accept(client, receipt["id"], acceptance_id=str(uuid4()),
                   learning=choice(row, **values), article="CORRECT", quantity="150")


def preview(client, row, *, inn=INN, supplier="Поставщик", confirmed=False):
    response = client.post("/api/stock/receipts/learning-preview", json={
        **choice(row), "supplier_inn": inn, "supplier": supplier, "confirm_supplier_name": confirmed,
    })
    assert response.status_code == 200, response.text
    return response.json()["learning"]


def test_opt_in_and_partial_quantity_never_infers_pack_size(stand):
    client, sessions = stand
    row = source(sessions)
    receipt = _receipt(client, supplier="Поставщик", supplier_inn=INN)
    assert accept(client, receipt, row).status_code == 200
    assert preview(client, row) is None
    assert accept(client, receipt, row, action="remember").status_code == 200
    result = preview(client, row)
    assert result["article"] == "CORRECT" and result["quantity"] is None and result["pack_size"] == ""
    with sessions() as session:
        assert session.query(StockLotRow).count() == 2
        assert session.query(ReceiptLineConfirmationRow).count() == 2
        assert session.query(SupplierItemRuleRow).one().pack_size is None
        assert session.get(WarehouseReceiptFileRow, UUID(row["file_id"])).recognition == "raw evidence"


@pytest.mark.parametrize("changes", [{"inn": OTHER_INN}, {"code": "S-2"}, {"name": "Контакт чёрный 2 мм, уп. 100 шт"},
                                    {"code": ""}, {"name": "Контакт белый 3 мм, уп. 100 шт"}])
def test_supplier_code_and_name_are_all_required_to_match(stand, changes):
    client, sessions = stand
    row = source(sessions)
    receipt = _receipt(client, supplier_inn=INN)
    assert accept(client, receipt, row, action="remember").status_code == 200
    other = source(sessions, **changes)
    assert preview(client, other, inn=changes.get("inn", INN)) is None


@pytest.mark.parametrize("code", ["S-1", ""])
def test_long_cyrillic_names_with_shared_prefix_remain_distinct_rules(stand, code):
    client, sessions = stand
    receipt = _receipt(client, supplier_inn=INN)
    names = ["Я" * 1500 + suffix * 500 for suffix in ("Б", "В")]
    rule_ids = []
    for name in names:
        row = source(sessions, name=name, code=code)
        assert preview(client, row) is None
        response = accept(client, receipt, row, action="remember")
        assert response.status_code == 200, response.text
        result = preview(client, source(sessions, name=f"  {name.lower()}  ", code=code))
        rule_ids.append(result["rule_id"])
    assert len(set(rule_ids)) == 2
    with sessions() as session:
        rules = session.query(SupplierItemRuleRow).all()
        assert {rule.name_key for rule in rules} == {name.lower() for name in names}
        assert {rule.name_hash for rule in rules} == {
            sha256(name.lower().encode("utf-8")).hexdigest() for name in names
        }


def test_code_only_rule_hashes_empty_name_and_is_reused(stand):
    client, sessions = stand
    receipt = _receipt(client, supplier_inn=INN)
    row = source(sessions, name="")
    response = accept(client, receipt, row, action="remember")
    assert response.status_code == 200, response.text
    result = preview(client, source(sessions, name=""))
    with sessions() as session:
        rule = session.query(SupplierItemRuleRow).one()
        assert str(rule.id) == result["rule_id"]
        assert rule.name_key == ""
        assert rule.name_hash == sha256(b"").hexdigest()


def test_missing_inn_requires_name_confirmation_for_both_learning_and_application(stand):
    client, sessions = stand
    row = source(sessions, inn="")
    receipt = _receipt(client, supplier="Поставщик")
    assert accept(client, receipt, row, action="remember").status_code == 422
    assert accept(client, receipt, row, action="remember", confirm_supplier_name=True).status_code == 200
    assert preview(client, row, inn="") is None
    assert preview(client, row, inn="", confirmed=True)["article"] == "CORRECT"
    response = client.post("/api/stock/receipts", json={"warehouse": "ЦАР", "supplier_inn": "123"})
    assert response.status_code == 422


def test_pack_rules_use_invoice_once_and_base_unit_invoices_stay_base(stand):
    client, sessions = stand
    row = source(sessions)
    receipt = _receipt(client, supplier_inn=INN)
    assert accept(client, receipt, row, action="remember", pack_size="100", quantity_basis="packs").status_code == 200
    assert preview(client, row)["quantity"] == "200"
    assert preview(client, row)["quantity"] == "200"
    base = source(sessions, quantity="70", unit="шт", parsed="7000")
    assert preview(client, base)["quantity"] == "70"
    unknown = source(sessions, quantity="3", unit="кг")
    assert preview(client, unknown)["quantity"] is None
    assert preview(client, unknown)["warning"]


def test_explicit_base_quantity_correction_without_a_pack(stand):
    client, sessions = stand
    row = source(sessions, unit="шт")
    receipt = _receipt(client, supplier_inn=INN)
    assert accept(client, receipt, row, action="remember", quantity_basis="base", convert_quantity=True).status_code == 200
    assert preview(client, row)["quantity"] == "2"


def test_article_only_rule_never_relabels_unknown_packs_as_pieces(stand):
    client, sessions = stand
    row = source(sessions, unit="шт", parsed="10", quantity="10")
    receipt = _receipt(client, supplier_inn=INN)
    assert accept(client, receipt, row, action="remember").status_code == 200
    other = source(sessions, parsed="2", parsed_unit="упак")
    suggestion = preview(client, other)
    assert suggestion["warning"] and suggestion["quantity"] is None


def test_article_only_rule_keeps_same_learned_pack_unit_conservative(stand):
    client, sessions = stand
    row = source(sessions, parsed="2", parsed_unit="упак")
    receipt = _receipt(client, supplier_inn=INN)
    assert accept(client, receipt, row, action="remember").status_code == 200
    suggestion = preview(client, row)
    assert suggestion["warning"] and suggestion["quantity"] is None


def test_pack_and_base_with_identical_unit_need_manual_review(stand):
    client, sessions = stand
    row = source(sessions, quantity="2", unit="шт")
    receipt = _receipt(client, supplier_inn=INN)
    assert accept(client, receipt, row, action="remember", pack_size="100", quantity_basis="packs").status_code == 200
    suggestion = preview(client, row)
    assert suggestion["warning"] and suggestion["quantity"] is None


def test_rereading_file_keeps_distinct_evidence_and_ignores_legacy_answers(stand):
    client, sessions = stand
    row = source(sessions)
    file_id = UUID(row["file_id"])
    with sessions() as session:
        first = json.loads(session.query(ScanJobRow).one().result)
        # A later answer for the same file has a different printed quantity. It must not resolve to the first.
        second = learning.with_provenance({**first, "lines": [{**row["original"], "invoice_quantity": "3"}]})
        assert second["lines"][0]["source_line_key"] != row["source_line_key"]
        assert learning.with_provenance(second) == second
        job = add_done_job(session, file_id=file_id, requested_by="test", now=NOW)
        session.get(ScanJobRow, job.id).result = json.dumps(second)
        session.commit()
        for answer, quantity in ((first, "2"), (second, "3")):
            original, _ = learning.original_line(session, file_id, answer["lines"][0]["source_line_key"])
            assert original["invoice_quantity"] == quantity
        # Older job answers without original provenance do not become learning evidence retroactively.
        for job in session.query(ScanJobRow).all():
            job.result = json.dumps({**first, "lines": [row["original"]]})
        session.commit()
    receipt = _receipt(client, supplier_inn=INN)
    assert accept(client, receipt, row, action="remember").status_code == 422


def test_retry_is_one_lot_job_and_confirmation_and_changed_payload_conflicts(stand):
    client, sessions = stand
    row = source(sessions)
    receipt = _receipt(client, supplier_inn=INN)
    payload = {"acceptance_id": str(uuid4()), "learning": choice(row, action="remember")}
    first = _accept(client, receipt["id"], **payload)
    second = _accept(client, receipt["id"], **payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert _accept(client, receipt["id"], **payload, quantity="11").status_code == 409
    with sessions() as session:
        assert session.query(StockLotRow).count() == session.query(LabelJobRow).count() == session.query(ReceiptLineConfirmationRow).count() == 1
        assert session.query(SupplierItemRuleRow).one().version == 1


@pytest.mark.parametrize("fields", [{"pack_size": "0"}, {"pack_size": "NaN"}, {"pack_size": "1000000000000"},
                                   {"quantity_basis": "packs"}, {"quantity_basis": "guess"}])
def test_failed_acceptance_rolls_back_lot_label_and_learning(stand, fields):
    client, sessions = stand
    row = source(sessions)
    receipt = _receipt(client, supplier_inn=INN)
    assert accept(client, receipt, row, action="remember", **fields).status_code == 422
    with sessions() as session:
        for model in (StockLotRow, LabelJobRow, ReceiptLineConfirmationRow, SupplierItemRuleRow):
            assert session.query(model).count() == 0


def test_replace_and_deactivate_are_versioned_and_transactional(stand):
    client, sessions = stand
    row = source(sessions)
    receipt = _receipt(client, supplier_inn=INN)
    assert accept(client, receipt, row, action="remember").status_code == 200
    rule = preview(client, row)
    identity = {key: rule[key] for key in ("rule_id", "rule_version")}
    assert accept(client, receipt, row, action="remember", pack_size="50", quantity_basis="packs", **identity).status_code == 200
    assert accept(client, receipt, row, action="deactivate", **identity).status_code == 409
    rule = preview(client, row)
    assert rule["quantity"] == "100"
    assert accept(client, receipt, row, action="deactivate", rule_id=rule["rule_id"], rule_version=rule["rule_version"]).status_code == 200
    assert preview(client, row)["active"] is False


def test_other_supplier_cannot_accept_or_learn_the_source_rule(stand):
    client, sessions = stand
    row = source(sessions)
    wrong = _receipt(client, supplier_inn=OTHER_INN)
    assert accept(client, wrong, row, action="remember").status_code == 422


def test_paper_and_cached_readings_suggest_without_rewriting_the_invoice(stand, monkeypatch):
    from app.modules.warehouse import scan_recognition
    from app.modules.warehouse.receipt_scan import RecognisedUpd
    from app.modules.warehouse.scan_reading import ScanReading, reading_from_json, reading_json
    from app.modules.warehouse.upd_lines import UpdLine

    client, sessions = stand
    row = source(sessions)
    receipt = _receipt(client, supplier_inn=INN)
    assert accept(client, receipt, row, action="remember", quantity_basis="packs", pack_size="50").status_code == 200
    reading = ScanReading(RecognisedUpd(supplier="Поставщик", inn=INN, upd_number="1"), (UpdLine(
        article="RAW", name=row["name"], quantity=Decimal(200), unit="шт", invoice_quantity=Decimal(2),
        invoice_unit="упак", supplier_code="S-1", number="7",
    ),))
    kept = reading_json(reading)
    sources = scan_recognition.ScanSources(warehouse=lambda: SqlAlchemyWarehouseUnitOfWork(sessions))
    for current in (reading, reading_from_json(kept)):
        answer = scan_recognition.scan_answer(sources, f"/api/stock/receipts/files/{row['file_id']}", current)
        assert answer["lines"][0]["quantity"] == "200"  # receiving proposal still sees the existing parser result
        assert answer["lines"][0]["original"]["invoice_quantity"] == "2"
        assert answer["lines"][0]["original"]["number"] == "7"
        assert answer["lines"][0]["learning"]["quantity"] == "100"
    assert reading_json(reading) == kept
    source_key = answer["lines"][0]["source_line_key"]
    monkeypatch.setattr(scan_recognition, "known_articles", lambda *args: [("raw", "каталога")])
    answer = scan_recognition.scan_answer(sources, f"/api/stock/receipts/files/{row['file_id']}", reading)
    assert answer["lines"][0]["article"] == "raw"
    assert answer["lines"][0]["original"]["article"] == "RAW"
    assert answer["lines"][0]["source_line_key"] == source_key
    # A registry hit by document number/date cannot substitute another supplier's rule scope.
    monkeypatch.setattr(scan_recognition, "upd_document", lambda *a, **kw: {
        "inn": OTHER_INN, "supplier": "Другой", "upd_number": "1", "upd_date": None, "basis": "",
        "lines": [{"article": "OTHER", "name": row["name"], "quantity": "2", "unit": "шт"}],
    })
    answer = scan_recognition.scan_answer(sources, f"/api/stock/receipts/files/{row['file_id']}", reading)
    assert answer["recognised"]["inn"] == INN
    assert answer["lines"][0]["learning"]["supplier_key"] == f"inn:{INN}"


def test_unknown_or_tampered_source_and_oversized_rule_never_write_stock(stand):
    client, sessions = stand
    row = source(sessions)
    receipt = _receipt(client, supplier_inn=INN)
    bad = {**row, "source_line_key": "line:99"}
    assert accept(client, receipt, bad, action="remember").status_code == 422
    long_row = source(sessions, name="я" * 2001)
    assert accept(client, receipt, long_row, action="remember").status_code == 422
    with sessions() as session:
        assert session.query(StockLotRow).count() == 0


def test_concurrent_retry_uses_one_transaction_result(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'concurrent.db'}", connect_args={"timeout": 30})
    create_schema(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    actor = Actor(name="Кладовщик", department="Склад")
    uow = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)
    row = source(sessions)
    receipt = create_receipt(uow(), ReceiptHeader(warehouse="ЦАР", supplier_inn=INN), actor, NOW)
    cmd = AcceptLineCommand(receipt_id=receipt.id, item=ManualStockItem(article="A", quantity="2", warehouse="ЦАР", storage_location="2A1",
                                                                     name="", item_type="", purpose="Склад", unit="шт"),
                            labels=1, confirm_expected=True, actor=actor, now=NOW, acceptance_id=uuid4(),
                            learning=learning.LearningChoice(file_id=UUID(row["file_id"]), source_line_key=row["source_line_key"], action="remember"))
    barrier = Barrier(2)

    def run():
        barrier.wait(timeout=10)
        return accept_receipt_line(cmd, uow())

    try:
        with ThreadPoolExecutor(2) as pool:
            results = [future.result(timeout=40) for future in [pool.submit(run), pool.submit(run)]]
        assert results[0].lot.id == results[1].lot.id
        with sessions() as session:
            for model in (StockLotRow, LabelJobRow, ReceiptLineConfirmationRow, SupplierItemRuleRow):
                assert session.query(model).count() == 1
        split = accept_receipt_line(replace(cmd, acceptance_id=uuid4(), learning=replace(cmd.learning, action="none")), uow())
        assert split.lot.id != results[0].lot.id
    finally:
        engine.dispose()

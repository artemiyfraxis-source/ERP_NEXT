"""Supplier requests (SupplierRequest.gs): grouping, № ЗАКАЗА prefix-NNN, ДАТА ЗАКАЗА, the letter."""

import io
import sqlite3
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from pypdf import PdfReader

from app.main import app
from app.modules.procurement.application import (
    BuildSupplierRequestsCommand,
    SaveProcurementFactsCommand,
    SkippedLine,
    TakeProcurementInWorkCommand,
    build_supplier_requests,
    consume_procurement_requested,
    save_procurement_facts,
    supplier_order,
    supplier_order_document,
    take_procurement_in_work,
)
from app.modules.procurement.domain import (
    ProcurementFacts,
    ProcurementStatus,
    ProcurementValidationError,
    ReplacementDecision,
    SupplierRequestGroup,
    ProcurementTraceability,
    create_line_from_requested,
    date_supplier_segments,
    delivery_receiver,
    group_supplier_requests,
    next_order_number,
    order_number,
    order_prefix,
    position_paragraphs,
    supplier_reference,
    supplier_request_blockers,
    supplier_request_document_title,
    supplier_request_folder,
    supplier_request_letter,
)
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.procurement.request_pdf import render_request_pdf
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.procurement.test_api import message as api_message, procurement_api  # noqa: F401
from tests.procurement.test_application import FakeUnitOfWork, requested_message

BUYER = Actor(name="Петров", department="Закупки")
OTHER = Actor(name="Сидоров", department="Закупки")


# --- № ЗАКАЗА -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("number", "expected"),
    [(1, "PO-001"), (42, "PO-042"), (999, "PO-999"), (1000, "PO-1000"), (12345, "PO-12345")],
)
def test_order_number_pads_to_three_digits_and_not_beyond(number, expected):
    assert order_number("PO", number) == expected


def test_next_order_number_continues_from_the_highest_of_its_prefix():
    existing = ["PO-002", " po-010 ", "PO-7a", "XPO-500", "PO-", "ORD-900", None, "", "PO-009"]

    assert next_order_number(existing, "PO") == "PO-011"
    assert next_order_number([], "PO") == "PO-001"
    assert next_order_number(["PO-999"], "PO") == "PO-1000"
    assert next_order_number(["ORD-900"], "ORD") == "ORD-901"


def test_next_order_number_escapes_the_prefix():
    assert next_order_number(["P.O-005", "PXO-009"], "P.O") == "P.O-006"


@pytest.mark.parametrize("task_type", ["Закупка", "  ЗАКУПКА ", "закупка", "Ремонт", "", None])
def test_order_prefix_defaults_to_po(task_type):
    assert order_prefix(task_type) == "PO"


# --- Grouping --------------------------------------------------------------------------------------


def ready_line(suffix: int, *, buyer=BUYER, supplier="ООО Поставка", task_type="Закупка", **changes):
    line = create_line_from_requested(requested_message(suffix))
    line = replace(line, task_type=task_type).take_in_work(buyer).update_facts(
        ProcurementFacts(supplier=supplier, actual_quantity=Decimal("2"), actual_unit="шт"), buyer
    )
    assert line.status is ProcurementStatus.READY
    return replace(line, **changes)


def test_groups_own_ready_lines_by_supplier_and_task_type_in_line_order():
    a1 = ready_line(1, supplier="ООО А")
    b1 = ready_line(2, supplier="ООО Б")
    a2 = ready_line(3, supplier="  ООО А ")
    a_other_type = ready_line(4, supplier="ООО А", task_type="Ремонт")
    foreign = ready_line(5, buyer=OTHER)
    in_work = create_line_from_requested(requested_message(6)).take_in_work(BUYER)

    groups, other_buyer = group_supplier_requests([a1, b1, a2, a_other_type, foreign, in_work], BUYER)

    assert [(g.supplier, g.task_type, [line.id for line in g.lines]) for g in groups] == [
        ("ООО А", "Закупка", [a1.id, a2.id]),
        ("ООО Б", "Закупка", [b1.id]),
        ("ООО А", "Ремонт", [a_other_type.id]),
    ]
    assert other_buyer == 1


def test_supplier_name_is_case_sensitive_like_the_sheet():
    groups, _ = group_supplier_requests([ready_line(1, supplier="ООО А"), ready_line(2, supplier="ооо а")], BUYER)

    assert len(groups) == 2


def test_blockers_mirror_the_legacy_skips():
    good = ready_line(1)
    assert supplier_request_blockers(SupplierRequestGroup("ООО Поставка", "Закупка", (good,))) is None
    assert "без поставщика" in supplier_request_blockers(
        SupplierRequestGroup("(без поставщика)", "Закупка", (replace(good, supplier=None),))
    )
    assert supplier_request_blockers(
        SupplierRequestGroup("ООО Поставка", "Закупка", (replace(good, delivery_point=" "),))
    ) == "ООО Поставка: нет адреса отгрузки"
    assert "КОЛ-ВО/ЕД.ИЗМ (факт)" in supplier_request_blockers(
        SupplierRequestGroup("ООО Поставка", "Закупка", (good, replace(good, actual_unit=""))),
    )


# PR-8 — SupplierRequest.gs:235-239: an open ЗАМЕНА keeps the whole supplier + type order unformed.
@pytest.mark.parametrize("decision", [None, ReplacementDecision.FORBIDDEN])
def test_open_replacement_blocks_the_whole_group(decision):
    good = ready_line(1)
    open_line = ready_line(2).set_replacement(True, decision, BUYER)

    assert supplier_request_blockers(SupplierRequestGroup("ООО Поставка", "Закупка", (good, open_line))) == (
        "ООО Поставка (Закупка): есть незакрытая замена (нет решения «Разрешено») — не сформирован"
    )


def test_allowed_replacement_does_not_block_the_group():
    allowed = ready_line(1).set_replacement(True, ReplacementDecision.ALLOWED, BUYER)
    withdrawn = ready_line(2).set_replacement(True, None, BUYER).set_replacement(False, None, BUYER)

    assert supplier_request_blockers(SupplierRequestGroup("ООО Поставка", "Закупка", (allowed, withdrawn))) is None


def test_build_skips_the_group_with_open_replacement_and_forms_the_others():
    uow = FakeUnitOfWork()
    now = datetime(2026, 9, 22, 10, 0)
    lines = [consume_procurement_requested(requested_message(i), uow) for i in (1, 2, 3)]
    for line, supplier in zip(lines, ("ООО А", "ООО А", "ООО Б")):
        taken = take_procurement_in_work(TakeProcurementInWorkCommand(line_ids=(line.id,), actor=BUYER, now=now), uow)[0]
        save_procurement_facts(
            SaveProcurementFactsCommand(
                line_id=taken.id,
                facts=ProcurementFacts(supplier=supplier, actual_quantity=Decimal("2"), actual_unit="шт"),
                expected_version=taken.version,
                actor=BUYER,
                now=now,
            ),
            uow,
        )
    blocked = uow.procurement.get(lines[1].id)
    uow.procurement.add(blocked.set_replacement(True, None, BUYER))

    result = build_supplier_requests(BuildSupplierRequestsCommand(actor=BUYER, now=now), uow)

    assert [order.supplier for order in result.orders] == ["ООО Б"]
    reason = "ООО А (Закупка): есть незакрытая замена (нет решения «Разрешено») — не сформирован"
    assert result.skipped == [
        SkippedLine(lines[0].id, lines[0].legacy_id, reason), SkippedLine(lines[1].id, lines[1].legacy_id, reason),
    ]
    assert {uow.procurement.get(line.id).status for line in lines[:2]} == {ProcurementStatus.READY}


def test_ticked_lines_that_cannot_go_are_skipped_with_the_reason_and_the_rest_are_formed():
    """Owner, 05.10: ticking an incomplete line does not fail the call — it comes back in `skipped`."""
    uow = FakeUnitOfWork()
    now = datetime(2026, 9, 22, 10, 0)
    ready = make_ready(uow, 1, supplier="ООО А")
    foreign = make_ready(uow, 2, buyer=OTHER)
    incomplete = take_procurement_in_work(
        TakeProcurementInWorkCommand(line_ids=(consume_procurement_requested(requested_message(3), uow).id,),
                                     actor=BUYER, now=now),
        uow,
    )[0]
    fresh = consume_procurement_requested(requested_message(4), uow)
    missing = uuid4()

    result = build_supplier_requests(
        BuildSupplierRequestsCommand(
            actor=BUYER, now=now, line_ids=(incomplete.id, ready.id, fresh.id, missing, foreign.id),
        ),
        uow,
    )

    assert [(order.supplier, order.line_ids) for order in result.orders] == [("ООО А", (ready.id,))]
    assert result.skipped == [
        SkippedLine(incomplete.id, incomplete.legacy_id, "не заполнены: ПОСТАВЩИК"),
        SkippedLine(fresh.id, fresh.legacy_id, "строка не взята в работу"),
        SkippedLine(missing, "", "строка не найдена"),
        SkippedLine(foreign.id, foreign.legacy_id, "строка другого закупщика — запрос формирует тот, кто взял её в работу"),
    ]
    assert result.other_buyer == 1
    assert uow.procurement.get(incomplete.id).status is ProcurementStatus.IN_WORK

    commits = uow.commits
    nothing = build_supplier_requests(
        BuildSupplierRequestsCommand(actor=BUYER, now=now, line_ids=(ready.id, incomplete.id)), uow,
    )
    assert nothing.orders == [] and uow.commits == commits  # nothing to form is not an error, nothing written
    assert [item.reason for item in nothing.skipped] == [
        "запрос уже сформирован (PO-001), статус «Жду счет»", "не заполнены: ПОСТАВЩИК",
    ]
    with pytest.raises(ProcurementValidationError, match="Нет ваших строк"):
        build_supplier_requests(BuildSupplierRequestsCommand(actor=BUYER, now=now), uow)


def test_delivery_point_is_the_first_non_empty_of_the_group():
    first = ready_line(1, delivery_point="")
    second = ready_line(2, delivery_point="г. Раменское")

    assert SupplierRequestGroup("ООО Поставка", "Закупка", (first, second)).delivery_point == "г. Раменское"


# --- Letter ----------------------------------------------------------------------------------------


def test_reference_header_follows_supplier_ref():
    assert supplier_reference(date(2026, 9, 15), " RND.001 ", "PO-003") == "REQ# 15.09.2026 - MR#RND.001 PO-003"
    assert supplier_reference(None, "", "PO-003") == "REQ#  - MR# PO-003"


def test_position_paragraphs_prefer_site_name_and_mark_quality_document():
    plain = ready_line(1, link=None, quality_document="Нет")
    rich = ready_line(
        2, site_name="Разъём SMA-KE", link="https://shop.test/sma", quality_document="Да",
        actual_quantity=Decimal("2.500000"),
    )

    assert position_paragraphs([plain, rich]) == [
        "1. Наименование: Разъём",
        "\tКол-во: 2 шт",
        "2. Наименование: Разъём SMA-KE",
        "\tКол-во: 2,5 шт",
        "\tСсылка: https://shop.test/sma",
        "\tДокумент по качеству: требуется.",
    ]


def test_position_paragraphs_name_the_quality_document_type():
    typed = ready_line(1, quality_document="Да", trace=ProcurementTraceability(quality_document_type=" Паспорт "))

    assert position_paragraphs([typed])[-1] == "\tДокумент по качеству: требуется (Паспорт)."


def test_letter_carries_supplier_reference_positions_vat_and_address():
    group = SupplierRequestGroup("ООО Поставка", "Закупка", (ready_line(1),))

    letter = supplier_request_letter(group, "PO-001").split("\n")

    assert letter[0] == "ООО Поставка · REQ# 15.09.2026 - MR#RND.001 PO-001"
    assert "1. Наименование: Разъём" in letter
    assert "Работаем с НДС 22%. Карточка организации — во вложении." in letter
    assert letter[-2] == "Адрес доставки: г. Москва, ул. 6-я Радиальная, д. 17."
    assert letter[-1] == "Контактное лицо для доставки: Гусев Алексей, тел. +79259647705."


def test_delivery_contact_follows_the_point_or_the_default_receiver():
    ramenskoe = "г. Раменское, ул. 100-й Свирской Дивизии, д. 52"
    group = SupplierRequestGroup("ООО Поставка", "Закупка", (ready_line(1, delivery_point=ramenskoe),))

    assert supplier_request_letter(group, "PO-001").split("\n")[-2:] == [
        f"Адрес доставки: {ramenskoe}.",
        "Контактное лицо для доставки: Малышев Степан, тел. +7 977 846 3541.",
    ]
    # receivers[g.ship] — точный ключ; иначе 'default'.
    assert delivery_receiver("г. Тверь").name == "Гусев Алексей"
    assert delivery_receiver(ramenskoe.upper()).name == "Гусев Алексей"


# --- Document --------------------------------------------------------------------------------------


def test_document_folder_is_year_month_supplier():
    assert date_supplier_segments(date(2026, 9, 5), ' ООО "А/Б" ') == ["2026", "09 Сентябрь", "ООО А Б"]
    assert date_supplier_segments(date(2027, 1, 31), "  ") == ["2027", "01 Январь", "Без поставщика"]
    assert supplier_request_folder(date(2026, 9, 5), "ООО А") == "Запросы поставщикам/2026/09 Сентябрь/ООО А"
    assert supplier_request_document_title("PO-007", date(2026, 9, 5)) == "PO-007 от 05.09.2026"


def _pdf_text(content: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)


def test_document_is_a_pdf_with_embedded_cyrillic_font_and_the_letter_text():
    content = render_request_pdf("Запрос PO-001 от 05.09.2026", "ООО <А> · REQ#\n\n1. Наименование: Жгут\n\tКол-во: 2 шт")

    assert content.startswith(b"%PDF")
    assert b"DejaVu" in content
    text = _pdf_text(content)
    assert "Запрос PO-001 от 05.09.2026" in text
    assert "ООО <А> · REQ#" in text and "1. Наименование: Жгут" in text and "Кол-во: 2 шт" in text


# --- Line transition -------------------------------------------------------------------------------


def test_send_supplier_request_needs_a_ready_own_line_and_is_idempotent():
    line = ready_line(1)

    sent = line.send_supplier_request("PO-001", date(2026, 9, 15), "REQ# ref", BUYER)
    assert (sent.status, sent.order_number, sent.order_date) == (
        ProcurementStatus.AWAITING_INVOICE, "PO-001", date(2026, 9, 15),
    )
    assert sent.supplier_request_reference == "REQ# ref"
    assert sent.version == line.version + 1
    assert sent.send_supplier_request("PO-001", date(2026, 9, 16), "x", BUYER) is sent

    with pytest.raises(ProcurementValidationError, match="готовой строке"):
        sent.send_supplier_request("PO-002", date(2026, 9, 15), "x", BUYER)
    with pytest.raises(ProcurementValidationError, match="только свои"):
        line.send_supplier_request("PO-001", date(2026, 9, 15), "x", OTHER)
    with pytest.raises(ProcurementValidationError, match="№ заказа"):
        line.send_supplier_request("  ", date(2026, 9, 15), "x", BUYER)


# --- Use case --------------------------------------------------------------------------------------


def make_ready(uow, suffix, buyer=BUYER, supplier="ООО Поставка"):
    line = consume_procurement_requested(requested_message(suffix), uow)
    line = take_procurement_in_work(
        TakeProcurementInWorkCommand(line_ids=(line.id,), actor=buyer, now=datetime(2026, 9, 15, 11, 0)), uow
    )[0]
    return save_procurement_facts(
        SaveProcurementFactsCommand(
            line_id=line.id,
            facts=ProcurementFacts(supplier=supplier, actual_quantity=Decimal("2"), actual_unit="шт"),
            expected_version=line.version,
            actor=buyer,
            now=datetime(2026, 9, 15, 11, 10),
        ),
        uow,
    )


def test_build_issues_one_order_per_group_numbered_through_the_table():
    uow = FakeUnitOfWork()
    a1 = make_ready(uow, 1, supplier="ООО А")
    b1 = make_ready(uow, 2, supplier="ООО Б")
    a2 = make_ready(uow, 3, supplier="ООО А")
    foreign = make_ready(uow, 4, buyer=OTHER)
    commits = uow.commits
    # 21:30 UTC is already 16.09 in Moscow: ДАТА ЗАКАЗА is the Moscow day.
    now = datetime(2026, 9, 15, 21, 30, tzinfo=ZoneInfo("UTC"))

    result = build_supplier_requests(BuildSupplierRequestsCommand(actor=BUYER, now=now), uow)

    assert [(o.order_number, o.supplier, o.line_ids) for o in result.orders] == [
        ("PO-001", "ООО А", (a1.id, a2.id)),
        ("PO-002", "ООО Б", (b1.id,)),
    ]
    assert result.other_buyer == 1 and result.skipped == []
    assert uow.commits == commits + 1
    for line in (a1, a2, b1):
        stored = uow.procurement.get(line.id)
        assert stored.status is ProcurementStatus.AWAITING_INVOICE
        assert stored.order_date == date(2026, 9, 16)
    assert uow.procurement.get(a2.id).supplier_request_reference == "REQ# 15.09.2026 - MR#RND.001 PO-001"
    assert uow.procurement.get(foreign.id).status is ProcurementStatus.READY
    sent = [e for e in uow.audit.items if e.event_type == "procurement.supplier_request_sent"]
    assert [(e.aggregate_id, e.details["order_number"]) for e in sent] == [
        (a1.id, "PO-001"), (a2.id, "PO-001"), (b1.id, "PO-002"),
    ]
    assert "PO-001" in result.orders[0].letter and "2. Наименование" in result.orders[0].letter
    meta, content = supplier_order_document("PO-001", uow)
    assert (meta.file_name, meta.folder, meta.created_by) == (
        "PO-001 от 16.09.2026.pdf", "Запросы поставщикам/2026/09 Сентябрь/ООО А", "Петров",
    )
    assert meta.size == len(content) and content.startswith(b"%PDF") and meta.content_type == "application/pdf"
    assert "2. Наименование" in _pdf_text(content)
    assert result.orders[0].document == meta

    # Заказ, выданный до PDF (в хранилище HTML-письмо), отдаёт PDF, собранный из текста письма заказа.
    old_meta = replace(meta, order_number="PO-009", file_name="PO-009 от 16.09.2026.html", content_type="text/html; charset=utf-8")
    uow.procurement.orders["PO-009"] = replace(result.orders[0], order_number="PO-009", letter="ООО А · старый заказ\nДобрый день!")
    uow.procurement.documents["PO-009"] = (old_meta, "<!doctype html><p>Добрый день!</p>".encode("utf-8"))
    legacy_meta, legacy = supplier_order_document("PO-009", uow)
    assert legacy.startswith(b"%PDF") and legacy_meta.file_name == "PO-009 от 16.09.2026.pdf"
    assert legacy_meta.content_type == "application/pdf" and legacy_meta.size == len(legacy)
    assert "ООО А · старый заказ" in _pdf_text(legacy)
    del uow.procurement.orders["PO-009"], uow.procurement.documents["PO-009"]

    make_ready(uow, 5, supplier="ООО А")
    again = build_supplier_requests(BuildSupplierRequestsCommand(actor=BUYER, now=now), uow)
    assert [o.order_number for o in again.orders] == ["PO-003"]
    assert supplier_order("PO-003", uow).supplier == "ООО А"
    with pytest.raises(KeyError):
        supplier_order("PO-404", uow)


def test_build_without_own_ready_lines_names_the_other_buyers_lines():
    uow = FakeUnitOfWork()
    make_ready(uow, 1, buyer=OTHER)
    commits = uow.commits

    with pytest.raises(ProcurementValidationError) as error:
        build_supplier_requests(BuildSupplierRequestsCommand(actor=BUYER, now=datetime(2026, 9, 15, 12)), uow)

    assert str(error.value) == (
        "Нет ваших строк «Готов к запросу». Есть 1 строк(и) других закупщиков — "
        "их формирует тот, кто взял их в работу"
    )
    assert uow.commits == commits
    with pytest.raises(ProcurementValidationError, match="^Нет ваших строк «Готов к запросу»$"):
        build_supplier_requests(BuildSupplierRequestsCommand(actor=BUYER, now=datetime(2026, 9, 15, 12)), FakeUnitOfWork())


# --- HTTP + SQL ------------------------------------------------------------------------------------


def _ready_via_api(client, line, supplier):
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    return client.patch(
        f"/api/procurement/{line.id}/facts",
        json={
            "version": taken.json()["lines"][0]["version"],
            "supplier": supplier,
            "actual_quantity": "2",
            "actual_unit": "шт",
        },
    )


def test_supplier_requests_endpoint_persists_orders_and_serves_the_letter(procurement_api):  # noqa: F811
    client, line, sessions = procurement_api
    second = consume_procurement_requested(api_message(2), SqlAlchemyProcurementUnitOfWork(sessions))
    _ready_via_api(client, line, "ООО А")
    _ready_via_api(client, second, "ООО Б")

    response = client.post("/api/procurement/supplier-requests")

    assert response.status_code == 200
    body = response.json()
    # Both lines share created_at, so the group order follows the id tie-break; numbers stay dense.
    numbers = {o["supplier"]: o["order_number"] for o in body["orders"]}
    assert sorted(numbers.values()) == ["PO-001", "PO-002"]
    assert {o["order_date"] for o in body["orders"]} == {"2026-09-15"}
    assert {(l["id"], l["order_number"], l["status"]) for l in body["lines"]} == {
        (str(line.id), numbers["ООО А"], "Жду счет"), (str(second.id), numbers["ООО Б"], "Жду счет"),
    }
    order = client.get(f"/api/procurement/supplier-requests/{numbers['ООО Б']}")
    assert order.status_code == 200
    assert order.json()["order"]["line_ids"] == [str(second.id)]
    assert order.json()["order"]["letter"].startswith(f"ООО Б · REQ# 15.09.2026 - MR#RND.002 {numbers['ООО Б']}")
    assert client.get("/api/procurement/supplier-requests/PO-404").status_code == 404
    document = order.json()["order"]["document"]
    assert document["url"] == f"/api/procurement/supplier-requests/{numbers['ООО Б']}/document"
    assert document["file_name"] == f"{numbers['ООО Б']} от 15.09.2026.pdf"
    assert document["folder"] == "Запросы поставщикам/2026/09 Сентябрь/ООО Б"
    assert body["orders"][0]["document"]["url"].endswith("/document")
    file = client.get(document["url"])
    assert file.status_code == 200
    assert file.headers["content-type"] == "application/pdf"
    assert file.headers["content-disposition"] == f"inline; filename*=UTF-8''{quote('Запрос ' + numbers['ООО Б'] + '.pdf')}"
    assert file.content.startswith(b"%PDF")
    assert "Контактное лицо для доставки: Гусев Алексей, тел. +79259647705." in _pdf_text(file.content).replace("\n", " ")
    download = client.get(document["url"] + "?download=1")
    assert download.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")
    assert download.content == file.content
    assert client.get("/api/procurement/supplier-requests/PO-404/document").status_code == 404
    mine = client.get("/api/procurement/mine").json()["lines"]
    assert {l["order_number"] for l in mine} == {"PO-001", "PO-002"}
    assert {l["order_url"] for l in mine} == {
        "/api/procurement/supplier-requests/PO-001/document", "/api/procurement/supplier-requests/PO-002/document",
    }

    nothing = client.post("/api/procurement/supplier-requests")
    assert nothing.status_code == 422
    assert nothing.json()["detail"] == "Нет ваших строк «Готов к запросу»"


def test_other_buyer_cannot_form_requests_for_claimed_lines(procurement_api):  # noqa: F811
    client, line, _ = procurement_api
    _ready_via_api(client, line, "ООО А")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=OTHER, email="sidorov@example.test", roles=frozenset({"buyer"}), permissions={"procurement": "edit"},
    )

    response = client.post("/api/procurement/supplier-requests")

    assert response.status_code == 422
    assert "Есть 1 строк(и) других закупщиков" in response.json()["detail"]


def test_supplier_orders_migration_round_trip(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "procurement.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")

    def state():
        with sqlite3.connect(database_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(procurement_lines)")}
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {"order_number", "order_date"} <= columns, "procurement_supplier_orders" in tables

    command.upgrade(config, "0027_warehouse_location_as_is")
    assert state() == (False, False)
    command.upgrade(config, "0029_procurement_supplier_orders")
    assert state() == (True, True)
    command.downgrade(config, "0027_warehouse_location_as_is")
    assert state() == (False, False)


def test_supplier_order_documents_migration_backfills_issued_orders(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "procurement.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")

    command.upgrade(config, "0061_procurement_traceability")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO procurement_supplier_orders (order_number, order_date, supplier, task_type, delivery_point,"
            " buyer_id, buyer_name, created_at, letter) VALUES ('PO-001', '2026-09-15', 'ООО А', 'Закупка', 'x',"
            " '00000000000000000000000000000001', 'Петров', '2026-09-15 10:00:00', 'ООО А · REQ#\nДобрый день!')"
        )
    command.upgrade(config, "0065_procurement_supplier_order_documents")
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT file_name, folder, created_by, size, content FROM procurement_supplier_order_documents"
        ).fetchone()
    assert row[:3] == ("PO-001 от 15.09.2026.html", "Запросы поставщикам/2026/09 Сентябрь/ООО А", "Петров")
    assert row[3] == len(row[4]) and "<p>Добрый день!</p>" in row[4].decode("utf-8")

    command.downgrade(config, "0061_procurement_traceability")
    with sqlite3.connect(database_path) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "procurement_supplier_order_documents" not in tables

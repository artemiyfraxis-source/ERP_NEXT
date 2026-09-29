"""Supplier requests (SupplierRequest.gs): grouping, № ЗАКАЗА prefix-NNN, ДАТА ЗАКАЗА, the letter."""

import sqlite3
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config

from app.main import app
from app.modules.procurement.application import (
    BuildSupplierRequestsCommand,
    SaveProcurementFactsCommand,
    TakeProcurementInWorkCommand,
    build_supplier_requests,
    consume_procurement_requested,
    save_procurement_facts,
    supplier_order,
    take_procurement_in_work,
)
from app.modules.procurement.domain import (
    ProcurementFacts,
    ProcurementStatus,
    ProcurementValidationError,
    ReplacementDecision,
    SupplierRequestGroup,
    create_line_from_requested,
    group_supplier_requests,
    next_order_number,
    order_number,
    order_prefix,
    position_paragraphs,
    supplier_reference,
    supplier_request_blockers,
    supplier_request_letter,
)
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
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
    assert result.skipped == ["ООО А (Закупка): есть незакрытая замена (нет решения «Разрешено») — не сформирован"]
    assert {uow.procurement.get(line.id).status for line in lines[:2]} == {ProcurementStatus.READY}


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


def test_letter_carries_supplier_reference_positions_vat_and_address():
    group = SupplierRequestGroup("ООО Поставка", "Закупка", (ready_line(1),))

    letter = supplier_request_letter(group, "PO-001").split("\n")

    assert letter[0] == "ООО Поставка · REQ# 15.09.2026 - MR#RND.001 PO-001"
    assert "1. Наименование: Разъём" in letter
    assert "Работаем с НДС 22%. Карточка организации — во вложении." in letter
    assert letter[-1] == "Адрес доставки: г. Москва, ул. 6-я Радиальная, д. 17."


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
    mine = client.get("/api/procurement/mine").json()["lines"]
    assert {l["order_number"] for l in mine} == {"PO-001", "PO-002"}

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

"""RQ-5: import of positions from a calculation (SUPPLYMAP `Imports.gs`, `parseZpk_`)."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.requests.application import CreateRequestsCommand, create_requests
from app.modules.requests.domain import (
    CalcImport,
    CalcImportItem,
    ValidationError,
    build_import_drafts,
    parse_calc_number,
    parse_zpk,
    project_from_title,
    unit_for_type,
)
from app.main import app
from app.modules.requests.infrastructure import OutboxMessageRow, RequestRow
from tests.factories import Actor
from tests.requests.test_application import FakeUnitOfWork
from tests.requests.test_write_api import request_payload, write_api  # noqa: F401 — fixture

POINT = "г. Москва, ул. 6-я Радиальная, д. 17"

GRID = [
    ["Расчёт 591К.3", "", "", "", "", "", "", ""],
    ["", "", "", "", "", "", "", ""],
    ["№", "Тип", "Обозначение", "На проект", "Цена  без НДС", "Цена с НДС", "Ссылка", "Место поставки"],
    ["1", "Кабель", " МГТФ 0,35 ", "12,5", "1 000,50", "1 200,60", "https://x.test/a", "Россия"],
    ["2", "Разъём", "", "3", "10", "12", "", ""],
    ["3", "Разъём", "DB-9M", "4", "", "", "", "Китай"],
]


def item(name: str = "DB-9M", **overrides) -> CalcImportItem:
    values = {
        "item_type": "Разъём",
        "name": name,
        "quantity": Decimal("4"),
        "unit": "шт",
        "quality_document": "Нет",
        "planned_date": date(2026, 10, 20),
    }
    values.update(overrides)
    return CalcImportItem(**values)


def batch(*items: CalcImportItem, **overrides) -> CalcImport:
    values = {
        "project": "591К.3",
        "purpose": "Обеспечение производства",
        "delivery_point": POINT,
        "procurement_deadline": date(2026, 10, 1),
        "items": items or (item(),),
    }
    values.update(overrides)
    return CalcImport(**values)


def test_parse_zpk_finds_header_and_skips_rows_without_designation():
    items = parse_zpk(GRID)

    assert [entry.name for entry in items] == ["МГТФ 0,35", "DB-9M"]
    cable = items[0]
    assert (cable.item_type, cable.quantity, cable.price, cable.price_with_vat) == (
        "Кабель", "12,5", "1 000,50", "1 200,60",
    )
    assert (cable.link, cable.country, cable.unit) == ("https://x.test/a", "Россия", "м")
    assert items[1].unit == "шт"


def test_parse_zpk_without_header_returns_nothing():
    assert parse_zpk([["Тип", "Обозначение"], ["Разъём", "DB-9M"]]) == []
    assert parse_zpk([]) == []


def test_parse_zpk_tolerates_short_rows_and_missing_optional_columns():
    items = parse_zpk([["Обозначение", "Без НДС"], ["DB-9M"], ["DB-15F", "5"]])

    assert [(entry.name, entry.price, entry.quantity, entry.item_type) for entry in items] == [
        ("DB-9M", "", "", ""),
        ("DB-15F", "5", "", ""),
    ]


def test_calc_helpers_follow_supplymap():
    assert [unit_for_type(value) for value in ("Кабель", " Провод ", "ТУТ", "Шлейф", "Разъём", "")] == [
        "м", "м", "м", "м", "шт", "шт",
    ]
    assert project_from_title("Расчёт 591К.3") == "591К.3"
    assert project_from_title("591К.3 расчет") == "591К.3"
    assert parse_calc_number("1 000,50") == Decimal("1000.50")
    assert parse_calc_number("1 200,6") == Decimal("1200.6")
    assert parse_calc_number("12шт") == Decimal("12")
    assert parse_calc_number("") is None
    assert parse_calc_number("—") is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (batch(items=()), "Нет позиций для импорта"),
        (batch(procurement_deadline=None), "Не указан дедлайн — заявки не создаются"),
        (batch(delivery_point=" "), "Не выбрана точка отгрузки — заявки не создаются"),
        (batch(item("A"), item("B", unit=" ")), "Не заполнена Ед.изм: B"),
        (batch(item("A", quality_document="")), "Не выбран Документ по качеству (Да/Нет): A"),
        (batch(item("A", planned_date=None)), "Не указана план. дата поступления: A"),
    ],
)
def test_import_gates_block_the_whole_batch(payload, message):
    with pytest.raises(ValidationError, match=f"^{message}$".replace("(", r"\(").replace(")", r"\)")):
        build_import_drafts(payload)


def test_quality_document_is_asked_even_for_op_before_the_category_rule():
    with pytest.raises(ValidationError, match="Документ по качеству"):
        build_import_drafts(batch(item(category="ОП", quality_document="")))

    (draft,) = build_import_drafts(batch(item(category="ОП", quality_document="Нет")))
    assert draft.quality_document == "Да"


def test_import_draft_defaults_category_and_uses_designation_as_article():
    (draft,) = build_import_drafts(batch(item("DB-9M")))

    assert draft.category == "Общепром"
    assert draft.design_article == "DB-9M"
    assert draft.project_reference == "591К.3"
    assert draft.purpose == "Обеспечение производства"


def test_project_outside_the_number_format_does_not_block_the_import():
    # Imports.gs:72-79 only warns about the project; RQ-4's format gate must not break the batch.
    (draft,) = build_import_drafts(batch(item("DB-9M"), project="Расчёт насоса"))
    (latin,) = build_import_drafts(batch(item("DB-9M"), project="591K.3"))

    assert draft.project_reference is None
    assert latin.project_reference is None
    (canonical,) = build_import_drafts(batch(item("DB-9M"), project="591к.3"))
    assert canonical.project_reference == "591К.3"


def test_invalid_position_is_named_in_the_error():
    with pytest.raises(ValidationError, match="КОЛ-ВО должно быть больше нуля: DB-9M"):
        build_import_drafts(batch(item(quantity=Decimal("0"))))


def test_import_batch_gets_one_project_designation_and_one_priority():
    uow = FakeUnitOfWork()
    actor = Actor(name="Иванов", department="РНД")
    drafts = build_import_drafts(batch(item("A"), item("B"), item("C", planned_date=date(2026, 11, 1))))
    command = CreateRequestsCommand(
        creation_key="import-1",
        drafts=tuple(drafts),
        actor=actor,
        now=datetime(2026, 9, 28, 10, 0),
        designation_base="591К.3",
        source="import",
    )

    first = create_requests(command, uow)
    second = create_requests(
        CreateRequestsCommand(
            creation_key="import-2",
            drafts=tuple(build_import_drafts(batch(item("D")))),
            actor=actor,
            now=datetime(2026, 9, 28, 10, 5),
            designation_base="591К.3",
            source="import",
        ),
        uow,
    )

    assert {request.designation for request in first} == {"591К.3.001"}
    assert [request.designation for request in second] == ["591К.3.002"]
    assert {request.priority for request in first} == {"Очень срочно"}  # deadline in 3 days
    assert len({request.legacy_id for request in first + second}) == 4
    assert {event.details.get("source") for event in uow.audit.items} == {"import"}
    assert create_requests(command, uow) == first  # idempotent replay


def test_empty_project_falls_back_to_purpose_designation():
    uow = FakeUnitOfWork()
    requests = create_requests(
        CreateRequestsCommand(
            creation_key="import-1",
            drafts=tuple(build_import_drafts(batch(item("A"), project=""))),
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 28, 10, 0),
            designation_base="",
            source="import",
        ),
        uow,
    )

    assert requests[0].designation == "Обеспечение производства.001"
    assert requests[0].project_reference is None


def import_payload(**overrides) -> dict:
    values = {
        "project": "591К.3",
        "purpose": "Обеспечение производства",
        "delivery_point": POINT,
        "procurement_deadline": "2026-09-20",
        "items": [
            {"item_type": "Кабель", "name": "МГТФ 0,35", "quantity": "12.5", "unit": "м",
             "quality_document": "Нет", "planned_date": "2026-10-01", "price_with_vat": "1200.60"},
            {"item_type": "Разъём", "name": "DB-9M", "quantity": "4", "unit": "шт",
             "quality_document": "Да", "planned_date": "2026-10-02", "category": "ИП"},
        ],
    }
    values.update(overrides)
    return values


def test_import_endpoint_creates_batch_and_is_idempotent(write_api):  # noqa: F811
    client, _engine, sessions = write_api
    # the form keeps its own purpose numbering next to the import
    client.post("/api/requests", json={"requests": [request_payload()]}, headers={"Idempotency-Key": "form-1"})

    response = client.post("/api/requests/import", json=import_payload(), headers={"Idempotency-Key": "imp-1"})
    replay = client.post("/api/requests/import", json=import_payload(), headers={"Idempotency-Key": "imp-1"})

    assert response.status_code == 201, response.text
    created = response.json()["requests"]
    assert [row["designation"] for row in created] == ["591К.3.001", "591К.3.001"]
    assert {row["priority"] for row in created} == {"Срочно"}
    assert [row["project_reference"] for row in created] == ["591К.3", "591К.3"]
    assert replay.json() == response.json()
    with sessions() as session:
        rows = session.execute(select(RequestRow.name, RequestRow.design_article, RequestRow.category)).all()
    assert sorted(rows) == [("DB-9M", "DB-9M", "ИП"), ("МГТФ 0,35", "МГТФ 0,35", "Общепром"), ("Разъём", None, "Общепром")]
    # The manual import is not handed off by itself (owner, 06.10): «Новый», «В закупку» stays a button.
    assert {row["status"] for row in created} == {"Новый"}
    with sessions() as session:
        assert session.scalars(select(OutboxMessageRow)).all() == []


def test_import_endpoint_warns_about_an_unknown_project_but_creates_the_batch(write_api):  # noqa: F811
    client, _engine, _sessions = write_api

    class Registry:
        def open_projects(self):
            return ["2000К.1"]

    app.state.project_registry = Registry()
    try:
        closed = client.post("/api/requests/import", json=import_payload(), headers={"Idempotency-Key": "imp-w1"})
        free = client.post(
            "/api/requests/import", json=import_payload(project="Насос"), headers={"Idempotency-Key": "imp-w2"}
        )
        known = client.post(
            "/api/requests/import", json=import_payload(project="2000к"), headers={"Idempotency-Key": "imp-w3"}
        )
    finally:
        del app.state.project_registry

    assert closed.status_code == 201, closed.text
    assert closed.json()["warnings"] == [
        "Проект «591К.3» не найден в реестре открытых проектов — импорт не заблокирован (переходный период)"
    ]
    assert free.status_code == 201, free.text
    assert "не в формате" in free.json()["warnings"][0]
    assert {row["project_reference"] for row in free.json()["requests"]} == {None}
    assert known.json()["warnings"] == []
    assert {row["project_reference"] for row in known.json()["requests"]} == {"2000К.1"}


def test_import_endpoint_reports_the_gate_and_writes_nothing(write_api):  # noqa: F811
    client, _engine, sessions = write_api
    payload = import_payload()
    payload["items"][1]["planned_date"] = None

    response = client.post("/api/requests/import", json=payload, headers={"Idempotency-Key": "imp-2"})

    assert response.status_code == 422
    assert response.json()["detail"] == "Не указана план. дата поступления: DB-9M"
    with sessions() as session:
        assert session.scalars(select(RequestRow)).all() == []


def test_parse_endpoint_prefills_units_and_numbers(write_api):  # noqa: F811
    client, _engine, _sessions = write_api

    response = client.post("/api/requests/import/parse", json={"grid": GRID, "title": "Расчёт 591К.3"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project"] == "591К.3"
    first = body["items"][0]
    assert (first["name"], first["unit"], first["quantity"], first["price"], first["price_with_vat"]) == (
        "МГТФ 0,35", "м", "12.5", "1000.5", "1200.6",
    )
    assert body["items"][1]["price"] is None
    assert client.post(
        "/api/requests/import/parse", json={"grid": GRID, "project": " 48К.13 ", "title": "Расчёт 1"}
    ).json()["project"] == "48К.13"

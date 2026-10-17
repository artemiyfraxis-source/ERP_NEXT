"""«Снабжение» of a project: what its purchase chain still brings (В закупке) — workflows/project_kitset.supply_rows."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.modules.procurement.domain import ProcurementStatus, create_line_from_requested
from app.modules.requests.domain import RequestDraft, RequestStatus, create_request_batch
from app.shared.events import OutboxMessage
from app.workflows.project_kitset import supply_rows
from tests.factories import Actor


NOW = datetime(2026, 10, 5, 10, 0)
ACTOR = Actor(name="Технолог", department="Производство")


def _request(name, quantity, *, article=None, project="708К.4"):
    draft = RequestDraft(
        purpose="Обеспечение производства", delivery_point="г. Москва, ул. 6-я Радиальная, д. 17", name=name,
        item_type="Комплектующие", quantity=Decimal(quantity), unit="шт", quality_document="Нет",
        procurement_deadline=date(2026, 10, 20), planned_date=date(2026, 10, 20), project_reference=project,
        design_article=article,
    )
    return create_request_batch([draft], actor=ACTOR, now=NOW, existing_legacy_ids=[], existing_designations=[])[0]


def _line(request, quantity, status, *, revision="A", later=0):
    line = create_line_from_requested(OutboxMessage(
        id=uuid4(), topic="procurement.requested", idempotency_key=f"request:{request.id}:{revision}",
        created_at=NOW + timedelta(minutes=later),
        payload={
            "source_request_id": str(request.id), "legacy_id": f"{request.legacy_id}{revision}",
            "request_date": "2026-10-05", "initiator": "Технолог", "department": "Производство",
            "task_type": "Закупка", "designation": request.designation, "project_reference": request.project_reference,
            "priority": "Срочно", "delivery_point": request.delivery_point, "planned_date": "2026-10-20",
            "item_type": "Комплектующие", "design_article": request.design_article, "category": "Общепром",
            "calculated_name": request.name, "calculated_quantity": quantity, "calculated_unit": "шт",
        },
    ), revision=revision)
    return replace(line, status=status)


def test_open_quantity_counts_each_purchase_once_and_stops_at_acceptance():
    fresh = _request("Корпус Molex", "3")  # not handed over yet: its КОЛ-ВО is on its way
    handed = replace(_request("Контакт", "30", article="43030-0007"), status=RequestStatus.IN_WORK)
    retransferred = replace(_request("Провод", "12", article="UL1007 22AWG"), status=RequestStatus.IN_WORK)
    accepted = replace(_request("Клемма", "5", article="K-1"), status=RequestStatus.IN_WORK)
    cancelled = replace(_request("Гильза", "7", article="G-1"), status=RequestStatus.CANCELLED)
    other = _request("Чужое", "100", article="43030-0007", project="591К.3")
    procurement = [
        replace(_line(handed, "30", ProcurementStatus.IN_WORK), stock_covered=Decimal("8")),  # 8 «Со склада»
        _line(retransferred, "12", ProcurementStatus.IN_WORK, revision="A"),
        _line(retransferred, "10", ProcurementStatus.READY, revision="B", later=5),  # the newest revision counts
        _line(accepted, "5", ProcurementStatus.DONE),
        _line(other, "100", ProcurementStatus.IN_WORK),
    ]
    rows = supply_rows("708К.4", [fresh, handed, retransferred, accepted, cancelled, other], procurement)

    open_by = {}
    for row in rows:
        open_by[(row.article or row.name, row.where)] = open_by.get((row.article or row.name, row.where), 0) + row.open_quantity
    assert open_by == {
        ("Корпус Molex", "Запрос"): Decimal("3"),
        ("43030-0007", "Запрос"): 0, ("43030-0007", "Закупка"): Decimal("22"),
        ("UL1007 22AWG", "Запрос"): 0, ("UL1007 22AWG", "Закупка"): Decimal("10"),
        ("K-1", "Запрос"): 0, ("K-1", "Приёмка"): 0,
        ("G-1", "Запрос"): 0,
    }
    assert next(row for row in rows if row.article == "" and row.where == "Запрос").name == "Корпус Molex"


def test_line_closed_from_stock_without_grn_is_not_accepted():
    """«Есть на складе, без ГРН» closes the line «Готово» without money, but nothing was accepted onto a lot."""
    covered = replace(_request("Контакт", "30", article="43030-0007"), status=RequestStatus.IN_WORK)
    bought = replace(_request("Клемма", "5", article="K-1"), status=RequestStatus.IN_WORK)
    lotless = replace(_line(covered, "30", ProcurementStatus.DONE), stock_covered=Decimal("30"))
    paid = replace(_line(bought, "5", ProcurementStatus.DONE), paid_at=NOW)  # bought, paid and accepted

    rows = {row.article: row for row in supply_rows(
        "708К.4", [covered, bought], [lotless, paid], lotless=[lotless.id, paid.id],
    ) if row.where != "Запрос"}

    assert (rows["43030-0007"].where, rows["43030-0007"].status, rows["43030-0007"].received) == (
        "Закупка", "Готово · без ГРН", False,
    )
    assert rows["43030-0007"].open_quantity == 0  # nothing left to buy
    assert (rows["K-1"].where, rows["K-1"].status, rows["K-1"].received) == ("Приёмка", "Готово", True)
    plain = {  # the cover was bound to a ГРН since: the goods are on a lot
        row.article: row for row in supply_rows("708К.4", [covered], [lotless]) if row.where != "Запрос"
    }
    assert (plain["43030-0007"].where, plain["43030-0007"].received) == ("Приёмка", True)

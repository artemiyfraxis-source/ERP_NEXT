"""Deleting a project takes its tails that never moved; anything with movement stays (решено агентом 04.10)."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.fulfillment.application import BindLotCommand, CreateFulfillmentCommand, bind_lot, create_fulfillment
from app.modules.fulfillment.domain import FulfillmentLine, FulfillmentStatus
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.procurement.application import (
    ConcurrentProcurementUpdate,
    TakeProcurementInWorkCommand,
    dispatch_next_procurement_requested,
    take_procurement_in_work,
)
from app.modules.procurement.domain import ProcurementStatus
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.procurement.money import FactMoney
from app.modules.production.application import LaunchProductionCommand, launch_production
from app.modules.production.domain import OperationShare, ProductionOperation
from app.modules.production.infrastructure import ProductionBoxRow, SqlAlchemyProductionUnitOfWork
from app.modules.requests.application import CreateRequestsCommand, HandoffRequestsCommand, create_requests, handoff_requests
from app.modules.requests.domain import RequestDraft, RequestStatus
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.shared.clock import moscow_now, moscow_today
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows import project_kitset
from app.workflows import purge_project_tails as cli
from app.workflows.project_kitset import purge_project_tails
from tests.factories import Actor
from tests.techcards.calc_sheets import FakeSheets

NOW = datetime(2026, 10, 4, 10, 0)
ACTOR = Actor(name="Технолог", department="Производство")
BUYER = Actor(name="Закупщик", department="Закупки")
OPERATOR = UUID("10000000-0000-0000-0000-0000000000a1")
STATE = (
    "production_uow_factory", "techcards_uow_factory", "fulfillment_uow_factory",
    "shipping_uow_factory", "uow_factory", "procurement_uow_factory",
)


@pytest.fixture()
def erp():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    state = SimpleNamespace(
        sessions=sessions,
        production_uow_factory=lambda: SqlAlchemyProductionUnitOfWork(sessions),
        techcards_uow_factory=lambda: SqlAlchemyTechcardsUnitOfWork(sessions),
        fulfillment_uow_factory=lambda: SqlAlchemyFulfillmentUnitOfWork(sessions),
        shipping_uow_factory=lambda: SqlAlchemyShippingUnitOfWork(sessions),
        uow_factory=lambda: SqlAlchemyUnitOfWork(sessions),
        procurement_uow_factory=lambda: SqlAlchemyProcurementUnitOfWork(sessions),
    )
    with sessions() as session:
        session.add(IdentityUserRow(
            id=OPERATOR, login="fitter", email="fitter@example.test", name="Монтажник",
            department="Производство", role="staff", site="ЦАР", active=True,
        ))
        session.commit()
    lot = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000009", package="", name="Корпус Molex 43025-0400",
        item_type="Комплектующие", purpose="Склад", unit="шт", quantity=Decimal("50"),
        storage_location="1F4", source_receiving_ids=("receiving-tails",), article="43025-0400",
    )
    with state.fulfillment_uow_factory() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    state.lot = lot
    yield state
    engine.dispose()


def _kit(state, purpose, *, reserve=Decimal("0"), status=None, issued=False):
    line = FulfillmentLine.create(article="43025-0400", name="Корпус Molex", unit="шт", requirement=Decimal("10"))
    request = create_fulfillment(CreateFulfillmentCommand(
        idempotency_key=f"tails:{uuid4()}", purpose=purpose, deadline=date(2026, 10, 20),
        lines=(line,), actor=ACTOR, now=NOW,
    ), state.fulfillment_uow_factory())
    if reserve:
        request = bind_lot(BindLotCommand(
            request_id=request.id, expected_version=request.version, line_id=line.id, lot_id=state.lot.id,
            actor=ACTOR, now=NOW,
        ), state.fulfillment_uow_factory())
    if status is not None or issued:
        with state.fulfillment_uow_factory() as uow:
            stored = uow.fulfillment.get(request.id)
            lines = tuple(replace(item, issued=Decimal("1"), reserved=Decimal("0")) for item in stored.lines) if issued else stored.lines
            uow.fulfillment.add(replace(stored, status=status or stored.status, lines=lines))
            uow.commit()
    return request


def _task(state, project, item, *, started=False, handed_to="", box=False, fulfillment_request_id=None):
    operation = ProductionOperation.create(
        position=1, code="OP-01", stage="Разделка", description="Подготовить кабель",
        seconds_per_unit=Decimal("60"), setup_seconds=Decimal("0"), quantity=2,
    )
    task = launch_production(LaunchProductionCommand(
        idempotency_key=f"tails:{uuid4()}", project=project, item=item, name="Жгут", quantity=2,
        fulfillment_request_id=fulfillment_request_id, deadline=date(2026, 10, 20),
        operations=(operation,), actor=ACTOR, now=NOW,
    ), state.production_uow_factory())
    if started or handed_to:
        share = OperationShare(OPERATOR, None, NOW, NOW, NOW if started else None, None)
        with state.production_uow_factory() as uow:
            stored = uow.production.get(task.id)
            operations = (stored.operations[0].with_shares((share,), "Мастер"),) if started else stored.operations
            uow.production.add(replace(stored.with_operations(operations), handed_to=handed_to))
            uow.commit()
    if box:
        with state.sessions() as session:
            session.add(ProductionBoxRow(
                id=uuid4(), number=None, version=1, task_id=task.id, operation_id=task.operations[0].id, item=item,
                operation_position=1, operator_id=OPERATOR, quantity=Decimal("1"), consumed=Decimal("0"),
                reserved=Decimal("0"), status="Открыта", created_at=NOW, return_reason="",
            ))
            session.commit()
    return task


def _request(state, *, reference=None, base=None, status=None):
    """`reference` — № ПРОЕКТА of the form; `base` — import from a calculation, designation «base.NNN»."""
    [request] = create_requests(CreateRequestsCommand(
        creation_key=f"tails:{uuid4()}", drafts=(RequestDraft(
            purpose="Обеспечение производства", delivery_point="г. Москва, ул. 6-я Радиальная, д. 17", name=f"Провод {uuid4().hex[:4]}",
            item_type="Комплектующие", quantity=Decimal("5"), unit="м", quality_document="Нет",
            procurement_deadline=date(2026, 10, 10), planned_date=date(2026, 10, 10), project_reference=reference,
        ),), actor=ACTOR, now=NOW, designation_base=base,
    ), state.uow_factory())
    if status is not None:
        with state.uow_factory() as uow:
            uow.requests.add(replace(request, status=status, version=request.version + 1))
            uow.commit()
    return request


def _handed(state, request, *, taken=True, retransfer=False, **changes):
    """«В закупку»: the request's line appears in procurement, the buyer takes it «В работе», then `changes`
    (invoice, payment, order, …) are written onto it as later stages would. Returns the stored line."""
    handoff_requests(HandoffRequestsCommand(request_ids=(request.id,), actor=BUYER, now=NOW, retransfer=retransfer),
                     state.uow_factory())
    line = dispatch_next_procurement_requested(NOW, state.procurement_uow_factory())
    if taken:
        [line] = take_procurement_in_work(TakeProcurementInWorkCommand(line_ids=(line.id,), actor=BUYER, now=NOW),
                                          state.procurement_uow_factory())
    if changes:
        line = replace(line, version=line.version + 1, **changes)
        with state.procurement_uow_factory() as uow:
            uow.procurement.add(line)
            uow.commit()
    return line


def _lines(state):
    with state.procurement_uow_factory() as uow:
        return {line.id: line for line in uow.procurement.list()}


def _seed(state, project="708К.4"):
    """Every kind of tail of `project`, one with movement each, and the same of a neighbour «…1» project."""
    seeded = SimpleNamespace(
        kit_free=_kit(state, f"{project} · КБ1 · Жгут питания", reserve=Decimal("10")),
        kit_cancelled=_kit(state, f"{project} · КБ4 · Жгут", status=FulfillmentStatus.CANCELLED),
        kit_issued=_kit(state, f"{project} · КБ2 · Жгут", issued=True, status=FulfillmentStatus.ISSUED),
        kit_picked=_kit(state, f"{project} · КБ3 · Жгут", status=FulfillmentStatus.COMPLETE),
        kit_neighbour=_kit(state, f"{project}1 · КБ1 · Жгут"),
    )
    seeded.kit_of_worked = _kit(state, f"{project} · КБ5 · Жгут")
    seeded.task_free = _task(state, project, "КБ1")
    seeded.task_started = _task(state, project, "КБ2", started=True, fulfillment_request_id=seeded.kit_of_worked.id)
    seeded.task_boxed = _task(state, project, "КБ3", box=True)
    seeded.task_handed = _task(state, project, "КБ4", handed_to="ЖУК")
    seeded.task_neighbour = _task(state, f"{project}1", "КБ1")
    seeded.req_designation = _request(state, base=project)
    seeded.req_reference = _request(state, reference=project)
    seeded.req_in_work = _request(state, reference=project, status=RequestStatus.IN_WORK)
    seeded.req_ordered = _request(state, base=project)
    seeded.line_ordered = _handed(state, seeded.req_ordered, status=ProcurementStatus.AWAITING_INVOICE,
                                  order_number="PO-0007", supplier_request_reference="PO-0007")
    seeded.req_neighbour = _request(state, reference=f"{project}1")
    seeded.req_neighbour_import = _request(state, base=f"{project}1")
    return seeded


def _purge(state, project="708К.4", apply=True):
    return purge_project_tails(
        project, ACTOR, NOW, fulfillment_uow=state.fulfillment_uow_factory,
        production_uow=state.production_uow_factory, requests_uow=state.uow_factory,
        procurement_uow=state.procurement_uow_factory, apply=apply,
    )


def _left(state):
    with state.fulfillment_uow_factory() as uow:
        kits = {request.id for request in uow.fulfillment.list(limit=100)}
        lot = uow.warehouse.get(state.lot.id)
    with state.production_uow_factory() as uow:
        tasks = {task.id for task in uow.production.list(limit=100)}
    with state.uow_factory() as uow:
        requests = {request.id for request in uow.requests.recent(100)}
    return kits, tasks, requests, lot


def test_purge_deletes_untouched_tails_releases_reserve_and_keeps_moved(erp):
    seeded = _seed(erp)
    assert _left(erp)[3].reserved == Decimal("10")

    tails = _purge(erp)

    kits, tasks, requests, lot = _left(erp)
    assert kits == {seeded.kit_issued.id, seeded.kit_picked.id, seeded.kit_neighbour.id, seeded.kit_of_worked.id}
    assert tasks == {seeded.task_started.id, seeded.task_boxed.id, seeded.task_handed.id, seeded.task_neighbour.id}
    assert requests == {seeded.req_in_work.id, seeded.req_ordered.id, seeded.req_neighbour.id, seeded.req_neighbour_import.id}
    assert set(_lines(erp)) == {seeded.line_ordered.id}
    assert lot.reserved == Decimal("0")  # the free kit's reserve went back to the lot
    payload = tails.payload()
    assert {section: payload[section] for section in ("fulfillment", "production", "requests", "procurement")} == {
        "fulfillment": {"deleted": 2, "kept": 3}, "production": {"deleted": 1, "kept": 3},
        "requests": {"deleted": 2, "kept": 2}, "procurement": {"deleted": 0, "kept": 0},
    }
    assert payload["freed"] == 1
    reasons = {(entry["section"], entry["reason"]) for entry in payload["kept"]}
    assert ("fulfillment", "по нему уже выдано со склада") in reasons
    assert ("production", "отдано в ЖУК, там работу в ERP не отмечают") in reasons
    assert ("requests", f"в закупке {seeded.line_ordered.legacy_id} «Жду счет»: запрос поставщику отправлен (PO-0007)") in reasons
    assert ("requests", "передана в закупку, строки закупки ещё нет — удалите позже") in reasons
    assert ("fulfillment", "к нему привязано задание с отмеченной работой") in reasons
    assert tails.message.startswith(
        "Удалено: 2 запроса на комплектацию, 1 производственное задание, 2 заявки на закупку; резерв снят по 1 строке. "
        f"Осталось: 2 производственных задания — отмечена работа ({seeded.task_started.number} · КБ2, "
        f"{seeded.task_boxed.number} · КБ3)"
    )
    assert f"1 запрос на комплектацию — по нему уже выдано со склада ({seeded.kit_issued.number} · " in tails.message

    with erp.sessions() as session:
        events = [row.event_type for row in session.scalars(select(AuditEventRow))]
    assert events.count("production.task_deleted") == 1
    assert events.count("fulfillment.deleted") == 2
    assert events.count("request.deleted") == 2
    assert events.count("procurement.line_deleted") == 0

    again = _purge(erp)
    assert again.deleted == [] and again.message.startswith("Удалять нечего")


def test_dry_run_writes_nothing_and_names_the_same_rows(erp):
    _seed(erp)
    before = _left(erp)

    preview = _purge(erp, apply=False)

    assert _left(erp) == before
    assert preview.message.startswith("Будет удалено: 2 запроса на комплектацию, 1 производственное задание")
    applied = _purge(erp)
    assert [entry.label for entry in applied.deleted] == [entry.label for entry in preview.deleted]
    assert preview.freed == applied.freed == 1


def test_matching_is_exact_after_normalisation(erp):
    neighbour = _task(erp, "719К.21", "КБ1")
    latin = _task(erp, "719K.2", "КБ2")  # Latin K is the same project
    short = _kit(erp, "719К · КБ1 · Жгут")  # «719К» ≡ «719К.1», not «719К.2»
    kit = _kit(erp, "719К.2 · КБ1 · Жгут")

    tails = _purge(erp, "719К.2")

    kits, tasks, _requests, _lot = _left(erp)
    assert neighbour.id in tasks and latin.id not in tasks
    assert short.id in kits and kit.id not in kits
    assert [entry.section for entry in tails.deleted] == ["production", "fulfillment"]


def test_owner_case_project_with_dash_number(erp):
    kits = [_kit(erp, f"П2510-1.2 · КБ{index} · Жгут") for index in (1, 2)]
    requests = [_request(erp, reference="П2510-1.2"), _request(erp, base="П2510-1.2"), _request(erp, base="П2510-1.2")]
    other = _request(erp, reference="П2510-1.21")

    tails = _purge(erp, "П2510-1.2")

    left_kits, _tasks, left_requests, _lot = _left(erp)
    assert not left_kits & {kit.id for kit in kits}
    assert left_requests == {other.id}
    assert tails.message == "Удалено: 2 запроса на комплектацию, 3 заявки на закупку."
    assert len(requests) == 3


# ── Заявки в закупке: строки без движения уходят вместе с заявкой (owner 04.10) ──


def _label(line, status):
    return f"{line.legacy_id} · {line.calculated_name} («{status}»)"


def test_untouched_procurement_lines_go_with_their_request(erp):
    """Owner 04.10: П2510-1.2 deleted, its SM-…-01/-02 stayed «В работе» in procurement although nothing was bought."""
    taken = _request(erp, reference="П2510-1.2")
    fresh = _request(erp, base="П2510-1.2")
    resent = _request(erp, base="П2510-1.2")
    other = _request(erp, reference="П2510-1.21")
    line_taken = _handed(erp, taken, supplier="ООО Ромашка")
    line_ready = _handed(erp, fresh, status=ProcurementStatus.READY, supplier="ООО Ромашка", actual_quantity=Decimal("5"), actual_unit="м")
    line_first = _handed(erp, resent)
    line_second = _handed(erp, resent, taken=False, retransfer=True, status=ProcurementStatus.APPROVAL)
    line_other = _handed(erp, other)

    tails = _purge(erp, "П2510-1.2")

    assert set(_lines(erp)) == {line_other.id}
    assert _left(erp)[2] == {other.id}
    assert tails.kept == []
    assert tails.message == "Удалено: 3 заявки на закупку, 4 строки закупки."
    assert {entry.label for entry in tails.deleted if entry.section == "procurement"} == {
        _label(line_taken, "В работе"), _label(line_ready, "Готов к запросу"),
        _label(line_first, "В работе"), _label(line_second, "Согласование"),
    }
    with erp.sessions() as session:
        deleted = [row for row in session.scalars(select(AuditEventRow)) if row.event_type == "procurement.line_deleted"]
    assert {row.aggregate_id for row in deleted} == {line_taken.id, line_ready.id, line_first.id, line_second.id}
    assert {row.details["project"] for row in deleted} == {"П2510-1.2"}
    assert {row.details["reason"] for row in deleted} == {"project_deleted"}


def test_lines_with_order_invoice_money_payment_or_reserve_keep_the_request_whole(erp):
    project = "П2510-1.2"
    paid, invoiced, quoted, reserved, mixed, plain = (_request(erp, reference=project) for _ in range(6))
    line_paid = _handed(erp, paid, status=ProcurementStatus.IN_TRANSIT, paid_at=NOW, order_number="PO-0001",
                        invoice_url="https://example.test/invoice-1.pdf")
    line_invoiced = _handed(erp, invoiced, status=ProcurementStatus.TO_PAY, order_number="PO-0002",
                            invoice_url="https://example.test/invoice-2.pdf")
    line_quoted = _handed(erp, quoted, status=ProcurementStatus.APPROVAL,
                          money=FactMoney(currency="RUB", price=Decimal("10"), sum=Decimal("50")))
    line_reserved = _handed(erp, reserved, status=ProcurementStatus.READY, stock_covered=Decimal("2"))
    line_mixed_free = _handed(erp, mixed)
    line_mixed_paid = _handed(erp, mixed, retransfer=True, status=ProcurementStatus.QUARANTINE, paid_at=NOW)
    line_plain = _handed(erp, plain)

    tails = _purge(erp, project)

    assert set(_lines(erp)) == {
        line_paid.id, line_invoiced.id, line_quoted.id, line_reserved.id, line_mixed_free.id, line_mixed_paid.id,
    }
    assert line_plain.id not in _lines(erp)
    assert _left(erp)[2] == {paid.id, invoiced.id, quoted.id, reserved.id, mixed.id}
    assert {(entry.label, entry.reason) for entry in tails.kept} == {
        (f"{paid.legacy_id} · {paid.name}", f"в закупке {line_paid.legacy_id} «В пути»: оплачена"),
        (f"{invoiced.legacy_id} · {invoiced.name}", f"в закупке {line_invoiced.legacy_id} «В оплату»: внесён счёт"),
        (f"{quoted.legacy_id} · {quoted.name}", f"в закупке {line_quoted.legacy_id} «Согласование»: внесены суммы КП"),
        (f"{reserved.legacy_id} · {reserved.name}",
         f"в закупке {line_reserved.legacy_id} «Готов к запросу»: держит резерв «Со склада» — снимите его в «Закупке»"),
        (f"{mixed.legacy_id} · {mixed.name}", f"в закупке {line_mixed_paid.legacy_id} «Карантин»: оплачена"),
    }
    assert [entry.label for entry in tails.deleted] == [_label(line_plain, "В работе"), f"{plain.legacy_id} · {plain.name}"]


def test_lines_changed_during_the_purge_keep_their_request(erp, monkeypatch):
    request = _request(erp, reference="П2510-1.2")
    line = _handed(erp, request)
    real = project_kitset.remove_untouched_request_lines

    def racing(command, uow):
        if command.apply:
            raise ConcurrentProcurementUpdate(f"Строку {line.legacy_id} изменили во время удаления")
        return real(command, uow)

    monkeypatch.setattr(project_kitset, "remove_untouched_request_lines", racing)

    tails = _purge(erp, "П2510-1.2")

    assert tails.deleted == []
    assert [(entry.label, entry.reason) for entry in tails.kept] == [
        (f"{request.legacy_id} · {request.name}", "строки закупки изменили во время удаления — повторите"),
    ]
    assert line.id in _lines(erp) and request.id in _left(erp)[2]


def test_a_line_that_moved_after_the_read_is_not_deleted(erp):
    request = _request(erp, reference="П2510-1.2")
    line = _handed(erp, request)

    with erp.procurement_uow_factory() as uow:
        assert uow.procurement.delete(line.id, line.version - 1) is False
        uow.commit()

    assert line.id in _lines(erp)


# ── Удаление проекта с экрана: каскад ───────────────────────────────────


def _principal():
    return Principal(
        actor=Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name="Технолог", department="Производство"),
        email="engineer@example.test", roles=frozenset({"engineer"}), permissions={"projects": "edit"},
    )


@pytest.fixture()
def client(erp):
    for name in STATE:
        setattr(app.state, name, getattr(erp, name))
    app.state.kitset_sheets_reader = FakeSheets()
    app.dependency_overrides[authenticated_principal] = _principal
    app.dependency_overrides[moscow_now] = lambda: NOW
    app.dependency_overrides[moscow_today] = lambda: NOW.date()
    yield TestClient(app)
    del app.state.kitset_sheets_reader
    for override in (authenticated_principal, moscow_now, moscow_today):
        app.dependency_overrides.pop(override, None)
    for name in STATE:
        delattr(app.state, name)


def test_deleting_the_project_card_takes_its_untouched_tails(client, erp):
    created = client.post("/api/techcards/kitsets/load", json={"url": "https://docs.google.com/spreadsheets/d/1AbC-calc_id/edit"})
    assert created.status_code == 200, created.text
    seeded = _seed(erp)
    in_work = _request(erp, reference="708К.4")
    line = _handed(erp, in_work)

    deleted = client.delete("/api/techcards/kitsets/708К.4")

    assert deleted.status_code == 200, deleted.text
    res = deleted.json()["res"]
    assert res["tails_error"] == ""
    tails = res["tails"]
    assert (tails["fulfillment"], tails["production"], tails["requests"], tails["procurement"]) == (
        {"deleted": 2, "kept": 3}, {"deleted": 1, "kept": 3}, {"deleted": 3, "kept": 2}, {"deleted": 1, "kept": 0},
    )
    assert "3 заявки на закупку, 1 строка закупки" in tails["message"] and "Осталось:" in tails["message"]
    kits, tasks, requests, _lot = _left(erp)
    assert seeded.kit_free.id not in kits and seeded.task_free.id not in tasks and seeded.req_designation.id not in requests
    assert seeded.kit_neighbour.id in kits and seeded.task_neighbour.id in tasks and seeded.req_neighbour.id in requests
    assert in_work.id not in requests and set(_lines(erp)) == {seeded.line_ordered.id}
    assert line.id not in _lines(erp)


# ── Команда purge_project_tails для уже удалённых проектов ──────────────


def test_cli_dry_run_by_default_then_yes_deletes(erp):
    seeded = _seed(erp, "719К.2")
    before = _left(erp)

    text, code = cli.run(["719К.2"], erp, ACTOR, NOW, apply=False)

    assert code == 0
    assert _left(erp) == before
    assert "Проект 719К.2: Будет удалено: 2 запроса на комплектацию" in text
    assert f"    удалить: {seeded.task_free.number} · КБ1" in text
    assert "    осталось: " in text and "Проверка без записи" in text

    text, code = cli.run(["719К.2"], erp, ACTOR, NOW, apply=True)

    assert code == 0 and "Проект 719К.2: Удалено:" in text and "Проверка без записи" not in text
    kits, tasks, requests, _lot = _left(erp)
    assert seeded.kit_free.id not in kits and seeded.task_free.id not in tasks and seeded.req_designation.id not in requests
    assert seeded.task_neighbour.id in tasks


def test_cli_dry_run_lists_requests_with_their_procurement_lines_then_yes_deletes(erp):
    request = _request(erp, reference="П2510-1.2")
    line = _handed(erp, request)
    paid_request = _request(erp, reference="П2510-1.2")
    paid = _handed(erp, paid_request, status=ProcurementStatus.IN_TRANSIT, paid_at=NOW, order_number="PO-0003")
    before = (_left(erp), set(_lines(erp)))

    text, code = cli.run(["П2510-1.2"], erp, ACTOR, NOW, apply=False)

    assert code == 0
    assert (_left(erp), set(_lines(erp))) == before
    assert text == "\n".join([
        "Проект П2510-1.2: Будет удалено: 1 заявка на закупку, 1 строка закупки. Осталось: 1 заявка на закупку — "
        f"в закупке {paid.legacy_id} «В пути»: оплачена ({paid_request.legacy_id} · {paid_request.name}).",
        "  Заявки на закупку:",
        f"    удалить: {request.legacy_id} · {request.name}",
        f"    осталось: {paid_request.legacy_id} · {paid_request.name} — в закупке {paid.legacy_id} «В пути»: оплачена",
        "  Строки закупки этих заявок:",
        f"    удалить: {line.legacy_id} · {line.calculated_name} («В работе»)",
        "Проверка без записи. Удалить — та же команда с --yes.",
    ])

    text, code = cli.run(["П2510-1.2"], erp, ACTOR, NOW, apply=True)

    assert code == 0 and f"    удалено: {line.legacy_id} · {line.calculated_name} («В работе»)" in text
    assert set(_lines(erp)) == {paid.id} and _left(erp)[2] == {paid_request.id}


def test_cli_refuses_a_project_that_still_has_a_card(client, erp):
    client.post("/api/techcards/kitsets/load", json={"url": "https://docs.google.com/spreadsheets/d/1AbC-calc_id/edit"})
    seeded = _seed(erp)
    before = _left(erp)

    text, code = cli.run(["708К.4"], erp, ACTOR, NOW, apply=True)

    assert code == 1
    assert "карточка ещё есть" in text
    assert _left(erp) == before
    assert seeded.task_free.id in before[1]


def test_cli_main_needs_database_url(monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    assert cli.main(["719К.2"]) == 2
    assert "ERP_DATABASE_URL" in capsys.readouterr().err


def test_cli_main_over_a_database_file(tmp_path, monkeypatch, capsys):
    url = f"sqlite:///{(tmp_path / 'erp.db').as_posix()}"
    engine = create_engine(url)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    state = SimpleNamespace(
        fulfillment_uow_factory=lambda: SqlAlchemyFulfillmentUnitOfWork(sessions),
        uow_factory=lambda: SqlAlchemyUnitOfWork(sessions),
    )
    kit = _kit(state, "719К.2 · КБ1 · Жгут")
    engine.dispose()
    monkeypatch.setenv("ERP_DATABASE_URL", url)

    assert cli.main(["719К.2"]) == 0
    assert "Будет удалено: 1 запрос на комплектацию" in capsys.readouterr().out
    assert cli.main(["719К.2", "--yes"]) == 0
    assert "Удалено: 1 запрос на комплектацию" in capsys.readouterr().out
    engine = create_engine(url)
    with SqlAlchemyFulfillmentUnitOfWork(sessionmaker(bind=engine, class_=Session)) as uow:
        assert uow.fulfillment.get(kit.id) is None
    engine.dispose()

"""RC-6: возврат поставщику уже принятой позиции (SupplierReturn.gs)."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.receiving.application import (
    InspectReceivingCommand,
    PlaceReceivingCommand,
    SupplierReturnCommand,
    SupplierReturnItem,
    consume_receiving_expected,
    inspect_receiving,
    place_receiving,
    return_to_supplier,
)
from app.modules.receiving.domain import (
    InspectionInput,
    ReceivingStatus,
    ReceivingValidationError,
    SupplierReturnMode,
    supplier_return_basis,
)
from app.modules.receiving.infrastructure import SqlAlchemyReceivingUnitOfWork
from app.modules.warehouse.application import (
    PoisonWarehouseMessage,
    dispatch_next_supplier_return,
    dispatch_next_warehouse_received,
)
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.shared.clock import moscow_now
from app.shared.events import OutboxMessage
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import OutboxMessageRow, create_schema
from app.workflows.receiving_stock import WarehouseStockBalanceLookup
from tests.factories import Actor

RECEIVER = Actor(name="Приёмщик", department="ОТК")
NOW = datetime(2026, 9, 21, 11, 0)
DEFECT = "Механическое повреждение / брак"


def expected_message(suffix: int = 1) -> OutboxMessage:
    return OutboxMessage(
        id=UUID(f"10000000-0000-0000-0000-{suffix:012d}"),
        topic="receiving.expected",
        idempotency_key=f"procurement:{suffix}:receiving:v1",
        created_at=datetime(2026, 9, 21, 10, 0),
        payload={
            "procurement_line_id": f"20000000-0000-0000-0000-{suffix:012d}",
            "legacy_id": f"SM-20260921-{suffix:02d}",
            "warehouse": "ЦАР",
            "article": "CON-001",
            "name": f"Разъём {suffix}",
            "item_type": "Комплектующие",
            "purpose": "FRX.001",
            "goal": "",
            "source_link": "",
            "supplier": "ООО Поставка",
            "ordered_quantity": "10",
            "unit": "шт",
            "quality_document_required": False,
        },
    )


def inspection(fact: str = "10") -> InspectionInput:
    return InspectionInput(
        fact_quantity=Decimal(fact),
        document_quantity=Decimal("10"),
        ordered_quantity=Decimal("10"),
        has_shipping_document=True,
        quality_document_required=False,
        quality_document_present=False,
        expiry_date=date(2027, 1, 1),
        inspection_date=NOW.date(),
        shipping_document_url="https://files.test/upd.pdf",
    )


@pytest.fixture()
def erp():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    receiving = lambda: SqlAlchemyReceivingUnitOfWork(sessions)
    warehouse = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)
    yield receiving, warehouse, WarehouseStockBalanceLookup(warehouse)
    engine.dispose()


def accept(receiving, suffix: int = 1, *, fact: str = "10"):
    line = consume_receiving_expected(expected_message(suffix), receiving())
    return inspect_receiving(
        InspectReceivingCommand(
            line_id=line.id, expected_version=line.version, inspection=inspection(fact),
            actor=RECEIVER, now=NOW,
        ),
        receiving(),
    )


def accept_and_stock(receiving, warehouse, suffix: int = 1):
    accepted = accept(receiving, suffix)
    placed = place_receiving(
        PlaceReceivingCommand(
            line_id=accepted.id, expected_version=accepted.version, storage_location="1F4",
            actor=RECEIVER, now=NOW,
        ),
        receiving(),
    )
    lot = dispatch_next_warehouse_received(NOW, warehouse())
    assert lot is not None and lot.balance == Decimal("10")
    return placed, lot


def command(*lines, mode=SupplierReturnMode.CLOSE, quantity=None, reason=DEFECT, **extra):
    return SupplierReturnCommand(
        items=tuple(
            SupplierReturnItem(line_id=line.id, expected_version=line.version, quantity=quantity)
            for line in lines
        ),
        reason=reason,
        mode=mode,
        actor=RECEIVER,
        now=NOW,
        **extra,
    )


def test_basis_joins_reason_claim_and_comment_like_supplymap():
    assert supplier_return_basis(DEFECT, "П-17", "сколы") == (
        "Возврат поставщику: Механическое повреждение / брак · претензия П-17 · сколы"
    )
    assert supplier_return_basis(DEFECT) == "Возврат поставщику: Механическое повреждение / брак"


def test_close_writes_off_the_returned_quantity_and_ends_the_order(erp):
    receiving, warehouse, stock = erp
    placed, lot = accept_and_stock(receiving, warehouse)

    result = return_to_supplier(
        command(placed, quantity=Decimal("4"), claim="П-17", comment="сколы"), receiving(), stock
    )

    returned = result.lines[0]
    assert returned.status is ReceivingStatus.RETURN
    assert returned.disposition == "Возврат поставщику"
    assert returned.nonconformity_description == (
        "Возврат поставщику: Механическое повреждение / брак · претензия П-17 · сколы"
    )
    assert result.writeoffs == 1 and result.notes == ()
    assert "Статус приёмки → «Возврат»" in result.message

    written = dispatch_next_supplier_return(NOW, warehouse())
    assert written is not None
    assert written.balance == Decimal("6")
    with warehouse() as uow:
        [entry] = uow.writeoffs.for_lot(lot.id)
    assert entry.kind == "supplier_return"
    assert entry.quantity == Decimal("4")
    assert entry.reason.startswith("Возврат поставщику: ")
    assert entry.purpose == "ООО Поставка"
    assert entry.author == "Приёмщик"
    assert entry.remaining_after == Decimal("6")
    # Posted once: the message is spent.
    assert dispatch_next_supplier_return(NOW, warehouse()) is None


@pytest.mark.parametrize("quantity, expected_balance, expected_return", [
    (Decimal("4"), Decimal("6"), Decimal("4")),
    (None, Decimal("0"), Decimal("10")),
])
@pytest.mark.parametrize("return_before_receipt", [False, True])
def test_supplier_return_converges_when_return_and_receipt_are_processed_in_either_order(
    erp, quantity, expected_balance, expected_return, return_before_receipt,
):
    receiving, warehouse, stock = erp
    accepted = accept(receiving)
    placed = place_receiving(
        PlaceReceivingCommand(
            line_id=accepted.id, expected_version=accepted.version, storage_location="1F4",
            actor=RECEIVER, now=NOW,
        ),
        receiving(),
    )
    if not return_before_receipt:
        dispatch_next_warehouse_received(NOW, warehouse())

    returned = return_to_supplier(
        command(placed, quantity=quantity), receiving(), stock
    ).lines[0]
    if return_before_receipt:
        # The source receipt is still pending: leave the return due later, without consuming attempts.
        assert dispatch_next_supplier_return(NOW, warehouse()) is None
        with warehouse() as uow:
            receipt = uow.outbox.by_idempotency_key(placed.warehouse_event_key)
            assert receipt is not None
            [deferred] = uow.session.scalars(select(OutboxMessageRow).where(
                OutboxMessageRow.idempotency_key
                == f"receiving:{returned.id}:supplier-return:{returned.supplier_returns}"
            ))
            assert deferred.attempt_count == 0
            assert deferred.dead_lettered_at is None
            assert deferred.next_attempt_at == NOW + timedelta(seconds=2)
        dispatch_next_warehouse_received(NOW, warehouse())
        dispatch_time = NOW + timedelta(seconds=2)
    else:
        dispatch_time = NOW

    written = dispatch_next_supplier_return(dispatch_time, warehouse())
    assert written is not None
    assert written.balance == expected_balance

    # Simulate broker redelivery after a lost acknowledgement. The stock and journal must remain single-posted.
    key = f"receiving:{returned.id}:supplier-return:{returned.supplier_returns}"
    with warehouse() as uow:
        uow.session.execute(update(OutboxMessageRow).where(
            OutboxMessageRow.idempotency_key == key
        ).values(published_at=None, next_attempt_at=None))
        uow.commit()
    replayed = dispatch_next_supplier_return(dispatch_time, warehouse())
    assert replayed is not None and replayed.balance == expected_balance
    with warehouse() as uow:
        final_lot = uow.warehouse.by_key("ЦАР", placed.grn, placed.package)
        entries = uow.writeoffs.for_lot(final_lot.id)
    assert final_lot.inbound == Decimal("10")
    assert final_lot.balance == expected_balance
    [entry] = [row for row in entries if row.kind == "supplier_return"]
    assert entry.quantity == expected_return
    assert entry.remaining_after == expected_balance
    assert len([row for row in entries if row.kind == "supplier_return"]) == 1


def test_supplier_return_does_not_wait_forever_for_a_dead_lettered_receipt(erp):
    receiving, warehouse, stock = erp
    accepted = accept(receiving)
    placed = place_receiving(
        PlaceReceivingCommand(
            line_id=accepted.id, expected_version=accepted.version, storage_location="1F4",
            actor=RECEIVER, now=NOW,
        ),
        receiving(),
    )
    returned = return_to_supplier(command(placed), receiving(), stock).lines[0]
    return_key = f"receiving:{returned.id}:supplier-return:{returned.supplier_returns}"
    with warehouse() as uow:
        uow.session.execute(update(OutboxMessageRow).where(
            OutboxMessageRow.idempotency_key == placed.warehouse_event_key
        ).values(dead_lettered_at=NOW))
        uow.commit()

    with pytest.raises(PoisonWarehouseMessage):
        dispatch_next_supplier_return(NOW, warehouse())
    with warehouse() as uow:
        [message] = uow.session.scalars(select(OutboxMessageRow).where(
            OutboxMessageRow.idempotency_key == return_key
        ))
        assert message.attempt_count == 1


def test_deferred_supplier_return_does_not_block_a_later_ready_return(erp):
    receiving, warehouse, stock = erp
    ready, ready_lot = accept_and_stock(receiving, warehouse, suffix=2)
    accepted = accept(receiving, suffix=1)
    pending = place_receiving(
        PlaceReceivingCommand(
            line_id=accepted.id, expected_version=accepted.version, storage_location="1F4",
            actor=RECEIVER, now=NOW,
        ),
        receiving(),
    )

    deferred = return_to_supplier(command(pending, quantity=Decimal("1")), receiving(), stock).lines[0]
    ready_command = replace(command(ready, quantity=Decimal("2")), now=NOW + timedelta(seconds=1))
    posted = return_to_supplier(ready_command, receiving(), stock).lines[0]

    assert dispatch_next_supplier_return(NOW, warehouse()) is None
    second = dispatch_next_supplier_return(NOW + timedelta(seconds=1), warehouse())
    assert second is not None and second.id == ready_lot.id and second.balance == Decimal("8")
    with warehouse() as uow:
        deferred_message = uow.outbox.by_idempotency_key(
            f"receiving:{deferred.id}:supplier-return:{deferred.supplier_returns}"
        )
        posted_message = uow.outbox.by_idempotency_key(
            f"receiving:{posted.id}:supplier-return:{posted.supplier_returns}"
        )
        assert deferred_message is not None
        assert posted_message is not None
        assert uow.outbox.is_open("warehouse.supplier_return_requested", deferred_message.idempotency_key)
        assert not uow.outbox.is_open("warehouse.supplier_return_requested", posted_message.idempotency_key)


def test_empty_quantity_returns_the_whole_fact(erp):
    receiving, warehouse, stock = erp
    placed, _ = accept_and_stock(receiving, warehouse)

    return_to_supplier(command(placed), receiving(), stock)

    assert dispatch_next_supplier_return(NOW, warehouse()).balance == Decimal("0")


def test_wait_reopens_the_line_and_the_replacement_reaches_stock_again(erp):
    receiving, warehouse, stock = erp
    placed, lot = accept_and_stock(receiving, warehouse)

    result = return_to_supplier(command(placed, mode=SupplierReturnMode.WAIT), receiving(), stock)
    waiting = result.lines[0]
    assert waiting.status is ReceivingStatus.IN_TRANSIT
    assert waiting.warehouse_event_emitted is False
    assert waiting.grn == placed.grn
    assert dispatch_next_supplier_return(NOW, warehouse()).balance == Decimal("0")

    replacement = inspect_receiving(
        InspectReceivingCommand(
            line_id=waiting.id, expected_version=waiting.version, inspection=inspection(),
            actor=RECEIVER, now=NOW,
        ),
        receiving(),
    )
    assert replacement.status is ReceivingStatus.ACCEPTED
    assert replacement.warehouse_event_emitted is True  # the place was kept, so it goes at once
    restocked = dispatch_next_warehouse_received(NOW, warehouse())
    assert restocked is not None and restocked.id == lot.id
    assert restocked.balance == Decimal("10")


def test_second_return_of_the_same_line_has_its_own_writeoff(erp):
    receiving, warehouse, stock = erp
    placed, _ = accept_and_stock(receiving, warehouse)
    waiting = return_to_supplier(
        command(placed, mode=SupplierReturnMode.WAIT), receiving(), stock
    ).lines[0]
    dispatch_next_supplier_return(NOW, warehouse())
    replacement = inspect_receiving(
        InspectReceivingCommand(
            line_id=waiting.id, expected_version=waiting.version, inspection=inspection(),
            actor=RECEIVER, now=NOW,
        ),
        receiving(),
    )
    dispatch_next_warehouse_received(NOW, warehouse())

    closed = return_to_supplier(command(replacement, quantity=Decimal("3")), receiving(), stock).lines[0]

    assert closed.supplier_returns == 2
    assert closed.nonconformity_description.count("Возврат поставщику:") == 2
    assert dispatch_next_supplier_return(NOW, warehouse()).balance == Decimal("7")


def test_not_placed_line_is_returned_in_receiving_only(erp):
    receiving, warehouse, stock = erp
    accepted = accept(receiving)

    result = return_to_supplier(command(accepted), receiving(), stock)

    assert result.lines[0].status is ReceivingStatus.RETURN
    assert result.writeoffs == 0
    assert result.notes == (
        f"Разъём 1: на складе партии {accepted.grn} нет (ещё не разложена) — снимать нечего",
    )
    assert dispatch_next_supplier_return(NOW, warehouse()) is None


def test_quantity_above_stock_is_not_written_off(erp):
    receiving, warehouse, stock = erp
    placed, _ = accept_and_stock(receiving, warehouse)

    result = return_to_supplier(command(placed, quantity=Decimal("11")), receiving(), stock)

    assert result.writeoffs == 0
    assert result.notes == ("Разъём 1: склад — больше остатка (есть 10), списание не поставлено",)
    assert dispatch_next_supplier_return(NOW, warehouse()) is None


def test_reserved_stock_is_not_available_for_a_return(erp):
    receiving, warehouse, stock = erp
    placed, lot = accept_and_stock(receiving, warehouse)
    with warehouse() as uow:
        uow.warehouse.add(uow.warehouse.get(lot.id).reserve(Decimal("8")))
        uow.commit()

    result = return_to_supplier(command(placed, quantity=Decimal("3")), receiving(), stock)

    assert result.writeoffs == 0
    assert "есть 2" in result.notes[0]


def test_lot_gone_by_the_time_of_posting_is_a_poison_message(erp):
    receiving, warehouse, stock = erp
    placed, lot = accept_and_stock(receiving, warehouse)
    return_to_supplier(command(placed, quantity=Decimal("5")), receiving(), stock)
    with warehouse() as uow:
        uow.warehouse.add(uow.warehouse.get(lot.id).write_off(Decimal("8")))
        uow.commit()

    with pytest.raises(PoisonWarehouseMessage):
        dispatch_next_supplier_return(NOW, warehouse())
    with warehouse() as uow:
        assert uow.warehouse.get(lot.id).balance == Decimal("2")


def test_batch_skips_lines_no_longer_accepted_and_reports_them(erp):
    receiving, warehouse, stock = erp
    first, _ = accept_and_stock(receiving, warehouse, 1)
    in_transit = consume_receiving_expected(expected_message(2), receiving())

    result = return_to_supplier(command(first, in_transit), receiving(), stock)

    assert [line.id for line in result.lines] == [first.id]
    assert result.notes == ("Разъём 2: статус уже «В пути» — пропущена",)


def test_nothing_returned_is_an_error(erp):
    receiving, _, stock = erp
    in_transit = consume_receiving_expected(expected_message(), receiving())

    with pytest.raises(ReceivingValidationError, match="Ни одна позиция не оформлена"):
        return_to_supplier(command(in_transit), receiving(), stock)


def test_unknown_reason_is_rejected(erp):
    receiving, _, stock = erp
    accepted = accept(receiving)

    with pytest.raises(ReceivingValidationError, match="Неизвестная причина"):
        return_to_supplier(command(accepted, reason="Просто так"), receiving(), stock)
    with receiving() as uow:
        assert uow.receiving.get(accepted.id).status is ReceivingStatus.ACCEPTED


def test_returned_line_is_closed_for_new_inspection(erp):
    receiving, _, stock = erp
    accepted = accept(receiving)
    returned = return_to_supplier(command(accepted), receiving(), stock).lines[0]

    with pytest.raises(ReceivingValidationError, match="Возвращённая поставка"):
        inspect_receiving(
            InspectReceivingCommand(
                line_id=returned.id, expected_version=returned.version, inspection=inspection(),
                actor=RECEIVER, now=NOW,
            ),
            receiving(),
        )


@pytest.fixture()
def api(erp):
    receiving, warehouse, stock = erp
    app.state.receiving_uow_factory = receiving
    app.state.receiving_stock_lookup = stock
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=RECEIVER,
        email="receiver@example.test",
        roles=frozenset({"receiver"}),
        permissions={"receiving": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), receiving, warehouse
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.receiving_uow_factory
    del app.state.receiving_stock_lookup


def test_api_lists_accepted_lines_with_stock_and_returns_them(api):
    client, receiving, warehouse = api
    placed, _ = accept_and_stock(receiving, warehouse, 1)
    consume_receiving_expected(expected_message(2), receiving())

    listing = client.get("/api/receiving/supplier-return")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert [line["legacy_id"] for line in body["lines"]] == ["SM-20260921-01"]
    assert body["lines"][0]["stock_warehouse"] == "ЦАР"
    assert body["lines"][0]["stock_balance"] == "10"
    assert body["reasons"][0] == DEFECT
    assert set(body["modes"]) == {"wait", "close"}

    response = client.post(
        "/api/receiving/supplier-return",
        json={
            "items": [{"id": str(placed.id), "version": placed.version, "quantity": "4"}],
            "reason": DEFECT,
            "mode": "close",
            "claim": "П-17",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["lines"][0]["status"] == "Возврат"
    assert response.json()["writeoffs"] == 1
    assert "претензия П-17" in response.json()["lines"][0]["nonconformity_description"]


def test_api_stale_version_is_a_conflict_and_bad_mode_is_rejected(api):
    client, receiving, warehouse = api
    placed, _ = accept_and_stock(receiving, warehouse)

    stale = client.post(
        "/api/receiving/supplier-return",
        json={"items": [{"id": str(placed.id), "version": 1}], "reason": DEFECT, "mode": "close"},
    )
    bad_mode = client.post(
        "/api/receiving/supplier-return",
        json={"items": [{"id": str(placed.id), "version": placed.version}], "reason": DEFECT, "mode": "later"},
    )

    assert stale.status_code == 409
    assert bad_mode.status_code == 422


def test_api_requires_receiving_edit(api):
    client, _, _ = api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Закупщик", department="Закупки"),
        email="buyer@example.test",
        roles=frozenset({"buyer"}),
        permissions={"receiving": "view"},
    )

    assert client.get("/api/receiving/supplier-return").status_code == 403

"""Возврат заявок на комплектацию, отменённых ручным переносом этапа (prod 663К.1, 16.11.2026; владелец 17.11.2026)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.fulfillment.application import (
    BindLotCommand,
    CreateFulfillmentCommand,
    UnbindLotCommand,
    VersionedFulfillmentCommand,
    bind_lot,
    cancel_fulfillment,
    cancel_request_in,
    create_fulfillment,
    unbind_lot,
)
from app.modules.fulfillment.domain import FulfillmentLine, FulfillmentStatus
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows import fulfillment_reopen as reopen
from app.workflows import project_kitset as flow
from tests.factories import Actor

ACTOR = Actor(name="Кладовщик", department="Склад")
PROJECT = "663К.1"
KBS = ("КБ7", "КБ10")
CASCADE = {"project": PROJECT, "kb": "КБ7", "stage": "В работе", "reason": "пропущено вручную: Новый → В работе"}


def at(day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute)


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    engine.dispose()


def _uow(sessions):
    return SqlAlchemyFulfillmentUnitOfWork(sessions)


def _lot(sessions, grn: str, article: str, quantity: str, unit: str = "шт") -> StockLot:
    lot = StockLot.create(
        warehouse="ЦАР", grn=grn, package="", name=f"Деталь {article}", item_type="Комплектующие", purpose="Склад",
        unit=unit, quantity=Decimal(quantity), storage_location="1F4", source_receiving_ids=(f"r-{grn}",),
        article=article,
    )
    with _uow(sessions) as uow:
        uow.warehouse.add(lot)
        uow.commit()
    return lot


def _create(sessions, kb: str, *lines: tuple[str, str, str]):
    return create_fulfillment(CreateFulfillmentCommand(
        idempotency_key=f"{kb}-{uuid4()}", purpose=f"{PROJECT} · {kb} · Жгут", deadline=at(20).date(),
        lines=tuple(FulfillmentLine.create(article=article, name=f"Деталь {article}", unit=unit,
                                           requirement=Decimal(quantity)) for article, quantity, unit in lines),
        actor=ACTOR, now=at(12),
    ), _uow(sessions))


def _get(sessions, request_id: UUID):
    with _uow(sessions) as uow:
        return uow.fulfillment.get(request_id)


def _bind(sessions, request_id: UUID, line_id: UUID, lot: StockLot, when: datetime):
    current = _get(sessions, request_id)
    return bind_lot(BindLotCommand(request_id, current.version, ACTOR, when, line_id, lot.id), _uow(sessions))


def _cascade_cancel(sessions, request_id: UUID, when: datetime, details=CASCADE):
    """What the 12.10 rule of `stage_cascade` did on «Новый → В работе»."""
    with _uow(sessions) as uow:
        cancel_request_in(uow.fulfillment.get(request_id), ACTOR, when, uow, dict(details))
        uow.commit()


def _reserve_elsewhere(sessions, lot: StockLot, quantity: str) -> None:
    with _uow(sessions) as uow:
        uow.warehouse.add(uow.warehouse.get(lot.id).reserve(Decimal(quantity)))
        uow.commit()


def _lots(sessions) -> dict[str, Decimal]:
    with _uow(sessions) as uow:
        return {lot.grn: lot.reserved for lot in uow.warehouse.list(limit=100, include_zero=True)}


def _events(sessions, event_type: str):
    with sessions() as session:
        return session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == event_type)).all()


def _run(sessions, *numbers: str, apply: bool):
    return reopen.run(list(numbers), lambda: _uow(sessions), ACTOR, at(17, 10), apply=apply)


def _picked_kb7(sessions):
    """КБ7 of 663К.1 as on 13.11: QS8 500 from one lot, the red wire 300 + 150 from two lots, a third position
    bound and unbound again (it waits)."""
    qs8 = _lot(sessions, "OLD.26.0000936", "QS8-S-M", "600")
    red1 = _lot(sessions, "G-RED-1", "8AWG-RED", "300", "м")
    red2 = _lot(sessions, "G-RED-2", "8AWG-RED", "270", "м")
    spare = _lot(sessions, "G-X", "X-1", "50")
    request = _create(sessions, "КБ7", ("QS8-S-M", "500", "шт"), ("8AWG-RED", "450", "м"), ("X-1", "10", "шт"))
    qs8_line, red_line, x_line = (line.id for line in request.lines)
    _bind(sessions, request.id, qs8_line, qs8, at(13, 9))
    split = _bind(sessions, request.id, red_line, red1, at(13, 10))  # 300 of 450: the line splits
    rest = next(line.id for line in split.lines if line.id not in (qs8_line, red_line, x_line))
    _bind(sessions, request.id, rest, red2, at(13, 11))
    _bind(sessions, request.id, x_line, spare, at(13, 12))
    current = _get(sessions, request.id)
    unbind_lot(UnbindLotCommand(request.id, current.version, ACTOR, at(13, 13), x_line), _uow(sessions))
    return _get(sessions, request.id), (qs8_line, red_line, rest, x_line), (qs8, red1, red2, spare)


# ── Правило: «В работе» больше не отменяет комплект ─────────────────────


def test_a_manual_move_to_work_keeps_the_picked_kit(sessions):
    production = lambda: SqlAlchemyProductionUnitOfWork(sessions)  # noqa: E731
    request, _ids, _lots_ = _picked_kb7(sessions)
    before = _lots(sessions)
    state = flow.load_project_stage_state(PROJECT, production=production, fulfillment=lambda: _uow(sessions),
                                          item_indexes=KBS, kb="КБ7")
    preview = flow.preview_project_stage(state, "В работе", "КБ7")
    [kb7] = [kb for kb in preview["kbs"] if kb["kb"] == "КБ7"]
    assert (kb7["from"], kb7["to"], kb7["change"]) == ("Новый", "В работе", True)
    assert kb7["close"] == [] and kb7["keep"] == [{
        "kind": "kit_request", "label": f"Заявка на комплектацию {request.number}",
        "reason": "Этап ниже «Готово» — комплект нужен для работы, заявка остаётся.",
    }]

    change = flow.change_project_stage(flow.ChangeProjectStageCommand(
        project=PROJECT, value="В работе", kb="КБ7", actor=ACTOR, now=at(16, 12, 17),
        preview_token=preview["preview_token"],
    ), state, production)

    assert [(event.item_index, event.value) for event in change.events] == [("КБ7", "В работе")]
    after = _get(sessions, request.id)
    assert (after.status, after.version) == (FulfillmentStatus.UNDERFILLED, request.version)
    assert _lots(sessions) == before == {
        "OLD.26.0000936": Decimal("500"), "G-RED-1": Decimal("300"), "G-RED-2": Decimal("150"), "G-X": Decimal("0"),
    }
    assert _events(sessions, "fulfillment.cancelled") == []


# ── Возврат: проверка без записи, запись, отказы ────────────────────────


def test_dry_run_shows_the_rebind_and_writes_nothing_then_apply_restores_lines_and_reserves(sessions):
    request, (qs8_line, red_line, rest, x_line), (qs8, red1, red2, _spare) = _picked_kb7(sessions)
    _cascade_cancel(sessions, request.id, at(16, 12, 17))
    cancelled = _get(sessions, request.id)
    assert cancelled.status is FulfillmentStatus.CANCELLED and all(line.lot is None for line in cancelled.lines)
    _reserve_elsewhere(sessions, red2, "200")  # meanwhile another kit took the second red lot: 70 free of 150
    lots_before, audit_before = _lots(sessions), len(_events(sessions, "fulfillment.reopened"))

    dry = _run(sessions, request.number, apply=False)

    [plan] = dry.plans
    assert not dry.refused and not dry.applied
    assert (plan.status_before, plan.status_after) == ("Отменена", "Недоукомплектован")
    assert plan.cancel_reason == "пропущено вручную: Новый → В работе"
    assert [(line.line_id, line.article, line.quantity, line.grn, line.storage_location, line.available)
            for line in plan.bound] == [
        (qs8_line, "QS8-S-M", Decimal("500"), "OLD.26.0000936", "1F4", Decimal("600")),
        (red_line, "8AWG-RED", Decimal("300"), "G-RED-1", "1F4", Decimal("300")),
    ]
    assert [(line.line_id, line.quantity, line.grn, line.available, line.reason) for line in plan.skipped] == [
        (rest, Decimal("150"), "G-RED-2", Decimal("70"), "свободного остатка меньше, чем было набрано"),
    ]
    text = reopen.format_result(dry, apply=False)
    assert "Отменена → Недоукомплектован" in text and "привязать: QS8-S-M" in text
    assert "НЕ привязать: 8AWG-RED" in text and text.endswith("Проверка без записи. Записать — та же команда с --apply.")
    # Nothing written.
    assert _get(sessions, request.id) == cancelled
    assert _lots(sessions) == lots_before and len(_events(sessions, "fulfillment.reopened")) == audit_before

    applied = _run(sessions, request.number, apply=True)

    assert applied.applied and "applied" in reopen.format_result(applied, apply=True)
    after = _get(sessions, request.id)
    assert after.status is FulfillmentStatus.UNDERFILLED and after.version == cancelled.version + 1
    assert after.at_closed is None and after.at_work == cancelled.at_work
    by_id = {line.id: line for line in after.lines}
    assert (by_id[qs8_line].lot.lot_id, by_id[qs8_line].reserved) == (qs8.id, Decimal("500"))
    assert (by_id[red_line].lot.lot_id, by_id[red_line].reserved) == (red1.id, Decimal("300"))
    assert by_id[rest].waiting and by_id[x_line].waiting  # short of free stock / unbound before the cancel
    assert _lots(sessions) == {
        "OLD.26.0000936": Decimal("500"), "G-RED-1": Decimal("300"), "G-RED-2": Decimal("200"), "G-X": Decimal("0"),
    }
    [event] = _events(sessions, "fulfillment.reopened")
    assert event.aggregate_id == request.id and event.details["by"] == "workflow"
    assert event.details["previous_cancel_at"].startswith("2026-11-16T12:17")
    assert [(item["lot_grn"], item["quantity"], item["lot_id"]) for item in event.details["bound"]] == [
        ("OLD.26.0000936", "500", str(qs8.id)), ("G-RED-1", "300", str(red1.id)),
    ]
    assert [(item["lot_grn"], item["quantity"], item["available"]) for item in event.details["skipped"]] == [
        ("G-RED-2", "150", "70"),
    ]

    # A second run finds nothing to reopen.
    again = _run(sessions, request.number, apply=True)
    assert again.refused and not again.applied
    assert "заявка не отменена («Недоукомплектован»)" in again.plans[0].refused


def test_a_refused_request_stops_the_whole_run(sessions):
    lot = _lot(sessions, "G-1", "A-1", "20")
    good = _create(sessions, "КБ7", ("A-1", "5", "шт"))
    _bind(sessions, good.id, good.lines[0].id, lot, at(13))
    _cascade_cancel(sessions, good.id, at(16))
    by_hand = _create(sessions, "КБ10", ("A-1", "3", "шт"))
    _bind(sessions, by_hand.id, by_hand.lines[0].id, lot, at(13))
    cancel_fulfillment(VersionedFulfillmentCommand(by_hand.id, _get(sessions, by_hand.id).version, ACTOR, at(16, 13)),
                       _uow(sessions))

    result = _run(sessions, good.number, by_hand.number, "REQ.26.9999999", apply=True)

    assert result.refused and not result.applied
    assert [plan.refused for plan in result.plans] == [
        "",
        "последняя отмена сделана не переносом этапа ниже «Готово» (вручную или другим путём) — вернуть этой командой нельзя",
        "заявка не найдена",
    ]
    assert _get(sessions, good.id).status is FulfillmentStatus.CANCELLED
    assert _lots(sessions) == {"G-1": Decimal("0")} and _events(sessions, "fulfillment.reopened") == []
    assert reopen.format_result(result, apply=True).endswith("исправьте отказы (или уберите эти заявки из команды) и повторите.")


def test_a_kb_with_another_live_request_is_refused(sessions):
    lot = _lot(sessions, "G-1", "A-1", "20")
    old = _create(sessions, "КБ7", ("A-1", "5", "шт"))
    _bind(sessions, old.id, old.lines[0].id, lot, at(13))
    _cascade_cancel(sessions, old.id, at(16))
    fresh = _create(sessions, "КБ7", ("A-1", "5", "шт"))

    result = _run(sessions, old.number, apply=True)

    assert result.refused and not result.applied
    assert result.plans[0].refused == (
        f"у КБ7 проекта {PROJECT} уже есть живая заявка {fresh.number} («Новая») — две заявки на одну КБ нельзя"
    )
    assert _get(sessions, old.id).status is FulfillmentStatus.CANCELLED


def test_a_lot_short_for_two_requests_binds_the_first_and_skips_the_second(sessions):
    lot = _lot(sessions, "G-1", "A-1", "8")
    first = _create(sessions, "КБ7", ("A-1", "5", "шт"))
    second = _create(sessions, "КБ10", ("A-1", "3", "шт"))
    _bind(sessions, first.id, first.lines[0].id, lot, at(13))
    _bind(sessions, second.id, second.lines[0].id, lot, at(13, 10))
    _cascade_cancel(sessions, first.id, at(16))
    _cascade_cancel(sessions, second.id, at(16), {**CASCADE, "kb": "КБ10"})
    _reserve_elsewhere(sessions, lot, "1")  # 7 free: 5 for the first, 2 left for the second's 3

    result = _run(sessions, first.number, second.number, apply=True)

    assert result.applied
    assert [len(plan.bound) for plan in result.plans] == [1, 0]
    assert [(line.available, line.reason) for line in result.plans[1].skipped] == [
        (Decimal("2"), "свободного остатка меньше, чем было набрано"),
    ]
    assert _lots(sessions) == {"G-1": Decimal("6")}
    assert {_get(sessions, item.id).status for item in (first, second)} == {FulfillmentStatus.UNDERFILLED}


def test_lots_picked_at_creation_are_not_in_the_trail_and_are_reported(sessions):
    lot = _lot(sessions, "G-1", "A-1", "20")
    request = create_fulfillment(CreateFulfillmentCommand(
        idempotency_key=f"КБ7-{uuid4()}", purpose=f"{PROJECT} · КБ7 · Жгут", deadline=at(20).date(),
        lines=(FulfillmentLine.create(article="A-1", name="Деталь A-1", unit="шт", requirement=Decimal("5")),),
        actor=ACTOR, now=at(12), lot_ids=(lot.id,),
    ), _uow(sessions))
    _cascade_cancel(sessions, request.id, at(16))

    [plan] = _run(sessions, request.number, apply=False).plans

    assert (plan.bound, plan.skipped, plan.status_after) == ((), (), "Новая")
    assert plan.notes == ("при создании заявки партии выбраны для 1 поз. — по строкам это в журнал не пишется: "
                          "такие позиции ждут, наберите их заново",)


# ── Восстановление привязок по журналу ──────────────────────────────────


def _event(kind: str, when: datetime, **details):
    return reopen.TrailEvent(f"fulfillment.{kind}", when, details)


def test_replay_follows_binds_removals_cancels_and_earlier_reopens():
    a, b, c = UUID(int=1), UUID(int=2), UUID(int=3)
    lot1, lot2 = UUID(int=11), UUID(int=12)
    trail = [
        _event("lot_bound", at(10), line_id=str(a), lot_id=str(lot1), quantity="5", lot_grn="G-1"),
        _event("cancelled", at(11), stage="Готово", reason="пропущено вручную: x"),
        _event("reopened", at(12), bound=[{"line_id": str(b), "lot_id": str(lot2), "quantity": "2", "lot_grn": "G-2"}]),
        _event("lot_bound", at(13), line_id=str(c), lot_id=str(lot1), quantity="1"),
        # Same instant: the removal is applied before the new pick of the line.
        _event("lot_bound", at(14), line_id=str(c), lot_id=str(lot2), quantity="1"),
        _event("lot_unbound", at(14), line_id=str(c), lot_id=str(lot1), quantity="1"),
        _event("cancelled", at(15), stage="В работе", reason="пропущено вручную: Новый → В работе"),
        _event("lot_bound", at(16), line_id=str(a), lot_id=str(lot1), quantity="9"),  # after the cancel: ignored
    ]

    held = reopen.bindings_before_cancel(trail)

    assert {line_id: (item.lot_id, item.quantity) for line_id, item in held.items()} == {
        b: (lot2, Decimal("2")), c: (lot2, Decimal("1")),
    }
    assert reopen.last_cancel(trail).occurred_at == at(15)
    assert reopen.from_cascade(reopen.last_cancel(trail).details)
    assert not reopen.from_cascade({"reason": "пропущено вручную: x"})  # no stage — not the cascade
    assert not reopen.from_cascade({"stage": "Готово", "reason": "ошибка"})
    # A kit the 17.11 rule still cancels (a move to «Готово» or higher) is not revived by this command.
    assert not reopen.from_cascade({"stage": "Готово", "reason": "пропущено вручную: В работе → Готово"})
    assert not reopen.from_cascade({"stage": "ОТК", "reason": "пропущено вручную: Новый → ОТК"})  # ОТК is after «Готово»

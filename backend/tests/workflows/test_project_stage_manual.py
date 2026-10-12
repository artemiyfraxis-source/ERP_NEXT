"""Ручная смена этапа проекта/КБ (владелец 10.10.2026): единый путь записи, история «было → стало», предпросмотр."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sqlite3
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.fulfillment.application import (
    CreateFulfillmentCommand,
    HoldKitReserveCommand,
    create_fulfillment,
    hold_kit_reserve,
)
from app.modules.fulfillment.domain import FulfillmentKind, FulfillmentLine, FulfillmentStatus
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.production.domain import (
    ProductionOperation,
    ProductionStatus,
    ProductionTask,
    ProductionValidationError,
)
from app.modules.production.traceability import WorkSession
from app.modules.warehouse.domain import StockLot
from app.modules.production.infrastructure import (
    PLAN_EVENT_CHECKS,
    ProductionPlanEventRow,
    SqlAlchemyPlanEventRepository,
    SqlAlchemyProductionUnitOfWork,
)
from app.modules.production.plan import (
    KIT_STAGES,
    PLAN_KINDS,
    PROJECT_TERMINALS,
    KitStageNow,
    PlanEvent,
    kit_stage_key,
    plan_stage_change,
    project_stage,
    summarize_kit_marks,
)
from app.modules.production.plan_application import ChangeStageCommand, StageChangeConflict, change_stage
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows import project_kitset as flow
from app.workflows import stage_cascade
from tests.factories import Actor

ACTOR = Actor(name="Мастер", department="Производство")
NOW = datetime(2026, 10, 10, 12, 0)
BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "migrations" / "versions" / "0101_production_stage_history.py"
LADDER_MIGRATION = BACKEND / "migrations" / "versions" / "0107_stage_ladder.py"
ACCEPTED_MIGRATION = BACKEND / "migrations" / "versions" / "0108_stage_warehouse_accepted.py"


def _kit(index, status, *, manual=False, auto=None, shipped=False):
    return KitStageNow(index, status, manual, auto or status, shipped)


# ── Правила (plan.py) ───────────────────────────────────────────────────


def test_project_forward_raises_only_the_kbs_below_and_lists_the_skipped_stages():
    kits = (_kit("КБ1", "Новый"), _kit("КБ2", "В работе"), _kit("КБ3", "Отгружено", shipped=True))

    plan = plan_stage_change(kits, "Принято складом")

    assert plan.direction == "forward" and not plan.reason_required and plan.error == ""
    assert [(c.item_index, c.from_stage, c.to_stage, c.skipped) for c in plan.changes] == [
        ("КБ1", "Новый", "Принято складом", ("ТК готова", "КБ набрана", "Распланировано", "В работе", "ОТК")),
        ("КБ2", "В работе", "Принято складом", ("ОТК",)),
    ]
    # Ушедшая вперёд КБ не понижается.
    assert [(k.item_index, k.stage, k.note) for k in plan.kept] == [
        ("КБ3", "Отгружено", "Выше выбранного этапа — не понижаем."),
    ]


def test_project_pick_at_its_own_stage_changes_nothing():
    plan = plan_stage_change((_kit("КБ1", "В работе"), _kit("КБ2", "Принято складом")), "В работе")

    assert plan.direction == "none" and plan.changes == ()


def test_backward_needs_a_reason_and_never_goes_below_a_shipment():
    kits = (_kit("КБ1", "Принято складом"), _kit("КБ2", "Отгружено", shipped=True))

    project = plan_stage_change(kits, "В работе")
    assert project.direction == "back" and project.reason_required
    assert [(c.item_index, c.to_stage, c.skipped) for c in project.changes] == [("КБ1", "В работе", ())]
    assert [(k.item_index, k.note) for k in project.kept] == [("КБ2", "Отгружено по факту — ниже не опускаем.")]

    single = plan_stage_change(kits, "В работе", "кб2")
    assert single.error == "КБ2 отгружена по факту — этап ниже «Отгружено» не ставится." and single.changes == ()

    all_shipped = plan_stage_change((_kit("КБ1", "Отгружено", shipped=True),), "Новый")
    assert all_shipped.error.startswith("Все КБ проекта отгружены по факту")


def test_kb_pick_moves_only_that_kb_and_auto_clears_manual_marks():
    kits = (_kit("КБ1", "В работе", manual=True, auto="ТК готова"), _kit("КБ2", "Новый"))

    forward = plan_stage_change(kits, "Отгружено", "КБ2")
    assert [(c.item_index, c.skipped) for c in forward.changes] == [
        ("КБ2", ("ТК готова", "КБ набрана", "Распланировано", "В работе", "ОТК", "Принято складом")),
    ]
    auto = plan_stage_change(kits, "авто")
    assert auto.direction == "auto" and not auto.reason_required
    assert [(c.item_index, c.from_stage, c.to_stage, c.value) for c in auto.changes] == [
        ("КБ1", "В работе", "ТК готова", "авто"),
    ]


def test_unknown_stage_and_bad_kb_are_refused_in_russian():
    with pytest.raises(ProductionValidationError, match="Неизвестный этап"):
        plan_stage_change((_kit("КБ1", "Новый"),), "Готово")
    with pytest.raises(ProductionValidationError, match="Укажи КБ"):
        plan_stage_change((_kit("КБ1", "Новый"),), "В работе", "корпус")
    with pytest.raises(ProductionValidationError, match="В проекте нет КБ7"):
        plan_stage_change((_kit("КБ1", "Новый"),), "В работе", "КБ7")


def test_shipped_fact_beats_a_manual_stage_and_the_card_shows_what_was_skipped():
    key = kit_stage_key("708К.4", "КБ1")
    events = (
        PlanEvent("708К.4", NOW, "Мастер", PLAN_KINDS["stage"], item_index="КБ1", value="КБ набрана", from_value="Новый"),
        PlanEvent("708К.4", NOW, "Мастер", PLAN_KINDS["stage"], item_index="КБ1", value="Принято складом",
                  from_value="КБ набрана"),
    )
    marks = summarize_kit_marks(events)
    assert marks[key].skipped == ("ТК готова", "Распланировано", "В работе", "ОТК")

    stage = project_stage("708К.4", ("КБ1",), {"tc": {key}}, marks=marks).kits[0]
    # «ТК готова» случилась по факту (ТК выдана) — пропущенными остаются «В работе» и «ОТК».
    assert (stage.status, stage.manual, stage.skipped) == ("Принято складом", True, ("Распланировано", "В работе", "ОТК"))

    shipped = project_stage("708К.4", ("КБ1",), {"shipped": {key}}, marks=marks).kits[0]
    assert (shipped.status, shipped.manual, shipped.skipped) == ("Отгружено", False, ())

    back = summarize_kit_marks((*events, PlanEvent(
        "708К.4", NOW, "Мастер", PLAN_KINDS["stage"], item_index="КБ1", value="КБ набрана", from_value="Принято складом",
    )))
    assert back[key].skipped == ("ТК готова",)


# ── Запись (plan_application.change_stage) ─────────────────────────────


@pytest.fixture()
def production():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    yield (lambda: SqlAlchemyProductionUnitOfWork(sessions)), sessions
    engine.dispose()


def _rows(sessions):
    with sessions() as session:
        return session.scalars(select(ProductionPlanEventRow).order_by(ProductionPlanEventRow.sequence)).all()


def _audit(sessions):
    with sessions() as session:
        return session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "production.stage_changed")).all()


def test_change_writes_every_row_with_from_and_reason_in_one_transaction(production):
    factory, sessions = production
    plan = plan_stage_change((_kit("КБ1", "Новый"), _kit("КБ2", "ТК готова")), "Принято складом")

    events = change_stage(ChangeStageCommand("708К.4", plan, ACTOR, NOW, expected_sequence=0), factory())

    assert [(e.sequence, e.item_index, e.from_value, e.value) for e in events] == [
        (1, "КБ1", "Новый", "Принято складом"), (2, "КБ2", "ТК готова", "Принято складом"),
    ]
    rows = _rows(sessions)
    assert [(row.kind, row.from_value, row.value, row.text) for row in rows] == [
        ("Этап КБ", "Новый", "Принято складом", "Новый → Принято складом · пропущены: ТК готова, КБ набрана, Распланировано, В работе, ОТК"),
        ("Этап КБ", "ТК готова", "Принято складом", "ТК готова → Принято складом · пропущены: КБ набрана, Распланировано, В работе, ОТК"),
    ]
    [audit] = _audit(sessions)
    assert audit.actor_name == "Мастер"
    assert audit.details == {
        "project": "708К.4", "value": "Принято складом", "direction": "forward", "kbs": ["КБ1", "КБ2"],
        "changes": ["КБ1: Новый → Принято складом", "КБ2: ТК готова → Принято складом"],
        "skipped": ["ТК готова", "КБ набрана", "Распланировано", "В работе", "ОТК"], "reason": "",
    }


def test_backward_without_reason_is_refused_and_with_reason_is_stored(production):
    factory, sessions = production
    plan = plan_stage_change((_kit("КБ1", "Принято складом"),), "В работе", "КБ1")

    with pytest.raises(ProductionValidationError, match="Этап назад — напишите причину"):
        change_stage(ChangeStageCommand("708К.4", plan, ACTOR, NOW, reason="  "), factory())
    assert _rows(sessions) == []

    change_stage(ChangeStageCommand("708К.4", plan, ACTOR, NOW, reason="брак на ОТК"), factory())
    [row] = _rows(sessions)
    assert (row.from_value, row.value, row.reason) == ("Принято складом", "В работе", "брак на ОТК")
    assert row.text == "Принято складом → В работе · причина: брак на ОТК"


def test_a_failure_mid_way_writes_nothing(production, monkeypatch):
    factory, sessions = production
    original = SqlAlchemyPlanEventRepository.add
    calls = []

    def failing_add(self, event):
        calls.append(event.item_index)
        if len(calls) == 2:
            raise RuntimeError("сбой на второй строке")
        return original(self, event)

    monkeypatch.setattr(SqlAlchemyPlanEventRepository, "add", failing_add)
    plan = plan_stage_change((_kit("КБ1", "Новый"), _kit("КБ2", "Новый"), _kit("КБ3", "Новый")), "В работе")

    with pytest.raises(RuntimeError):
        change_stage(ChangeStageCommand("708К.4", plan, ACTOR, NOW), factory())

    assert calls == ["КБ1", "КБ2"]
    assert _rows(sessions) == [] and _audit(sessions) == []


def test_a_journal_moved_since_reading_refuses_the_change(production):
    factory, sessions = production
    plan = plan_stage_change((_kit("КБ1", "Новый"),), "В работе")
    change_stage(ChangeStageCommand("708К.4", plan, ACTOR, NOW, expected_sequence=0), factory())

    with pytest.raises(StageChangeConflict, match="обновите"):
        change_stage(ChangeStageCommand("708К.4", plan, ACTOR, NOW, expected_sequence=0), factory())
    assert len(_rows(sessions)) == 1


def test_a_closed_project_is_returned_to_work_first(production):
    factory, sessions = production
    plan = plan_stage_change((_kit("КБ1", "Новый"),), "В работе")

    change_stage(ChangeStageCommand("708К.4", plan, ACTOR, NOW, reopen_from="Отменён"), factory())

    assert [(row.kind, row.from_value, row.value) for row in _rows(sessions)] == [
        ("Статус проекта", "Отменён", "авто"), ("Этап КБ", "Новый", "В работе"),
    ]


def test_the_database_refuses_a_bad_stage_row(production):
    _factory, sessions = production
    with sessions() as session:
        session.add(ProductionPlanEventRow(
            id=uuid4(), project="708К.4", sequence=1, occurred_at=NOW, who="x", kind="Этап КБ", blocker="", text="",
            item_index="КБ1", value="Готово", field="", from_value="", reason="",
        ))
        with pytest.raises(Exception, match="CHECK"):
            session.commit()


# ── Служба и предпросмотр (workflows.project_kitset) ───────────────────


def _facts(**extra) -> flow.KitsetFacts:
    return flow.KitsetFacts(**extra)


def _task_fact(status, number="П-1"):
    return SimpleNamespace(id=uuid4(), version=1, number=number, name="Жгут", quantity=10, status=status,
                           project="708К.4", item="КБ1", handed_to="",
                           operations=(SimpleNamespace(planned_start=None, shares=()),))


def _request_fact(status, number="К-1"):
    return SimpleNamespace(id=uuid4(), version=1, number=number, kind=FulfillmentKind.MAIN, lines=(), status=status,
                           purpose="708К.4 · КБ1 · Жгут")


def test_preview_lists_from_to_skipped_what_closes_and_what_stays():
    facts = _facts(
        tasks=(_task_fact(ProductionStatus.RUNNING), _task_fact(ProductionStatus.DONE, "П-2")),
        fulfillment=(_request_fact(FulfillmentStatus.NEW), _request_fact(FulfillmentStatus.CANCELLED, "К-2")),
        goods=(SimpleNamespace(project="708К.4", item_index="КБ1", ordered=Decimal("10"), accepted=Decimal("4"),
                               shipped=Decimal("1"), sent=Decimal("1"), unit="шт", name="Жгут", article="708К.4.01"),),
        events=(PlanEvent("708К.4", NOW, "РП", PLAN_KINDS["block"], blocker="ХОЛД", text="ждём оплату"),),
    )
    state = flow.project_stage_state("708К.4", ("КБ1", "КБ2"), facts, sequence=1)

    preview = flow.preview_project_stage(state, "Отгружено")

    # Выполненное задание цеха — производство закончено: КБ1 «ОТК».
    assert preview["direction"] == "forward" and preview["reason_required"] is False and preview["error"] == ""
    kb1, kb2 = preview["kbs"]
    assert {key: kb1[key] for key in ("kb", "from", "to", "skipped", "change", "note")} == {
        "kb": "КБ1", "from": "ОТК", "to": "Отгружено", "skipped": ["Принято складом"], "change": True,
        "note": "",
    }
    # Пропущенное закрывается, готовая продукция остаётся — с причиной.
    assert [(item["kind"], item["label"], item["qty"], item["unit"]) for item in kb1["close"]] == [
        ("kit_request", "Заявка на комплектацию К-1", "0", "поз."), ("task", "Задание П-1 · Жгут", "10", "шт"),
    ]
    assert kb1["keep"] == [
        {"kind": "goods", "label": "Готовая продукция Жгут",
         "reason": "Принято на склад ГП, не отгружено: 3 шт — этап не отгружает, переносу не мешает."},
        {"kind": "goods", "label": "Готовая продукция Жгут", "reason": "Отгружено 1 шт — отгрузки остаются, переносу не мешают."},
    ]
    assert kb1["warnings"] == [
        "Закроется (пропущено вручную: ОТК → Отгружено): заданий цеха — 1, заявок на комплектацию — 1.",
        "Готовая продукция Жгут: Принято на склад ГП, не отгружено: 3 шт — этап не отгружает, переносу не мешает.",
        "Готовая продукция Жгут: Отгружено 1 шт — отгрузки остаются, переносу не мешают.",
    ]
    assert (kb2["skipped"], kb2["close"], kb2["keep"], kb2["warnings"]) == (
        ["ТК готова", "КБ набрана", "Распланировано", "В работе", "ОТК", "Принято складом"], [], [], [],
    )
    assert preview["totals"] == {"tasks": 1, "kit_requests": 1, "reserves": 0, "close": 2, "keep": 2}
    assert len(preview["preview_token"]) == 32
    assert preview["warnings"] == ["Активный блокер «ХОЛД»: ждём оплату — смена этапа его не снимает."]
    assert preview["message"].startswith(
        "КБ1: ОТК → Отгружено (пропущены: Принято складом); КБ2: Новый → Отгружено",
    )


def test_preview_of_a_backward_pick_says_a_reason_is_needed():
    facts = _facts(events=(
        PlanEvent("708К.4", NOW, "РП", PLAN_KINDS["stage"], item_index="КБ1", value="Принято складом", sequence=1),
    ))
    preview = flow.preview_project_stage(flow.project_stage_state("708К.4", ("КБ1",), facts), "В работе", "КБ1")

    assert (preview["direction"], preview["reason_required"]) == ("back", True)
    assert [(kb["from"], kb["to"], kb["skipped"]) for kb in preview["kbs"]] == [("Принято складом", "В работе", [])]
    assert (preview["close"], preview["keep"], preview["cascade_note"]) == ([], [], "Назад ничего не открывается")
    assert preview["warnings"] == ["Назад ничего не открывается"]


def test_project_without_kbs_changes_its_own_stage(production):
    factory, sessions = production
    with factory() as uow:
        uow.plan_projects.replace((flow.PlanProjectSnapshot(project="70К.26.13", customer="—", imported_at=NOW),))
        uow.commit()
    state = flow.load_project_stage_state("70К.26.13", production=factory, item_indexes=())

    change = flow.change_project_stage(flow.ChangeProjectStageCommand(
        project="70К.26.13", value="Отгружено", actor=ACTOR, now=NOW,
    ), state, factory)

    assert [(e.item_index, e.from_value, e.value) for e in change.events] == [("", "Новый", "Отгружено")]
    assert change.payload()["kbs"][0]["label"] == "Проект"


# ── Вперёд закрывает пропущенное (workflows.stage_cascade, владелец 12.10.2026) ─────────────────────────────────


PROJECT = "708К.4"
KBS = ("КБ1", "КБ2", "КБ3")


def _lot(sessions, grn: str, article: str, quantity: str = "20") -> StockLot:
    lot = StockLot.create(
        warehouse="ЦАР", grn=grn, package="", name=f"Деталь {article}", item_type="Комплектующие", purpose="Склад",
        unit="шт", quantity=Decimal(quantity), storage_location="1F4", source_receiving_ids=(f"r-{grn}",),
        article=article,
    )
    with SqlAlchemyFulfillmentUnitOfWork(sessions) as uow:
        uow.warehouse.add(lot)
        uow.commit()
    return lot


def _request(sessions, kb: str, lot: StockLot, quantity: str, *, status=None, kind=FulfillmentKind.MAIN):
    request = create_fulfillment(CreateFulfillmentCommand(
        idempotency_key=f"{kb}-{kind.value}-{uuid4()}", purpose=f"{PROJECT} · {kb} · Жгут", deadline=NOW.date(),
        lines=(FulfillmentLine.create(article=lot.article, name=lot.name, unit="шт", requirement=Decimal(quantity)),),
        actor=ACTOR, now=NOW, lot_ids=(lot.id,), kind=kind,
    ), SqlAlchemyFulfillmentUnitOfWork(sessions))
    if status is not None:
        with SqlAlchemyFulfillmentUnitOfWork(sessions) as uow:
            request = replace(request, status=status, version=request.version + 1)
            uow.fulfillment.add(request)
            uow.commit()
    return request


def _reserve(sessions, kb: str, lot: StockLot, quantity: str):
    with SqlAlchemyFulfillmentUnitOfWork(sessions) as uow:
        reserve = hold_kit_reserve(HoldKitReserveCommand(
            project=PROJECT, item_index=kb, article=lot.article, lot_id=lot.id, quantity=Decimal(quantity), unit="шт",
            actor=ACTOR, now=NOW,
        ), uow)
        uow.commit()
    return reserve


def _task(factory, kb: str, number: str, *, working: bool = False) -> ProductionTask:
    task = ProductionTask.create(
        number=number, source_idempotency_key=f"task-{number}", source_fingerprint="fp", project=PROJECT, item=kb,
        name="Жгут", quantity=10, fulfillment_request_id=None, deadline=None, launched_by="Мастер",
        operations=(ProductionOperation.create(
            position=1, code="OP-01", stage="Разделка", description="Кабель", seconds_per_unit=Decimal("60"),
            setup_seconds=Decimal("0"), quantity=10,
        ),),
        created_at=NOW,
    )
    with factory() as uow:
        uow.production.add(task)
        if working:
            uow.work.save(WorkSession.start(task.id, task.operations[0].id, uuid4(), NOW))
        uow.commit()
    return task


def _state(factory, sessions, kb: str = ""):
    return flow.load_project_stage_state(
        PROJECT, production=factory, fulfillment=lambda: SqlAlchemyFulfillmentUnitOfWork(sessions),
        item_indexes=KBS, kb=kb,
    )


def _move(factory, sessions, value: str, *, kb: str = "", token: str = "", reason: str = ""):
    return flow.change_project_stage(flow.ChangeProjectStageCommand(
        project=PROJECT, value=value, kb=kb, reason=reason, actor=ACTOR, now=NOW, preview_token=token,
    ), _state(factory, sessions, kb), factory)


def _world(sessions):
    """Everything the cascade may touch: request statuses, lots (balance, reserve), techcard reserves, tasks."""
    with SqlAlchemyFulfillmentUnitOfWork(sessions) as uow:
        requests = {item.number: item.status.value for item in uow.fulfillment.list(limit=100)}
        lots = {lot.grn: (lot.balance, lot.reserved) for lot in uow.warehouse.list(limit=100, include_zero=True)}
        reserves = {(item.item_index, item.article): item.quantity for item in uow.kit_reserves.for_project(PROJECT)}
    with SqlAlchemyProductionUnitOfWork(sessions) as uow:
        tasks = {task.number: task.status.value for task in uow.production.list(limit=100)}
    return requests, lots, reserves, tasks


def test_forward_to_work_cancels_new_and_underfilled_kits_and_gives_every_reserve_back(production):
    factory, sessions = production
    lot, other, picked_lot = _lot(sessions, "G-1", "A-1"), _lot(sessions, "G-2", "B-2"), _lot(sessions, "G-3", "C-3")
    new = _request(sessions, "КБ1", lot, "5")
    extra = _request(sessions, "КБ1", lot, "2", kind=FulfillmentKind.EXTRA)
    under = _request(sessions, "КБ2", lot, "4", status=FulfillmentStatus.UNDERFILLED)
    picked = _request(sessions, "КБ3", picked_lot, "3", status=FulfillmentStatus.COMPLETE)
    _reserve(sessions, "КБ1", other, "3")
    requests, lots, _reserves, _tasks = _world(sessions)
    assert lots == {"G-1": (20, 11), "G-2": (20, 3), "G-3": (20, 3)}

    preview = flow.preview_project_stage(_state(factory, sessions), "В работе")

    kb1, kb2, kb3 = preview["kbs"]
    assert [(item["kind"], item["label"]) for item in kb1["close"]] == [
        ("kit_request", f"Заявка на комплектацию {new.number} · снимается резерв с 1 поз."),
        ("kit_request", f"Заявка на комплектацию {extra.number} (довыдача) · снимается резерв с 1 поз."),
        ("reserve", "Резерв техкарты B-2 · G-2"),
    ]
    assert kb1["close"][2]["qty"] == "3" and kb1["close"][2]["unit"] == "шт"
    assert [item["label"] for item in kb2["close"]] == [f"Заявка на комплектацию {under.number} · снимается резерв с 1 поз."]
    assert kb3["close"] == [] and kb3["keep"] == [{
        "kind": "kit_request", "label": f"Заявка на комплектацию {picked.number}",
        "reason": "Комплект собран («Укомплектован») — остаётся, не мешает переносу.",
    }]
    assert preview["totals"] == {"tasks": 0, "kit_requests": 3, "reserves": 1, "close": 4, "keep": 1}
    assert _world(sessions)[:2] == (requests, lots)  # предпросмотр ничего не пишет

    change = _move(factory, sessions, "В работе", token=preview["preview_token"])

    requests, lots, reserves, _tasks = _world(sessions)
    assert requests == {new.number: "Отменена", extra.number: "Отменена", under.number: "Отменена",
                        picked.number: "Укомплектован"}
    # РЕЗЕРВ партий и резерв техкарты — в ноль; остатки (БАЛАНС) не двигались, собранный комплект держит своё.
    assert lots == {"G-1": (20, 0), "G-2": (20, 0), "G-3": (20, 3)}
    assert reserves == {("КБ1", "B-2"): 0}
    assert [item["kind"] for item in change.payload()["close"]] == ["kit_request", "kit_request", "reserve", "kit_request"]
    # Причина — в журнале проекта и в журнале действий.
    rows = {row.item_index: row for row in _rows(sessions)}
    assert rows["КБ1"].text.startswith("Новый → В работе · пропущены: ТК готова, КБ набрана, Распланировано · пропущено вручную: "
                                       "Новый → В работе — закрыто: Заявка на комплектацию")
    assert "пропущено вручную" not in rows["КБ3"].text
    with sessions() as session:
        audit = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type.in_((
            "fulfillment.cancelled", "fulfillment.kit_reserve_released", "warehouse.kit_reserve_released",
        )))).all()
    assert len(audit) == 5
    assert {item.details["reason"] for item in audit} == {"пропущено вручную: Новый → В работе"}
    [stage_audit] = _audit(sessions)
    assert sorted(stage_audit.details["closed"]) == ["КБ1", "КБ2"]


def test_forward_to_qc_cancels_idle_running_tasks_and_keeps_the_one_being_worked_on(production):
    factory, sessions = production
    idle = _task(factory, "КБ1", "П-1")
    busy = _task(factory, "КБ1", "П-2", working=True)
    other_kb = _task(factory, "КБ2", "П-3")

    # Порог заданий цеха — «ОТК» (производство закончено); ниже — test_stage_ladder.
    preview = flow.preview_project_stage(_state(factory, sessions, "КБ1"), "ОТК", "КБ1")
    [kb1] = [kb for kb in preview["kbs"] if kb["kb"] == "КБ1"]
    assert [(item["kind"], item["label"], item["qty"]) for item in kb1["close"]] == [("task", "Задание П-1 · Жгут", "10")]
    assert kb1["keep"] == [{"kind": "task", "label": "Задание П-2 · Жгут",
                            "reason": "По заданию идёт работа (смена открыта) — остаётся, не мешает переносу."}]

    _move(factory, sessions, "ОТК", kb="КБ1", token=preview["preview_token"], reason="собрали на ЖУК")

    tasks = _world(sessions)[3]
    assert tasks == {idle.number: "Отменено", busy.number: "Запущено", other_kb.number: "Запущено"}
    with sessions() as session:
        [cancelled] = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "production.cancelled")).all()
    assert cancelled.details == {"project": PROJECT, "kb": "КБ1", "stage": "ОТК",
                                 "reason": "пропущено вручную: собрали на ЖУК"}


def test_a_target_below_the_rule_keeps_the_work_and_says_why(production):
    factory, sessions = production
    lot = _lot(sessions, "G-1", "A-1")
    new = _request(sessions, "КБ1", lot, "5")
    task = _task(factory, "КБ2", "П-1", working=True)

    preview = flow.preview_project_stage(_state(factory, sessions), "КБ набрана")

    assert preview["close"] == []
    assert preview["keep"] == [
        {"kind": "kit_request", "label": f"Заявка на комплектацию {new.number}",
         "reason": "Этап ниже «В работе» — заявка остаётся."},
    ]
    # КБ2 уже «В работе» по факту (смена по заданию) — на «КБ набрана» не опускается и ничего не закрывает.
    assert [kb["change"] for kb in preview["kbs"]] == [True, False, True]
    assert task.number not in str(preview["keep"])


def test_a_second_identical_apply_changes_nothing(production):
    factory, sessions = production
    lot = _lot(sessions, "G-1", "A-1")
    _request(sessions, "КБ1", lot, "5")
    preview = flow.preview_project_stage(_state(factory, sessions), "В работе")
    _move(factory, sessions, "В работе", token=preview["preview_token"])
    after, rows, audit = _world(sessions), len(_rows(sessions)), len(_audit(sessions))

    again = _move(factory, sessions, "В работе", token=preview["preview_token"])  # двойной клик тем же токеном
    fresh = _move(factory, sessions, "В работе")

    assert again.events == () and fresh.events == ()
    assert again.payload()["close"] == [] and fresh.payload()["close"] == []
    assert (_world(sessions), len(_rows(sessions)), len(_audit(sessions))) == (after, rows, audit)


def test_a_stale_token_is_refused_and_writes_nothing(production):
    factory, sessions = production
    lot = _lot(sessions, "G-1", "A-1")
    _request(sessions, "КБ1", lot, "5")
    task = _task(factory, "КБ1", "П-1")
    preview = flow.preview_project_stage(_state(factory, sessions), "Принято складом")
    # Пока окно открыто, по заданию начали работу: закрываемое изменилось.
    with factory() as uow:
        uow.work.save(WorkSession.start(task.id, task.operations[0].id, uuid4(), NOW))
        uow.commit()
    before = _world(sessions)

    with pytest.raises(StageChangeConflict) as refused:
        _move(factory, sessions, "Принято складом", token=preview["preview_token"])

    assert str(refused.value) == "Этапы проекта или связанные задания изменились — обновите и повторите"
    assert _world(sessions) == before and _rows(sessions) == [] and _audit(sessions) == []


def test_a_failure_mid_cascade_writes_nothing(production, monkeypatch):
    factory, sessions = production
    lot, other = _lot(sessions, "G-1", "A-1"), _lot(sessions, "G-2", "B-2")
    _request(sessions, "КБ1", lot, "5")
    _reserve(sessions, "КБ1", other, "3")
    _task(factory, "КБ1", "П-1")
    before = _world(sessions)

    def broken(*_args, **_kwargs):
        raise RuntimeError("сбой на отмене задания")

    monkeypatch.setattr(stage_cascade, "cancel_task_in", broken)
    with pytest.raises(RuntimeError, match="сбой на отмене задания"):
        _move(factory, sessions, "Принято складом")

    # Заявка уже была отменена и резервы сняты в этой транзакции — откатилось всё, вместе со строками журнала.
    assert _world(sessions) == before and _rows(sessions) == [] and _audit(sessions) == []
    with sessions() as session:
        written = set(session.scalars(select(AuditEventRow.event_type)))
    assert not written & {"fulfillment.cancelled", "fulfillment.kit_reserve_released", "production.cancelled"}


def test_backward_and_auto_close_nothing_and_reopen_nothing(production):
    factory, sessions = production
    lot = _lot(sessions, "G-1", "A-1")
    _request(sessions, "КБ1", lot, "5")
    _move(factory, sessions, "Принято складом", kb="КБ1")
    _task(factory, "КБ1", "П-1")
    lot2 = _lot(sessions, "G-2", "B-2")
    fresh = _request(sessions, "КБ1", lot2, "2", kind=FulfillmentKind.EXTRA)
    before = _world(sessions)

    back_preview = flow.preview_project_stage(_state(factory, sessions, "КБ1"), "Новый", "КБ1")
    assert (back_preview["direction"], back_preview["close"], back_preview["cascade_note"]) == (
        "back", [], "Назад ничего не открывается",
    )
    back = _move(factory, sessions, "Новый", kb="КБ1", reason="ошиблись проектом")
    auto_preview = flow.preview_project_stage(_state(factory, sessions, "КБ1"), "авто", "КБ1")
    auto = _move(factory, sessions, "авто", kb="КБ1")

    assert back.events and auto.events
    assert (auto_preview["close"], auto_preview["cascade_note"]) == ([], "Назад ничего не открывается")
    # Отменённая заявка не воскресла, новая довыдача и задание не тронуты.
    assert _world(sessions) == before
    assert before[0][fresh.number] == "Новая"


# ── Миграция 0100 ───────────────────────────────────────────────────────


def _config(path: Path, *x: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    if x:
        config.cmd_opts = argparse.Namespace(x=list(x))
    return config


@pytest.fixture(scope="module")
def at_0099(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("stage-history") / "0099.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        patch.delenv("ERP_MIGRATION_ON_VIOLATION", raising=False)
        command.upgrade(_config(path), "0100_identity_documents")
    return path


@pytest.fixture()
def database(at_0099, tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    monkeypatch.delenv("ERP_MIGRATION_ON_VIOLATION", raising=False)
    path = tmp_path / "erp.db"
    shutil.copyfile(at_0099, path)
    return path


def _insert(path: Path, kind: str, value: str, sequence: int = 1) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO production_plan_events (id, project, sequence, occurred_at, who, kind, blocker, text,"
            " deadline, item_index, value, field) VALUES (?, '708К.4', ?, '2026-10-01 09:00:00', 'РП', ?, '', 'старая',"
            " NULL, 'КБ1', ?, '')",
            (uuid4().hex, sequence, kind, value),
        )


def _revision(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def test_migration_checks_block_updates_and_downgrades_cleanly(database):
    _insert(database, "Этап КБ", "В работе")
    command.upgrade(_config(database), "0101_production_stage_history")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT from_value, reason, value FROM production_plan_events").fetchall() == [
            ("", "", "В работе"),
        ]
        sql = connection.execute("SELECT sql FROM sqlite_master WHERE name = 'production_plan_events'").fetchone()[0]
        assert all(name in sql for name in PLAN_EVENT_CHECKS)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute(
                "INSERT INTO production_plan_events (id, project, sequence, occurred_at, who, kind, blocker, text,"
                " item_index, value, field, from_value, reason) VALUES ('x', '708К.4', 2, '2026-10-01', 'РП',"
                " 'Этап КБ', '', '', 'КБ1', 'Готово', '', '', '')"
            )
        with pytest.raises(sqlite3.DatabaseError, match="только дополняется"):
            connection.execute("UPDATE production_plan_events SET text = 'правка'")
        connection.execute("DELETE FROM production_plan_events")  # удаление проекта сносит журнал — можно

    command.downgrade(_config(database), "0100_identity_documents")
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(production_plan_events)")}
        assert not {"from_value", "reason"} & columns
        assert connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall() == []


def test_violating_rows_stop_the_migration_on_request_and_are_kept_by_default(database, capsys):
    _insert(database, "comment", "", 1)
    _insert(database, "Этап КБ", "Готово", 2)

    with pytest.raises(RuntimeError, match="остановлена, данные не тронуты") as stopped:
        command.upgrade(_config(database, "on_violation=stop"), "0101_production_stage_history")
    assert "2 строк(и) нарушают" in str(stopped.value) and "«Готово»" in str(stopped.value)
    assert _revision(database) == "0100_identity_documents"

    command.upgrade(_config(database), "0101_production_stage_history")
    assert "2 строк(и) нарушают" in capsys.readouterr().out
    with sqlite3.connect(database) as connection:
        assert sorted(connection.execute("SELECT kind, value FROM production_plan_events").fetchall()) == [
            ("comment", ""), ("Этап КБ", "Готово"),
        ]
    command.downgrade(_config(database), "0100_identity_documents")


def _migration(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_checks_match_the_model():
    module = _migration(MIGRATION, "stage_history_migration")
    ladder = _migration(LADDER_MIGRATION, "stage_ladder_migration")
    accepted = _migration(ACCEPTED_MIGRATION, "stage_accepted_migration")

    # 0101 поставил проверку вида, 0108 перевела три проверки (этап, статус проекта, «было») на лестницу из 8 этапов
    # и один способ закрыть проект.
    assert {**module.CHECKS, **accepted.CHECKS} == PLAN_EVENT_CHECKS
    assert module.KINDS == tuple(PLAN_KINDS.values())
    assert accepted.STAGES == KIT_STAGES and accepted.OLD_STAGES == ladder.STAGES
    assert accepted.TERMINALS == PROJECT_TERMINALS and accepted.OLD_TERMINALS == module.TERMINALS
    assert accepted.OLD_CHECKS == {name: ladder.CHECKS.get(name, module.CHECKS[name]) for name in accepted.CHECKS}

"""Лестница этапов из 8 шагов (владелец 12.10.2026, «Загрузка цеха»): факты каждого этапа, старое слово
«КБ на складе», пороги закрытия при ручном шаге вперёд, один набор КБ у плана и экрана проекта, миграция 0107."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.fulfillment.domain import FulfillmentKind, FulfillmentStatus
from app.modules.production.domain import ProductionStatus, ProductionValidationError
from app.modules.production.infrastructure import PLAN_EVENT_CHECKS, SqlAlchemyPlanEventRepository
from app.modules.production.plan import (
    KIT_STAGES,
    PLAN_KINDS,
    KitStageNow,
    PlanEvent,
    PlanProjectSnapshot,
    plan_stage_change,
    project_stage,
    summarize_kit_marks,
    validate_plan_note,
)
from app.modules.production.traceability import BoxStatus
from app.modules.shipping.domain import FinishedGood
from app.modules.techcards.kitsets import KitAssembly, Kitset
from app.modules.techcards.project_cards import ProjectTechcard
from app.workflows import project_kitset as flow
from app.workflows import stage_cascade
from app.workflows.production_plan import build_production_plan, kit_stage_facts

PROJECT = "708К.4"
FINAL = uuid4()
EARLIER = uuid4()
NOW = datetime(2026, 10, 12, 12, 0)
BACKEND = Path(__file__).resolve().parents[2]
LEGACY = "КБ на складе"


def test_the_ladder_is_eight_steps_in_shop_order():
    assert KIT_STAGES == (
        "Новый", "ТК готова", "КБ набрана", "В работе", "ОТК", "Готово к отгрузке", "На отгрузке", "Отгружено",
    )


# ── Факты этапов ────────────────────────────────────────────────────────


def _task(status=ProductionStatus.RUNNING, item="КБ1"):
    return SimpleNamespace(id=uuid4(), status=status, project=PROJECT, item=item,
                           final_operation=SimpleNamespace(id=FINAL))


def _box(task, status=BoxStatus.CONFIRMED, operation=FINAL):
    return SimpleNamespace(id=uuid4(), task_id=task.id, operation_id=operation, status=status)


def _good(accepted="0"):
    good = FinishedGood.create(project=PROJECT, item_index="КБ1", customer="", article="", name="Жгут",
                               ordered=Decimal("10"), unit="шт")
    return good.accept(Decimal(accepted), date(2026, 10, 12), "ГП.26.0000001") if Decimal(accepted) else good


def _stage(tasks=(), boxes=(), inspected=(), goods=()) -> str:
    facts, _marks = kit_stage_facts(
        cards=(), tasks=tasks, fulfillment=(), goods=goods, marks={}, boxes=boxes, inspected=inspected,
    )
    return project_stage(PROJECT, ("КБ1",), facts).status


def test_a_running_task_is_at_work_and_a_finished_task_without_a_box_is_at_qc():
    assert _stage((_task(),)) == "В работе"
    assert _stage((_task(ProductionStatus.DONE),)) == "ОТК"


def test_a_final_operation_box_in_the_qc_queue_or_inspected_is_at_qc():
    task = _task()
    assert _stage((task,), (_box(task),)) == "ОТК"  # подтверждена мастером — в очереди ОТК
    inspected = _box(task, BoxStatus.CONSUMED)
    assert _stage((task,), (inspected,), {inspected.id}) == "ОТК"  # проверена ОТК


def test_a_box_not_yet_at_qc_keeps_the_kb_at_work():
    task = _task()
    assert _stage((task,), (_box(task, BoxStatus.OPERATOR_CLOSED),)) == "В работе"  # мастер ещё не подтвердил
    assert _stage((task,), (_box(task, operation=EARLIER),)) == "В работе"  # не последняя операция
    cancelled = _task(ProductionStatus.CANCELLED)
    assert _stage((cancelled,), (_box(cancelled),)) == "Новый"


def test_accepted_taken_and_sent_goods_climb_the_ladder():
    done = (_task(ProductionStatus.DONE),)
    assert _stage(done, goods=(_good("4"),)) == "ОТК"  # принята не вся ГП
    accepted = _good("10")
    assert _stage(done, goods=(accepted,)) == "Готово к отгрузке"
    taken = accepted.take(Decimal("10"))
    assert _stage(done, goods=(taken,)) == "На отгрузке"
    assert _stage(done, goods=(taken.dispatch(Decimal("4")),)) == "На отгрузке"  # уехала часть
    assert _stage(done, goods=(taken.dispatch(Decimal("10")),)) == "Отгружено"


# ── Старое слово «КБ на складе» ─────────────────────────────────────────


def test_an_old_journal_word_reads_as_ready_to_ship():
    marks = summarize_kit_marks((
        PlanEvent(PROJECT, NOW, "РП", PLAN_KINDS["stage"], item_index="КБ1", value=LEGACY, from_value="В работе"),
    ))
    stage = project_stage(PROJECT, ("КБ1",), {}, marks=marks).kits[0]
    assert (stage.status, stage.manual, stage.skipped) == ("Готово к отгрузке", True, ("ОТК",))
    back = summarize_kit_marks((
        PlanEvent(PROJECT, NOW, "РП", PLAN_KINDS["stage"], item_index="КБ1", value="В работе", from_value=LEGACY),
    ))
    assert back[next(iter(back))].stage == "В работе"


def test_a_new_write_of_the_old_word_is_refused_by_name():
    hint = "Этапа «КБ на складе» больше нет — теперь «Готово к отгрузке»."
    with pytest.raises(ProductionValidationError) as picked:
        plan_stage_change((KitStageNow("КБ1", "Новый", False, "Новый"),), LEGACY)
    assert str(picked.value) == hint
    with pytest.raises(ProductionValidationError) as noted:
        validate_plan_note({"project": PROJECT, "kind": "stage", "item_index": "КБ1", "value": LEGACY}, (PROJECT,))
    assert str(noted.value) == hint


# ── Пороги закрытия при шаге вперёд (stage_cascade) ─────────────────────


def _cascade(to_stage: str):
    task = SimpleNamespace(id=uuid4(), version=1, number="П-1", name="Жгут", quantity=10,
                           status=ProductionStatus.RUNNING, project=PROJECT, item="КБ1")
    request = SimpleNamespace(id=uuid4(), version=1, number="К-1", kind=FulfillmentKind.MAIN, lines=(),
                              status=FulfillmentStatus.NEW, purpose=f"{PROJECT} · КБ1 · Жгут")
    plan = plan_stage_change((KitStageNow("КБ1", "Новый", False, "Новый"),), to_stage)
    cascade = stage_cascade.plan_cascade(PROJECT, plan, stage_cascade.CascadeFacts(tasks=(task,), requests=(request,)))
    [kb] = cascade.kbs
    return [item.kind for item in kb.close], [(item.kind, item.reason) for item in kb.keep]


def test_kit_requests_close_from_work_and_idle_tasks_from_qc():
    assert _cascade("КБ набрана") == ([], [
        ("kit_request", "Этап ниже «В работе» — заявка остаётся."),
        ("task", "Этап ниже «ОТК» — задание остаётся."),
    ])
    assert _cascade("В работе") == (["kit_request"], [("task", "Этап ниже «ОТК» — задание остаётся.")])
    assert _cascade("ОТК") == (["kit_request", "task"], [])
    assert _cascade("Готово к отгрузке") == (["kit_request", "task"], [])


# ── Один набор КБ у плана и экрана проекта (T5) ─────────────────────────


def test_a_techcard_kb_outside_the_kitset_gives_the_same_stage_on_the_plan_and_the_project_screen():
    kitset = Kitset(
        project=PROJECT, customer="ООО", calc_source="", updated_at=NOW, updated_by="Технолог", created_at=NOW,
        assemblies=(KitAssembly(index="КБ1", name="Жгут", run=Decimal("10")),),
    )
    outside = ProjectTechcard.create(
        project=PROJECT, item_index="КБ2", product_name="Корпус", source_calculation="", deadline=None, now=NOW,
    )
    goods = (_good("10"),)  # КБ1 — «Готово к отгрузке»; КБ2 только в техкартах — «Новый»
    snapshot = PlanProjectSnapshot(PROJECT, customer="ООО")

    screen, _blocker = flow.kitset_stages(
        (kitset,), flow.KitsetFacts(cards=(outside,), goods=goods, projects=(snapshot,)),
    )[PROJECT]
    [row] = build_production_plan(
        cards=(outside,), tasks=(), fulfillment=(), goods=goods, requests=(), procurement=(),
        projects=(snapshot,), events=(), today=NOW.date(), kitsets=(kitset,),
    )

    assert [(kit.item_index, kit.status) for kit in screen.kits] == [("КБ1", "Готово к отгрузке"), ("КБ2", "Новый")]
    assert screen.status == row.stage.status == "Новый"
    assert [(kit.item_index, kit.status) for kit in row.stage.kits] == [(kit.item_index, kit.status) for kit in screen.kits]


# ── Миграция 0107 ───────────────────────────────────────────────────────


def _config(path: Path) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


@pytest.fixture(scope="module")
def at_0106(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("stage-ladder") / "0106.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        command.upgrade(_config(path), "0106_fulfillment_line_absent")
    return path


@pytest.fixture()
def database(at_0106, tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    shutil.copyfile(at_0106, path)
    return path


_INSERT = (
    "INSERT INTO production_plan_events (id, project, sequence, occurred_at, who, kind, blocker, text, item_index,"
    " value, field, from_value, reason) VALUES (?, '708К.4', ?, '2026-10-01 09:00:00', 'РП', 'Этап КБ', '', ?, 'КБ1',"
    " ?, '', ?, '')"
)


def _schema(connection) -> list[tuple]:
    return connection.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE tbl_name = 'production_plan_events' ORDER BY type, name"
    ).fetchall()


def test_migration_keeps_old_rows_checks_the_new_ladder_and_keeps_the_journal_append_only(database, capsys):
    with sqlite3.connect(database) as connection:
        connection.execute(_INSERT, (uuid4().hex, 1, "Новый → КБ на складе", LEGACY, "Новый"))
        connection.execute(_INSERT, (uuid4().hex, 2, "КБ на складе → В работе", "В работе", LEGACY))
        before = connection.execute("SELECT * FROM production_plan_events ORDER BY sequence").fetchall()
        indexes = {row[1] for row in _schema(connection) if row[0] == "index"}

    command.upgrade(_config(database), "0107_stage_ladder")
    assert "2 строк(и) со словами прежней лестницы оставлены как есть" in capsys.readouterr().out

    with sqlite3.connect(database) as connection:
        # Строки не тронуты: журнал неизменяем.
        assert connection.execute("SELECT * FROM production_plan_events ORDER BY sequence").fetchall() == before
        schema = _schema(connection)
        sql = next(row[2] for row in schema if row[0] == "table")
        assert all(name in sql for name in PLAN_EVENT_CHECKS)
        assert "'ОТК', 'Готово к отгрузке', 'На отгрузке'" in sql and LEGACY not in sql
        assert {row[1] for row in schema if row[0] == "index"} == indexes
        assert [row[1] for row in schema if row[0] == "trigger"] == ["production_plan_events_no_update"]
        with pytest.raises(sqlite3.IntegrityError, match="ck_production_plan_events_stage_value"):
            connection.execute(_INSERT, (uuid4().hex, 3, "", LEGACY, ""))
        with pytest.raises(sqlite3.IntegrityError, match="ck_production_plan_events_from_value"):
            connection.execute(_INSERT, (uuid4().hex, 3, "", "В работе", LEGACY))
        connection.execute(_INSERT, (uuid4().hex, 3, "В работе → ОТК", "ОТК", "В работе"))
        with pytest.raises(sqlite3.DatabaseError, match="только дополняется"):
            connection.execute("UPDATE production_plan_events SET text = 'правка'")
        connection.commit()

    # Старое слово читается как этап новой лестницы.
    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        with Session(engine) as session:
            events = sorted(SqlAlchemyPlanEventRepository(session).list(), key=lambda event: event.sequence)
    finally:
        engine.dispose()
    assert [(event.from_value, event.value, event.text) for event in events] == [
        ("Новый", "Готово к отгрузке", "Новый → КБ на складе"),
        ("Готово к отгрузке", "В работе", "КБ на складе → В работе"),
        ("В работе", "ОТК", "В работе → ОТК"),
    ]

    # Повторный upgrade ничего не делает.
    with sqlite3.connect(database) as connection:
        schema = _schema(connection)
    command.upgrade(_config(database), "head")
    with sqlite3.connect(database) as connection:
        assert _schema(connection) == schema
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0107_stage_ladder"


def test_migration_downgrades_to_the_old_checks_and_keeps_the_trigger(database):
    command.upgrade(_config(database), "0107_stage_ladder")
    with sqlite3.connect(database) as connection:
        connection.execute(_INSERT, (uuid4().hex, 1, "Новый → ОТК", "ОТК", "Новый"))

    command.downgrade(_config(database), "0106_fulfillment_line_absent")

    with sqlite3.connect(database) as connection:
        schema = _schema(connection)
        sql = next(row[2] for row in schema if row[0] == "table")
        assert LEGACY in sql and "'ОТК'" not in sql
        assert [row[1] for row in schema if row[0] == "trigger"] == ["production_plan_events_no_update"]
        assert connection.execute("SELECT value FROM production_plan_events").fetchall() == [("ОТК",)]
        with pytest.raises(sqlite3.DatabaseError, match="только дополняется"):
            connection.execute("UPDATE production_plan_events SET text = 'правка'")

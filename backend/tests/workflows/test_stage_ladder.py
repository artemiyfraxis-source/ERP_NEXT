"""Лестница этапов из 8 шагов (владелец 12.10.2026, «Загрузка цеха»): факты каждого этапа, старые слова
(«КБ на складе», «Готово к отгрузке», «На отгрузке»), пороги закрытия при ручном шаге вперёд, один набор КБ
у плана и экрана проекта, миграции 0107 и 0108."""

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
    kit_stage_key,
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
from app.workflows.production_plan import build_production_plan, issued_runs, kit_stage_facts

PROJECT = "708К.4"
FINAL = uuid4()
EARLIER = uuid4()
NOW = datetime(2026, 10, 12, 12, 0)
BACKEND = Path(__file__).resolve().parents[2]
LEGACY = "КБ на складе"


def test_the_ladder_is_six_steps_in_shop_order():
    # Владелец 29.10: «ТК готова» / «КБ набрана» — отметки рядом с этапом, «Распланировано» убрано.
    assert KIT_STAGES == ("Новый", "В работе", "ОТК", "Принято складом", "Упаковано", "Отгружено")


# ── Факты этапов ────────────────────────────────────────────────────────


def _task(status=ProductionStatus.RUNNING, item="КБ1", planned=False, started=False, quantity=10):
    """``planned`` — every operation has a window in the resource plan; ``started`` — someone took a share."""
    share = SimpleNamespace(started_at=NOW if started else None)
    operation = SimpleNamespace(planned_start=NOW if planned else None, shares=(share,))
    return SimpleNamespace(id=uuid4(), status=status, project=PROJECT, item=item, handed_to="", quantity=quantity,
                           operations=(operation,), final_operation=SimpleNamespace(id=FINAL))


def _box(task, status=BoxStatus.CONFIRMED, operation=FINAL, quantity=None):
    """``quantity`` None — box facts without pieces (the whole task counts)."""
    box = SimpleNamespace(id=uuid4(), task_id=task.id, operation_id=operation, status=status)
    if quantity is not None:
        box.quantity = Decimal(quantity)
    return box


def _good(accepted="0"):
    good = FinishedGood.create(project=PROJECT, item_index="КБ1", customer="", article="", name="Жгут",
                               ordered=Decimal("10"), unit="шт")
    return good.accept(Decimal(accepted), date(2026, 10, 12), "ГП.26.0000001") if Decimal(accepted) else good


def _stage(tasks=(), boxes=(), inspected=(), goods=(), worked=(), run=None) -> str:
    facts, _marks = kit_stage_facts(
        cards=(), tasks=tasks, fulfillment=(), goods=goods, marks={}, boxes=boxes, inspected=inspected, worked=worked,
        runs={kit_stage_key(PROJECT, "КБ1"): run} if run else None,
    )
    return project_stage(PROJECT, ("КБ1",), facts).status


def test_a_launched_or_placed_task_is_not_work_yet_a_worked_one_is_at_work():
    assert _stage((_task(),)) == "Новый"  # запущено, но в ресурсном плане не стоит
    # «Распланировано» убрано (29.10): задание в ресурсном плане этап не двигает.
    assert _stage((_task(planned=True),)) == "Новый"
    # Смена по заданию («след работы») или «взял» по доле — работа начата.
    placed = _task(planned=True)
    assert _stage((placed,), worked={placed.id}) == "В работе"
    assert _stage((_task(planned=True, started=True),)) == "В работе"
    assert _stage((_task(started=True),)) == "В работе"  # работа без плана — тоже работа
    cancelled = _task(ProductionStatus.CANCELLED, planned=True, started=True)
    assert _stage((cancelled,), worked={cancelled.id}) == "Новый"


def test_a_finished_task_without_a_box_is_at_qc():
    assert _stage((_task(ProductionStatus.DONE),)) == "ОТК"


def test_a_final_operation_box_in_the_qc_queue_or_inspected_is_at_qc():
    task = _task(started=True)
    assert _stage((task,), (_box(task),)) == "ОТК"  # подтверждена мастером — в очереди ОТК
    inspected = _box(task, BoxStatus.CONSUMED)
    assert _stage((task,), (inspected,), {inspected.id}) == "ОТК"  # проверена ОТК


def test_a_box_not_yet_at_qc_keeps_the_kb_at_work():
    task = _task(started=True)
    assert _stage((task,), (_box(task, BoxStatus.RETURNED),)) == "В работе"  # возвращена на исправление
    assert _stage((task,), (_box(task, BoxStatus.OPEN),)) == "В работе"
    # Сдана с последней операции — уже в очереди ОТК: подтверждения мастером нет (13.10).
    assert _stage((task,), (_box(task, BoxStatus.OPERATOR_CLOSED),)) == "ОТК"
    assert _stage((task,), (_box(task, operation=EARLIER),)) == "В работе"  # не последняя операция
    cancelled = _task(ProductionStatus.CANCELLED)
    assert _stage((cancelled,), (_box(cancelled),)) == "Новый"


def test_qc_only_once_the_whole_kb_run_is_made():
    """Владелец 22.10: «ОТК» — весь тираж КБ сделан, а не одно его задание; отменённые не считаются."""
    first = _task(ProductionStatus.DONE, started=True, quantity=4)
    # Тираж 10, выполнено 4, остальное не запущено — не «ОТК».
    assert _stage((first,), run=10) == "В работе"
    # Запущено ещё 6 и они в работе — тоже не «ОТК».
    second = _task(started=True, quantity=6)
    assert _stage((first, second), run=10) == "В работе"
    # Сдано с последней операции 5 из 6 — 9 из 10, ещё не «ОТК»; 6 из 6 — весь тираж: «ОТК».
    assert _stage((first, second), (_box(second, quantity="5"),), run=10) == "В работе"
    assert _stage((first, second), (_box(second, quantity="5"), _box(second, quantity="1")), run=10) == "ОТК"
    # Выполнено всё запущенное — 10 из 10.
    assert _stage((first, _task(ProductionStatus.DONE, quantity=6)), run=10) == "ОТК"
    # Отменённое задание тираж не закрывает.
    cancelled = _task(ProductionStatus.CANCELLED, started=True, quantity=6)
    assert _stage((first, cancelled), run=10) == "В работе"


def test_a_kb_without_a_known_run_is_at_qc_when_everything_launched_is_made():
    done = _task(ProductionStatus.DONE, started=True, quantity=4)
    assert _stage((done,)) == "ОТК"
    assert _stage((done, _task(started=True, quantity=6))) == "В работе"


def test_accepted_and_sent_goods_climb_the_ladder():
    done = (_task(ProductionStatus.DONE),)
    assert _stage(done, goods=(_good("4"),)) == "ОТК"  # принята не вся ГП
    accepted = _good("10")
    assert _stage(done, goods=(accepted,)) == "Принято складом"
    taken = accepted.take(Decimal("10"))
    assert _stage(done, goods=(taken,)) == "Упаковано"  # отобрано в отправки и упаковано, ещё не уехало
    assert _stage(done, goods=(taken.dispatch(Decimal("4")),)) == "Упаковано"  # уехала часть
    partly = accepted.take(Decimal("4"))
    assert _stage(done, goods=(partly,)) == "Принято складом"  # упакована не вся ГП
    assert _stage(done, goods=(taken.dispatch(Decimal("10")),)) == "Отгружено"


# ── Старое слово «КБ на складе» ─────────────────────────────────────────


def test_an_old_journal_word_reads_as_accepted_by_the_warehouse():
    marks = summarize_kit_marks((
        PlanEvent(PROJECT, NOW, "РП", PLAN_KINDS["stage"], item_index="КБ1", value=LEGACY, from_value="В работе"),
    ))
    stage = project_stage(PROJECT, ("КБ1",), {}, marks=marks).kits[0]
    assert (stage.status, stage.manual, stage.skipped) == ("Принято складом", True, ("ОТК",))
    back = summarize_kit_marks((
        PlanEvent(PROJECT, NOW, "РП", PLAN_KINDS["stage"], item_index="КБ1", value="В работе", from_value=LEGACY),
    ))
    assert back[next(iter(back))].stage == "В работе"
    for word, now in (("Готово к отгрузке", "Принято складом"), ("На отгрузке", "Упаковано")):
        marks = summarize_kit_marks((
            PlanEvent(PROJECT, NOW, "РП", PLAN_KINDS["stage"], item_index="КБ1", value=word, from_value="ОТК"),
        ))
        assert project_stage(PROJECT, ("КБ1",), {}, marks=marks).kits[0].status == now


def test_a_new_write_of_the_old_word_is_refused_by_name():
    for word, now in ((LEGACY, "Принято складом"), ("Готово к отгрузке", "Принято складом"), ("На отгрузке", "Упаковано")):
        hint = f"Этапа «{word}» больше нет — теперь «{now}»."
        with pytest.raises(ProductionValidationError) as picked:
            plan_stage_change((KitStageNow("КБ1", "Новый", False, "Новый"),), word)
        assert str(picked.value) == hint
        with pytest.raises(ProductionValidationError) as noted:
            validate_plan_note({"project": PROJECT, "kind": "stage", "item_index": "КБ1", "value": word}, (PROJECT,))
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
    assert _cascade("В работе") == (["kit_request"], [("task", "Этап ниже «ОТК» — задание остаётся.")])
    assert _cascade("ОТК") == (["kit_request", "task"], [])
    assert _cascade("Принято складом") == (["kit_request", "task"], [])


def test_a_manual_move_to_a_former_stage_is_refused():
    for word in ("ТК готова", "КБ набрана", "Распланировано"):
        with pytest.raises(ProductionValidationError, match=f"Этапа «{word}» больше нет"):
            _cascade(word)


# ── Один набор КБ у плана и экрана проекта (T5) ─────────────────────────


def test_a_techcard_kb_outside_the_kitset_gives_the_same_stage_on_the_plan_and_the_project_screen():
    kitset = Kitset(
        project=PROJECT, customer="ООО", calc_source="", updated_at=NOW, updated_by="Технолог", created_at=NOW,
        assemblies=(KitAssembly(index="КБ1", name="Жгут", run=Decimal("10")),),
    )
    outside = ProjectTechcard.create(
        project=PROJECT, item_index="КБ2", product_name="Корпус", source_calculation="", deadline=None, now=NOW,
    )
    goods = (_good("10"),)  # КБ1 — «Принято складом»; КБ2 только в техкартах — «Новый»
    snapshot = PlanProjectSnapshot(PROJECT, customer="ООО")

    screen, _blocker = flow.kitset_stages(
        (kitset,), flow.KitsetFacts(cards=(outside,), goods=goods, projects=(snapshot,)),
    )[PROJECT]
    [row] = build_production_plan(
        cards=(outside,), tasks=(), fulfillment=(), goods=goods, requests=(), procurement=(),
        projects=(snapshot,), events=(), today=NOW.date(), kitsets=(kitset,),
    )

    assert [(kit.item_index, kit.status) for kit in screen.kits] == [("КБ1", "Принято складом"), ("КБ2", "Новый")]
    assert screen.status == row.stage.status == "Новый"
    assert [(kit.item_index, kit.status) for kit in row.stage.kits] == [(kit.item_index, kit.status) for kit in screen.kits]


def test_the_plan_takes_the_kb_run_from_the_kitset_and_the_etk_batch_for_qc():
    """«ОТК» на плане — по тиражу КБ из карточки набора (нет набора — партия выданной ЭТК), как остаток к запуску."""
    kitset = Kitset(
        project=PROJECT, customer="ООО", calc_source="", updated_at=NOW, updated_by="Технолог", created_at=NOW,
        assemblies=(KitAssembly(index="КБ1", name="Жгут", run=Decimal("10")),),
    )
    etk = ProjectTechcard.create(
        project=PROJECT, item_index="КБ2", product_name="Корпус", source_calculation="", deadline=None, now=NOW,
    ).rebuild({"operations": [{"code": "OP-1", "connector": "1", "side": ""}], "kit": {"part_quantity": "8"}}, NOW).issue(NOW)
    assert issued_runs((etk,), (kitset,)) == {kit_stage_key(PROJECT, "КБ1"): 10, kit_stage_key(PROJECT, "КБ2"): 8}
    tasks = (_task(ProductionStatus.DONE, started=True, quantity=4), _task(ProductionStatus.DONE, item="КБ2", quantity=8))

    [row] = build_production_plan(
        cards=(etk,), tasks=tasks, fulfillment=(), goods=(), requests=(), procurement=(),
        projects=(PlanProjectSnapshot(PROJECT, customer="ООО"),), events=(), today=NOW.date(), kitsets=(kitset,),
    )

    assert [(kit.item_index, kit.status) for kit in row.stage.kits] == [("КБ1", "В работе"), ("КБ2", "ОТК")]


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
        assert all(name in sql for name in PLAN_EVENT_CHECKS if name != "ck_production_plan_events_blocker")  # 0161
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
        ("Новый", "Принято складом", "Новый → КБ на складе"),
        ("Принято складом", "В работе", "КБ на складе → В работе"),
        ("В работе", "ОТК", "В работе → ОТК"),
    ]

    # Повторный upgrade ничего не делает.
    with sqlite3.connect(database) as connection:
        schema = _schema(connection)
    command.upgrade(_config(database), "0107_stage_ladder")
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

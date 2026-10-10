"""Ручная смена этапа проекта/КБ (владелец 10.10.2026): единый путь записи, история «было → стало», предпросмотр."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sqlite3
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

from app.modules.fulfillment.domain import FulfillmentStatus
from app.modules.production.domain import ProductionStatus, ProductionValidationError
from app.modules.production.infrastructure import (
    PLAN_EVENT_CHECKS,
    ProductionPlanEventRow,
    SqlAlchemyPlanEventRepository,
    SqlAlchemyProductionUnitOfWork,
)
from app.modules.production.plan import (
    PLAN_KINDS,
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
from tests.factories import Actor

ACTOR = Actor(name="Мастер", department="Производство")
NOW = datetime(2026, 10, 10, 12, 0)
BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "migrations" / "versions" / "0101_production_stage_history.py"


def _kit(index, status, *, manual=False, auto=None, shipped=False):
    return KitStageNow(index, status, manual, auto or status, shipped)


# ── Правила (plan.py) ───────────────────────────────────────────────────


def test_project_forward_raises_only_the_kbs_below_and_lists_the_skipped_stages():
    kits = (_kit("КБ1", "Новый"), _kit("КБ2", "В работе"), _kit("КБ3", "Отгружено", shipped=True))

    plan = plan_stage_change(kits, "КБ на складе")

    assert plan.direction == "forward" and not plan.reason_required and plan.error == ""
    assert [(c.item_index, c.from_stage, c.to_stage, c.skipped) for c in plan.changes] == [
        ("КБ1", "Новый", "КБ на складе", ("ТК готова", "КБ набрана", "В работе")),
        ("КБ2", "В работе", "КБ на складе", ()),
    ]
    # Ушедшая вперёд КБ не понижается.
    assert [(k.item_index, k.stage, k.note) for k in plan.kept] == [
        ("КБ3", "Отгружено", "Выше выбранного этапа — не понижаем."),
    ]


def test_project_pick_at_its_own_stage_changes_nothing():
    plan = plan_stage_change((_kit("КБ1", "В работе"), _kit("КБ2", "КБ на складе")), "В работе")

    assert plan.direction == "none" and plan.changes == ()


def test_backward_needs_a_reason_and_never_goes_below_a_shipment():
    kits = (_kit("КБ1", "КБ на складе"), _kit("КБ2", "Отгружено", shipped=True))

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
        ("КБ2", ("ТК готова", "КБ набрана", "В работе", "КБ на складе")),
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
        PlanEvent("708К.4", NOW, "Мастер", PLAN_KINDS["stage"], item_index="КБ1", value="КБ на складе",
                  from_value="КБ набрана"),
    )
    marks = summarize_kit_marks(events)
    assert marks[key].skipped == ("ТК готова", "В работе")

    stage = project_stage("708К.4", ("КБ1",), {"tc": {key}}, marks=marks).kits[0]
    # «ТК готова» случилась по факту (ТК выдана) — пропущенной остаётся только «В работе».
    assert (stage.status, stage.manual, stage.skipped) == ("КБ на складе", True, ("В работе",))

    shipped = project_stage("708К.4", ("КБ1",), {"shipped": {key}}, marks=marks).kits[0]
    assert (shipped.status, shipped.manual, shipped.skipped) == ("Отгружено", False, ())

    back = summarize_kit_marks((*events, PlanEvent(
        "708К.4", NOW, "Мастер", PLAN_KINDS["stage"], item_index="КБ1", value="КБ набрана", from_value="КБ на складе",
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
    plan = plan_stage_change((_kit("КБ1", "Новый"), _kit("КБ2", "ТК готова")), "КБ на складе")

    events = change_stage(ChangeStageCommand("708К.4", plan, ACTOR, NOW, expected_sequence=0), factory())

    assert [(e.sequence, e.item_index, e.from_value, e.value) for e in events] == [
        (1, "КБ1", "Новый", "КБ на складе"), (2, "КБ2", "ТК готова", "КБ на складе"),
    ]
    rows = _rows(sessions)
    assert [(row.kind, row.from_value, row.value, row.text) for row in rows] == [
        ("Этап КБ", "Новый", "КБ на складе", "Новый → КБ на складе · пропущены: ТК готова, КБ набрана, В работе"),
        ("Этап КБ", "ТК готова", "КБ на складе", "ТК готова → КБ на складе · пропущены: КБ набрана, В работе"),
    ]
    [audit] = _audit(sessions)
    assert audit.actor_name == "Мастер"
    assert audit.details == {
        "project": "708К.4", "value": "КБ на складе", "direction": "forward", "kbs": ["КБ1", "КБ2"],
        "changes": ["КБ1: Новый → КБ на складе", "КБ2: ТК готова → КБ на складе"],
        "skipped": ["ТК готова", "КБ набрана", "В работе"], "reason": "",
    }


def test_backward_without_reason_is_refused_and_with_reason_is_stored(production):
    factory, sessions = production
    plan = plan_stage_change((_kit("КБ1", "КБ на складе"),), "В работе", "КБ1")

    with pytest.raises(ProductionValidationError, match="Этап назад — напишите причину"):
        change_stage(ChangeStageCommand("708К.4", plan, ACTOR, NOW, reason="  "), factory())
    assert _rows(sessions) == []

    change_stage(ChangeStageCommand("708К.4", plan, ACTOR, NOW, reason="брак на ОТК"), factory())
    [row] = _rows(sessions)
    assert (row.from_value, row.value, row.reason) == ("КБ на складе", "В работе", "брак на ОТК")
    assert row.text == "КБ на складе → В работе · причина: брак на ОТК"


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

    change_stage(ChangeStageCommand("708К.4", plan, ACTOR, NOW, reopen_from="Отказ"), factory())

    assert [(row.kind, row.from_value, row.value) for row in _rows(sessions)] == [
        ("Статус проекта", "Отказ", "авто"), ("Этап КБ", "Новый", "В работе"),
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


def test_preview_lists_from_to_skipped_and_what_the_change_does_not_touch():
    facts = _facts(
        tasks=(SimpleNamespace(status=ProductionStatus.RUNNING, project="708К.4", item="КБ1"),
               SimpleNamespace(status=ProductionStatus.DONE, project="708К.4", item="КБ1")),
        fulfillment=(SimpleNamespace(status=FulfillmentStatus.NEW, purpose="708К.4 КБ1"),
                     SimpleNamespace(status=FulfillmentStatus.CANCELLED, purpose="708К.4 КБ1")),
        goods=(SimpleNamespace(project="708К.4", item_index="КБ1", ordered=Decimal("10"), accepted=Decimal("4"),
                               sent=Decimal("1"), unit="шт"),),
        events=(PlanEvent("708К.4", NOW, "РП", PLAN_KINDS["block"], blocker="ХОЛД", text="ждём оплату"),),
    )
    state = flow.project_stage_state("708К.4", ("КБ1", "КБ2"), facts, sequence=1)

    preview = flow.preview_project_stage(state, "Отгружено")

    # Выполненное задание цеха — факт работы: КБ1 «В работе».
    assert preview["direction"] == "forward" and preview["reason_required"] is False and preview["error"] == ""
    assert preview["kbs"] == [
        {"kb": "КБ1", "label": "КБ1", "from": "В работе", "to": "Отгружено", "skipped": ["КБ на складе"],
         "change": True, "note": "", "warnings": [
             "Открыто заданий цеха: 1 — смена этапа их не закрывает.",
             "Живых заявок на комплектацию: 1 — смена этапа их не трогает.",
             "Готовая продукция принята, не отгружена: 3 шт — смена этапа не отгружает.",
         ]},
        {"kb": "КБ2", "label": "КБ2", "from": "Новый", "to": "Отгружено",
         "skipped": ["ТК готова", "КБ набрана", "В работе", "КБ на складе"], "change": True, "note": "", "warnings": []},
    ]
    assert preview["warnings"] == ["Активный блокер «ХОЛД»: ждём оплату — смена этапа его не снимает."]
    assert preview["message"].startswith("КБ1: В работе → Отгружено (пропущены: КБ на складе); КБ2: Новый → Отгружено")


def test_preview_of_a_backward_pick_says_a_reason_is_needed():
    facts = _facts(events=(
        PlanEvent("708К.4", NOW, "РП", PLAN_KINDS["stage"], item_index="КБ1", value="КБ на складе", sequence=1),
    ))
    preview = flow.preview_project_stage(flow.project_stage_state("708К.4", ("КБ1",), facts), "В работе", "КБ1")

    assert (preview["direction"], preview["reason_required"]) == ("back", True)
    assert [(kb["from"], kb["to"], kb["skipped"]) for kb in preview["kbs"]] == [("КБ на складе", "В работе", [])]


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
        command.upgrade(_config(path), "0099_worker_heartbeats")
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

    command.downgrade(_config(database), "0099_worker_heartbeats")
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
    assert _revision(database) == "0099_worker_heartbeats"

    command.upgrade(_config(database), "0101_production_stage_history")
    assert "2 строк(и) нарушают" in capsys.readouterr().out
    with sqlite3.connect(database) as connection:
        assert sorted(connection.execute("SELECT kind, value FROM production_plan_events").fetchall()) == [
            ("comment", ""), ("Этап КБ", "Готово"),
        ]
    command.downgrade(_config(database), "0099_worker_heartbeats")


def test_migration_checks_match_the_model():
    spec = importlib.util.spec_from_file_location("stage_history_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.CHECKS == PLAN_EVENT_CHECKS
    assert module.KINDS == tuple(PLAN_KINDS.values())

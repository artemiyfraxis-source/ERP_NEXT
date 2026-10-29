"""Короткая лестница, две отметки «ТК готова» / «КБ набрана», один блокер «СТОП» (владелец 29.10.2026): чтение старых
записей журнала, ручной переключатель отметок, миграция 0161 (проверки этапа и блокера, журнал не переписывается)."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.production.domain import ProductionValidationError
from app.modules.production.infrastructure import PLAN_EVENT_CHECKS, SqlAlchemyPlanEventRepository
from app.modules.production.plan import (
    KIT_STAGES,
    PLAN_BLOCKERS,
    PLAN_KINDS,
    SHOP_STATUSES,
    PlanEvent,
    kit_stage_key,
    project_markers,
    project_stage,
    project_stage_now,
    summarize_journal,
    summarize_kit_marks,
    summarize_plan_marks,
    validate_plan_note,
)
from app.modules.production.plan_sheet import SHOP_HOLD, STAGE_SHOP_STATUS

BACKEND = Path(__file__).resolve().parents[2]
BEFORE = "0160_cancel_stale_new_requests"
AFTER = "0161_ladder_markers_blockers"
PROJECT = "708К.4"
T0 = datetime(2026, 10, 1, 9, 0)


def _stage_event(value, minute=0, item_index="КБ1", from_value=""):
    return PlanEvent(PROJECT, T0.replace(minute=minute), "Мастер", PLAN_KINDS["stage"], item_index=item_index,
                     value=value, from_value=from_value)


def _kit(*events, facts=None):
    return project_stage(PROJECT, ("КБ1",), facts or {}, marks=summarize_kit_marks(events)).kits[0]


# ── Чтение старых записей ──────────────────────────────────────────────


def test_the_lists_have_no_removed_words():
    assert KIT_STAGES == ("Новый", "В работе", "ОТК", "Принято складом", "Упаковано", "Отгружено")
    assert PLAN_BLOCKERS == ("СТОП", "ОЖИД ИНФ", "ОЖИД КОМПЛ")
    assert not {"ХОЛД", "ПАУЗА", "К ЗАПУСК", "РАСПЛАН"} & set(SHOP_STATUSES)
    assert SHOP_HOLD == frozenset(("СТОП",))
    assert not {"К ЗАПУСК", "РАСПЛАН"} & set(STAGE_SHOP_STATUS.values())
    assert set(STAGE_SHOP_STATUS) <= set(KIT_STAGES)


@pytest.mark.parametrize(("word", "markers"), [
    ("ТК готова", (True, False)), ("КБ набрана", (True, True)), ("Распланировано", (True, True)),
])
def test_an_old_marker_stage_row_is_no_manual_stage_and_turns_its_markers_on(word, markers):
    kit = _kit(_stage_event(word))
    assert (kit.status, kit.manual, (kit.tc, kit.kit), kit.skipped) == ("Новый", False, markers, ())
    # Факты решают этап: начатая работа — «В работе», отметки остаются.
    worked = _kit(_stage_event(word), facts={"work": {kit_stage_key(PROJECT, "КБ1")}})
    assert (worked.status, worked.manual, worked.tc, worked.kit) == ("В работе", False, True, True)
    now = project_stage_now(PROJECT, ("КБ1",), {}, summarize_kit_marks((_stage_event(word),)))[0]
    assert (now.status, now.manual, now.auto_status) == ("Новый", False, "Новый")


def test_old_marker_rows_hold_through_later_stages_and_clear_on_auto_or_new():
    old = _stage_event("ТК готова")
    assert (_kit(old, _stage_event("ОТК", 1, from_value="ТК готова")).tc) is True
    assert _kit(old, _stage_event("ОТК", 1), _stage_event("авто", 2)).tc is False
    assert _kit(old, _stage_event("Новый", 1)).tc is False
    # Назад со старого «КБ набрана» на «ТК готова» — комплект больше не отмечен.
    back = _kit(_stage_event("КБ набрана"), _stage_event("ТК готова", 1))
    assert (back.tc, back.kit) == (True, False)
    # «было» старым словом не ломает пропущенное: ручной «ОТК» после «КБ набрана» пропустил только «В работе».
    jumped = _kit(_stage_event("КБ набрана"), _stage_event("ОТК", 1, from_value="КБ набрана"))
    assert (jumped.status, jumped.manual, jumped.skipped) == ("ОТК", True, ())


def test_a_project_without_kbs_reads_an_old_marker_mark():
    marks = summarize_kit_marks((_stage_event("КБ набрана", item_index=""),))
    stage = project_stage(PROJECT, (), {}, marks=marks)
    assert (stage.status, stage.manual, stage.tc, stage.kit) == ("Новый", False, True, True)


def test_an_old_hold_or_pause_reads_as_stop_and_a_new_one_is_refused():
    for word in ("ХОЛД", "ПАУЗА"):
        journal = summarize_journal((PlanEvent(PROJECT, T0, "РП", PLAN_KINDS["block"], word, "ждём"),))[PROJECT]
        assert journal.blocker.type == "СТОП"
        with pytest.raises(ProductionValidationError, match=f"Блокера «{word}» больше нет — теперь «СТОП»."):
            validate_plan_note({"project": PROJECT, "kind": "block", "blocker": word, "text": "ждём"}, (PROJECT,))
    validate_plan_note({"project": PROJECT, "kind": "block", "blocker": "СТОП", "text": "ждём"}, (PROJECT,))


# ── Отметки проекта: факты и ручной переключатель ──────────────────────


def _mark(field, value, minute=0):
    return PlanEvent(PROJECT, T0.replace(minute=minute), "РП", PLAN_KINDS["mark"], field=field, value=value)


def test_project_markers_need_every_kb_and_the_toggle_overrides_both_ways():
    first, second = kit_stage_key(PROJECT, "КБ1"), kit_stage_key(PROJECT, "КБ2")
    stage = project_stage(PROJECT, ("КБ1", "КБ2"), {"tc": {first, second}, "kit": {first}})
    assert (stage.tc, stage.kit) == (True, False)
    auto = project_markers(stage)
    assert auto.payload() == {"tc_ready": True, "kit_picked": False, "tc_manual": None, "kit_manual": None}

    marks = summarize_plan_marks((_mark("kit_picked", "да"), _mark("tc_ready", "нет", 1)))[PROJECT]
    manual = project_markers(stage, marks)
    assert manual.payload() == {"tc_ready": False, "kit_picked": True, "tc_manual": "нет", "kit_manual": "да"}
    assert (manual.tc_auto, manual.kit_auto) == (True, False)

    cleared = summarize_plan_marks((_mark("kit_picked", "да"), _mark("kit_picked", "авто", 1)))[PROJECT]
    assert project_markers(stage, cleared).payload()["kit_manual"] is None


# ── Миграция 0161 ──────────────────────────────────────────────────────


def _config(path: Path) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


@pytest.fixture(scope="module")
def at_0160(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("ladder-markers") / "0160.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        command.upgrade(_config(path), BEFORE)
    return path


@pytest.fixture()
def database(at_0160, tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    shutil.copyfile(at_0160, path)
    return path


_EVENT = (
    "INSERT INTO production_plan_events (id, project, sequence, occurred_at, who, kind, blocker, text, item_index,"
    " value, field, from_value, reason) VALUES (?, ?, ?, '2026-10-01 09:00:00', 'РП', ?, ?, '', ?, ?, '', ?, '')"
)


def _event(connection, sequence, kind, value="", blocker="", item_index="КБ1", from_value="", project=PROJECT):
    connection.execute(_EVENT, (uuid4().hex, project, sequence, kind, blocker, item_index, value, from_value))


def _rows(path: Path):
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT project, sequence, kind, blocker, value, from_value FROM production_plan_events ORDER BY project, sequence"
        ).fetchall()


def _seed(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        _event(connection, 1, "Этап КБ", "ТК готова", from_value="Новый")
        _event(connection, 2, "Этап КБ", "КБ набрана", from_value="ТК готова")
        _event(connection, 3, "Этап КБ", "Распланировано", item_index="КБ2")
        _event(connection, 4, "Блокер", blocker="ХОЛД", item_index="")
        _event(connection, 5, "Блокер", blocker="ПАУЗА", item_index="")


def test_upgrade_keeps_every_old_row_reports_them_and_reads_them_as_the_new_model(database, capsys):
    _seed(database)
    before = _rows(database)

    command.upgrade(_config(database), AFTER)

    assert _rows(database) == before  # журнал не переписан
    out = capsys.readouterr().out
    for line in ("«Этап КБ» = «ТК готова»: 1", "«Этап КБ» = «КБ набрана»: 1", "«Этап КБ» = «Распланировано»: 1",
                 "«Блокер» = «ХОЛД»: 1", "«Блокер» = «ПАУЗА»: 1"):
        assert line in out
    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        with Session(engine) as session:
            events = tuple(SqlAlchemyPlanEventRepository(session).list())
    finally:
        engine.dispose()
    assert [event.blocker for event in events if event.kind == "Блокер"] == ["СТОП", "СТОП"]
    assert summarize_journal(events)[PROJECT].blocker.type == "СТОП"
    stage = project_stage(PROJECT, ("КБ1", "КБ2"), {}, marks=summarize_kit_marks(events))
    assert [(kit.status, kit.manual, kit.tc, kit.kit) for kit in stage.kits] == [
        ("Новый", False, True, True), ("Новый", False, True, True),
    ]


def test_new_checks_refuse_the_removed_words_keep_the_journal_append_only_and_downgrade(database):
    _seed(database)
    command.upgrade(_config(database), AFTER)

    with sqlite3.connect(database) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'production_plan_events'"
        ).fetchone()[0]
        assert all(name in sql for name in PLAN_EVENT_CHECKS)
        for word in ("ТК готова", "КБ набрана", "Распланировано"):
            with pytest.raises(sqlite3.IntegrityError, match="ck_production_plan_events_stage_value"):
                _event(connection, 10, "Этап КБ", word)
        with pytest.raises(sqlite3.IntegrityError, match="ck_production_plan_events_from_value"):
            _event(connection, 10, "Этап КБ", "В работе", from_value="КБ набрана")
        for word in ("ХОЛД", "ПАУЗА", ""):
            with pytest.raises(sqlite3.IntegrityError, match="ck_production_plan_events_blocker"):
                _event(connection, 10, "Блокер", blocker=word, item_index="")
        _event(connection, 10, "Этап КБ", "В работе", from_value="Новый")
        _event(connection, 11, "Блокер", blocker="СТОП", item_index="")
        _event(connection, 12, "Блокер снят", blocker="ХОЛД", item_index="")  # проверка только у «Блокер»
        with pytest.raises(sqlite3.DatabaseError, match="только дополняется"):
            connection.execute("UPDATE production_plan_events SET text = 'правка'")

    command.downgrade(_config(database), BEFORE)
    with sqlite3.connect(database) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'production_plan_events'"
        ).fetchone()[0]
        assert "ck_production_plan_events_blocker" not in sql and "'Распланировано'" in sql
        _event(connection, 20, "Этап КБ", "Распланировано", from_value="КБ набрана")
        _event(connection, 21, "Блокер", blocker="ХОЛД", item_index="")
        with pytest.raises(sqlite3.DatabaseError, match="только дополняется"):
            connection.execute("UPDATE production_plan_events SET text = 'правка'")
    command.upgrade(_config(database), AFTER)  # повторный проход по старым строкам не падает

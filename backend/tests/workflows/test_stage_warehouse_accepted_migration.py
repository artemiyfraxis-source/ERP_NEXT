"""Миграция 0108 (владелец 12.10.2026): проверки журнала на новую лестницу, «Отказ» / «Слился» → «Отменён» с прежним
словом в причине, этапы КБ в журнале не переписываются (читаются через псевдонимы)."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.production.infrastructure import PLAN_EVENT_CHECKS, SqlAlchemyPlanEventRepository
from app.modules.production.plan import PlanProjectSnapshot, project_terminal, summarize_journal

BACKEND = Path(__file__).resolve().parents[2]
BEFORE = "0107_stage_ladder"
AFTER = "0108_stage_warehouse_accepted"


def _config(path: Path) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


@pytest.fixture(scope="module")
def at_0107(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("stage-accepted") / "0107.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        command.upgrade(_config(path), BEFORE)
    return path


@pytest.fixture()
def database(at_0107, tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    shutil.copyfile(at_0107, path)
    return path


_EVENT = (
    "INSERT INTO production_plan_events (id, project, sequence, occurred_at, who, kind, blocker, text, item_index,"
    " value, field, from_value, reason) VALUES (?, ?, ?, '2026-10-01 09:00:00', 'РП', ?, '', ?, ?, ?, '', ?, '')"
)
_SNAPSHOT = (
    "INSERT INTO production_plan_projects (project, customer, opened_on, deadline, status, terminal, imported_at)"
    " VALUES (?, 'ООО', NULL, NULL, ?, ?, '2026-10-01 09:00:00')"
)


def _event(connection, project, sequence, kind, value, text="", item_index="", from_value=""):
    connection.execute(_EVENT, (uuid4().hex, project, sequence, kind, text, item_index, value, from_value))


def _seed(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        # А: закрыт словом «Отказ»; Б: закрыт «Слился» и возвращён в работу; В: только снимок реестра «Слился»;
        # Г: закрыт «Отменён»; Д: снимок «Отказ», но журнал вернул проект в работу.
        _event(connection, "10К.1", 1, "Статус проекта", "Отказ", "Отказ")
        _event(connection, "11К.1", 1, "Статус проекта", "Слился", "Слился")
        _event(connection, "11К.1", 2, "Статус проекта", "авто", "в работе", from_value="Слился")
        _event(connection, "13К.1", 1, "Статус проекта", "Отменён", "Отменён")
        _event(connection, "14К.1", 1, "Этап КБ", "Готово к отгрузке", "ГО", "КБ1", "ОТК")
        _event(connection, "14К.1", 2, "Этап КБ", "На отгрузке", "НО", "КБ1", "Готово к отгрузке")
        connection.execute(_SNAPSHOT, ("12К.1", "Слился", 1))
        connection.execute(_SNAPSHOT, ("15К.1", "Отказ", 1))
        _event(connection, "15К.1", 1, "Статус проекта", "авто", "в работе", from_value="Отказ")


def _rows(database: Path):
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT project, sequence, kind, value, from_value, reason, who FROM production_plan_events"
            " ORDER BY project, sequence"
        ).fetchall()


def test_closed_projects_get_one_cancelled_row_with_the_old_word_and_stage_rows_stay(database, capsys):
    _seed(database)
    before = _rows(database)

    command.upgrade(_config(database), AFTER)

    out = capsys.readouterr().out
    assert "проектов, закрытых словами «Отказ» / «Слился», переведено на «Отменён» записью журнала: 2" in out
    assert "строк реестра production_plan_projects переписано: 2" in out
    after = _rows(database)
    added = [row for row in after if row not in before]
    assert added == [
        ("10К.1", 2, "Статус проекта", "Отменён", "", "было «Отказ»", "миграция 0108"),
        ("12К.1", 1, "Статус проекта", "Отменён", "", "было «Слился»", "миграция 0108"),
    ]
    assert [row for row in before if row in after] == before  # журнал дополнен, не переписан
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT project, status FROM production_plan_projects ORDER BY project").fetchall() == [
            ("12К.1", "Отменён"), ("15К.1", "Отменён"),
        ]

    # Читается по-новому: закрытые — «Отменён», вернувшиеся в работу — нет, старые этапы — «Принято складом».
    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        with Session(engine) as session:
            events = tuple(SqlAlchemyPlanEventRepository(session).list())
    finally:
        engine.dispose()
    journal = summarize_journal(events)
    assert {project: entry.status for project, entry in journal.items()} == {
        "10К.1": "Отменён", "11К.1": "", "12К.1": "Отменён", "13К.1": "Отменён", "14К.1": None, "15К.1": "",
    }
    stages = [(event.from_value, event.value) for event in events if event.project == "14К.1"]
    assert stages == [("ОТК", "Принято складом"), ("Принято складом", "Принято складом")]
    assert project_terminal(PlanProjectSnapshot("12К.1", status="Слился", terminal=True), None) == "Отменён"


def test_second_run_and_new_checks(database):
    _seed(database)
    command.upgrade(_config(database), AFTER)
    first = _rows(database)
    command.downgrade(_config(database), BEFORE)
    command.upgrade(_config(database), AFTER)
    assert _rows(database) == first  # «Отменён» уже стоит - повторный проход ничего не добавляет

    with sqlite3.connect(database) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'production_plan_events'"
        ).fetchone()[0]
        assert all(name in sql for name in PLAN_EVENT_CHECKS)
        assert "'Распланировано'" in sql and "'Принято складом'" in sql
        with pytest.raises(sqlite3.IntegrityError, match="ck_production_plan_events_status_value"):
            _event(connection, "20К.1", 1, "Статус проекта", "Отказ")
        with pytest.raises(sqlite3.IntegrityError, match="ck_production_plan_events_stage_value"):
            _event(connection, "20К.1", 1, "Этап КБ", "На отгрузке", item_index="КБ1")
        with pytest.raises(sqlite3.IntegrityError, match="ck_production_plan_events_from_value"):
            _event(connection, "20К.1", 1, "Этап КБ", "Распланировано", item_index="КБ1", from_value="Готово к отгрузке")
        _event(connection, "20К.1", 1, "Этап КБ", "Распланировано", item_index="КБ1", from_value="КБ набрана")
        with pytest.raises(sqlite3.DatabaseError, match="только дополняется"):
            connection.execute("UPDATE production_plan_events SET text = 'правка'")

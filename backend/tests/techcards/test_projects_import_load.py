"""scripts/projects_import.py load: проекты по одному (владелец 30.09.2026) — только номера списка, один раз."""

import importlib.util
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database import configure_database
from app.shared.persistence import create_schema
from app.workflows.projects_wipe import loaded_projects, mark_loaded
from tests.techcards.calc_sheets import FakeSheets
from tests.techcards.test_kitset_api import CALC

_spec = importlib.util.spec_from_file_location("projects_import", Path(__file__).parents[3] / "scripts" / "projects_import.py")
projects_import = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(projects_import)

NOW = datetime(2026, 9, 30, 12, 0)
PROD = {
    "Проекты": [
        ["№ ПРОЕКТА", "ЗАКАЗЧИК", "ДАТА", "ДЕДЛАЙН", "СТАТУС"],
        ["708К.4", "ООО Заказчик", "01.09.2026", "20.10.2026", "В работе"],
        ["591К.3", "ООО Другой", "02.09.2026", "", ""],
    ],
    "Журнал проектов": [
        ["ДАТА", "№ ПРОЕКТА", "КТО", "ВИД", "БЛОКЕР", "ТЕКСТ", "КБ", "ЗНАЧЕНИЕ"],
        ["10.09.2026 11:30", "708К.4", "Технолог", "Комментарий", "", "Ждём КД от заказчика", "", ""],
        ["11.09.2026 09:00", "591К.3", "Технолог", "Комментарий", "", "Чужой проект", "", ""],
    ],
    "Наборы": [
        ["№ ПРОЕКТА", "РАСЧЁТ"],
        ["708К.4", CALC],
        ["591К.3", "https://docs.google.com/spreadsheets/d/1Other-calc/edit"],
    ],
    "Техкарты": [],
}
SHOP = [
    ["", "", "", "", "", ""],
    ["Комментарий", "Статус", "% Готовн", "В плане", "№ Проекта", "Наименование организции"],
    ["ОТГРУЗКА", "ОТГРУЖЕН", "100%", "TRUE", "714К. 1", "ООО"],
    ["Делаем", "В РАБОТЕ", "50%", "TRUE", "708К.4", "ООО Заказчик"],
    ["", "В РАБОТЕ", "10%", "TRUE", "591К.3", "ООО Другой"],
]


@pytest.fixture()
def state(tmp_path):
    holder = SimpleNamespace(state=SimpleNamespace())
    engine = configure_database(holder, f"sqlite+pysqlite:///{tmp_path / 'real.db'}")
    create_schema(engine)
    yield holder.state
    engine.dispose()


def test_only_listed_numbers_come_in_with_registry_journal_and_shop_sheet(state):
    report = projects_import.load_projects(["708К.4", "714К.1", "555К.1"], PROD, SHOP, FakeSheets(), state, now=NOW)

    assert report["loaded"] == ["708К.4: КБ 2, строк 3"]
    assert report["skipped"] == ["714К.1: отгружен по листу цеха «ЗАГРУЗ» — не загружаю"]
    assert report["failed"] == ["555К.1: номера нет ни в «Наборы», ни в «Проекты», ни в листе цеха"]
    assert sorted(report["done"]) == ["708К.4", "714К.1"]
    assert any("ТК генератором не собраны" in note for note in report["notes"])
    with state.techcards_uow_factory() as uow:
        assert [kitset.project for kitset in uow.kitsets.list()] == ["708К.4"]
    with state.production_uow_factory() as uow:
        registry = {item.project: item for item in uow.plan_projects.list()}
        events = list(uow.plan_events.list())
    assert list(registry) == ["708К.4"]
    assert (registry["708К.4"].customer, registry["708К.4"].deadline) == ("ООО Заказчик", date(2026, 10, 20))
    assert {event.project for event in events} == {"708К.4"}
    assert "Ждём КД от заказчика" in {event.text for event in events}
    assert any(event.who.startswith("план цеха") for event in events)


def test_a_rerun_duplicates_nothing(state):
    projects_import.load_projects(["708К.4"], PROD, SHOP, FakeSheets(), state, now=NOW)
    with state.production_uow_factory() as uow:
        before = len(list(uow.plan_events.list()))

    again = projects_import.load_projects(["708К.4"], PROD, SHOP, FakeSheets(), state, now=NOW)

    assert again["loaded"] == [] and again["done"] == ["708К.4"]
    with state.production_uow_factory() as uow:
        assert len(list(uow.plan_events.list())) == before


def test_list_file_takes_the_first_word_and_skips_comments(tmp_path):
    path = tmp_path / "projects.txt"
    path.write_text("# шапка\n\n708К.4   # проверить КБ2\n  591К.3\n", encoding="utf-8")
    assert projects_import.read_list(path) == ["708К.4", "591К.3"]
    assert projects_import.read_list(tmp_path / "нет.txt") == []


def test_cli_skips_loaded_numbers_without_google(tmp_path, monkeypatch, capsys, state):
    engine = state.database_engine
    mark_loaded(engine, ["708К.4"], NOW)
    path = tmp_path / "projects.txt"
    path.write_text("708К.4\n", encoding="utf-8")
    monkeypatch.setenv("ERP_DATABASE_URL", str(engine.url))
    monkeypatch.delenv("ERP_GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    args = SimpleNamespace(numbers=[], list=str(path), key=None, prod="", shop="")

    assert projects_import.load(args) == 0
    assert "новых 0" in capsys.readouterr().out
    assert loaded_projects(engine) == {"708К.4"}

    path.write_text("708К.4\n591К.3\n", encoding="utf-8")
    assert projects_import.load(args) == 2  # новый номер, а ключа Google нет — не загружен, повтор в следующий раз
    assert loaded_projects(engine) == {"708К.4"}

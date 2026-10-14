"""Миграция 0104: нормальные отделы (владелец 12.10.2026). Переименование отделов и перенос людей по отделу + ФИО,
порядок списка, флаг «Монтажник» по правилу не меняется ни у кого, повторный прогон ничего не трогает, откат
возвращает отделы, на пустой базе пишется только список."""

import importlib.util
import logging
import logging.config
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.modules.identity.level_migration import shop_worker_rule

BEFORE = "0103_hookup_wire_conductor_insulation"
AFTER = "0104_staff_departments"
MIGRATION = Path(__file__).parents[2] / "migrations" / "versions" / f"{AFTER}.py"
FINAL = [
    "Руководящий состав", "Производственный отдел", "Монтажный отдел", "Технический отдел",
    "Отдел технического контроля (ОТК)", "Отдел снабжения", "Склад", "Отдел продаж", "Маркетинг", "Бухгалтерия",
    "Административный отдел",
]
OLD_LIST = ["бог", "Монтаж", "Офис", "Производство (Цар)", "Руководство", "Фраксис", "Прочее"]
OFFICE_HEAD = uuid4()
# (name, department, role, site, active) — the shape of the production directory on 11.10.2026.
PEOPLE = [
    *[(name, "Монтаж", "staff", "ЦАР", True) for name in (
        "Богородская Элина", "Думанская Анна", "Епифанов Илья", "Макарова Полина", "Наумова Елена", "Попов Игнат",
        "Пузанов Александр", "Рыбкина Ольга", "Шилов Артем",
    )],
    *[(name, "Монтаж", "staff", "ЖУК", True) for name in (
        "Безменов Никита", "Евсеев Антон", "Зингер Антон", "Наумов Александр",
    )],
    ("Степан", "Монтаж", "master", "ЖУК", True),
    ("Монтажник без площадки", "Монтаж", "staff", None, True),  # shop only through the «монтаж» department
    ("Баковский Всеволод", "Офис", "management", None, True),
    ("Костромин Василий", "Офис", "management", None, True),
    ("Домрачев Виктор", "Офис", "buyer", None, True),
    ("Ладутько Ульяна", "Офис", "staff", None, True),
    ("Васильев Артемий", "Производство (Цар)", "admin", "ЦАР", True),
    ("Гвоздева Валерия", "Производство (Цар)", "master", "ЦАР", True),
    ("Касаточкин Василий", "Производство (Цар)", "prodhead", None, True),
    ("Мурашко Владимир", "Производство (Цар)", "staff", None, True),
    ("Администратор ERP", "Руководство", "admin", None, True),
    ("Артемий", "Руководство", "admin", None, False),
    ("Рустамов Вадим", "Фраксис", "management", None, True),
    ("Вишнев Александр", "Фраксис", "staff", None, True),
    ("Вася Пупкин", "бог", "admin", None, False),
    ("Гусев Алексей", "Приёмка/склад", "receiver", None, True),
]
EXPECTED = {
    "Монтажный отдел": [name for name, department, *_ in PEOPLE if department == "Монтаж"],
    "Производственный отдел": ["Васильев Артемий", "Гвоздева Валерия", "Касаточкин Василий", "Мурашко Владимир"],
    "Руководящий состав": [
        "Администратор ERP", "Артемий", "Баковский Всеволод", "Костромин Василий", "Рустамов Вадим",
    ],
    "Отдел снабжения": ["Домрачев Виктор"],
    "Административный отдел": ["Ладутько Ульяна", "Вишнев Александр"],
    "Склад": ["Гусев Алексей"],
    "": ["Вася Пупкин"],
}


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _module():
    spec = importlib.util.spec_from_file_location("migration_0104", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(path: Path, people=PEOPLE, saved=OLD_LIST) -> None:
    with sqlite3.connect(path) as connection:
        for index, (name, department, role, site, active) in enumerate(people):
            user_id = OFFICE_HEAD if name == "Ладутько Ульяна" else uuid4()
            connection.execute(
                "INSERT INTO identity_users (id, login, email, name, department, role, site, active, job_title, "
                "qualification, access_level, shop_worker, site_master) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', "
                "'operator', ?, ?)",
                (user_id.hex, f"u{index}", f"u{index}@example.test", name, department, role, site, active,
                 shop_worker_rule(role, site, department), role == "master"),
            )
        for position, name in enumerate(saved):
            connection.execute(
                "INSERT INTO identity_departments (position, name, head_user_id) VALUES (?, ?, ?)",
                (position, name, OFFICE_HEAD.hex if name == "Офис" else None),
            )
        connection.execute(
            "INSERT INTO identity_documents (id, title, kind, scope, scope_value, archived, created_by, created_at) "
            "VALUES (?, 'Положение о премировании', 'regulation', 'department', 'Монтаж', 0, ?, "
            "'2026-10-12 10:00:00.000000')",
            (uuid4().hex, OFFICE_HEAD.hex),
        )


def _snapshot(path: Path) -> dict:
    with sqlite3.connect(path) as connection:
        return {
            "departments": connection.execute(
                "SELECT name, head_user_id FROM identity_departments ORDER BY position").fetchall(),
            "users": sorted(connection.execute(
                "SELECT id, name, department, role, site, shop_worker FROM identity_users").fetchall()),
            "documents": connection.execute("SELECT scope, scope_value FROM identity_documents").fetchall(),
            "events": connection.execute(
                "SELECT count(*) FROM audit_events WHERE event_type = 'identity.departments_saved'").fetchone()[0],
        }


def _people(snapshot: dict) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for _id, name, department, *_rest in snapshot["users"]:
        result.setdefault(department, []).append(name)
    return {department: sorted(names) for department, names in result.items()}


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "departments.db"
    command.upgrade(_config(path), BEFORE)
    _seed(path)
    return path


def test_departments_are_renamed_and_people_moved(seeded):
    before = _snapshot(seeded)
    command.upgrade(_config(seeded), AFTER)
    after = _snapshot(seeded)

    assert [name for name, _head in after["departments"]] == FINAL + ["Прочее"]
    heads = dict(after["departments"])
    assert heads["Административный отдел"] == OFFICE_HEAD.hex  # «Офис» head carried by the rename
    assert all(head is None for name, head in after["departments"] if name != "Административный отдел")
    assert _people(after) == {department: sorted(names) for department, names in EXPECTED.items()}
    assert after["documents"] == [("department", "Монтажный отдел")]
    assert after["events"] == before["events"] + 1


def test_shop_flag_is_the_same_for_everyone(seeded):
    before = {row[0]: row for row in _snapshot(seeded)["users"]}
    command.upgrade(_config(seeded), AFTER)
    after = {row[0]: row for row in _snapshot(seeded)["users"]}
    assert set(before) == set(after)
    for user_id, (_id, name, department, role, site, stored) in before.items():
        new_department, new_stored = after[user_id][2], after[user_id][5]
        assert shop_worker_rule(role, site, department) == shop_worker_rule(role, site, new_department), name
        assert stored == new_stored, name
    assert sum(row[5] for row in after.values()) == 14  # 13 installers with a site + one through the department


def test_every_mapping_keeps_the_installer_rule():
    module = _module()
    pairs = [*module.RENAMES.items(), *((source, target) for source, _name, target in module.MOVES)]
    for source, target in pairs:
        assert ("монтаж" in source.lower()) == ("монтаж" in target.lower()), (source, target)
    assert list(module.FINAL) == FINAL


def test_second_run_changes_nothing(seeded):
    config = _config(seeded)
    command.upgrade(config, AFTER)
    first = _snapshot(seeded)
    command.stamp(config, BEFORE)
    command.upgrade(config, AFTER)
    assert _snapshot(seeded) == first


def test_downgrade_brings_the_departments_back(seeded):
    before = _snapshot(seeded)
    config = _config(seeded)
    command.upgrade(config, AFTER)
    command.downgrade(config, BEFORE)
    after = _snapshot(seeded)
    assert after["users"] == before["users"]
    assert after["documents"] == before["documents"]
    names = [name for name, _head in after["departments"]]
    assert sorted(names) == sorted(OLD_LIST)
    assert dict(after["departments"])["Офис"] == OFFICE_HEAD.hex


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_a_person_who_is_not_found_is_skipped_not_guessed(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "partial.db"
    command.upgrade(_config(path), BEFORE)
    people = [
        ("Гусев Алексей", "Склад старый", "receiver", None, True),  # another department — not him
        ("Рустамов Вадим", "Фраксис", "management", None, True),
        ("Рустамов Вадим", "Фраксис", "management", None, True),  # twice — ambiguous
        ("Ладутько Ульяна", "Офис", "staff", None, True),
    ]
    _seed(path, people=people)
    # env.py's fileConfig would reset every handler of the alembic loggers; without it the warnings reach ours.
    monkeypatch.setattr(logging.config, "fileConfig", lambda *_args, **_kwargs: None)
    records = _Records()
    migration_log = logging.getLogger("alembic.runtime.migration")
    migration_log.addHandler(records)
    try:
        command.upgrade(_config(path), AFTER)
    finally:
        migration_log.removeHandler(records)
    after = _snapshot(path)
    assert _people(after) == {
        "Склад старый": ["Гусев Алексей"], "Фраксис": ["Рустамов Вадим", "Рустамов Вадим"],
        "Административный отдел": ["Ладутько Ульяна"],
    }
    names = [name for name, _head in after["departments"]]
    assert names == FINAL + ["Фраксис", "Прочее"]  # «Фраксис» still has people, «бог» is empty and leaves
    warned = " ".join(records.messages)
    assert "Гусев Алексей" in warned and "Рустамов Вадим" in warned and "найдено 2" in warned


def test_a_fresh_database_gets_only_the_list(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "fresh.db"
    command.upgrade(_config(path), AFTER)
    snapshot = _snapshot(path)
    assert snapshot["departments"] == [(name, None) for name in FINAL]
    assert snapshot["users"] == [] and snapshot["events"] == 0
    command.stamp(_config(path), BEFORE)
    command.upgrade(_config(path), AFTER)
    assert _snapshot(path) == snapshot


def test_list_helpers_keep_order_and_heads():
    module = _module()
    head = UUID(int=7)
    saved = [("Офис", head), ("бог", None), ("Цех", None)]
    assert module.upgraded_list(saved, {"цех"}) == [
        *((name, head if name == "Административный отдел" else None) for name in FINAL), ("Цех", None),
    ]
    # Renamed entries get their old names back; added ones nobody has leave; «бог» comes back when someone has it.
    assert module.downgraded_list(module.upgraded_list(saved, {"цех"}), {"офис", "бог", "цех"}) == [
        ("Руководство", None), ("Производство (Цар)", None), ("Монтаж", None), ("Офис", head), ("Цех", None),
        ("бог", None),
    ]

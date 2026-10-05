"""Проверка целостности базы вокруг миграции: снимок до, снимок после, сравнение (app.db_integrity) на SQLite."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app import db_integrity
from app.db_integrity import DEFAULT_MIGRATIONS_DIR, compare, heads, migrations_between, read_migrations, take_snapshot
from tests.migrations.seed_any_revision import seed

MIGRATIONS = read_migrations(DEFAULT_MIGRATIONS_DIR)
HEAD = heads(MIGRATIONS)[0]
PREVIOUS = MIGRATIONS[HEAD].down_revisions[0]


def _url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


def _config(path: Path) -> Config:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", _url(path))
    return config


@pytest.fixture(scope="module")
def seeded_previous(tmp_path_factory) -> Path:
    """A database at head-1 with a few rows in every table, built once and copied per test."""
    path = tmp_path_factory.mktemp("integrity") / "previous.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        command.upgrade(_config(path), PREVIOUS)
    engine = create_engine(_url(path))
    try:
        inserted = seed(engine)
    finally:
        engine.dispose()
    assert inserted.get("warehouse_lots") and inserted.get("fulfillment_kit_reserves")
    return path


@pytest.fixture
def database(seeded_previous, tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    shutil.copyfile(seeded_previous, path)
    return path


def _snapshot(path: Path) -> dict:
    engine = create_engine(_url(path))
    try:
        return take_snapshot(engine)
    finally:
        engine.dispose()


def _execute(path: Path, sql: str) -> None:
    engine = create_engine(_url(path))
    try:
        with engine.begin() as connection:
            connection.execute(text(sql))
    finally:
        engine.dispose()


def _upgraded(path: Path) -> tuple[dict, dict]:
    before = _snapshot(path)
    command.upgrade(_config(path), "head")
    return before, _snapshot(path)


def test_seeded_snapshot_is_clean_and_counts_every_table(database):
    snapshot = _snapshot(database)

    assert snapshot["revision"] == PREVIOUS
    assert snapshot["tables"]["alembic_version"] == 1
    assert snapshot["tables"]["warehouse_lots"] == 6
    assert snapshot["sums"]["stock_total"] == "555"  # Σ (100 + i − 10), i = 0…5
    assert snapshot["sums"]["reserved_total"] == "120"
    assert {"stock:ЦАР", "stock:ЖУК"} <= set(snapshot["sums"])
    assert {key for key, value in snapshot["counts"].items() if value} == set()
    assert "fk_orphans:production_operations(task_id)->production_tasks" in snapshot["counts"]
    assert "fk_not_validated" not in snapshot["counts"]  # PostgreSQL only


def test_upgrade_over_real_rows_passes(database):
    before, after = _upgraded(database)

    assert after["revision"] == HEAD
    assert compare(before, after) == []


def test_unchanged_database_passes(database):
    command.upgrade(_config(database), "head")

    assert compare(_snapshot(database), _snapshot(database)) == []


def test_table_losing_rows_without_destructive_fails_in_russian(database):
    before = _snapshot(database)
    command.upgrade(_config(database), "head")
    _execute(database, "DELETE FROM warehouse_writeoffs WHERE id IN (SELECT id FROM warehouse_writeoffs LIMIT 2)")

    violations = compare(before, _snapshot(database))

    assert violations == [
        f"Таблица warehouse_writeoffs потеряла строки: было 6, стало 4 (−2), "
        f"а миграция {HEAD} не объявляет DESTRUCTIVE = True."
    ]


def test_table_losing_rows_passes_when_every_migration_in_range_is_destructive(database, tmp_path):
    before = _snapshot(database)
    command.upgrade(_config(database), "head")
    _execute(database, "DELETE FROM warehouse_writeoffs WHERE id IN (SELECT id FROM warehouse_writeoffs LIMIT 2)")
    _execute(database, "UPDATE warehouse_lots SET written_off = written_off + 1")
    after = _snapshot(database)
    versions = tmp_path / "versions"
    shutil.copytree(DEFAULT_MIGRATIONS_DIR, versions, ignore=shutil.ignore_patterns("__pycache__"))
    head_file = versions / MIGRATIONS[HEAD].file

    assert compare(before, after, versions) != []
    head_file.write_text(head_file.read_text(encoding="utf-8") + "\nDESTRUCTIVE = True\n", encoding="utf-8")
    assert compare(before, after, versions) == []


def test_over_reserve_appearing_fails(database):
    command.upgrade(_config(database), "head")
    before = _snapshot(database)
    _execute(database, "UPDATE fulfillment_kit_reserves SET quantity = 50"
                       " WHERE id = (SELECT min(id) FROM fulfillment_kit_reserves)")

    violations = compare(before, _snapshot(database))

    assert violations == [
        "Стало хуже — партии, где держатели резерва (комплектация, техкарты, «Со склада») держат больше РЕЗЕРВА "
        "партии: было 0, стало 1."
    ]


def test_negative_stock_and_changed_stock_sum_fail(database):
    command.upgrade(_config(database), "head")
    before = _snapshot(database)
    _execute(database, "UPDATE warehouse_lots SET written_off = 200 WHERE id = (SELECT min(id) FROM warehouse_lots)")

    violations = compare(before, _snapshot(database))

    assert "Стало хуже — отрицательный остаток (ПРИХОД − СПИСАНО) в warehouse_lots: было 0, стало 1." in violations
    assert "Стало хуже — партии с РЕЗЕРВОМ больше остатка (ПРИХОД − СПИСАНО): было 0, стало 1." in violations
    changed = "Изменилась сумма остатков по партиям (ПРИХОД − СПИСАНО): было 555"
    assert any(line.startswith(changed) for line in violations)


def test_orphan_rows_and_reserves_on_missing_lot_fail(database):
    command.upgrade(_config(database), "head")
    before = _snapshot(database)
    _execute(database, "DELETE FROM production_tasks WHERE id = (SELECT min(task_id) FROM production_operations)")
    _execute(database, "UPDATE procurement_stock_covers SET lot_id = '00000000000000000000000000000000'"
                       " WHERE id = (SELECT min(id) FROM procurement_stock_covers)")

    violations = compare(before, _snapshot(database))

    assert "Стало хуже — строки production_operations(task_id) ссылаются на отсутствующие строки production_tasks: " \
           "было 0, стало 1." in violations
    assert "Стало хуже — резервы «Со склада» на несуществующей партии (procurement_stock_covers): было 0, стало 1." \
           in violations


def test_revision_behind_head_fails(database):
    snapshot = _snapshot(database)

    assert compare(snapshot, snapshot) == [
        f"Ревизия базы после обновления — {PREVIOUS}, а последняя миграция — {HEAD}."
    ]


def test_cli_snapshot_and_compare(database, tmp_path, capsys):
    url = _url(database)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    assert db_integrity.main(["snapshot", "--out", str(before), "--database-url", url]) == 0
    command.upgrade(_config(database), "head")
    assert db_integrity.main(["snapshot", "--out", str(after), "--database-url", url]) == 0
    assert db_integrity.main(["compare", str(before), str(after)]) == 0
    _execute(database, "DELETE FROM notifications")
    assert db_integrity.main(["snapshot", "--out", str(after), "--database-url", url]) == 0
    capsys.readouterr()

    assert db_integrity.main(["compare", str(before), str(after)]) == 1
    assert "Таблица notifications потеряла строки: было 6, стало 0 (−6)" in capsys.readouterr().out


def _write(directory: Path, name: str, body: str) -> None:
    (directory / name).write_text(body, encoding="utf-8")


def test_range_is_destructive_only_when_every_migration_declares_it(tmp_path):
    _write(tmp_path, "0001_a.py", 'revision = "0001_a"\ndown_revision = None\n')
    _write(tmp_path, "0002_b.py",
           'revision: str = "0002_b"\ndown_revision: str | None = "0001_a"\nDESTRUCTIVE = True\n')
    _write(tmp_path, "0003_c.py", 'revision: str = "0003_c"\ndown_revision: str | None = "0002_b"\n')
    migrations = read_migrations(tmp_path)

    assert heads(migrations) == ["0003_c"]
    assert [m.revision for m in migrations_between(migrations, "0001_a", "0003_c")] == ["0002_b", "0003_c"]
    assert [m.destructive for m in migrations_between(migrations, "0001_a", "0003_c")] == [True, False]
    assert migrations_between(migrations, "0003_c", "0003_c") == []
    assert migrations_between(migrations, "0003_c", "0001_a") is None  # a downgrade is not a range
    assert [m.revision for m in migrations_between(migrations, None, "0002_b")] == ["0001_a", "0002_b"]

    before = {"revision": "0001_a", "tables": {"t": 3}, "counts": {}, "sums": {}}
    after = {"revision": "0003_c", "tables": {"t": 1}, "counts": {}, "sums": {}}
    assert compare(before, after, tmp_path) == [
        "Таблица t потеряла строки: было 3, стало 1 (−2), а миграция 0003_c не объявляет DESTRUCTIVE = True."
    ]
    before["revision"] = "0002_b"
    _write(tmp_path, "0003_c.py", 'revision = "0003_c"\ndown_revision = "0002_b"\nDESTRUCTIVE: bool = True\n')
    assert compare(before, after, tmp_path) == []

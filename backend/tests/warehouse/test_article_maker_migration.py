"""Migration 0146: the old lots «XHP-4@JST» get the bare article and the maker in «Производитель»; free kit lines that
copied the lot's article follow it; kit lines of a project and techcard reserves keep the spec row's article; downgrade
puts everything back from the logs (owner 23.10.2026: «дели старые партии тоже»)."""

import importlib.util
import json
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.shared.article_maker import split_article_maker
from tests.migrations.seed_any_revision import seed

ROOT = Path(__file__).parents[2]
REVISION = "0146_split_article_maker"
FIXTURE = Path(__file__).parent / "fixtures" / "prod-maker-articles-1023.json"


def _migration():
    path = ROOT / "migrations" / "versions" / f"{REVISION}.py"
    spec = importlib.util.spec_from_file_location("migration_0146", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def test_migration_follows_the_scan_queue_and_is_the_head():
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert script.get_revision(REVISION).down_revision == "0145_warehouse_scan_jobs"
    assert script.get_heads() == [REVISION]
    assert _migration().DESTRUCTIVE is False


def test_frozen_copy_splits_like_the_shared_rule():
    split = _migration()._split
    cases = list(json.loads(FIXTURE.read_text(encoding="utf-8"))) + [
        "", "  ", "GHR-04V-S", "info@mail.ru", "XHP-4@J", "@JST", "A@B@JST", "SXH-001T-P0.6##6000",
        "SHF-001T-0.8BS упаковка 5 000 шт.", "0022552401©MOLEX", "XHP-4 @ jst ", "PHR-2@Китай",
    ]
    for text in cases:
        assert tuple(split(text)) == tuple(split_article_maker(text)), text


def _rows(connection, sql, *args):
    return connection.execute(sql, args).fetchall()


def test_lots_are_split_logged_and_restored(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = _config(path)
    command.upgrade(config, "0145_warehouse_scan_jobs")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        seed(engine)
    finally:
        engine.dispose()

    articles = [
        ("XHP-4@JST", "", ""),
        ("SPH-002T-P0.5L##8000@JST упаковка 8000 шт", "J.S.T. Mfg", "контакт"),
        ("0510210700@MOLEX SH12501-7Y china (на тесты)", "", ""),
        ("SMA-LC174P@RUICHI, SMA-Male R/A RG174/RG316", "", ""),
        ("GHR-04V-S", "", ""),
        ("0500588001 @MOLEX", "", ""),
    ]
    with sqlite3.connect(path) as connection:
        lots = [row[0] for row in _rows(connection, "SELECT id FROM warehouse_lots ORDER BY id")][:6]
        assert len(lots) == 6
        for lot, (article, maker, description) in zip(lots, articles):
            connection.execute(
                "UPDATE warehouse_lots SET article = ?, manufacturer = ?, description = ?, version = 3 WHERE id = ?",
                (article, maker, description, lot))
        requests = [row[0] for row in _rows(connection, "SELECT id FROM fulfillment_requests ORDER BY id")][:2]
        free, kit = requests
        connection.execute("UPDATE fulfillment_requests SET project_key = NULL, version = 5 WHERE id = ?", (free,))
        connection.execute("UPDATE fulfillment_requests SET project_key = '591К.4', version = 5 WHERE id = ?", (kit,))
        lines = [row[0] for row in _rows(connection, "SELECT id FROM fulfillment_lines ORDER BY id")][:3]
        line_free, line_kit, line_other = lines
        for line, request, lot, article in (
            (line_free, free, lots[0], " XHP-4@JST"),  # free request, the lot's article: follows the lot
            (line_kit, kit, lots[0], "XHP-4@JST"),  # a project's kit: the spec row's article stays
            (line_other, free, lots[5], "Контакт PicoBlade"),  # own text: stays
        ):
            connection.execute(
                "UPDATE fulfillment_lines SET request_id = ?, lot_id = ?, article = ? WHERE id = ?",
                (request, lot, article, line))
        reserve = _rows(connection, "SELECT id FROM fulfillment_kit_reserves ORDER BY id")[0][0]
        connection.execute(
            "UPDATE fulfillment_kit_reserves SET lot_id = ?, article = 'XHP-4@JST' WHERE id = ?", (lots[0], reserve))
        other_requests = _rows(connection, "SELECT id, version FROM fulfillment_requests WHERE id NOT IN (?, ?)", free, kit)

    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        lot_rows = {
            row[0]: row[1:] for row in _rows(
                connection, "SELECT id, article, manufacturer, description, version FROM warehouse_lots")
        }
        assert [lot_rows[lot] for lot in lots] == [
            ("XHP-4", "JST", "", 4),
            ("SPH-002T-P0.5L", "J.S.T. Mfg", "контакт; упаковка 8000 шт", 4),  # a filled maker is kept
            ("0510210700@MOLEX SH12501-7Y china (на тесты)", "", "", 3),  # not a maker: left for the owner
            ("SMA-LC174P", "RUICHI", "SMA-Male R/A RG174/RG316", 4),
            ("GHR-04V-S", "", "", 3),
            ("0500588001", "MOLEX", "", 4),
        ]
        log = _rows(connection, "SELECT lot_id, old_article, new_article, old_manufacturer, new_manufacturer "
                                "FROM warehouse_article_split_log ORDER BY lot_id")
        assert {row[0] for row in log} == {lots[0], lots[1], lots[3], lots[5]}
        assert (lots[0], "XHP-4@JST", "XHP-4", "", "JST") in log
        line_rows = dict(_rows(connection, "SELECT id, article FROM fulfillment_lines"))
        assert (line_rows[line_free], line_rows[line_kit], line_rows[line_other]) == (
            "XHP-4", "XHP-4@JST", "Контакт PicoBlade")
        assert _rows(connection, "SELECT line_id, old_article, new_article FROM warehouse_article_split_line_log") == [
            (line_free, " XHP-4@JST", "XHP-4")]
        versions = dict(_rows(connection, "SELECT id, version FROM fulfillment_requests"))
        assert (versions[free], versions[kit]) == (6, 5)
        assert all(versions[request] == version for request, version in other_requests)
        assert _rows(connection, "SELECT article FROM fulfillment_kit_reserves WHERE id = ?", reserve) == [
            ("XHP-4@JST",)]
        # A hand edit after the migration survives the downgrade.
        connection.execute("UPDATE warehouse_lots SET manufacturer = 'JST Mfg' WHERE id = ?", (lots[0],))

    command.downgrade(config, "0145_warehouse_scan_jobs")
    with sqlite3.connect(path) as connection:
        lot_rows = {
            row[0]: row[1:] for row in _rows(
                connection, "SELECT id, article, manufacturer, description, version FROM warehouse_lots")
        }
        assert [lot_rows[lot] for lot in lots] == [
            ("XHP-4@JST", "JST Mfg", "", 5),
            ("SPH-002T-P0.5L##8000@JST упаковка 8000 шт", "J.S.T. Mfg", "контакт", 5),
            ("0510210700@MOLEX SH12501-7Y china (на тесты)", "", "", 3),
            ("SMA-LC174P@RUICHI, SMA-Male R/A RG174/RG316", "", "", 5),
            ("GHR-04V-S", "", "", 3),
            ("0500588001 @MOLEX", "", "", 5),
        ]
        line_rows = dict(_rows(connection, "SELECT id, article FROM fulfillment_lines"))
        assert line_rows[line_free] == " XHP-4@JST"
        versions = dict(_rows(connection, "SELECT id, version FROM fulfillment_requests"))
        assert (versions[free], versions[kit]) == (7, 5)
        tables = {row[0] for row in _rows(connection, "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert not {"warehouse_article_split_log", "warehouse_article_split_line_log"} & tables


def test_the_ci_seeder_fills_the_logs_at_head(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    command.upgrade(_config(path), "head")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        inserted = seed(engine)
    finally:
        engine.dispose()
    assert inserted.get("warehouse_article_split_log") and inserted.get("warehouse_article_split_line_log")

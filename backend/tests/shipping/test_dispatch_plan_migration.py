"""0122: план отгрузки в карточке проекта и место хранения ГП — пустые колонки, старые строки не меняются; откат
снимает колонки и делает пустым тип «БЕЗ УПД», которого старый код не знает."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

BEFORE = "0121_kitset_folder_url"
AFTER = "0122_shipping_dispatch_plan"

_GOOD = (
    "INSERT INTO finished_goods (id, version, project, item_index, customer, article, name, ordered, unit, accepted,"
    " shipped, received_at, grn, note, sent, before_erp, project_key)"
    " VALUES ('a1', 1, '591К.3', 'КБ1', '', '', 'Жгут', 10, 'шт', 4, 0, '2026-10-02', NULL, '', 0, 0, '591К.3')"
)
_CARD = (
    "INSERT INTO shipping_project_addressees (project, version, receiver, shipping_point, contact, phone, carrier,"
    " upd_type, updated_by) VALUES ('591К.3', 3, '', 'Москва', '', '', '', 'ЭДО', '')"
)
_SHIPMENT = (
    "INSERT INTO shipments (id, number, version, created_at, status, customer, receiver, shipping_point, contact, phone,"
    " terms, carrier, deadline, upd_type, upd_number, upd_date, upd_url, pl_number, dn_number, track_number, packed_at,"
    " shipped_at, manager, manager_id, note, tk_note, inherited_fields) VALUES ('s1', 'ОТГ-1', 1,"
    " '2026-10-01 09:00:00.000000', 'Новая', '', 'П', 'Москва', '', '', NULL, '', NULL, 'ЭДО', '', NULL, '', '', '', '',"
    " NULL, NULL, 'М', NULL, '', '', '')"
)


def _config(path: Path) -> Config:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _columns(connection, table: str) -> dict[str, tuple]:
    return {row[1]: (row[2], row[3]) for row in connection.execute(f"PRAGMA table_info({table})")}


def test_dispatch_plan_columns_are_empty_on_old_rows_and_downgrade_clears_without_upd(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute(_GOOD)
        connection.execute(_CARD)
        connection.execute(_SHIPMENT)
    capsys.readouterr()

    command.upgrade(config, AFTER)

    assert "shipping_project_addressees 1 → 1, finished_goods 1 → 1" in capsys.readouterr().out
    with sqlite3.connect(path) as connection:
        cards = _columns(connection, "shipping_project_addressees")
        assert cards["plan_date"][1] == 0 and cards["note"][1] == 1
        assert _columns(connection, "finished_goods")["location"] == ("VARCHAR(255)", 1)
        assert connection.execute("SELECT version, upd_type, plan_date, note FROM shipping_project_addressees").fetchall() == [
            (3, "ЭДО", None, ""),
        ]
        assert connection.execute("SELECT accepted, location FROM finished_goods").fetchall() == [(4, "")]
        connection.execute("UPDATE shipping_project_addressees SET upd_type = 'БЕЗ УПД', plan_date = '2026-10-20'")
        connection.execute("UPDATE shipments SET upd_type = 'БЕЗ УПД'")
        connection.execute("UPDATE finished_goods SET location = 'Стеллаж 3'")

    command.downgrade(config, BEFORE)

    assert "тип «БЕЗ УПД» снят у 2 строк" in capsys.readouterr().out
    with sqlite3.connect(path) as connection:
        assert {"plan_date", "note"}.isdisjoint(_columns(connection, "shipping_project_addressees"))
        assert "location" not in _columns(connection, "finished_goods")
        assert connection.execute("SELECT upd_type FROM shipments").fetchall() == [(None,)]
        assert connection.execute("SELECT project, upd_type FROM shipping_project_addressees").fetchall() == [("591К.3", None)]
        assert connection.execute("SELECT COUNT(*) FROM finished_goods").fetchone()[0] == 1

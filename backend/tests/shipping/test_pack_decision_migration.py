"""0162: решение «упаковывать» ставится проектам, которые сегодня упаковываются (незакрытая отправка или принятая и не
упакованная ГП), CHECK «кто и когда вместе» у карточки и у разрешения «без УПД»; откат снимает столбцы."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

BEFORE = "0161_ladder_markers_blockers"
AFTER = "0162_pack_decision_no_upd"

_CARD = (
    "INSERT INTO shipping_project_addressees (project, version, receiver, shipping_point, contact, phone, carrier,"
    " updated_by) VALUES (?, ?, '', 'Москва', '', '', '', '')"
)
_SHIPMENT = (
    "INSERT INTO shipments (id, number, version, created_at, status, customer, receiver, shipping_point, contact, phone,"
    " terms, carrier, deadline, upd_type, upd_number, upd_date, upd_url, pl_number, dn_number, track_number, packed_at,"
    " shipped_at, manager, manager_id, note, tk_note, inherited_fields) VALUES (?, ?, 1, '2026-10-01 09:00:00.000000', ?,"
    " '', 'П', '', '', '', NULL, '', NULL, 'ЭДО', '', NULL, '', '', '', '', NULL, NULL, 'М', NULL, '', '', '')"
)
_GOOD = (
    "INSERT INTO finished_goods (id, version, project, item_index, customer, article, name, ordered, unit, accepted,"
    " shipped, received_at, grn, note, sent, before_erp, project_key, location) VALUES (?, 1, ?, 'КБ1', '', '', 'Жгут',"
    " 10, 'шт', ?, ?, NULL, NULL, '', ?, 0, ?, '')"
)
_LINE = (
    "INSERT INTO shipment_lines (id, shipment_id, finished_good_id, position, project, item_index, article, name,"
    " requested, picked, unit, grn, project_key) VALUES (?, ?, ?, 0, ?, 'КБ1', '', 'Жгут', 1, 1, 'шт', NULL, ?)"
)


def _id(n: int) -> str:
    return f"{n:032x}"


def _config(path: Path) -> Config:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def test_pack_decision_for_projects_being_packed_and_the_checks(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        # 600: открытая отправка, карточка есть; 601: принято и не упаковано, карточки нет; 602: всё уехало и закрыто;
        # 603: карточка без движения ГП.
        connection.execute(_CARD, ("600", 4))
        connection.execute(_CARD, ("603", 2))
        connection.execute(_GOOD, (_id(1), "600", 1, 1, 0, "600"))
        connection.execute(_GOOD, (_id(2), "601", 5, 2, 0, "601"))
        connection.execute(_GOOD, (_id(3), "602", 1, 1, 1, "602"))
        connection.execute(_SHIPMENT, (_id(11), "ОТГ-1", "Собрана"))
        connection.execute(_SHIPMENT, (_id(12), "ОТГ-2", "Закрыта"))
        connection.execute(_LINE, (_id(21), _id(11), _id(1), "600", "600"))
        connection.execute(_LINE, (_id(22), _id(12), _id(3), "602", "602"))
    capsys.readouterr()

    command.upgrade(config, AFTER)

    out = capsys.readouterr().out
    assert "проектов 2 (карточек обновлено 1, добавлено 1)" in out
    assert "shipping_project_addressees 2 → 3, shipments 2 → 2" in out
    with sqlite3.connect(path) as connection:
        cards = {row[0]: row[1:] for row in connection.execute(
            "SELECT project, version, pack_decided, pack_decided_by, pack_decided_at IS NOT NULL, upd_number, upd_date"
            " FROM shipping_project_addressees"
        )}
        shipments = set(connection.execute("SELECT no_upd_approved_by, no_upd_approved_at FROM shipments"))
        assert cards == {
            "600": (5, 1, "Миграция", 1, "", None),
            "601": (1, 1, "Миграция", 1, "", None),
            "603": (2, 0, "", 0, "", None),
        }
        assert shipments == {("", None)}
        with pytest.raises(sqlite3.IntegrityError):  # решение без «кто»
            connection.execute("UPDATE shipping_project_addressees SET pack_decided_by = '' WHERE project = '600'")
        with pytest.raises(sqlite3.IntegrityError):  # «кто» без флага и времени
            connection.execute("UPDATE shipping_project_addressees SET pack_decided_by = 'Ульяна' WHERE project = '603'")
        with pytest.raises(sqlite3.IntegrityError):  # разрешение без времени
            connection.execute(f"UPDATE shipments SET no_upd_approved_by = 'Зингер' WHERE id = '{_id(11)}'")
        connection.execute(
            f"UPDATE shipments SET no_upd_approved_by = 'Зингер', no_upd_approved_at = '2026-10-29 10:00:00' WHERE id = '{_id(11)}'"
        )

    command.downgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        card_columns = {row[1] for row in connection.execute("PRAGMA table_info(shipping_project_addressees)")}
        shipment_columns = {row[1] for row in connection.execute("PRAGMA table_info(shipments)")}
        rows = connection.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]
    assert not {"pack_decided", "pack_decided_by", "pack_decided_at", "upd_number", "upd_date"} & card_columns
    assert not {"no_upd_approved_by", "no_upd_approved_at"} & shipment_columns
    assert rows == 2

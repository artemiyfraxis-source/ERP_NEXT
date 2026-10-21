"""0125: бумажная УПД в данных отправки — пустая колонка в карточке, файл УПД может принадлежать проекту; откат отдаёт
файлы карточек отправкам, которые их взяли, остальные удаляет."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

BEFORE = "0124_levels_pm_storekeeper"
AFTER = "0125_addressee_upd_file"

_CARD = (
    "INSERT INTO shipping_project_addressees (project, version, receiver, shipping_point, contact, phone, carrier,"
    " upd_type, updated_by) VALUES ('591К.3', 3, '', 'Москва', '', '', '', 'БУМАГА', '')"
)
_SHIPMENT = (
    "INSERT INTO shipments (id, number, version, created_at, status, customer, receiver, shipping_point, contact, phone,"
    " terms, carrier, deadline, upd_type, upd_number, upd_date, upd_url, pl_number, dn_number, track_number, packed_at,"
    " shipped_at, manager, manager_id, note, tk_note, inherited_fields) VALUES ('{id}', 'ОТГ-1', 1,"
    " '2026-10-01 09:00:00.000000', 'Новая', '', 'П', 'Москва', '', '', NULL, '', NULL, 'БУМАГА', '', NULL, '{url}', '', '',"
    " '', NULL, NULL, 'М', NULL, '', '', '')"
)
_FILE = (
    "INSERT INTO shipping_files (id, shipment_id, file_name, content_type, size, content, uploaded_by, uploaded_at{extra})"
    " VALUES ('{id}', {shipment}, 'УПД.pdf', 'application/pdf', 3, X'414243', 'М', '2026-10-01 09:00:00.000000'{value})"
)
SHIPMENT_ID = "aa" * 16
OLD_FILE, TAKEN_FILE, LOOSE_FILE = "b1" * 16, "c2" * 16, "d3" * 16


def _uuid(hex32: str) -> str:
    return f"{hex32[:8]}-{hex32[8:12]}-{hex32[12:16]}-{hex32[16:20]}-{hex32[20:]}"


def _config(path: Path) -> Config:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _columns(connection, table: str) -> dict[str, tuple]:
    return {row[1]: (row[2], row[3]) for row in connection.execute(f"PRAGMA table_info({table})")}


def test_card_upd_column_and_project_files_up_and_down(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute(_CARD)
        connection.execute(_SHIPMENT.format(id=SHIPMENT_ID, url=f"/api/shipping/files/{_uuid(TAKEN_FILE)}"))
        connection.execute(_FILE.format(id=OLD_FILE, shipment=f"'{SHIPMENT_ID}'", extra="", value=""))
    capsys.readouterr()

    command.upgrade(config, AFTER)

    assert "shipping_project_addressees 1 → 1, shipping_files 1 → 1" in capsys.readouterr().out
    with sqlite3.connect(path) as connection:
        assert _columns(connection, "shipping_project_addressees")["upd_url"] == ("VARCHAR(1024)", 1)
        files = _columns(connection, "shipping_files")
        assert files["project"] == ("VARCHAR(255)", 1) and files["shipment_id"][1] == 0
        assert connection.execute("SELECT version, upd_type, upd_url FROM shipping_project_addressees").fetchall() == [
            (3, "БУМАГА", ""),
        ]
        assert connection.execute("SELECT shipment_id, project FROM shipping_files").fetchall() == [(SHIPMENT_ID, "")]
        # Файл без отправки и без проекта невозможен.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(_FILE.format(id="e4" * 16, shipment="NULL", extra="", value=""))
        for file_id in (TAKEN_FILE, LOOSE_FILE):
            connection.execute(_FILE.format(id=file_id, shipment="NULL", extra=", project", value=", '591К.3'"))
        connection.execute(f"UPDATE shipping_project_addressees SET upd_url = '/api/shipping/files/{_uuid(LOOSE_FILE)}'")

    command.downgrade(config, BEFORE)

    assert "файлов карточек передано отправкам 1, удалено 1" in capsys.readouterr().out
    with sqlite3.connect(path) as connection:
        assert "upd_url" not in _columns(connection, "shipping_project_addressees")
        files = _columns(connection, "shipping_files")
        assert "project" not in files and files["shipment_id"][1] == 1
        assert sorted(connection.execute("SELECT id, shipment_id FROM shipping_files").fetchall()) == [
            (OLD_FILE, SHIPMENT_ID), (TAKEN_FILE, SHIPMENT_ID),
        ]
        assert connection.execute("SELECT COUNT(*) FROM shipping_project_addressees").fetchone()[0] == 1

"""0163: разрешительный ПИН — два пустых столбца у `identity_credentials`, CHECK «хеш и дата вместе», откат их снимает;
пароль и PIN входа не трогаются."""

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

BEFORE = "0162_pack_decision_no_upd"
AFTER = "0163_approval_pin"


def _config(path: Path) -> Config:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def test_approval_pin_columns_check_and_downgrade(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    user = uuid4().hex
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO identity_users (id, login, email, name, department, role, active, job_title, qualification, "
            "access_level, shop_worker, site_master) VALUES (?, 'zinger', 'zinger@example.test', 'Зингер', 'Офис', "
            "'staff', 1, '', '', 'commercial', 0, 0)",
            (user,),
        )
        connection.execute(
            "INSERT INTO identity_credentials (user_id, password_hash, pin_hash) VALUES (?, 'pw-hash', 'pin-hash')", (user,),
        )
    capsys.readouterr()

    command.upgrade(config, AFTER)

    assert "identity_credentials 1 → 1, разрешительных ПИН задано 0" in capsys.readouterr().out
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT password_hash, pin_hash, approval_pin_hash, approval_pin_set_at FROM identity_credentials"
        ).fetchone()
        assert row == ("pw-hash", "pin-hash", None, None)
        with pytest.raises(sqlite3.IntegrityError):  # хеш без даты
            connection.execute("UPDATE identity_credentials SET approval_pin_hash = 'h'")
        with pytest.raises(sqlite3.IntegrityError):  # дата без хеша
            connection.execute("UPDATE identity_credentials SET approval_pin_set_at = '2026-10-29 10:00:00'")
        connection.execute(
            "UPDATE identity_credentials SET approval_pin_hash = 'h', approval_pin_set_at = '2026-10-29 10:00:00'"
        )

    command.downgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(identity_credentials)")}
        kept = connection.execute("SELECT password_hash, pin_hash FROM identity_credentials").fetchall()
    assert not {"approval_pin_hash", "approval_pin_set_at"} & columns
    assert kept == [("pw-hash", "pin-hash")]

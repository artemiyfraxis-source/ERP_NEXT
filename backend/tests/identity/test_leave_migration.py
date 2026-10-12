"""Миграция 0089: таблицы заявлений на отпуск и подписей, ссылка календаря цеха на заявление; ORM совпадает со схемой,
строки календаря мастера переживают миграцию и откат, ограничения не пускают плохие состояния."""

import sqlite3
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.identity.infrastructure import IdentityLeaveRequestRow, IdentityLeaveSignatureRow
from app.modules.production.infrastructure import ProductionAbsenceRow
from app.shared.clock import MOSCOW

BEFORE = "0088_api_tokens_assembly_drafts"
AFTER = "0095_identity_leave_requests"
NOW = datetime(2026, 10, 14, 10, 0, tzinfo=MOSCOW)
USER = uuid4()


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "leave.db"
    command.upgrade(_config(path), BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO identity_users (id, login, email, name, department, role, active, job_title, qualification, "
            "access_level, shop_worker, site_master) VALUES (?, 'fitter', 'fitter@example.test', 'Монтажник', 'Цех', "
            "'staff', 1, '', '', 'operator', 1, 0)",
            (USER.hex,),
        )
        connection.execute(
            "INSERT INTO production_absences (id, operator_id, from_date, to_date, kind) "
            "VALUES (?, ?, '2026-10-20', '2026-10-22', 'Больничный')",
            (uuid4().hex, USER.hex),
        )
    command.upgrade(_config(path), AFTER)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    yield path, engine
    engine.dispose()


def test_migration_creates_the_tables_as_the_orm_rows_describe_them(migrated):
    _path, engine = migrated
    schema = inspect(engine)
    for row in (IdentityLeaveRequestRow, IdentityLeaveSignatureRow, ProductionAbsenceRow):
        table = row.__table__
        columns = {column["name"]: column for column in schema.get_columns(table.name)}
        assert set(columns) == {column.name for column in table.columns}, table.name
        for column in table.columns:
            assert columns[column.name]["nullable"] == column.nullable, (table.name, column.name)
        assert {index["name"] for index in schema.get_indexes(table.name)} >= {index.name for index in table.indexes}
    checks = {check["name"] for check in schema.get_check_constraints("identity_leave_requests")}
    assert {"ck_identity_leave_requests_status", "ck_identity_leave_requests_period"} <= checks
    [unique] = schema.get_unique_constraints("identity_leave_signatures")
    assert unique["column_names"] == ["request_id", "step", "content_version"]
    leave_fk = [fk for fk in schema.get_foreign_keys("production_absences") if fk["referred_table"] == "identity_leave_requests"]
    assert leave_fk and leave_fk[0]["constrained_columns"] == ["leave_request_id"]


def test_the_masters_absences_survive_as_manual_rows(migrated):
    path, _engine = migrated
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT kind, leave_request_id FROM production_absences").fetchall() == [
            ("Больничный", None),
        ]


def _request(**overrides) -> IdentityLeaveRequestRow:
    values = dict(
        id=uuid4(), employee_id=USER, kind="vacation", from_date=date(2026, 11, 2), to_date=date(2026, 11, 15),
        comment="", status="draft", content_version=1, content_sha256="a" * 64, version=1, created_at=NOW,
        updated_at=NOW,
    )
    values.update(overrides)
    return IdentityLeaveRequestRow(**values)


@pytest.mark.parametrize("bad", [
    {"status": "approved_by_cat"},
    {"kind": "sick"},
    {"to_date": date(2026, 11, 1)},
    {"content_version": 0},
])
def test_constraints_refuse_bad_states(migrated, bad):
    _path, engine = migrated
    with Session(engine) as session:
        session.add(_request(**bad))
        with pytest.raises(IntegrityError):
            session.commit()


def test_one_signature_per_step_and_content_version(migrated):
    _path, engine = migrated
    with Session(engine) as session:
        request = _request()
        session.add(request)
        session.flush()

        def signature():
            return IdentityLeaveSignatureRow(
                id=uuid4(), request_id=request.id, step="employee", decision="signed", signer_id=USER,
                signer_name="Монтажник", signed_at=NOW, method="pin", content_version=1, content_sha256="a" * 64,
            )

        session.add(signature())
        session.commit()
        session.add(signature())
        with pytest.raises(IntegrityError):
            session.commit()


def test_downgrade_drops_the_tables_and_keeps_the_calendar(migrated):
    path, engine = migrated
    command.downgrade(_config(path), BEFORE)
    schema = inspect(engine)
    tables = set(schema.get_table_names())
    assert "identity_leave_requests" not in tables and "identity_leave_signatures" not in tables
    assert "leave_request_id" not in {column["name"] for column in schema.get_columns("production_absences")}
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT kind FROM production_absences").fetchall() == [("Больничный",)]

"""КД в базе ERP: разовый перенос строк из времён Диска (Диск только читается) и миграция 0069 туда и обратно."""

import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.requests.google_drive import DriveError, GoogleDriveReader
from app.modules.requests.google_sheets import ServiceAccount
from app.modules.techcards.import_kd_from_drive import format_report, import_kd, main
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.kitset_infrastructure import KitDocumentRow
from app.shared.persistence import create_schema
from tests.requests.test_calc_link import key_pem  # noqa: F401 — fixture
from tests.requests.test_google_drive import drive_handler

OLD = UUID("70000000-0000-0000-0000-000000000001")
GONE = UUID("70000000-0000-0000-0000-000000000002")
NEW = UUID("70000000-0000-0000-0000-000000000003")


@pytest.fixture()
def uow_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with sessions() as session:
        common = {"project": "708К.4", "uploaded_at": datetime(2026, 9, 29, 9, 0), "uploaded_by": "Технолог"}
        session.add_all([
            KitDocumentRow(id=OLD, item_index="КБ1", file_name="708К.4КБ1 — Чертёж.pdf", file_id="f-old",
                           url="https://drive.google.com/file/d/f-old/view", **common),
            KitDocumentRow(id=GONE, item_index="КБ2", file_name="708К.4КБ2 — Схема.png", file_id="f-gone",
                           url="https://drive.google.com/file/d/f-gone/view", **common),
            KitDocumentRow(id=NEW, item_index="КБ1", file_name="708К.4КБ1 — ТЗ.docx", file_id="", url="",
                           content_type="application/octet-stream", size=2, content=b"tz", **common),
        ])
        session.commit()
    yield lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    engine.dispose()


def _reader(key_pem, calls):  # noqa: F811
    account = ServiceAccount.from_info({"client_email": "erp-reader@x.iam.gserviceaccount.com", "private_key": key_pem[0]})
    handler = drive_handler({"f-old": ("708К.4КБ1 — Чертёж.pdf", "application/pdf", b"%PDF-old")}, calls)
    return GoogleDriveReader(account, client=httpx.Client(transport=httpx.MockTransport(handler)), clock=lambda: 1)


def _content(uow_factory, document_id):
    with uow_factory() as uow:
        found = uow.kitsets.document_content("708К.4", document_id)
    return None if found is None else (found[0].content_type, found[0].size, found[1])


def test_dry_run_lists_drive_only_rows_without_reading_drive(uow_factory):
    def no_reader():
        raise AssertionError("dry-run must not build a Drive reader")

    result = import_kd(uow_factory, no_reader, apply=False)
    assert [item.split(" [")[1] for item in result.found] == ["f-old]", "f-gone]"]
    assert result.imported == [] and result.failed == [] and result.written is False
    assert "Пробный прогон" in format_report(result)
    assert _content(uow_factory, OLD) is None


def test_apply_downloads_readonly_and_stores_bytes_rerun_is_empty(uow_factory, key_pem):  # noqa: F811
    calls: list[httpx.Request] = []
    result = import_kd(uow_factory, lambda: _reader(key_pem, calls), apply=True)
    assert len(result.imported) == 1 and "f-old" in result.imported[0]
    assert len(result.failed) == 1 and "f-gone" in result.failed[0]
    assert {call.method for call in calls if call.url.host != "oauth2.googleapis.com"} == {"GET"}
    assert _content(uow_factory, OLD) == ("application/pdf", 8, b"%PDF-old")
    assert _content(uow_factory, NEW) == ("application/octet-stream", 2, b"tz")
    with uow_factory() as uow:
        kept = uow.kitsets.document("708К.4", OLD)
    # Ссылка на Диск остаётся на строке — архив, только для чтения.
    assert (kept.file_id, kept.url, kept.stored) == ("f-old", "https://drive.google.com/file/d/f-old/view", True)
    assert "Не прочитано с Диска: 1" in format_report(result)

    again = import_kd(uow_factory, lambda: _reader(key_pem, []), apply=True)
    assert [item.split(" [")[1] for item in again.found] == ["f-gone]"]


def test_refused_key_stops_the_import(uow_factory, key_pem):  # noqa: F811
    account = ServiceAccount.from_info({"client_email": "erp-reader@x.iam.gserviceaccount.com", "private_key": key_pem[0]})
    refused = GoogleDriveReader(account, client=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(401, json={}),
    )))
    with pytest.raises(DriveError):
        import_kd(uow_factory, lambda: refused, apply=True)
    assert _content(uow_factory, OLD) is None


def test_cli_needs_database_url(monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    assert main([]) == 2
    assert "ERP_DATABASE_URL" in capsys.readouterr().err


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def test_migration_0069_round_trip_keeps_drive_rows(tmp_path):
    database_path = tmp_path / "kd.db"
    config = _config(database_path)
    command.upgrade(config, "0068_techcard_kd_on_drive")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO techcard_kitset_documents (id, project, item_index, file_name, file_id, url, uploaded_at,"
            " uploaded_by) VALUES (?, '708К.4', 'КБ1', '708К.4КБ1 — Чертёж.pdf', 'f-old',"
            " 'https://drive.google.com/file/d/f-old/view', '2026-09-29 09:00:00', 'Технолог')",
            (OLD.hex,),
        )

    command.upgrade(config, "0069_techcard_kd_in_database")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT file_id, url, content, content_type, size FROM techcard_kitset_documents"
        ).fetchall() == [("f-old", "https://drive.google.com/file/d/f-old/view", None, None, None)]
        connection.execute("UPDATE techcard_kitset_documents SET content = ?, size = 3", (b"pdf",))

    command.downgrade(config, "0068_techcard_kd_on_drive")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(techcard_kitset_documents)")}
        assert "content" not in columns and "size" not in columns
        assert connection.execute("SELECT file_id FROM techcard_kitset_documents").fetchall() == [("f-old",)]

    command.upgrade(config, "0069_techcard_kd_in_database")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT file_id, content FROM techcard_kitset_documents"
        ).fetchall() == [("f-old", None)]

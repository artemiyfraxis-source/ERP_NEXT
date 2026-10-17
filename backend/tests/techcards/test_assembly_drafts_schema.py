"""Миграция 0088: пустые таблицы токенов API и черновиков схем сборки; ORM-строка черновика совпадает со схемой."""

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.identity.infrastructure import IdentityApiTokenRow
from app.modules.techcards.assembly_drafts_infrastructure import AssemblyDraftRow
from app.shared.clock import MOSCOW

BEFORE = "0087_fulfillment_off_book_kit_reserves"
AFTER = "0088_api_tokens_assembly_drafts"
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=MOSCOW)


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "drafts.db"
    command.upgrade(_config(path), AFTER)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    yield path, engine
    engine.dispose()


def test_migration_creates_both_tables_as_the_orm_rows_describe_them(migrated):
    _path, engine = migrated
    schema = inspect(engine)
    for row in (AssemblyDraftRow, IdentityApiTokenRow):
        table = row.__table__
        columns = {column["name"]: column for column in schema.get_columns(table.name)}
        assert set(columns) == {column.name for column in table.columns}, table.name
        for column in table.columns:
            assert columns[column.name]["nullable"] == column.nullable, (table.name, column.name)
        assert {index["name"] for index in schema.get_indexes(table.name)} == {index.name for index in table.indexes}
    [drawing] = schema.get_foreign_keys("techcard_assembly_drafts")
    assert drawing["referred_table"] == "techcard_kitset_documents" and drawing["constrained_columns"] == [
        "drawing_document_id"
    ]
    assert {fk["referred_table"] for fk in schema.get_foreign_keys("identity_api_tokens")} == {"identity_users"}


def test_draft_row_round_trips_and_status_is_draft_or_final(migrated):
    _path, engine = migrated
    with Session(engine) as session:
        session.add(AssemblyDraftRow(
            id=uuid4(), project="708К.4", item_index="КБ1", drawing_document_id=None, source="claude-code",
            author="Claude Code", draft_config={"wires": [{"name": "МК 26-13", "length_mm": 150}]},
            created_at=NOW, updated_at=NOW,
        ))
        session.commit()
        stored = session.query(AssemblyDraftRow).one()
        assert (stored.status, stored.lesson, stored.final_config, stored.final_revision) == ("draft", "", None, None)
        assert stored.draft_config["wires"][0]["length_mm"] == 150
        stored.status = "accepted"
        with pytest.raises(IntegrityError):
            session.commit()


def test_downgrade_drops_both_tables(migrated):
    path, engine = migrated
    command.downgrade(_config(path), BEFORE)
    tables = set(inspect(engine).get_table_names())
    assert "techcard_assembly_drafts" not in tables and "identity_api_tokens" not in tables
    assert "techcard_kitset_documents" in tables

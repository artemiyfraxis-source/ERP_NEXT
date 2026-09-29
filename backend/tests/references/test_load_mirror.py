"""REF-MIRROR-LOAD: mirror БД.ПРОВ / БД.МАТ TSV → references snapshot."""

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.shared.persistence import create_schema
from app.modules.references.application import ReplaceSnapshotCommand, TechRecordInput, replace_snapshot
from app.modules.references.domain import TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import ACTOR, MirrorLoadError, default_source, load_mirror, main, read_mirror

NOW = datetime(2026, 9, 29, 12, 0)
PROV = "Марка\tAWG\nRG316\t\nUL1061\t28\n\t\n"
MAT = "﻿Наименование\tГруппа\r\nТУТ 4/2\tТУТ\r\n"


@pytest.fixture()
def uow():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    return SqlAlchemyReferencesUnitOfWork(sessionmaker(bind=engine, expire_on_commit=False, class_=Session))


def mirror(tmp_path: Path, prov: str | None = PROV, mat: str | None = MAT) -> Path:
    db = tmp_path / "_local" / "db"
    db.mkdir(parents=True)
    for name, text in (("БД.ПРОВ.tsv", prov), ("БД.МАТ.tsv", mat)):
        if text is not None:
            (db / name).write_text(text, encoding="utf-8", newline="")
    return tmp_path


def rows(uow, tab):
    with uow:
        return [item.display_text for item in uow.tech_operations.list(tab=tab, query="", limit=1000)]


def test_reads_header_and_skips_empty_rows_and_bom(tmp_path):
    tables = read_mirror(mirror(tmp_path))

    assert [r.display_text for r in tables["prov"]] == ["RG316", "UL1061"]
    assert tables["mat"][0].fields == {"Наименование": "ТУТ 4/2", "Группа": "ТУТ"}


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(MirrorLoadError, match="БД.МАТ.tsv"):
        read_mirror(mirror(tmp_path, mat=None))


def test_file_without_header_is_an_error(tmp_path):
    with pytest.raises(MirrorLoadError, match="БД.ПРОВ.tsv"):
        read_mirror(mirror(tmp_path, prov=""))


def test_dry_run_counts_rows_and_writes_nothing(tmp_path, uow):
    result = load_mirror(mirror(tmp_path), uow, dry_run=True, now=NOW)

    assert result.counts == {"prov": 2, "mat": 1} and not result.written
    assert rows(uow, TechOperationTab.WIRE) == [] and rows(uow, TechOperationTab.MATERIAL) == []
    with uow:
        assert uow.tech_operations.metadata() is None


def test_load_replaces_only_prov_and_mat_and_is_repeatable(tmp_path, uow):
    replace_snapshot(ReplaceSnapshotCommand(
        schema_version=14, source_updated_at=None, actor=ACTOR, now=NOW,
        records=(TechRecordInput(TechOperationTab.TERMINAL, "T1", "t1", "БД.ТЕР", "T1", {"a": "1"}),
                 TechRecordInput(TechOperationTab.WIRE, "OLD", "", "БД.ПРОВ", "OLD", {"Марка": "OLD"})),
    ), uow)
    source = mirror(tmp_path)

    load_mirror(source, uow, dry_run=False, now=NOW)
    load_mirror(source, uow, dry_run=False, now=NOW)

    assert rows(uow, TechOperationTab.TERMINAL) == ["T1"]
    assert rows(uow, TechOperationTab.WIRE) == ["RG316", "UL1061"]
    assert rows(uow, TechOperationTab.MATERIAL) == ["ТУТ 4/2"]
    with uow:
        assert uow.tech_operations.metadata()["record_count"] == 4


def test_default_source_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPPLYMAP_SOURCE_REPOSITORY", str(tmp_path))
    assert default_source() == tmp_path


def test_main_needs_database_url(monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    assert main(["--dry-run"]) == 2
    assert "ERP_DATABASE_URL" in capsys.readouterr().err


def test_main_missing_mirror_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ERP_DATABASE_URL", f"sqlite:///{tmp_path / 'x.db'}")
    assert main(["--dry-run", "--source", str(tmp_path)]) == 2
    assert "Зеркало не читается" in capsys.readouterr().err

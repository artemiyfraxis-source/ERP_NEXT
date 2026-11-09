from types import SimpleNamespace

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from app.database import check_equipment_schema, configure_database, equipment_setup_enabled
from app.shared.warehouse_location import set_rack_layout_loader
from app.workflows.production_resource_context import production_resources
from app.workflows.project_kitset import SqlAlchemyKbProgressUnitOfWork
from tests.shipping.test_pack_decision_migration import _config


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "runtime.db"
    command.upgrade(_config(path), "0172_resource_unit_ids")  # 0170 tables, machines by unit id
    return f"sqlite+pysqlite:///{path.as_posix()}"


@pytest.mark.parametrize(("value", "expected"), [(None, False), ("0", False), ("1", True)])
def test_release_flag_is_explicit(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("ERP_EQUIPMENT_SETUP_ENABLED", raising=False)
    else:
        monkeypatch.setenv("ERP_EQUIPMENT_SETUP_ENABLED", value)
    assert equipment_setup_enabled() is expected


@pytest.mark.parametrize("value", ["", "false", "true", "2"])
def test_unknown_release_flag_is_error_not_disabled(monkeypatch, value):
    monkeypatch.setenv("ERP_EQUIPMENT_SETUP_ENABLED", value)
    with pytest.raises(RuntimeError, match="must be 0 or 1"):
        equipment_setup_enabled()


def test_enabled_bootstrap_uses_same_session_and_kb_progress_begins_once(migrated):
    holder = SimpleNamespace(state=SimpleNamespace())
    engine = configure_database(holder, migrated, equipment_enabled=True)
    try:
        prototype = holder.state.production_uow_factory()
        assert prototype.resource_factory is production_resources
        with prototype as uow:
            assert uow.resources.session is uow.session
            assert uow.production.resources is uow.resources
            assert uow.work.resources is uow.resources
        with SqlAlchemyKbProgressUnitOfWork(prototype.session_factory, prototype.resource_factory) as uow:
            assert uow.current_run("NONE", "КБ1") is None
            uow.commit()
        with holder.state.equipment_setup_uow_factory() as uow:
            assert uow.context.session is uow.session
            assert uow.reservations.claims.session is uow.session
            assert uow.tasks.session is uow.session
            assert uow.recipes.session is uow.session
    finally:
        set_rack_layout_loader(None)
        engine.dispose()


def test_missing_schema_and_singleton_never_enable_fallback(migrated, tmp_path):
    holder = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(RuntimeError, match="migration 0170"):
        configure_database(holder, f"sqlite+pysqlite:///{tmp_path / 'empty.db'}", equipment_enabled=True)
    assert not hasattr(holder.state, "production_uow_factory")
    engine = create_engine(migrated)
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM production_resource_generation"))
        with pytest.raises(RuntimeError, match="singleton"):
            configure_database(holder, migrated, equipment_enabled=True)
        assert not hasattr(holder.state, "production_uow_factory")
    finally:
        engine.dispose()


def test_disabled_before_use_and_rejects_disabled_after_use(migrated):
    holder = SimpleNamespace(state=SimpleNamespace())
    engine = configure_database(holder, migrated, equipment_enabled=False)
    try:
        assert holder.state.production_uow_factory().resource_factory is None
        assert holder.state.equipment_setup_uow_factory is None
        with engine.begin() as connection:
            connection.execute(text("UPDATE production_resource_generation SET version=1"))
        with pytest.raises(RuntimeError, match="stop writers"):
            check_equipment_schema(engine, enabled=False)
        with engine.begin() as connection:
            connection.execute(text("UPDATE production_resource_generation SET version=0"))
            connection.execute(text("INSERT INTO equipment_setup_commands (key,fingerprint,result) VALUES ('test','test','{}')"))
        with pytest.raises(RuntimeError, match="stop writers"):
            check_equipment_schema(engine, enabled=False)
    finally:
        set_rack_layout_loader(None)
        engine.dispose()


def test_cli_rollback_uses_release_gate_and_resource_factory(migrated, monkeypatch):
    from app.workflows import autoplan_rollback

    monkeypatch.setenv("ERP_DATABASE_URL", migrated)
    monkeypatch.setenv("ERP_EQUIPMENT_SETUP_ENABLED", "1")
    seen = []
    def run(sessions, **kwargs):
        seen.append(kwargs)
        assert kwargs["resource_factory"] is production_resources
        return "checked", 0
    monkeypatch.setattr(autoplan_rollback, "run", run)
    assert autoplan_rollback.main(["--since", "2026-11-01 00:00", "--dry-run"]) == 0
    assert len(seen) == 1
    assert seen[0]["apply"] is False


def test_kb_progress_propagates_resource_factory(monkeypatch):
    from app.workflows import project_kitset
    from tests.factories import Actor
    from datetime import datetime

    sessions = object()
    prototype = SimpleNamespace(session_factory=sessions, resource_factory=production_resources)
    class Captured(Exception):
        pass
    def capture(command, uow):
        assert uow.session_factory is sessions
        assert uow.resource_factory is production_resources
        raise Captured
    monkeypatch.setattr(project_kitset, "change_kb_progress", capture)
    with pytest.raises(Captured):
        project_kitset.save_project_kb_progress(SimpleNamespace(project="100К.1"), kb="КБ1", changes={},
            expected_sequence=0, expected_run=1, actor=Actor(name="Мастер", department="Производство"),
            now=datetime(2026, 11, 3), uow_factory=lambda: prototype)


@pytest.mark.parametrize("enabled", ["0", "1"])
def test_main_mounts_setup_routes_only_with_same_bootstrap_gate(migrated, monkeypatch, enabled):
    import subprocess
    import sys
    from pathlib import Path

    monkeypatch.setenv("ERP_DATABASE_URL", migrated)
    monkeypatch.setenv("ERP_EQUIPMENT_SETUP_ENABLED", enabled)
    result = subprocess.run([sys.executable, "-c",
        "from app.main import app; "
        "paths = app.openapi()['paths']; "
        "mounted = any(p.startswith('/api/production/equipment-setup') for p in paths); "
        "assert mounted == app.state.equipment_setup_enabled; "
        "assert (app.state.production_uow_factory().resource_factory is not None) == mounted"],
        capture_output=True, text=True, timeout=30, cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 0, result.stderr

"""CI must distinguish a deliberate loss-prevention refusal from broken migrations."""

import shutil
from unittest.mock import Mock

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from tests.migrations import check_seeded_downgrade as contract
from tests.migrations.seed_any_revision import seed
from tests.shipping.test_pack_decision_migration import _config


def test_schema_evidence_ignores_fk_order_but_detects_changed_delete_policy(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'constraints.db'}")
    try:
        def create_child(reverse=False, policy="RESTRICT"):
            constraints = [f"CONSTRAINT fk_first FOREIGN KEY (first_id) REFERENCES parent (id) ON DELETE {policy}",
                           "CONSTRAINT fk_second FOREIGN KEY (second_id) REFERENCES parent (id) ON DELETE SET NULL"]
            with engine.begin() as connection:
                connection.exec_driver_sql("DROP TABLE IF EXISTS child")
                connection.exec_driver_sql("CREATE TABLE child (first_id INTEGER, second_id INTEGER, "
                                           + ", ".join(reversed(constraints) if reverse else constraints) + ")")

        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        create_child()
        before = contract.evidence(engine)
        create_child(reverse=True)
        assert contract.evidence(engine) == before
        create_child(policy="CASCADE")
        assert contract.evidence(engine) != before
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def seeded_head(tmp_path_factory):
    path = tmp_path_factory.mktemp("downgrade-contract") / "seeded.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        config = _config(path)
        command.upgrade(config, "0168_requests_calc_compositions")
        engine = create_engine(config.get_main_option("sqlalchemy.url"))
        try:
            seed(engine)
        finally:
            engine.dispose()
        command.upgrade(config, contract.GUARDED_REVISION)
    return path


@pytest.fixture
def guarded_head(monkeypatch):
    # Exercise the historical 0169 contract even after later migrations become head.
    scripts = Mock()
    scripts.from_config.return_value.get_current_head.return_value = contract.GUARDED_REVISION
    monkeypatch.setattr(contract, "ScriptDirectory", scripts)


@pytest.fixture
def database(seeded_head, tmp_path, monkeypatch, guarded_head):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "copy.db"
    shutil.copyfile(seeded_head, path)
    config = _config(path)
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        yield config, engine
    finally:
        engine.dispose()


def test_seeded_guard_refuses_without_changing_any_evidence(database):
    config, engine = database
    before = contract.evidence(engine)
    assert contract.check(config, engine).startswith("0169 refused")
    assert contract.evidence(engine) == before


@pytest.mark.parametrize("error", [RuntimeError("unrelated failure"), ValueError(contract.REFUSAL)])
def test_unexpected_errors_are_not_accepted(database, monkeypatch, error):
    monkeypatch.setattr(contract.command, "downgrade", Mock(side_effect=error))
    with pytest.raises(type(error), match=str(error)):
        contract.check(*database)


def test_successful_lossy_downgrade_is_not_accepted(database, monkeypatch):
    monkeypatch.setattr(contract.command, "downgrade", Mock())
    with pytest.raises(AssertionError, match="unexpectedly allowed"):
        contract.check(*database)


def test_expected_refusal_with_changed_rows_is_not_accepted(database, monkeypatch):
    config, engine = database

    def corrupt_then_refuse(*args):
        with engine.begin() as connection:
            connection.execute(text("UPDATE shipping_pack_permissions SET version = version + 1"))
        raise RuntimeError(contract.REFUSAL)

    monkeypatch.setattr(contract.command, "downgrade", corrupt_then_refuse)
    with pytest.raises(AssertionError, match="changed schema, rows or revision"):
        contract.check(config, engine)


def test_other_heads_keep_the_seeded_roundtrip(database, monkeypatch):
    config, engine = database
    previous = "0168_requests_calc_compositions"
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = :revision"), {"revision": previous})
    script = Mock()
    script.get_current_head.return_value = previous
    monkeypatch.setattr(contract.ScriptDirectory, "from_config", Mock(return_value=script))
    downgrade = Mock()
    upgrade = Mock()
    monkeypatch.setattr(contract.command, "downgrade", downgrade)
    monkeypatch.setattr(contract.command, "upgrade", upgrade)
    assert contract.check(config, engine) == "seeded roundtrip passed"
    downgrade.assert_called_once_with(config, "-1")
    upgrade.assert_called_once_with(config, "head")


def test_empty_0169_roundtrips_but_cannot_stand_in_for_seeded_guard(tmp_path, monkeypatch, guarded_head):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    config = _config(tmp_path / "empty.db")
    command.upgrade(config, contract.GUARDED_REVISION)
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        before = contract.evidence(engine)
        with pytest.raises(AssertionError, match="must contain permissions"):
            contract.check(config, engine)
        command.downgrade(config, "-1")
        command.upgrade(config, contract.GUARDED_REVISION)
        assert contract.evidence(engine) == before
    finally:
        engine.dispose()


def test_current_head_roundtrips_over_seeded_permissions(seeded_head, tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "current-head.db"
    shutil.copyfile(seeded_head, path)
    config = _config(path)
    command.upgrade(config, "head")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        before = contract.evidence(engine)
        assert contract.check(config, engine) == "seeded roundtrip passed"
        assert contract.evidence(engine) == before
    finally:
        engine.dispose()

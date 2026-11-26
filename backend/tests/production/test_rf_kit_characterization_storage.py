"""Real SQLite constraints, immutable evidence, aggregate CAS and publication transactions."""

import importlib
from dataclasses import asdict
from uuid import uuid4

import pytest
from app.main import app  # noqa: F401 -- register every table owner used by the test schema
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, delete, event, insert, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from app.modules.production.application import ConcurrentProductionUpdate
from app.modules.production.domain import ProductionValidationError
from app.modules.production.infrastructure import ProductionInstrumentRow
from app.modules.production.rf_infrastructure import RfDetailsRow, RfInterfaceRow, RfRepository
from app.modules.production.rf_interfaces import RfDetails, parse_interfaces
from app.modules.production.rf_kit_characterization import REQUIRED_PIECES
from app.modules.production.rf_kit_characterization_infrastructure import (
    RfKitRepository, RfKitStandardRow, RfStandardCharacterizationRow,
)
from app.modules.production.vna_session_infrastructure import VnaArtifactRow  # noqa: F401
from app.shared.persistence import create_schema
from tests.production.test_rf_kit_characterization import NOW, VALID, curve, piece, provenance

MIGRATION = importlib.import_module("migrations.versions.0220_rf_kit_characterization")


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'kit.db').as_posix()}", connect_args={"timeout": 0.05})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    create_schema(engine)
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        MIGRATION._protect_revisions()
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def seed_kit(session):
    kit_id = uuid4()
    session.add(ProductionInstrumentRow(id=kit_id, version=1, inventory_number=f"KIT-{kit_id}", name="SMA-MF06",
                instrument_type="Набор", kind="Принадлежность", status="Действует"))
    session.flush()
    session.add(RfDetailsRow(instrument_id=kit_id, version=1, role="load_kit", confirmed=False,
                            source="Test", kit_definition="SMA-MF06-loads"))
    session.flush()
    return kit_id


def append(repo, standard, **changes):
    return repo.append_revision(standard.id, **{
        "expected_version": standard.version, "format": "s2p" if standard.kind == "THRU" else "s1p",
        "content": curve("s2p" if standard.kind == "THRU" else "s1p"), "provenance": provenance(),
        "validation": VALID, "actor": "Контролёр", "now": NOW, **changes})


def complete_kit(session):
    kit_id = seed_kit(session)
    repo = RfKitRepository(session)
    revisions = {}
    for kind, gender, _ in sorted(REQUIRED_PIECES):
        standard = repo.register(kit_id, piece(kind, gender), expected_kit_version=session.get(RfDetailsRow, kit_id).version,
                                 actor="Контролёр", now=NOW)
        revisions[standard.id] = append(repo, standard).id
    return kit_id, revisions


def activation_args(session, kit_id):
    return {"expected_kit_version": session.get(RfDetailsRow, kit_id).version,
            "expected_standard_versions": {s.id: s.version for s in RfKitRepository(session).standards(kit_id)},
            "actor": "Контролёр", "now": NOW}


def test_activation_is_complete_atomic_and_advances_generation_even_for_same_selection(sessions):
    with sessions.begin() as session:
        kit_id, revisions = complete_kit(session)
        repo = RfKitRepository(session)
        before = repo.snapshot(kit_id)
        assert before["generation"] == 0 and before["kit_definition"] == "SMA-MF06-loads"
        assert all(row["current_revision"] is None for row in before["standards"])
        args = activation_args(session, kit_id)
        first = repo.activate(kit_id, revisions, **args)
        assert first["generation"] == 1 and len(first["hash"]) == 64
        assert first["version"] == args["expected_kit_version"] + 1
        after = repo.snapshot(kit_id)
        assert after["kit_definition"] == "SMA-MF06-characterized"
        assert {row["current_revision"]["id"] for row in after["standards"]} == set(revisions.values())
        with pytest.raises(ConcurrentProductionUpdate):
            repo.activate(kit_id, revisions, **args)
        second = repo.activate(kit_id, revisions, **activation_args(session, kit_id))
        assert second["generation"] == 2 and second["hash"] != first["hash"]


@pytest.mark.parametrize("failure", ["missing", "wrong_owner", "stale_piece", "failed_latest", "older_revision"])
def test_failed_publication_preserves_every_pointer_and_generation(sessions, failure):
    with sessions.begin() as session:
        kit_id, revisions = complete_kit(session)
        repo = RfKitRepository(session)
        repo.activate(kit_id, revisions, **activation_args(session, kit_id))
    with sessions.begin() as session:
        repo = RfKitRepository(session)
        standard = repo.standards(kit_id)[0]
        if failure in ("failed_latest", "older_revision"):
            newer = append(repo, standard, content=curve(value=0.4), validation={"valid": False, "checks": {"repeatability": False}})
            if failure == "failed_latest":
                revisions[standard.id] = newer.id
    with sessions() as session:
        repo = RfKitRepository(session)
        before = repo.snapshot(kit_id)
        args = activation_args(session, kit_id)
        if failure == "missing":
            revisions.pop(next(iter(revisions)))
        elif failure == "wrong_owner":
            keys = list(revisions)
            revisions[keys[0]], revisions[keys[1]] = revisions[keys[1]], revisions[keys[0]]
        elif failure == "stale_piece":
            args["expected_standard_versions"][next(iter(revisions))] -= 1
        with pytest.raises((ProductionValidationError, ConcurrentProductionUpdate)):
            repo.activate(kit_id, revisions, **args)
        session.rollback()
        assert repo.snapshot(kit_id) == before


def test_incomplete_kit_and_generic_rf_edits_cannot_promote(sessions):
    with sessions.begin() as session:
        kit_id = seed_kit(session)
        repo = RfKitRepository(session)
        standard = repo.register(kit_id, piece(), expected_kit_version=1, actor="QC", now=NOW)
        revision = append(repo, standard)
        with pytest.raises(ProductionValidationError, match="восемь"):
            repo.activate(kit_id, {standard.id: revision.id}, **activation_args(session, kit_id))
        with pytest.raises(ProductionValidationError, match="активации"):
            RfRepository(session).save(kit_id, RfDetails(role="solt_kit", kit_definition="SMA-MF06-characterized",
                                                       version=session.get(RfDetailsRow, kit_id).version))


def test_concurrent_publishers_cannot_mix_snapshots(sessions):
    with sessions.begin() as session:
        kit_id, revisions = complete_kit(session)
        args = activation_args(session, kit_id)
    with sessions() as first, sessions() as second:
        RfKitRepository(first).activate(kit_id, revisions, **args)
        with pytest.raises(OperationalError, match="locked"):
            RfKitRepository(second).activate(kit_id, revisions, **args)
        second.rollback()
        first.commit()
        with pytest.raises(ConcurrentProductionUpdate):
            RfKitRepository(second).activate(kit_id, revisions, **args)
        second.rollback()
        assert RfKitRepository(second).snapshot(kit_id)["generation"] == 1


def test_outer_rollback_rolls_back_activation_and_all_pointers(sessions):
    with sessions.begin() as session:
        kit_id, revisions = complete_kit(session)
    with sessions() as session:
        repo = RfKitRepository(session)
        repo.activate(kit_id, revisions, **activation_args(session, kit_id))
        session.rollback()
        snapshot = repo.snapshot(kit_id)
        assert snapshot["generation"] == 0
        assert all(row["current_revision"] is None for row in snapshot["standards"])


def test_confirmed_one_connector_legacy_kit_remains_readable_after_activation(sessions):
    with sessions.begin() as session:
        kit_id, revisions = complete_kit(session)
        session.get(RfDetailsRow, kit_id).confirmed = True
        port, = parse_interfaces("SMA female 50 ohm")
        session.add(RfInterfaceRow(instrument_id=kit_id, position=1, **asdict(port)))
        session.flush()
        assert RfRepository(session).get(kit_id).confirmed
        RfKitRepository(session).activate(kit_id, revisions, **activation_args(session, kit_id))
        saved = RfRepository(session).get(kit_id)
        assert saved.confirmed and len(saved.interfaces) == 1 and saved.kit_definition == "SMA-MF06-characterized"


def test_read_refuses_mixed_generation_and_revisions_during_activation(sessions, monkeypatch):
    with sessions.begin() as session:
        kit_id, revisions = complete_kit(session)
    with sessions() as reader:
        repo = RfKitRepository(reader)
        original = repo.standards

        def publish_between_reads(kit_id):
            with sessions.begin() as writer:
                RfKitRepository(writer).activate(kit_id, revisions, **activation_args(writer, kit_id))
            return original(kit_id)

        monkeypatch.setattr(repo, "standards", publish_between_reads)
        with pytest.raises(ConcurrentProductionUpdate, match="во время чтения"):
            repo.snapshot(kit_id)


def test_postgresql_lock_order_matches_existing_registry_editors(sessions):
    with sessions.begin() as session:
        kit_id = seed_kit(session)
    statements = []
    engine = sessions.kw["bind"]

    def record(_connection, clause, *_args):
        if getattr(clause, "is_select", False):
            statements.append(str(clause.compile(dialect=postgresql.dialect())))

    event.listen(engine, "before_execute", record)
    try:
        with sessions() as session:
            RfKitRepository(session).register(kit_id, piece(), expected_kit_version=1, actor="QC", now=NOW)
        writer, statements[:] = list(statements), []
        with sessions() as session:
            RfKitRepository(session).snapshot(kit_id)
    finally:
        event.remove(engine, "before_execute", record)
    assert "FROM production_instruments" in writer[0] and writer[0].endswith("FOR UPDATE")
    assert "FROM production_rf_details" in writer[1] and writer[1].endswith("FOR UPDATE")
    # A view-only read (GET /standards) takes no row locks at all.
    assert statements and not any("FOR UPDATE" in statement for statement in statements)


@pytest.mark.parametrize("action", ["update", "delete"])
def test_revisions_are_immutable_even_through_direct_sql(sessions, action):
    with sessions.begin() as session:
        kit_id, revisions = complete_kit(session)
        revision_id = next(iter(revisions.values()))
    with sessions.begin() as session:
        table = RfStandardCharacterizationRow
        statement = update(table).where(table.id == revision_id).values(validation=VALID) if action == "update" else delete(table).where(table.id == revision_id)
        with pytest.raises(IntegrityError, match="неизменяемы"):
            session.execute(statement)


@pytest.mark.parametrize("changes", [
    {"kind": "BAD"}, {"gender": "unknown"}, {"gender2": "female"}, {"kind": "THRU", "gender2": None},
    {"f_max_hz": 3e9 + 1}, {"f_min_hz": -1}, {"f_min_hz": 3e9}, {"version": 0},
    {"kit_instrument_id": uuid4()}, {"current_revision_id": uuid4()}, {"piece_label": " "},
])
def test_piece_checks_and_restrictive_foreign_keys(sessions, changes):
    with sessions.begin() as session:
        kit_id = seed_kit(session)
        values = {"id": uuid4(), "kit_instrument_id": kit_id, **asdict(piece()), "version": 1,
                  "created_at": NOW, "updated_at": NOW, "created_by": "QC", "updated_by": "QC", **changes}
        with pytest.raises(IntegrityError):
            session.execute(insert(RfKitStandardRow).values(**values))


def test_unique_labels_hashes_revision_numbers_and_owned_pointers(sessions):
    with sessions.begin() as session:
        kit_id, revisions = complete_kit(session)
    with sessions() as session:
        first, second, *_ = RfKitRepository(session).standards(kit_id)
        first_id, second_id = first.id, second.id
        label = first.piece_label
        revision = session.get(RfStandardCharacterizationRow, revisions[first_id])
        values = {column.name: getattr(revision, column.name) for column in revision.__table__.columns}
        for changes in ({"id": uuid4()}, {"id": uuid4(), "revision": 2},
                        {"id": uuid4(), "sha256": "0" * 64}, {"id": uuid4(), "revision": 2, "sha256": "0" * 64, "content": b""}):
            with pytest.raises(IntegrityError):
                session.execute(insert(RfStandardCharacterizationRow).values(**{**values, **changes}))
            session.rollback()
        for statement in (
            update(RfKitStandardRow).where(RfKitStandardRow.id == second_id).values(piece_label=label),
            update(RfKitStandardRow).where(RfKitStandardRow.id == second_id).values(current_revision_id=revisions[first_id]),
            delete(RfKitStandardRow).where(RfKitStandardRow.id == first_id),
            delete(ProductionInstrumentRow).where(ProductionInstrumentRow.id == kit_id),
            update(RfDetailsRow).where(RfDetailsRow.instrument_id == kit_id).values(role="solt_kit", kit_definition="SMA-MF06-characterized"),
        ):
            with pytest.raises(IntegrityError):
                session.execute(statement)
            session.rollback()


def test_append_checks_stale_versions_duplicate_bytes_kind_and_raw_link(sessions):
    with sessions.begin() as session:
        kit_id = seed_kit(session)
        repo = RfKitRepository(session)
        standard = repo.register(kit_id, piece(), expected_kit_version=1, actor="QC", now=NOW)
        append(repo, standard)
        for changes, error in (({"expected_version": 1}, ConcurrentProductionUpdate), ({}, ProductionValidationError),
                               ({"format": "s2p", "content": curve("s2p")}, ProductionValidationError),
                               ({"raw_artifact_id": uuid4(), "content": curve(value=.2)}, ProductionValidationError)):
            with pytest.raises(error):
                append(repo, standard, **changes)
        assert len(list(session.scalars(select(RfStandardCharacterizationRow)))) == 1

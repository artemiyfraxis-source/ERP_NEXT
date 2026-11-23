"""0218 SQLite rehearsal with legacy data, lineage, constraints and preservation checks."""

import json
from pathlib import Path
import shutil
import sqlite3
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.modules.warehouse.grn_registry import GrnCounterRow
from app.modules.warehouse.grn import GrnCategory, GrnContext
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseRepository
from app.workflows.grn_allocation import grn_allocator
from tests.warehouse.test_grn_allocator import NOW, request

REVISION = "0218_grn_registry"
PARENT = "0216_equipment_screen_group"
STAMP = "2026-11-23 12:00:00"


def config(path):
    result = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    result.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return result


@pytest.fixture(scope="module")
def previous(tmp_path_factory):
    path = tmp_path_factory.mktemp("grn-parent") / "previous.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        command.upgrade(config(path), PARENT)
    return path


@pytest.fixture
def database(previous, tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "grn.db"
    shutil.copyfile(previous, path)
    return path


def insert(db, table, **values):
    """Fill only legacy NOT NULL fields without defaults; callers provide domain-specific values."""
    columns = db.execute(f"PRAGMA table_info({table})").fetchall()
    integer_id = any(name == "id" and kind == "INTEGER" for _, name, kind, *_ in columns)
    values = {"id": None if integer_id else uuid4().hex, **values}
    for _, name, kind, required, default, _ in columns:
        if name in values or not required or default is not None:
            continue
        if name.endswith("_id"):
            values[name] = uuid4().hex
        elif "INT" in kind or "NUMERIC" in kind or "BOOLEAN" in kind:
            values[name] = 0
        elif "DATE" in kind:
            values[name] = STAMP
        elif "JSON" in kind:
            values[name] = "[]"
        else:
            values[name] = ""
    db.execute(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})", tuple(values.values()))
    return values["id"]


def lot(db, number, **values):
    return insert(db, "warehouse_lots", **{"grn": number, "warehouse": "ЦАР", "package": "", "version": 1,
                  "inbound": 10, "written_off": 2, "reserved": 3, "article": "A", "series": "05364575", **values})


def receiving(db, number, **values):
    return insert(db, "receiving_lines", **{"grn": number, "warehouse": "ЦАР", "status": "Ожидание", "version": 1,
                  "source_idempotency_key": str(uuid4()), "ordered_quantity": 10, **values})


def good_and_act(db, number):
    good = insert(db, "finished_goods", grn=number, project="591К", item_index=str(uuid4()), version=1,
                  accepted=10, shipped=3, sent=1, before_erp=0)
    move = insert(db, "finished_goods_moves", finished_good_id=good, kind="accept", from_state="production",
                  to_state="accepted", quantity=10)
    act = insert(db, "finished_goods_acts", finished_good_id=good, move_id=move, grn=number, quantity=10)
    return good, act


def snapshot(db):
    tables = [name for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name <> 'alembic_version'")]
    return {table: ([row[1] for row in db.execute(f"PRAGMA table_info({table})")],
                    sorted(db.execute(f"SELECT * FROM {table}").fetchall(), key=repr)) for table in tables}


def unchanged(db, before):
    for table, (columns, rows) in before.items():
        assert sorted(db.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall(), key=repr) == rows, table


def test_upgrade_downgrade_upgrade_preserves_legacy_rows_and_single_head(database):
    with sqlite3.connect(database) as db:
        for number in ("PPM.26.000042", "PPM.26.0000043", "R&D.25.0000009", "OLD.26.9000000", "malformed historical value"):
            lot(db, number)
        good_and_act(db, "ГП.26.0000012")
        insert(db, "warehouse_label_templates", kind="lot", format="100x50", template='{"custom":true}', version=7)
        insert(db, "warehouse_label_jobs", kind="sample", copies=1, tspl=b"legacy bytes", status="pending")
        before = snapshot(db)
    command.upgrade(config(database), REVISION)
    with sqlite3.connect(database) as db:
        unchanged(db, before)
        assert db.execute("SELECT category, full_year, last_serial FROM warehouse_grn_counters ORDER BY category").fetchall() == [
            ("PPM", 2026, 43), ("R&D", 2025, 9)]
        assert db.execute("SELECT count(*) FROM warehouse_grn_registry").fetchone()[0] == 6
        assert db.execute("SELECT count(*) FROM warehouse_lots WHERE quality_state IS NOT NULL OR grn_contract_version IS NOT NULL OR material_owner_id IS NOT NULL").fetchone()[0] == 0
        assert db.execute("SELECT category, form_id FROM warehouse_label_templates").fetchone() == ("", None)
        assert db.execute("SELECT grn_id, grn_provenance FROM warehouse_label_jobs").fetchone() == (None, None)
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        goods_id = db.execute("SELECT grn_id FROM finished_goods").fetchone()[0]
        assert goods_id == db.execute("SELECT grn_id FROM finished_goods_acts").fetchone()[0]
        first_upgrade = snapshot(db)
    command.downgrade(config(database), PARENT)
    with sqlite3.connect(database) as db:
        assert snapshot(db) == before
    command.upgrade(config(database), REVISION)
    assert ScriptDirectory.from_config(config(database)).get_heads() == [REVISION]
    with sqlite3.connect(database) as db:
        assert snapshot(db) == first_upgrade
    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        with Session(engine) as session, session.begin():
            assert grn_allocator(session).allocate(request(), registered_at=NOW).number == "PPM.26.000044"
    finally:
        engine.dispose()


def test_counter_maxima_include_receiving_and_production_but_do_not_alias_six_seven_digits(database):
    with sqlite3.connect(database) as db:
        lot(db, "PPM.26.000021")
        receiving(db, "PPM.26.0000021")
        receiving(db, "PPM.25.0000040")
        good_and_act(db, "FRX.26.0000088")
        lot(db, "OLD.26.9999999")
        good_and_act(db, "ГП.26.9999999")
    command.upgrade(config(database), REVISION)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT category, full_year, last_serial FROM warehouse_grn_counters ORDER BY category, full_year").fetchall() == [
            ("FRX", 2026, 88), ("PPM", 2025, 40), ("PPM", 2026, 21)]
        assert db.execute("SELECT count(*) FROM warehouse_grn_registry WHERE category='PPM' AND full_year=2026").fetchone()[0] == 2


def test_proven_receiving_packages_and_outbox_alias_share_one_identity(database):
    with sqlite3.connect(database) as db:
        original = receiving(db, "CSM.26.0000041", package="1")
        receiving(db, "CSM.26.0000041", package="2", split_of_id=original,
                  source_idempotency_key=f"receiving:{UUID(original)}:package:2")
        token = f"{UUID(original)}:r1"
        insert(db, "outbox_messages", topic="warehouse.received", idempotency_key="receipt-test", payload=json.dumps({
            "receipt_id": token, "receiving_line_id": str(UUID(original))}))
        lot(db, "CSM.26.0000041", source_receiving_ids=json.dumps([token]))
    command.upgrade(config(database), REVISION)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM warehouse_grn_registry").fetchone()[0] == 1
        assert db.execute("SELECT count(DISTINCT grn_id) FROM receiving_lines").fetchone()[0] == 1
        assert db.execute("SELECT grn_category, material_owner_id, quality_state FROM warehouse_lots").fetchone() == ("CSM", None, None)
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("problem", ["duplicate", "capacity", "orphan"])
def test_reconciliation_blockers_fail_before_creating_schema(database, problem):
    with sqlite3.connect(database) as db:
        if problem == "duplicate":
            lot(db, "PPM.26.000001")
            receiving(db, "PPM.26.000001")
        elif problem == "capacity":
            lot(db, "PAM.26.1000000")
        else:
            lot(db, "PPM.26.000001", source_receiving_ids=json.dumps([str(uuid4())]))
        before = snapshot(db)
    with pytest.raises(RuntimeError, match={"duplicate": "ambiguous", "capacity": "capacity blocker", "orphan": "orphan"}[problem]):
        command.upgrade(config(database), REVISION)
    with sqlite3.connect(database) as db:
        assert snapshot(db) == before
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == PARENT


@pytest.mark.parametrize("kind", ["batch", "revision"])
def test_batch_and_replacement_lineage_does_not_prove_same_receipt(database, kind):
    with sqlite3.connect(database) as db:
        original = receiving(db, "PPM.26.000001", package="1")
        link = {"split_of_id": original} if kind == "batch" else {"revision_of_id": original, "revision_root_id": original}
        receiving(db, "PPM.26.000001", package="2", **link, source_idempotency_key=f"receiving:{UUID(original)}:{kind}:2")
    with pytest.raises(RuntimeError, match="ambiguous"):
        command.upgrade(config(database), REVISION)


def test_backfilled_identity_survives_existing_repository_reservation(database):
    from decimal import Decimal
    with sqlite3.connect(database) as db:
        lot_id = lot(db, "PPM.26.0000077")
    command.upgrade(config(database), REVISION)
    with sqlite3.connect(database) as db:
        identity = db.execute("SELECT grn_id, grn_category FROM warehouse_lots").fetchone()
    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        with Session(engine) as session, session.begin():
            repository = SqlAlchemyWarehouseRepository(session)
            current = repository.get(UUID(lot_id))
            repository.add(current.reserve(Decimal(1)))
    finally:
        engine.dispose()
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT grn_id, grn_category FROM warehouse_lots").fetchone() == identity
        assert db.execute("SELECT reserved FROM warehouse_lots").fetchone()[0] == 4


def test_stock_topup_does_not_merge_unrelated_receipts_into_one_identity(database):
    with sqlite3.connect(database) as db:
        first = receiving(db, "PPM.26.000001", package="1")
        second = receiving(db, "PPM.26.000001", package="2")
        lot(db, "PPM.26.000001", source_receiving_ids=json.dumps([str(UUID(first)), str(UUID(second))]))
    with pytest.raises(RuntimeError, match="conflicting receipt origins"):
        command.upgrade(config(database), REVISION)


def test_imported_lot_relocated_between_warehouses_keeps_both_validated_marks(database):
    with sqlite3.connect(database) as db:
        lot(db, "OLD.26.0000042", warehouse="ЖУК", package="1", source_receiving_ids=json.dumps([
            "supplymap-import:ЦАР:OLD.26.0000042:1", "supplymap-import:ЖУК:OLD.26.0000042:1"]))
        before = snapshot(db)
    command.upgrade(config(database), REVISION)
    with sqlite3.connect(database) as db:
        unchanged(db, before)
        assert db.execute("SELECT count(*) FROM warehouse_grn_registry").fetchone()[0] == 1


def test_downgrade_refuses_to_erase_a_new_owner_without_receipts(database):
    command.upgrade(config(database), REVISION)
    with sqlite3.connect(database) as db:
        insert(db, "warehouse_material_owners", display_name="Заказчик", legal_identity="ИНН:1234567890")
    with pytest.raises(RuntimeError, match="downgrade refused"):
        command.downgrade(config(database), PARENT)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM warehouse_material_owners").fetchone()[0] == 1


def test_ci_seeded_previous_roundtrip_preserves_every_original_cell(database):
    from tests.migrations.seed_any_revision import seed
    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        seed(engine, rows=2)
    finally:
        engine.dispose()
    with sqlite3.connect(database) as db:
        before = snapshot(db)
    command.upgrade(config(database), REVISION)
    with sqlite3.connect(database) as db:
        unchanged(db, before)
    command.downgrade(config(database), PARENT)
    with sqlite3.connect(database) as db:
        assert snapshot(db) == before
    command.upgrade(config(database), REVISION)


def test_new_registry_constraints_and_immutable_identity(database):
    command.upgrade(config(database), REVISION)
    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        with Session(engine) as session, session.begin():
            identity = grn_allocator(session).allocate(request(), registered_at=NOW)
    finally:
        engine.dispose()
    with sqlite3.connect(database) as db:
        for sql in ("UPDATE warehouse_grn_registry SET category='PAM'", "DELETE FROM warehouse_grn_registry"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute(sql)
        columns = [row[1] for row in db.execute("PRAGMA table_info(warehouse_grn_registry)")]
        original = dict(zip(columns, db.execute("SELECT * FROM warehouse_grn_registry").fetchone()))
        mutations = [
            {"number": "INVALID", "serial": 2}, {"category": "BAD"}, {"serial": 0}, {"serial": 1000000},
            {"category": "CSM", "number": "CSM.26.000001"}, {"request_fingerprint": None},
            {"registered_at": None}, {"number": identity.number},
            {"source_op_id": original["source_op_id"], "serial": 2, "number": "PPM.26.000002"},
        ]
        for changes in mutations:
            row = {**original, "id": uuid4().hex, "source_op_id": str(uuid4()), **changes}
            with pytest.raises(sqlite3.IntegrityError):
                insert(db, "warehouse_grn_registry", **row)
        db.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(RuntimeError, match="downgrade refused"):
        command.downgrade(config(database), PARENT)


def test_source_quality_owner_and_identity_checks_allow_legacy_only_without_new_requirements(database):
    command.upgrade(config(database), REVISION)
    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        with Session(engine) as session, session.begin():
            identity = grn_allocator(session).allocate(request(), registered_at=NOW)
    finally:
        engine.dispose()
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        legacy = lot(db, "OLD.26.0000001")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute("UPDATE warehouse_lots SET grn_contract_version=1 WHERE id=?", (legacy,))
        valid = {"grn_id": identity.id.hex, "grn_category": "PPM", "grn_context": "{}", "grn_contract_version": 1,
                 "quality_state": "IQH", "quality_version": 1, "expiry_required": 0}
        for changes in ({"grn_category": "BAD"}, {"grn_category": "CSM"}, {"quality_state": "NOT_REQUIRED"},
                        {"quality_state": "RELEASED"}, {"quality_version": 0}, {"grn_id": uuid4().hex}):
            with pytest.raises(sqlite3.IntegrityError):
                lot(db, identity.number, **{**valid, **changes})
        first = lot(db, identity.number, **valid)
        # Same identity may have multiple physical packages; no UNIQUE(grn) on warehouse stock.
        insert(db, "warehouse_lots", grn=identity.number, package="2", warehouse="ЦАР", version=1, inbound=1, **valid)
        assert db.execute("SELECT grn_id FROM warehouse_lots WHERE id=?", (first,)).fetchone()[0] == identity.id.hex


def test_csm_project_constraint_preserves_history_and_requires_project_on_new_stock(database):
    command.upgrade(config(database), REVISION)
    owner = uuid4()
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO warehouse_material_owners (id, display_name, legal_identity) VALUES (?, ?, ?)",
                   (owner.hex, "Заказчик", "ИНН:7707083893"))
        historical = lot(db, "CSM.25.0000001")
        assert db.execute("SELECT csm_project_id FROM warehouse_lots WHERE id=?", (historical,)).fetchone() == (None,)
    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        with Session(engine) as session, session.begin():
            context = GrnContext(owner_id=owner, customer_order="591К.3", transfer_document="Акт 1")
            identity = grn_allocator(session).allocate(request(GrnCategory.CSM, context=context), registered_at=NOW)
    finally:
        engine.dispose()
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        fields = {"grn_id": identity.id.hex, "grn_category": "CSM", "grn_context": json.dumps(context.payload()),
                  "grn_contract_version": 1, "material_owner_id": owner.hex, "quality_state": "IQH",
                  "quality_version": 1, "expiry_required": 0}
        for project in (None, "", " "):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                lot(db, identity.number, **fields, csm_project_id=project)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            lot(db, identity.number, **{**fields, "material_owner_id": None}, csm_project_id="591К.3")
        saved = lot(db, identity.number, **fields, csm_project_id="591К.3")
        # Snapshot refresh may delete/reinsert projects. Historical ownership does not depend on those rows.
        db.execute("DELETE FROM production_plan_projects")
        assert db.execute("SELECT csm_project_id FROM warehouse_lots WHERE id=?", (saved,)).fetchone() == ("591К.3",)
        assert "ix_warehouse_lots_csm_project_id" in {row[1] for row in db.execute("PRAGMA index_list(warehouse_lots)")}


def test_templates_keep_legacy_and_separate_categories_and_print_provenance(database):
    with sqlite3.connect(database) as db:
        insert(db, "warehouse_label_templates", kind="lot", format="100x50", version=7, template='{"old":true}')
    command.upgrade(config(database), REVISION)
    with sqlite3.connect(database) as db:
        for category in ("PPM", "PAM", "R&D"):
            insert(db, "warehouse_label_templates", kind="lot", category=category, format="100x50", version=1, template="{}")
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "warehouse_label_templates", kind="lot", category="PPM", format="100x50", version=2, template="{}")
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "warehouse_label_templates", kind="instrument", category="PPM", format="100x50", version=1, template="{}")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE warehouse_label_templates SET form_id='05.11' WHERE category='PPM'")
        assert db.execute("SELECT template, version FROM warehouse_label_templates WHERE category=''").fetchone() == ('{"old":true}', 7)
        with pytest.raises(sqlite3.IntegrityError, match="snapshot"):
            insert(db, "warehouse_label_jobs", kind="sample", copies=1, tspl=b"print", status="pending", grn_id=uuid4().hex)


def test_new_print_snapshot_is_immutable_while_agent_status_can_progress(database):
    command.upgrade(config(database), REVISION)
    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        with Session(engine) as session, session.begin():
            identity = grn_allocator(session).allocate(request(), registered_at=NOW)
    finally:
        engine.dispose()
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        job_id = insert(db, "warehouse_label_jobs", kind="sample", copies=1, tspl=b"print", status="pending",
                        grn_id=identity.id.hex, grn_number=identity.number, grn_category="PPM", grn_provenance="{}",
                        template_kind="lot", template_format="100x50", template_version="builtin:1")
        for field, value in (("tspl", b"different"), ("grn_provenance", '{"changed":true}'), ("template_version", "builtin:2"), ("grn_id", None)):
            with pytest.raises(sqlite3.IntegrityError, match="print snapshot is immutable"):
                db.execute(f"UPDATE warehouse_label_jobs SET {field}=? WHERE id=?", (value, job_id))
        db.execute("UPDATE warehouse_label_jobs SET status='taken', taken_at=?, lease_until='2026-11-23 12:02:00' WHERE id=?", (STAMP, job_id))
        assert db.execute("SELECT tspl, status FROM warehouse_label_jobs WHERE id=?", (job_id,)).fetchone() == (b"print", "taken")

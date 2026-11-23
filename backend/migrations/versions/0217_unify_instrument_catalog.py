"""One canonical instrument catalog; reviewed UUID mapping, never a fuzzy merge.

The data file is a frozen transform of the 2026-11-23 read-only export. Production
writes must be frozen for deployment; PostgreSQL locks protect the preflight/backfill
window too. Empty databases get only DDL. Any populated compatibility catalog must
match the entire reviewed baseline, including metrology values, before any DDL/DML.

Downgrade is only a reversal of an untouched rehearsal: it refuses changed rows,
new catalog values, new bindings or references to newly backfilled instruments.
Only pristine rows created here can be removed. Audit is append-only across both
directions. After resumed writes use a coordinated backup restore or a forward fix.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from uuid import NAMESPACE_URL, UUID, uuid5

from alembic import op
import sqlalchemy as sa

revision = "0217_unify_instrument_catalog"
down_revision = "0216_equipment_screen_group"
branch_labels = None
depends_on = None
# The assigned T1 brief explicitly requests False for this non-dropping upgrade.
DESTRUCTIVE = False

MANIFEST_PATH = Path(__file__).parents[1] / "data" / "0217_instrument_manifest.json"
MANIFEST_SHA256 = "388c947feeddb36f0e898df1d4c805fab7e2ec264b3c1146172794a1472f1cf5"
CATALOG_DEFAULTS = {"article": "", "notes": "", "measurement_category": "", "custom_fields": {}}
MEASURING = "equipment_measuring_units"
BINDINGS = "equipment_instrument_bindings"
INSTRUMENTS = "production_instruments"
FK_BINDING = "fk_instrument_binding_measuring_unit"
FK_OWNER = "fk_measuring_unit_canonical_instrument"
MOSCOW = timezone(timedelta(hours=3))


def _normal(value):
    if isinstance(value, dict):
        return {key: _normal(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_normal(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or MOSCOW).astimezone(timezone.utc).isoformat()
    if isinstance(value, str):
        try:
            return str(UUID(value))
        except ValueError:
            pass
        if re.match(r"^\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d", value):
            try:
                return _normal(datetime.fromisoformat(value))
            except ValueError:
                pass
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(_normal(value), ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _manifest():
    content = MANIFEST_PATH.read_bytes()
    if hashlib.sha256(content).hexdigest() != MANIFEST_SHA256:
        raise RuntimeError("0217: frozen manifest checksum changed; review the mapping before refreezing")
    manifest = json.loads(content)
    if manifest["review"]["blocking_conflicts"]:
        raise RuntimeError("0217: unresolved identity conflicts in the reviewed manifest")
    if fingerprint(manifest["baseline"]) != manifest["baseline_sha256"]:
        raise RuntimeError("0217: invalid baseline fingerprint")
    return manifest


def _lock(connection):
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "LOCK TABLE production_instruments, equipment_models, equipment_units, "
            "equipment_measuring_units, equipment_instrument_bindings IN ACCESS EXCLUSIVE MODE")
    elif not connection.connection.driver_connection.in_transaction:
        # SQLite's legacy transaction mode does not begin a transaction for DDL.
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def _table(connection, name):
    return sa.Table(name, sa.MetaData(), autoload_with=connection)


def _flush_deferred(connection):
    if connection.dialect.name == "postgresql":
        # env.py defers existing FKs. PostgreSQL cannot ALTER a table with pending
        # FK trigger events, even when every referenced row already exists.
        connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
        connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")


def _begin_sqlite_rebuild(connection):
    if connection.dialect.name != "sqlite":
        return None
    previous = connection.exec_driver_sql("PRAGMA defer_foreign_keys").scalar_one()
    connection.exec_driver_sql("PRAGMA defer_foreign_keys = ON")
    return previous


def _finish_sqlite_rebuild(connection, previous):
    if previous is None:
        return
    if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
        raise RuntimeError("0217: orphan foreign keys after catalog rebuild")
    # DROP + RENAME leaves SQLite's deferred counters referring to the old table,
    # even when every real FK is valid. After checking ALL FKs, reset that stale
    # bookkeeping and restore the caller's deferral mode. Enforcement stays on.
    connection.exec_driver_sql("PRAGMA defer_foreign_keys = OFF")
    if previous:
        connection.exec_driver_sql("PRAGMA defer_foreign_keys = ON")


def _rows(connection, name):
    return [_normal(dict(row)) for row in connection.execute(sa.select(_table(connection, name))).mappings()]


def _values(table, values):
    result = {}
    for key, value in values.items():
        column = table.c[key]
        if value is not None and isinstance(column.type, sa.DateTime) and isinstance(value, str):
            value = datetime.fromisoformat(value)
            # SQLite stores wall time without a timezone; the application uses Moscow.
            if not column.type.timezone and value.tzinfo:
                value = value.astimezone(MOSCOW).replace(tzinfo=None)
        elif value is not None and isinstance(column.type, sa.Uuid):
            value = UUID(str(value))
        elif isinstance(value, str) and isinstance(column.type, sa.CHAR) and column.type.length == 32:
            value = UUID(value).hex
        result[key] = value
    return result


def _inventory(value):
    return re.sub(r"[\s._-]+", "", unicodedata.normalize("NFKC", value).casefold())


def _assert_unique(rows):
    for key, normalize in (("inventory_number", _inventory),
                           ("serial_number", lambda s: unicodedata.normalize("NFKC", s).strip().casefold())):
        seen = {}
        for row in rows:
            value = normalize(row[key])
            if value and value in seen:
                raise RuntimeError(f"0217: duplicate normalized {key}: {seen[value]} / {row['id']}")
            if value:
                seen[value] = row["id"]


def _assert_rows(actual, expected, label, key="id"):
    found = {row[key]: row for row in actual}
    wanted = {row[key]: _normal(row) for row in expected}
    if set(found) != set(wanted):
        raise RuntimeError(f"0217: unexpected {label} UUID set; missing={sorted(set(wanted)-set(found))}, "
                           f"extra={sorted(set(found)-set(wanted))}")
    for row_id in wanted:
        if fingerprint(found[row_id]) != fingerprint(wanted[row_id]):
            raise RuntimeError(f"0217: {label} fingerprint changed: {row_id}")


def _expected_after(manifest):
    rows = {row["id"]: {**row, **CATALOG_DEFAULTS} for row in manifest["baseline"]["instruments"]}
    for entry in manifest["entries"]:
        if entry["outcome"] == "paired":
            before = rows[entry["instrument_id"]]
            rows[entry["instrument_id"]] = {**before, **entry["write"], "version": before["version"] + 1}
        else:
            rows[entry["instrument_id"]] = {"id": entry["instrument_id"], **entry["write"]}
    return list(rows.values())


def _check_sources(connection, manifest):
    source = manifest["baseline"]["measuring_units"]
    _assert_rows(_rows(connection, MEASURING), [item["measuring"] for item in source], MEASURING)
    units = [row for row in _rows(connection, "equipment_units") if row["catalog"] == "ob_measuring"]
    _assert_rows(units, [item["unit"] for item in source], "measuring identities")
    models = {item["model"]["id"]: item["model"] for item in source}
    _assert_rows([row for row in _rows(connection, "equipment_models") if row["id"] in models],
                 list(models.values()), "measuring models")


def _preflight(connection, manifest):
    instruments = _rows(connection, INSTRUMENTS)
    _assert_unique(instruments)
    measuring = _rows(connection, MEASURING)
    bindings = _rows(connection, BINDINGS)
    units = [row for row in _rows(connection, "equipment_units") if row["catalog"] == "ob_measuring"]
    if not measuring and not units and not bindings:
        known = {row["id"] for row in manifest["baseline"]["instruments"]}
        if known.intersection(row["id"] for row in instruments):
            raise RuntimeError("0217: exported instruments exist but their reviewed measuring identities are missing")
        return False
    _check_sources(connection, manifest)
    _assert_rows(instruments, manifest["baseline"]["instruments"], INSTRUMENTS)
    _assert_rows(bindings, manifest["baseline"]["bindings"], BINDINGS, "unit_id")
    sources = {item["unit"]["id"]: item for item in manifest["baseline"]["measuring_units"]}
    before = {row["id"]: row for row in instruments}
    if len(manifest["entries"]) != len(sources) or {e["unit_id"] for e in manifest["entries"]} != set(sources):
        raise RuntimeError("0217: the manifest must resolve every measuring identity exactly once")
    if len({e["instrument_id"] for e in manifest["entries"]}) != len(sources):
        raise RuntimeError("0217: manifest is not one-to-one")
    limits = {"inventory_number": 128, "name": 255, "instrument_type": 255, "article": 512,
              "measurement_category": 255, "manufacturer": 255, "storage_location": 255, "serial_number": 128}
    for entry in manifest["entries"]:
        if entry["review_status"] != "evidence_reviewed" or entry["outcome"] not in {"paired", "new"}:
            raise RuntimeError(f"0217: unresolved entry {entry['unit_id']}")
        if fingerprint(sources[entry["unit_id"]]) != entry["source_sha256"]:
            raise RuntimeError(f"0217: invalid source fingerprint {entry['unit_id']}")
        values = entry["write"]
        if not isinstance(values["custom_fields"], dict):
            raise RuntimeError("0217: custom_fields must be an object")
        if entry["outcome"] == "paired":
            original = before.get(entry["instrument_id"])
            if fingerprint(original) != entry["instrument_sha256"]:
                raise RuntimeError(f"0217: changed instrument pair {entry['instrument_id']}")
            for key, value in values.items():
                if key not in CATALOG_DEFAULTS and original[key] not in ("", None) and original[key] != value:
                    raise RuntimeError(f"0217: nonempty destination conflict {entry['instrument_id']}:{key}")
        else:
            expected_id = str(uuid5(NAMESPACE_URL, "erp-next:instrument:equipment:" + entry["unit_id"]))
            if entry["instrument_id"] != expected_id or entry["instrument_id"] in before:
                raise RuntimeError("0217: new instrument identity collision")
            if not values["instrument_type"].strip():
                raise RuntimeError("0217: new instrument needs a meaningful type")
        for key, limit in limits.items():
            if key in values and (not isinstance(values[key], str) or len(values[key]) > limit):
                raise RuntimeError(f"0217: {entry['unit_id']}:{key} exceeds the field contract")
    _assert_unique(_expected_after(manifest))
    return True


def _event(connection, entry, before, after, direction):
    table = _table(connection, "audit_events")
    event_id = str(uuid5(NAMESPACE_URL, f"erp-next:{revision}:{direction}:{entry['unit_id']}"))
    details = _normal({"source": "migration", "revision": revision, "legacy_unit_id": entry["unit_id"],
                       "before": before, "after": after, "outcome": entry["outcome"],
                       "field_resolutions": entry["field_resolutions"]})
    predicate = table.c.id == _values(table, {"id": event_id})["id"]
    existing = connection.execute(sa.select(table.c.details).where(predicate)).scalar_one_or_none()
    if existing is not None:
        if fingerprint(existing) != fingerprint(details):
            raise RuntimeError(f"0217: migration audit identity collision {event_id}")
        return
    connection.execute(table.insert().values(**_values(table, {
        "id": event_id, "aggregate_id": entry["instrument_id"],
        "event_type": "production.instrument_catalog_" + direction,
        "actor_id": str(uuid5(NAMESPACE_URL, "erp-next:migration:0217")),
        "actor_name": "Миграция единого реестра СИ", "actor_department": "ERP",
        "occurred_at": datetime.now(MOSCOW), "details": details,
    })))


def upgrade():
    connection = op.get_bind()
    _lock(connection)
    manifest = _manifest()
    populated = _preflight(connection, manifest)
    for name, kind in (("article", sa.String(512)), ("notes", sa.Text()), ("measurement_category", sa.String(255))):
        op.add_column(INSTRUMENTS, sa.Column(name, kind, nullable=False, server_default=""))
    function = "json_typeof" if connection.dialect.name == "postgresql" else "json_type"
    # Inline CHECK avoids rebuilding the heavily referenced instrument table on SQLite.
    connection.exec_driver_sql(f"ALTER TABLE {INSTRUMENTS} ADD COLUMN custom_fields JSON NOT NULL DEFAULT '{{}}' "
                               f"CONSTRAINT ck_production_instruments_custom_fields_object "
                               f"CHECK ({function}(custom_fields) = 'object')")
    if populated:
        table, bindings = _table(connection, INSTRUMENTS), _table(connection, BINDINGS)
        before = {row["id"]: row for row in manifest["baseline"]["instruments"]}
        after = {row["id"]: row for row in _expected_after(manifest)}
        for entry in manifest["entries"]:
            instrument_id = entry["instrument_id"]
            if entry["outcome"] == "new":
                connection.execute(table.insert().values(**_values(table, after[instrument_id])))
            else:
                values = {**entry["write"], "version": after[instrument_id]["version"]}
                connection.execute(table.update().where(table.c.id == _values(table, {"id": instrument_id})["id"])
                                   .values(**_values(table, values)))
            connection.execute(bindings.insert().values(**_values(bindings, {
                "unit_id": entry["unit_id"], "instrument_id": instrument_id})))
            _event(connection, entry, before.get(instrument_id), after[instrument_id], "migrated")
        _assert_rows(_rows(connection, INSTRUMENTS), list(after.values()), "post-cutover instruments")
    _flush_deferred(connection)
    sqlite_deferral = _begin_sqlite_rebuild(connection)
    # Preserve the stable-unit FK too; both directions are required at transaction commit.
    # Add the reciprocal NO ACTION FK before the RESTRICT FK: SQLite rebuilds each
    # table and must not DROP the measuring table while a RESTRICT link points at it.
    with op.batch_alter_table(MEASURING) as batch:
        batch.create_foreign_key(FK_OWNER, BINDINGS, ["id"], ["unit_id"], deferrable=True, initially="DEFERRED")
    with op.batch_alter_table(BINDINGS) as batch:
        batch.create_foreign_key(FK_BINDING, MEASURING, ["unit_id"], ["id"],
                                 ondelete="RESTRICT", deferrable=True, initially="DEFERRED")
    _finish_sqlite_rebuild(connection, sqlite_deferral)
    print(f"0217: instruments={len(_rows(connection, INSTRUMENTS))}; "
          f"bindings={len(_rows(connection, BINDINGS))}; reviewed_backfill={populated}")


def _check_downgrade(connection, manifest):
    instruments = _rows(connection, INSTRUMENTS)
    if not _rows(connection, MEASURING) and not _rows(connection, BINDINGS):
        if any(any(row[key] != value for key, value in CATALOG_DEFAULTS.items()) for row in instruments):
            raise RuntimeError("0217: unsafe downgrade after catalog edits; restore the coordinated backup")
        return False
    _check_sources(connection, manifest)
    _assert_rows(instruments, _expected_after(manifest), "unsafe downgrade: instruments")
    _assert_rows(_rows(connection, BINDINGS), [{"unit_id": e["unit_id"], "instrument_id": e["instrument_id"]}
                                            for e in manifest["entries"]], "unsafe downgrade: bindings", "unit_id")
    new_ids = [e["instrument_id"] for e in manifest["entries"] if e["outcome"] == "new"]
    inspector = sa.inspect(connection)
    # Refuse even unchanged new instruments once other aggregates have referenced them.
    for name in inspector.get_table_names():
        if name == BINDINGS:
            continue
        for fk in inspector.get_foreign_keys(name):
            if fk["referred_table"] != INSTRUMENTS:
                continue
            table = _table(connection, name)
            column = table.c[fk["constrained_columns"][0]]
            ids = [_values(table, {column.name: value})[column.name] for value in new_ids]
            if connection.execute(sa.select(column).where(column.in_(ids)).limit(1)).first():
                raise RuntimeError(f"0217: unsafe downgrade: new instrument referenced by {name}")
    return True


def downgrade():
    connection = op.get_bind()
    _lock(connection)
    manifest = _manifest()
    populated = _check_downgrade(connection, manifest)
    sqlite_deferral = _begin_sqlite_rebuild(connection)
    with op.batch_alter_table(BINDINGS) as batch:
        batch.drop_constraint(FK_BINDING, type_="foreignkey")
    with op.batch_alter_table(MEASURING) as batch:
        batch.drop_constraint(FK_OWNER, type_="foreignkey")
    if populated:
        table, bindings = _table(connection, INSTRUMENTS), _table(connection, BINDINGS)
        before = {row["id"]: row for row in manifest["baseline"]["instruments"]}
        after = {row["id"]: row for row in _expected_after(manifest)}
        connection.execute(bindings.delete())  # Preflight proved this exact set is wholly migration-owned.
        for entry in manifest["entries"]:
            instrument_id = entry["instrument_id"]
            predicate = table.c.id == _values(table, {"id": instrument_id})["id"]
            if entry["outcome"] == "new":
                connection.execute(table.delete().where(predicate))
            else:
                connection.execute(table.update().where(predicate).values(**_values(table, before[instrument_id])))
            _event(connection, entry, after[instrument_id], before.get(instrument_id), "downgraded")
        print("0217: reversed pristine backfill only; audit retained; 108 original instruments preserved")
    _flush_deferred(connection)
    for name in reversed(CATALOG_DEFAULTS):
        connection.exec_driver_sql(f"ALTER TABLE {INSTRUMENTS} DROP COLUMN {name}")
    _finish_sqlite_rebuild(connection, sqlite_deferral)

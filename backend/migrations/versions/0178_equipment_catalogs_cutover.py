"""Copy equipment with unchanged UUID/payload, retarget relations, freeze legacy rows.

Rehearse on a restored production database with writers stopped. No names, inventory numbers or
instrument verification facts are inferred. The retained archive is not a rollback source after new writes.
"""

from collections import Counter
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0178_equipment_catalogs_cutover"
down_revision = "0177_equipment_catalogs"
branch_labels = depends_on = None
DESTRUCTIVE = False

CATALOGS = {
    "ob": "equipment_machines", "ob_measuring": "equipment_measuring_units",
    "ob_support": "equipment_support_units", "ob_applicators": "equipment_applicators",
    "ob_dies": "equipment_dies", "ob_tools": "equipment_tools",
}
TYPES = {"Оборудование": "ob", "Оборудование (ПР)": "ob", "Средства измерений": "ob_measuring",
         "Обеспечивающее оборудование": "ob_support", "Аппликаторы": "ob_applicators",
         "Матрицы": "ob_dies", "Инструмент": "ob_tools", "Обжимной инструмент": "ob_tools"}
RELATIONS = {
    "equipment_unit_links": ("unit_id", "target_id"),
    "equipment_setup_recipe_versions": ("machine_id",), "equipment_setup_tasks": ("machine_id",),
    "production_resource_reservations": ("machine_id",), "production_active_resources": ("machine_id",),
    "production_resource_allocation_machines": ("machine_id",),
}
COUNTED = (*RELATIONS, "equipment_models", "equipment_operation_links", "equipment_template_tools",
           "reference_stock_links")
NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _freeze(bind):
    if bind.dialect.name == "sqlite":
        for action, condition in (("INSERT", "NEW.tab = 'ob'"), ("UPDATE", "OLD.tab = 'ob' OR NEW.tab = 'ob'"),
                                  ("DELETE", "OLD.tab = 'ob'")):
            bind.exec_driver_sql(f"CREATE TRIGGER freeze_legacy_equipment_{action.lower()} BEFORE {action} "
                f"ON tech_operation_records WHEN {condition} BEGIN SELECT RAISE(ABORT, 'legacy equipment is frozen'); END")
    else:
        bind.exec_driver_sql("""CREATE FUNCTION freeze_legacy_equipment() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            IF OLD.tab = 'ob' THEN RAISE EXCEPTION 'legacy equipment is frozen'; END IF;
          END IF;
          IF TG_OP <> 'DELETE' THEN
            IF NEW.tab = 'ob' THEN RAISE EXCEPTION 'legacy equipment is frozen'; END IF;
            RETURN NEW;
          END IF;
          RETURN OLD;
        END $$""")
        bind.exec_driver_sql("CREATE TRIGGER freeze_legacy_equipment BEFORE INSERT OR UPDATE OR DELETE "
                             "ON tech_operation_records FOR EACH ROW EXECUTE FUNCTION freeze_legacy_equipment()")


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql("LOCK TABLE " + ", ".join(("tech_operation_records", *COUNTED)) + " IN ACCESS EXCLUSIVE MODE")
    metadata = sa.MetaData()
    source = sa.Table("tech_operation_records", metadata, autoload_with=bind)
    models = sa.Table("equipment_models", metadata, autoload_with=bind)
    roles = {row.id: row.role if row.role_confirmed else "" for row in bind.execute(sa.select(models))}
    rows = list(bind.execute(sa.select(source).where(source.c.tab == "ob")).mappings())
    manifest, errors = {}, []
    for row in rows:
        fields = row["fields"] or {}
        kind = str(fields.get("Тип") or fields.get("obType") or "").strip()
        tab = TYPES.get(kind)
        if tab is None or row["equipment_model_id"] not in roles:
            errors.append(f"{row['id']} / {fields.get('Инв. номер', '')}: Тип={kind!r}, model={row['equipment_model_id']}")
            continue
        if tab == "ob" and roles[row["equipment_model_id"]] == "supporting":
            tab = "ob_support"
        manifest[row["id"]] = (tab, kind, fields.get("Инв. номер", ""))
    if errors:
        raise RuntimeError("Equipment catalog cutover blocked; unknown type or model: " + "; ".join(errors))
    # Compare complete relation sets, not just totals, including journal text and issued task snapshots.
    before = {name: list(bind.execute(sa.select(sa.Table(name, metadata, autoload_with=bind))).mappings())
              for name in COUNTED}
    units = sa.Table("equipment_units", metadata, autoload_with=bind)
    for row in rows:
        tab = manifest[row["id"]][0]
        bind.execute(units.insert().values(id=row["id"], model_id=row["equipment_model_id"], catalog=tab,
                                          **{f"{tab}_id": row["id"]}))
        dest = sa.Table(CATALOGS[tab], metadata, autoload_with=bind)
        bind.execute(dest.insert().values(**{key: row[key] for key in dest.c.keys() if key != "tab"}, tab=tab))
    for name, columns in RELATIONS.items():
        fks = sa.inspect(bind).get_foreign_keys(name)
        with op.batch_alter_table(name, naming_convention=NAMING) as batch:
            for fk in fks:
                if fk["referred_table"] != "tech_operation_records" or not set(fk["constrained_columns"]) <= set(columns):
                    continue
                key = fk["name"] or f"fk_{name}_{fk['constrained_columns'][0]}_tech_operation_records"
                batch.drop_constraint(key, type_="foreignkey")
                batch.create_foreign_key(key, "equipment_units", fk["constrained_columns"], ["id"],
                    ondelete=fk.get("options", {}).get("ondelete"), deferrable=True, initially="DEFERRED")
    with op.batch_alter_table("reference_stock_links") as batch:
        batch.add_column(sa.Column("equipment_unit_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_reference_stock_links_equipment_unit", "equipment_units", ["equipment_unit_id"],
                                ["id"], ondelete="SET NULL", deferrable=True, initially="DEFERRED")
        batch.create_check_constraint("ck_reference_stock_links_one_target", "record_id IS NULL OR equipment_unit_id IS NULL")
        batch.drop_constraint("ck_reference_stock_links_tab", type_="check")
        tabs = ("ob", "op", "ter", "lug", "coax", "prov", "cab_coax", "cab_multi", "cab_lan", "mat",
                *tuple(CATALOGS)[1:])
        batch.create_check_constraint("ck_reference_stock_links_tab", "record_tab IN (" + ", ".join(repr(t) for t in tabs) + ")")
    journal = sa.Table("reference_stock_links", sa.MetaData(), autoload_with=bind)
    bind.execute(journal.update().where(journal.c.record_id.in_(list(manifest))).values(
        equipment_unit_id=journal.c.record_id, record_id=None))
    # Give existing custom columns and views independent metadata keys. No field payload is discarded.
    entries = sa.Table("reference_entries", sa.MetaData(), autoload_with=bind)
    for entry in list(bind.execute(sa.select(entries)).mappings()):
        if not ((entry["kind"] == "custom_column" and entry["key"].startswith("ob:")) or
                (entry["kind"] == "column_view" and entry["key"].startswith("ob|"))):
            continue
        for tab in tuple(CATALOGS)[1:]:
            # An untouched cutover can be rolled back and repeated. Independent column metadata
            # survives that rollback, including edits made while the catalogs were available.
            key = tab + entry["key"][2:]
            if bind.execute(sa.select(entries.c.id).where(entries.c.kind == entry["kind"], entries.c.key == key)).first():
                continue
            values = dict(entry)
            values.update(id=uuid4().hex if bind.dialect.name == "sqlite" else uuid4(), key=key, normalized_key=tab + entry["normalized_key"][2:])
            bind.execute(entries.insert().values(**values))
    expected = {row["id"]: row for row in rows}
    seen = set()
    for tab, name in CATALOGS.items():
        dest = sa.Table(name, sa.MetaData(), autoload_with=bind)
        for row in bind.execute(sa.select(dest)).mappings():
            assert row["id"] not in seen, f"Unit owned twice: {row['id']}"
            seen.add(row["id"])
            assert manifest[row["id"]][0] == tab
            assert all(value == expected[row["id"]][key] for key, value in row.items() if key != "tab"), row["id"]
    assert seen == set(expected), "Equipment source/destination UUID mismatch"
    for name in COUNTED:
        after = list(bind.execute(sa.select(sa.Table(name, sa.MetaData(), autoload_with=bind))).mappings())
        normalized = []
        for item in after:
            item = dict(item)
            if name == "reference_stock_links":
                item["record_id"] = item["record_id"] or item.pop("equipment_unit_id")
                item.pop("equipment_unit_id", None)
            normalized.append(item)
        assert sorted(map(repr, sorted((dict(x) for x in before[name]), key=repr))) == sorted(map(repr, sorted(normalized, key=repr))), name
    _freeze(bind)
    print(f"0178: equipment manifest {dict(Counter(tab for tab, _, _ in manifest.values()))}; "
          f"{len(seen)} UUIDs and all relation sets preserved; legacy rows frozen")


def downgrade():
    """Reverse an unchanged copy only; never replace live catalog edits with the frozen archive.

    Keep each catalog's column metadata so rollback/re-upgrade cannot discard independent views.
    Relationships can be retargeted without changing their UUIDs. Any unit change or binding blocks
    before the first schema/data mutation and requires forward repair or a coordinated restore.
    """
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql("LOCK TABLE " + ", ".join(("tech_operation_records", *COUNTED,
            "equipment_units", *CATALOGS.values(), "equipment_instrument_bindings")) + " IN ACCESS EXCLUSIVE MODE")
    metadata = sa.MetaData()
    source = sa.Table("tech_operation_records", metadata, autoload_with=bind)
    archive = {row["id"]: dict(row) for row in bind.execute(sa.select(source).where(source.c.tab == "ob")).mappings()}
    live = {}
    for name in CATALOGS.values():
        table = sa.Table(name, metadata, autoload_with=bind)
        for row in bind.execute(sa.select(table)).mappings():
            values = dict(row)
            values["tab"] = "ob"
            live[row["id"]] = values
    units = sa.Table("equipment_units", metadata, autoload_with=bind)
    owners = {row.id: row.model_id for row in bind.execute(sa.select(units))}
    bindings = sa.Table("equipment_instrument_bindings", metadata, autoload_with=bind)
    changed = set(live) != set(archive) or any(
        any(value != archive[identifier][key] for key, value in row.items()) for identifier, row in live.items())
    if changed or owners != {identifier: row["equipment_model_id"] for identifier, row in archive.items()} or bind.execute(
            sa.select(bindings.c.unit_id).limit(1)).first():
        raise RuntimeError("Equipment catalogs changed after cutover; cannot restore a stale archive. "
                           "Use a reviewed forward repair or coordinated restore.")
    for name, columns in RELATIONS.items():
        fks = sa.inspect(bind).get_foreign_keys(name)
        with op.batch_alter_table(name, naming_convention=NAMING) as batch:
            for fk in fks:
                if fk["referred_table"] != "equipment_units" or not set(fk["constrained_columns"]) <= set(columns):
                    continue
                key = fk["name"] or f"fk_{name}_{fk['constrained_columns'][0]}_equipment_units"
                batch.drop_constraint(key, type_="foreignkey")
                batch.create_foreign_key(key, "tech_operation_records", fk["constrained_columns"], ["id"],
                    ondelete=fk.get("options", {}).get("ondelete"), deferrable=True, initially="DEFERRED")
    journal = sa.Table("reference_stock_links", metadata, autoload_with=bind)
    bind.execute(journal.update().where(journal.c.equipment_unit_id.is_not(None)).values(
        record_id=journal.c.equipment_unit_id, equipment_unit_id=None))
    bind.execute(journal.update().where(journal.c.record_tab.in_(tuple(CATALOGS)[1:])).values(record_tab="ob"))
    if bind.dialect.name == "postgresql":
        # PostgreSQL cannot ALTER a table with pending deferred FK trigger events.
        bind.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    with op.batch_alter_table("reference_stock_links") as batch:
        batch.drop_constraint("fk_reference_stock_links_equipment_unit", type_="foreignkey")
        batch.drop_constraint("ck_reference_stock_links_one_target", type_="check")
        batch.drop_constraint("ck_reference_stock_links_tab", type_="check")
        batch.drop_column("equipment_unit_id")
        batch.create_check_constraint("ck_reference_stock_links_tab",
            "record_tab IN ('ob', 'op', 'ter', 'lug', 'coax', 'prov', 'cab_coax', 'cab_multi', 'cab_lan', 'mat')")
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
    for name in CATALOGS.values():
        bind.execute(metadata.tables[name].delete())
    bind.execute(units.delete())
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    if bind.dialect.name == "sqlite":
        for action in ("insert", "update", "delete"):
            bind.exec_driver_sql(f"DROP TRIGGER freeze_legacy_equipment_{action}")
    else:
        bind.exec_driver_sql("DROP TRIGGER freeze_legacy_equipment ON tech_operation_records")
        bind.exec_driver_sql("DROP FUNCTION freeze_legacy_equipment()")

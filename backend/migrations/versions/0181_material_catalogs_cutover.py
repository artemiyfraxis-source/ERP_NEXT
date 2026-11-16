"""Split heat shrink and other coverings without discarding the legacy material archive.

Run with writers stopped and reconcile a restored database before deployment. Classification
below is frozen with this revision; future application rules must not alter a historical cutover.
"""

import re
from collections import Counter
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0181_material_catalogs_cutover"
down_revision = "0180_equipment_catalogs_cutover"
branch_labels = depends_on = None
DESTRUCTIVE = False

TABLES = {"heat_shrink": "heat_shrink_records", "cov": "covering_records"}
GROUPS = {"тут": "tut", "ттк": "ttk", "оплётка": "braid", "оплетка": "braid",
          "плетёнка": "braid", "плетенка": "braid", "гофра": "corrugated", "лента": "tape", "кембрик": "cambric"}
OVERRIDES = {"тут": "tut", "ттк": "ttk", "оплётки": "braid", "оплётка": "braid", "оплетки": "braid",
             "оплетка": "braid", "гофра": "corrugated", "кембрик": "cambric", "ленты": "tape", "лента": "tape", "прочее": "other"}
SHRINK = re.compile(r"термоусад|\bтут(?:нг)?|heat[\s-]*shrink|(?<!\w)(?:с?ттк|нттк)\d*", re.I)
GLUE = re.compile(r"клеев|с\s+клеем|adhesive|dual[\s-]*wall|glue|\bттк\b", re.I)
OTHER = (("cambric", r"кембрик"), ("corrugated", r"гофр|corrugated"),
         ("braid", r"опл[её]т|плет[её]н|braid"), ("tape", r"изолент|(?<![а-яё])лент[аы]?(?![а-яё])|\btape\b"))
PAYLOAD = ("id", "display_text", "normalized_search", "source_sheet", "sort_key", "fields",
           "mirror_key", "edited_in_erp", "equipment_model_id")
OLD_TABS = ("ob", "op", "ter", "lug", "coax", "prov", "cab_coax", "cab_multi", "cab_lan", "mat",
            "ob_measuring", "ob_support", "ob_applicators", "ob_dies", "ob_tools")
TARGETS = ("record_id", "equipment_unit_id", "heat_shrink_id", "covering_id")


def classify(fields):
    value = lambda key: str(fields.get(key) or "").strip()
    override, sheet = value("Раздел оболочек").casefold(), value("Группа").casefold()
    text = value("Наименование") + " " + value("Серия")
    glued = value("Клеевой слой").casefold() in ("да", "yes", "true", "+") or bool(GLUE.search(text))
    if override in OVERRIDES:
        group = OVERRIDES[override]
    elif sheet in GROUPS:
        group = GROUPS[sheet]
        if group == "tut" and glued:
            group = "ttk"
    elif override:
        group = None
    elif SHRINK.search(text):
        group = "ttk" if glued else "tut"
    else:
        group = next((key for key, pattern in OTHER if re.search(pattern, text, re.I)), None)
    return ("mat", "other", "unknown") if group is None else (
        "heat_shrink" if group in ("tut", "ttk") else "cov", group, "classified")


def _freeze(bind):
    if bind.dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            bind.exec_driver_sql(f"CREATE TRIGGER freeze_legacy_material_{action.lower()} BEFORE {action} "
                "ON tech_operation_records WHEN OLD.material_archived = 1 "
                "BEGIN SELECT RAISE(ABORT, 'legacy material is frozen'); END")
    else:
        bind.exec_driver_sql("""CREATE FUNCTION freeze_legacy_material() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.material_archived THEN RAISE EXCEPTION 'legacy material is frozen'; END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$""")
        bind.exec_driver_sql("CREATE TRIGGER freeze_legacy_material BEFORE UPDATE OR DELETE ON tech_operation_records "
                             "FOR EACH ROW EXECUTE FUNCTION freeze_legacy_material()")


def _stock_tab_check(tabs):
    return "record_tab IN (" + ", ".join(repr(tab) for tab in tabs) + ")"


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql("LOCK TABLE tech_operation_records, reference_stock_links, reference_entries "
                             "IN ACCESS EXCLUSIVE MODE")
    # ADD COLUMN preserves existing equipment freeze triggers on SQLite (no source-table rebuild).
    op.add_column("tech_operation_records", sa.Column("material_archived", sa.Boolean(), nullable=False, server_default=sa.false()))
    for column in ("material_group", "classification_status"):
        op.add_column("tech_operation_records", sa.Column(column, sa.String(32), nullable=False, server_default=""))
    for tab, name in TABLES.items():
        op.create_table(name,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("tab", sa.String(16), nullable=False),
            sa.Column("display_text", sa.String(2000), nullable=False),
            sa.Column("normalized_search", sa.Text(), nullable=False),
            sa.Column("source_sheet", sa.String(255), nullable=False),
            sa.Column("sort_key", sa.String(2000), nullable=False),
            sa.Column("fields", sa.JSON(), nullable=False),
            sa.Column("mirror_key", sa.String(64), nullable=False, server_default=""),
            sa.Column("edited_in_erp", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("equipment_model_id", sa.Uuid(), nullable=True),
            sa.Column("material_group", sa.String(32), nullable=False, server_default="other"),
            sa.Column("classification_status", sa.String(32), nullable=False, server_default="unknown"),
            sa.CheckConstraint(f"tab = '{tab}'", name=f"ck_{name}_tab"),
            sa.CheckConstraint("equipment_model_id IS NULL", name=f"ck_{name}_model"),
            sa.CheckConstraint("classification_status IN ('classified', 'unknown')", name=f"ck_{name}_status"),
            sa.CheckConstraint("material_group IN " + ("('tut', 'ttk', 'other')" if tab == "heat_shrink" else
                "('cambric', 'corrugated', 'braid', 'tape', 'other')"), name=f"ck_{name}_group"))
        op.create_index(f"ix_{name}_sort", name, ["sort_key"])
    source = sa.Table("tech_operation_records", sa.MetaData(), autoload_with=bind)
    rows = list(bind.execute(sa.select(source).where(source.c.tab == "mat")).mappings())
    manifest = {}
    for row in rows:
        tab, group, status = classify(row["fields"])
        values = {"material_group": group, "classification_status": status}
        if tab in TABLES:
            if row["equipment_model_id"] is not None:
                raise RuntimeError(f"Material has an unexpected equipment model: {row['id']}")
            dest = sa.Table(TABLES[tab], sa.MetaData(), autoload_with=bind)
            bind.execute(dest.insert().values(**{key: row[key] for key in PAYLOAD}, tab=tab, **values))
            values["material_archived"] = True
            manifest[row["id"]] = tab
        bind.execute(source.update().where(source.c.id == row["id"]).values(**values))
    journal = sa.Table("reference_stock_links", sa.MetaData(), autoload_with=bind)
    before_links = list(bind.execute(sa.select(journal)).mappings())
    with op.batch_alter_table("reference_stock_links") as batch:
        for column, table in (("heat_shrink_id", "heat_shrink_records"), ("covering_id", "covering_records")):
            batch.add_column(sa.Column(column, sa.Uuid(), nullable=True))
            batch.create_foreign_key(f"fk_reference_stock_links_{column}", table, [column], ["id"], ondelete="SET NULL")
            batch.create_index(f"ix_reference_stock_links_{column}", [column])
        batch.drop_constraint("ck_reference_stock_links_one_target", type_="check")
        batch.create_check_constraint("ck_reference_stock_links_one_target",
            " + ".join(f"CASE WHEN {column} IS NULL THEN 0 ELSE 1 END" for column in TARGETS) + " <= 1")
        batch.create_check_constraint("ck_reference_stock_links_material_target",
            "(heat_shrink_id IS NULL OR record_tab = 'heat_shrink') AND (covering_id IS NULL OR record_tab = 'cov')")
        batch.drop_constraint("ck_reference_stock_links_tab", type_="check")
        batch.create_check_constraint("ck_reference_stock_links_tab", _stock_tab_check((*OLD_TABS, *TABLES)))
    journal = sa.Table("reference_stock_links", sa.MetaData(), autoload_with=bind)
    for tab, column in (("heat_shrink", "heat_shrink_id"), ("cov", "covering_id")):
        identifiers = [identifier for identifier, owner in manifest.items() if owner == tab]
        bind.execute(journal.update().where(journal.c.record_id.in_(identifiers)).values(
            **{column: journal.c.record_id}, record_id=None, record_tab=tab))
    # Copy metadata by value: later edits of any tab must never affect another tab.
    entries = sa.Table("reference_entries", sa.MetaData(), autoload_with=bind)
    for row in list(bind.execute(sa.select(entries)).mappings()):
        separator = ":" if row["kind"] in ("custom_column", "removed_column") else "|" if row["kind"] == "column_view" else None
        if not separator or not row["key"].startswith("mat" + separator):
            continue
        for tab in TABLES:
            key = tab + row["key"][3:]
            if bind.execute(sa.select(entries.c.id).where(entries.c.kind == row["kind"], entries.c.key == key)).first():
                continue
            values = dict(row)
            values.update(id=uuid4().hex if bind.dialect.name == "sqlite" else uuid4(), key=key,
                          normalized_key=tab + row["normalized_key"][3:])
            bind.execute(entries.insert().values(**values))
    originals = {row["id"]: row for row in rows}
    seen = set()
    for tab, name in TABLES.items():
        dest = sa.Table(name, sa.MetaData(), autoload_with=bind)
        for row in bind.execute(sa.select(dest)).mappings():
            if row["id"] in seen or manifest.get(row["id"]) != tab or any(
                    row[key] != originals[row["id"]][key] for key in PAYLOAD):
                raise RuntimeError(f"Material copy reconciliation failed: {row['id']}")
            seen.add(row["id"])
    if seen != set(manifest):
        raise RuntimeError("Material UUID coverage failed")
    after_links = []
    for row in bind.execute(sa.select(journal)).mappings():
        values = dict(row)
        values["record_id"] = values["record_id"] or values.pop("heat_shrink_id") or values.pop("covering_id")
        values.pop("heat_shrink_id", None)
        values.pop("covering_id", None)
        if values["record_id"] in manifest:
            values["record_tab"] = "mat"
        after_links.append(values)
    if sorted(map(repr, map(dict, before_links))) != sorted(map(repr, after_links)):
        raise RuntimeError("Stock-link journal reconciliation failed")
    _freeze(bind)
    print(f"0181: {dict(Counter(manifest.values()))}; remaining mat={len(rows) - len(manifest)}; "
          f"{len(manifest)} UUIDs/payloads copied, source retained/frozen; {len(before_links)} stock links preserved")


def downgrade():
    # Forward-only is deliberate: a later move/edit/delete must never reactivate stale archive data.
    # Restoring a coordinated pre-cutover backup remains possible without this unsafe fallback.
    raise RuntimeError("0181 retains a frozen material archive. Use a reviewed forward repair or coordinated restore; "
                       "automatic downgrade cannot safely restore current material ownership.")

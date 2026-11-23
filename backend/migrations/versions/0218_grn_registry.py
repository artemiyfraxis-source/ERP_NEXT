"""Shared GRN identities, per-category/year counters and receipt/print metadata.

Follows 0217_unify_instrument_catalog (single Alembic head).
Legacy identifiers, stock quantities, package keys and printed bytes are never rewritten.
Reconciliation runs BEFORE DDL, including ambiguous lineage and six-digit capacity checks.
Downgrade is for pre-cutover rehearsal only and refuses new-contract activity.
"""

from collections import defaultdict
import json
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from alembic import op
import sqlalchemy as sa

revision = "0218_grn_registry"
down_revision = "0217_unify_instrument_catalog"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

# Frozen contract: migrations must not import live application definitions.
PREFIXES = "'PPM', 'PAM', 'GOS', 'SUB', 'CSM', 'FRX', 'R&D'"
NUMBER = re.compile(r"^(PPM|PAM|GOS|SUB|CSM|FRX|R&D)\.([0-9]{2})\.([0-9]{6,7})$")
CONTEXT = ("customer_order", "transfer_document", "project", "production_order", "subcontractor",
           "acceptance_document", "purpose", "equipment")
SOURCES = ("warehouse_lots", "receiving_lines", "finished_goods_acts", "finished_goods")
CONTEXT_CHECKS = {
    "pam": "category <> 'PAM' OR purpose IS NOT NULL OR equipment IS NOT NULL",
    "gos": "category <> 'GOS' OR purpose IS NOT NULL",
    "sub": "category <> 'SUB' OR (subcontractor IS NOT NULL AND production_order IS NOT NULL AND acceptance_document IS NOT NULL)",
    "csm": "category <> 'CSM' OR (owner_id IS NOT NULL AND customer_order IS NOT NULL AND transfer_document IS NOT NULL)",
    "frx": "category <> 'FRX' OR (production_order IS NOT NULL AND acceptance_document IS NOT NULL)",
    "rd": "category <> 'R&D' OR (project IS NOT NULL AND purpose IS NOT NULL)",
    "owner_category": "owner_id IS NULL OR category = 'CSM'",
}


def _id(value):
    return UUID(str(value)).hex if value is not None else None


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _legacy(connection):
    """One identity only where existing links prove a shared physical receipt.

Same text, article, delivery header or procurement line alone is NOT lineage evidence.
    Explicit package splits, stock receipt tokens and first-act links are evidence.
    Batch splits and supplier replacement revisions identify separate physical receipts.
"""
    columns = {
        "warehouse_lots": "id, grn, warehouse, package, source_receiving_ids",
        "receiving_lines": "id, grn, split_of_id, revision_of_id, revision_root_id, source_idempotency_key",
        "finished_goods_acts": "id, grn, finished_good_id",
        "finished_goods": "id, grn",
    }
    rows = {table: list(connection.execute(sa.text(f"SELECT {columns[table]} FROM {table}")).mappings()) for table in SOURCES}
    parents = {}

    def root(node):
        parents.setdefault(node, node)
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    def union(left, right):
        parents[root(right)] = root(left)

    receiving = {_id(row["id"]): row for row in rows["receiving_lines"]}
    goods = {_id(row["id"]): row for row in rows["finished_goods"]}
    aliases = {}
    for payload, in connection.execute(sa.text("SELECT payload FROM outbox_messages WHERE topic = 'warehouse.received'")):
        payload = _json(payload)
        if payload.get("receiving_line_id"):
            line_id = _id(payload["receiving_line_id"])
            token = str(payload.get("receipt_id") or payload["receiving_line_id"])
            if token in aliases and aliases[token] != line_id:
                raise RuntimeError(f"0218 conflicting outbox receipt alias: {token}")
            aliases[token] = line_id
    for row in rows["receiving_lines"]:
        node = ("receiving_lines", _id(row["id"]))
        for field in ("split_of_id", "revision_of_id", "revision_root_id"):
            other = _id(row[field])
            if other:
                if other not in receiving:
                    raise RuntimeError(f"0218 orphan receiving lineage: {node}, {field}={other}")
                key = row["source_idempotency_key"]
                prefix = f"receiving:{UUID(other)}:package:"
                if field == "split_of_id" and key.startswith(prefix) and key[len(prefix):].isdigit():
                    if row["grn"] != receiving[other]["grn"]:
                        raise RuntimeError(f"0218 inconsistent package identity: {node}, parent={other}")
                    union(node, ("receiving_lines", other))
    for row in rows["warehouse_lots"]:
        node = ("warehouse_lots", _id(row["id"]))
        origins = set()
        imports = set()
        for token in _json(row["source_receiving_ids"]) or []:
            token = str(token)
            # manual:<delivery UUID> describes a whole header, not a physical receipt line.
            if token.startswith("manual:"):
                continue
            target = aliases.get(token)
            if target is None:
                try:
                    target = _id(token)
                except (ValueError, AttributeError):
                    target = None
            if target in receiving:
                if row["grn"] != receiving[target]["grn"]:
                    raise RuntimeError(f"0218 inconsistent stock identity: {node}, receiving={target}")
                origins.add(root(("receiving_lines", target)))
            elif target is not None:
                raise RuntimeError(f"0218 orphan stock receipt token: {node}, {token}")
            elif token.startswith("supplymap-import:"):
                # _sync_legacy_lot retains old AND new warehouse marks after relocation.
                # Validate the exact stock key before treating those marks as evidence.
                try:
                    warehouse, rest = token.removeprefix("supplymap-import:").split(":", 1)
                    number, package = rest.rsplit(":", 1)
                except ValueError:
                    raise RuntimeError(f"0218 malformed import identity: {node}, {token}") from None
                if warehouse not in {"ЦАР", "ЖУК"} or (number, package) != (row["grn"], row["package"]):
                    raise RuntimeError(f"0218 inconsistent import identity: {node}, {token}")
                imports.add(root(("import", token)))
        if len(origins) > 1 or (origins and imports):
            raise RuntimeError(f"0218 conflicting receipt origins in stock: {node}, {sorted(origins)}")
        for origin in origins | imports:
            union(origin, node)
    for row in rows["finished_goods_acts"]:
        good = goods.get(_id(row["finished_good_id"]))
        if good is None:
            raise RuntimeError(f"0218 orphan finished-goods act: {row['id']}")
        if row["grn"] == good["grn"]:
            union(("finished_goods_acts", _id(row["id"])), ("finished_goods", _id(good["id"])))

    grouped = defaultdict(list)
    maxima = {}
    for table, records in rows.items():
        for row in records:
            if row["grn"] is not None and row["grn"] != "":
                grouped[row["grn"]].append((table, _id(row["id"])))
    identities = []
    for number, references in sorted(grouped.items()):
        if len({root(node) for node in references}) != 1:
            raise RuntimeError(f"0218 ambiguous legacy GRN {number!r}: {references}; reconcile origins before cutover")
        match = NUMBER.fullmatch(number)
        category, year = None, None
        if match:
            category, yy, serial = match.groups()
            year, serial = 2000 + int(yy), int(serial)
            if serial > 999999:
                raise RuntimeError(f"0218 GRN capacity blocker: {category}/{year} legacy maximum {serial} exceeds 999999")
            maxima[category, year] = max(maxima.get((category, year), 0), serial)
        source = min(references)
        # Rehearsal up/down/up must restore the same identity and references, not just raw GRNs.
        identity_id = uuid5(NAMESPACE_URL, f"erp-next:grn:legacy:{source[0]}:{source[1]}:{number}")
        identities.append((dict(id=identity_id, number=number, category=category, full_year=year, serial=None,
                                is_legacy=True, source_kind="legacy", source_op_id=f"{source[0]}:{source[1]}"), references))
    print(f"0218 reconciliation: {len(identities)} legacy identities; source rows "
          + ", ".join(f"{table}={len(records)}" for table, records in rows.items()))
    for (category, year), maximum in sorted(maxima.items()):
        print(f"0218 counter {category}/{year}: legacy maximum={maximum}, next={maximum + 1}")
    return identities, maxima


def _registry():
    op.create_table("warehouse_material_owners",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("display_name", sa.String(512), nullable=False),
        sa.Column("legal_identity", sa.String(512), nullable=False),
        sa.CheckConstraint("trim(display_name) <> ''", name="ck_material_owner_name"),
        sa.CheckConstraint("trim(legal_identity) <> ''", name="ck_material_owner_identity"),
        sa.UniqueConstraint("legal_identity", name="uq_material_owner_identity"))
    op.create_table("warehouse_grn_counters",
        sa.Column("category", sa.String(3), primary_key=True),
        sa.Column("full_year", sa.Integer(), primary_key=True),
        sa.Column("last_serial", sa.Integer(), nullable=False),
        sa.CheckConstraint(f"category IN ({PREFIXES})", name="ck_grn_counter_category"),
        sa.CheckConstraint("full_year BETWEEN 2000 AND 9999", name="ck_grn_counter_year"),
        sa.CheckConstraint("last_serial BETWEEN 0 AND 999999", name="ck_grn_counter_serial"))
    return op.create_table("warehouse_grn_registry",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("number", sa.String(32), nullable=False),
        sa.Column("category", sa.String(3)), sa.Column("full_year", sa.Integer()), sa.Column("serial", sa.Integer()),
        sa.Column("is_legacy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_kind", sa.String(32), nullable=False), sa.Column("source_op_id", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64)), sa.Column("registered_at", sa.DateTime(timezone=True)),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("warehouse_material_owners.id", ondelete="RESTRICT")),
        *(sa.Column(name, sa.String(512)) for name in CONTEXT),
        sa.UniqueConstraint("number", name="uq_grn_number"),
        sa.UniqueConstraint("category", "full_year", "serial", name="uq_grn_category_year_serial"),
        sa.UniqueConstraint("source_kind", "source_op_id", name="uq_grn_source_operation"),
        sa.UniqueConstraint("id", "category", "number", name="uq_grn_reference"),
        sa.UniqueConstraint("id", "owner_id", name="uq_grn_owner_reference"),
        sa.CheckConstraint(f"category IS NULL OR category IN ({PREFIXES})", name="ck_grn_category"),
        sa.CheckConstraint("full_year IS NULL OR full_year BETWEEN 2000 AND 9999", name="ck_grn_year"),
        sa.CheckConstraint("serial IS NULL OR serial BETWEEN 1 AND 999999", name="ck_grn_serial"),
        sa.CheckConstraint("length(number) > 0", name="ck_grn_number"),
        sa.CheckConstraint("trim(source_kind) <> '' AND trim(source_op_id) <> ''", name="ck_grn_source"),
        sa.CheckConstraint("(is_legacy AND serial IS NULL AND source_kind = 'legacy') OR "
                           "(NOT is_legacy AND category IS NOT NULL AND full_year IS NOT NULL AND serial IS NOT NULL "
                           "AND registered_at IS NOT NULL AND request_fingerprint IS NOT NULL "
                           "AND length(request_fingerprint) = 64 AND source_kind <> 'legacy')", name="ck_grn_new_identity"),
        sa.CheckConstraint("is_legacy OR (number = category || '.' || substr(CAST(full_year AS VARCHAR), 3, 2) || '.' || "
                           "substr('000000', 1, 6 - length(CAST(serial AS VARCHAR))) || CAST(serial AS VARCHAR))",
                           name="ck_grn_canonical_number"),
        *(sa.CheckConstraint(f"is_legacy OR ({sql})", name=f"ck_grn_context_{name}") for name, sql in CONTEXT_CHECKS.items()),
        *(sa.CheckConstraint(f"{name} IS NULL OR trim({name}) <> ''", name=f"ck_grn_text_{name}") for name in CONTEXT))


def _source_columns():
    return [sa.Column("grn_id", sa.Uuid()), sa.Column("grn_category", sa.String(3)),
            sa.Column("grn_context", sa.JSON()), sa.Column("grn_contract_version", sa.Integer()),
            sa.Column("material_owner_id", sa.Uuid()), sa.Column("quality_state", sa.String(16)),
            sa.Column("quality_basis", sa.Text()), sa.Column("quality_decided_by", sa.Uuid()),
            sa.Column("quality_decided_at", sa.DateTime(timezone=True)), sa.Column("quality_version", sa.Integer()),
            sa.Column("expiry_required", sa.Boolean())]


def _source_checks(table):
    return {
        "category": f"grn_category IS NULL OR grn_category IN ({PREFIXES})",
        "contract": "grn_contract_version IS NULL OR grn_contract_version = 1",
        "new": "grn_contract_version IS NULL OR (grn_category IS NOT NULL AND grn_context IS NOT NULL "
               "AND quality_state IS NOT NULL AND quality_version IS NOT NULL AND expiry_required IS NOT NULL "
               "AND (grn IS NULL OR grn_id IS NOT NULL))" +
               (" AND (grn_contract_version IS NULL OR grn_id IS NOT NULL)" if table != "receiving_lines" else ""),
        "owner": "grn_contract_version IS NULL OR ((grn_category = 'CSM' AND material_owner_id IS NOT NULL) "
                 "OR (grn_category <> 'CSM' AND material_owner_id IS NULL))",
        "quality": "quality_state IS NULL OR quality_state IN ('IQH', 'QTN', 'RELEASED', 'REJECTED', 'NOT_REQUIRED')",
        "quality_version": "quality_version IS NULL OR quality_version >= 1",
        "decision": "quality_state IS NULL OR quality_state IN ('IQH', 'QTN') OR "
                    "(quality_basis IS NOT NULL AND trim(quality_basis) <> '' AND quality_decided_by IS NOT NULL "
                    "AND quality_decided_at IS NOT NULL)",
        "required_qc": "grn_contract_version IS NULL OR grn_category NOT IN ('PPM', 'CSM', 'SUB') "
                       "OR quality_state <> 'NOT_REQUIRED'",
    }


def _sources():
    for table in SOURCES:
        with op.batch_alter_table(table) as batch:
            if table == "receiving_lines":
                batch.drop_constraint("uq_receiving_grn_package", type_="unique")
                batch.create_index("uq_receiving_grn_package_active", ["grn", "package"], unique=True,
                    sqlite_where=sa.text("status <> 'Архив ВК'"), postgresql_where=sa.text("status <> 'Архив ВК'"))
            for column in _source_columns():
                batch.add_column(column)
            if table == "warehouse_lots":
                batch.add_column(sa.Column("csm_project_id", sa.String(255)))
                # production_plan_projects is a replace-all imported snapshot, not an FK target.
                # Acceptance verifies its stable project key; historical CSM keeps NULL.
                batch.create_index("ix_warehouse_lots_csm_project_id", ["csm_project_id"])
                batch.create_check_constraint("ck_warehouse_lots_csm_project",
                    "grn_contract_version IS NULL OR ((grn_category = 'CSM' AND csm_project_id IS NOT NULL "
                    "AND trim(csm_project_id) <> '') "
                    "OR (grn_category <> 'CSM' AND csm_project_id IS NULL))")
            batch.create_foreign_key(f"fk_{table}_grn", "warehouse_grn_registry", ["grn_id"], ["id"], ondelete="RESTRICT")
            batch.create_foreign_key(f"fk_{table}_grn_identity", "warehouse_grn_registry",
                                     ["grn_id", "grn_category", "grn"], ["id", "category", "number"], ondelete="RESTRICT")
            batch.create_foreign_key(f"fk_{table}_grn_owner", "warehouse_grn_registry",
                                     ["grn_id", "material_owner_id"], ["id", "owner_id"], ondelete="RESTRICT")
            batch.create_foreign_key(f"fk_{table}_material_owner", "warehouse_material_owners",
                                     ["material_owner_id"], ["id"], ondelete="RESTRICT")
            for suffix, sql in _source_checks(table).items():
                batch.create_check_constraint(f"ck_{table}_grn_{suffix}", sql)
            batch.create_index(f"ix_{table}_grn_id", ["grn_id"])


def _templates():
    with op.batch_alter_table("warehouse_label_templates") as batch:
        batch.add_column(sa.Column("category", sa.String(3), nullable=False, server_default=""))
        batch.add_column(sa.Column("form_id", sa.String(128)))
        batch.add_column(sa.Column("form_revision", sa.String(64)))
        batch.add_column(sa.Column("form_effective_date", sa.Date()))
        batch.drop_constraint("uq_warehouse_label_templates_kind_format", type_="unique")
        batch.create_unique_constraint("uq_label_templates_kind_category_format", ["kind", "category", "format"])
        batch.create_check_constraint("ck_label_templates_category", f"category = '' OR category IN ({PREFIXES})")
        batch.create_check_constraint("ck_label_templates_instrument_category", "kind <> 'instrument' OR category = ''")
        batch.create_check_constraint("ck_label_templates_form", "(form_id IS NULL AND form_revision IS NULL AND form_effective_date IS NULL) OR "
                                      "(form_id IS NOT NULL AND trim(form_id) <> '' AND form_revision IS NOT NULL "
                                      "AND trim(form_revision) <> '' AND form_effective_date IS NOT NULL)")
    with op.batch_alter_table("warehouse_label_jobs") as batch:
        batch.add_column(sa.Column("grn_id", sa.Uuid()))
        batch.add_column(sa.Column("grn_category", sa.String(3)))
        batch.add_column(sa.Column("grn_number", sa.String(32)))
        batch.add_column(sa.Column("grn_provenance", sa.JSON()))
        batch.add_column(sa.Column("template_kind", sa.String(16)))
        batch.add_column(sa.Column("template_format", sa.String(8)))
        batch.add_column(sa.Column("template_version", sa.String(64)))
        batch.add_column(sa.Column("form_id", sa.String(128)))
        batch.add_column(sa.Column("form_revision", sa.String(64)))
        batch.add_column(sa.Column("form_effective_date", sa.Date()))
        batch.create_foreign_key("fk_label_jobs_grn", "warehouse_grn_registry", ["grn_id"], ["id"], ondelete="RESTRICT")
        batch.create_foreign_key("fk_label_jobs_grn_identity", "warehouse_grn_registry",
                                 ["grn_id", "grn_category", "grn_number"], ["id", "category", "number"], ondelete="RESTRICT")
        batch.create_check_constraint("ck_label_jobs_grn_category", f"grn_category IS NULL OR grn_category IN ({PREFIXES})")
        batch.create_check_constraint("ck_label_jobs_grn_snapshot", "grn_id IS NULL OR (grn_number IS NOT NULL "
                                      "AND grn_provenance IS NOT NULL AND template_kind IS NOT NULL "
                                      "AND template_format IS NOT NULL AND template_version IS NOT NULL "
                                      "AND trim(template_version) <> '')")


def _immutable():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE FUNCTION grn_registry_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ "
                   "BEGIN RAISE EXCEPTION 'GRN identity is immutable'; END; $$")
        op.execute("CREATE TRIGGER grn_registry_immutable BEFORE UPDATE OR DELETE ON warehouse_grn_registry "
                   "FOR EACH ROW EXECUTE FUNCTION grn_registry_immutable()")
    else:
        for action in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER grn_registry_immutable_{action.lower()} BEFORE {action} ON warehouse_grn_registry "
                       "BEGIN SELECT RAISE(ABORT, 'GRN identity is immutable'); END")
    # Status/lease/result fields remain writable; the payload of a snapshotted print does not.
    frozen = ("grn_id", "grn_category", "grn_number", "grn_provenance", "template_kind", "template_format",
              "template_version", "form_id", "form_revision", "form_effective_date", "tspl", "png", "copies",
              "kind", "lot_id", "format", "created_at", "created_by")
    if op.get_bind().dialect.name == "postgresql":
        changed = " OR ".join(f"CAST(OLD.{name} AS TEXT) IS DISTINCT FROM CAST(NEW.{name} AS TEXT)" for name in frozen)
        op.execute("CREATE FUNCTION grn_print_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ "
                   f"BEGIN IF OLD.grn_id IS NOT NULL AND ({changed}) THEN "
                   "RAISE EXCEPTION 'GRN print snapshot is immutable'; END IF; RETURN NEW; END; $$")
        op.execute("CREATE TRIGGER grn_print_immutable BEFORE UPDATE ON warehouse_label_jobs "
                   "FOR EACH ROW EXECUTE FUNCTION grn_print_immutable()")
    else:
        changed = " OR ".join(f"OLD.{name} IS NOT NEW.{name}" for name in frozen)
        op.execute("CREATE TRIGGER grn_print_immutable BEFORE UPDATE ON warehouse_label_jobs "
                   f"WHEN OLD.grn_id IS NOT NULL AND ({changed}) "
                   "BEGIN SELECT RAISE(ABORT, 'GRN print snapshot is immutable'); END")


def upgrade():
    connection = op.get_bind()
    identities, maxima = _legacy(connection)
    registry = _registry()
    _sources()
    _templates()
    for identity, references in identities:
        connection.execute(registry.insert().values(**identity))
        for table, row_id in references:
            source = sa.table(table, sa.column("id", sa.Uuid()), sa.column("grn_id", sa.Uuid()),
                              sa.column("grn_category", sa.String()))
            connection.execute(source.update().where(source.c.id == UUID(row_id)).values(
                grn_id=identity["id"], grn_category=identity["category"]))
    counters = sa.table("warehouse_grn_counters", sa.column("category", sa.String()),
                        sa.column("full_year", sa.Integer()), sa.column("last_serial", sa.Integer()))
    for (category, year), maximum in maxima.items():
        connection.execute(counters.insert().values(category=category, full_year=year, last_serial=maximum))
    _immutable()


def downgrade():
    connection = op.get_bind()
    guards = ["SELECT 1 FROM warehouse_material_owners",
              "SELECT 1 FROM receiving_lines WHERE grn IS NOT NULL GROUP BY grn, package HAVING count(*) > 1",
              "SELECT 1 FROM warehouse_grn_registry WHERE NOT is_legacy",
              "SELECT 1 FROM warehouse_label_templates WHERE category <> '' OR form_id IS NOT NULL",
              "SELECT 1 FROM warehouse_label_jobs WHERE grn_id IS NOT NULL OR grn_category IS NOT NULL "
              "OR grn_provenance IS NOT NULL OR template_version IS NOT NULL OR form_id IS NOT NULL"]
    guards += [f"SELECT 1 FROM {table} WHERE grn_contract_version IS NOT NULL OR quality_state IS NOT NULL" for table in SOURCES]
    if any(connection.execute(sa.text(sql + " LIMIT 1")).first() for sql in guards):
        raise RuntimeError("0218 downgrade refused after GRN cutover; use a separately approved forward recovery")
    if connection.dialect.name == "postgresql":
        op.execute("DROP TRIGGER grn_print_immutable ON warehouse_label_jobs")
        op.execute("DROP FUNCTION grn_print_immutable()")
    else:
        op.execute("DROP TRIGGER grn_print_immutable")
    with op.batch_alter_table("warehouse_label_jobs") as batch:
        for name in ("fk_label_jobs_grn", "fk_label_jobs_grn_identity"):
            batch.drop_constraint(name, type_="foreignkey")
        for name in ("ck_label_jobs_grn_category", "ck_label_jobs_grn_snapshot"):
            batch.drop_constraint(name, type_="check")
        for name in ("grn_id", "grn_category", "grn_number", "grn_provenance", "template_kind", "template_format",
                     "template_version", "form_id", "form_revision", "form_effective_date"):
            batch.drop_column(name)
    with op.batch_alter_table("warehouse_label_templates") as batch:
        for name in ("ck_label_templates_category", "ck_label_templates_instrument_category", "ck_label_templates_form"):
            batch.drop_constraint(name, type_="check")
        batch.drop_constraint("uq_label_templates_kind_category_format", type_="unique")
        for name in ("category", "form_id", "form_revision", "form_effective_date"):
            batch.drop_column(name)
        batch.create_unique_constraint("uq_warehouse_label_templates_kind_format", ["kind", "format"])
    for table in reversed(SOURCES):
        with op.batch_alter_table(table) as batch:
            if table == "warehouse_lots":
                batch.drop_constraint("ck_warehouse_lots_csm_project", type_="check")
                batch.drop_index("ix_warehouse_lots_csm_project_id")
                batch.drop_column("csm_project_id")
            if table == "receiving_lines":
                batch.drop_index("uq_receiving_grn_package_active")
                batch.create_unique_constraint("uq_receiving_grn_package", ["grn", "package"])
            batch.drop_index(f"ix_{table}_grn_id")
            for suffix in ("grn", "grn_identity", "grn_owner", "material_owner"):
                batch.drop_constraint(f"fk_{table}_{suffix}", type_="foreignkey")
            for suffix in _source_checks(table):
                batch.drop_constraint(f"ck_{table}_grn_{suffix}", type_="check")
            for column in _source_columns():
                batch.drop_column(column.name)
    op.drop_table("warehouse_grn_registry")
    if connection.dialect.name == "postgresql":
        op.execute("DROP FUNCTION grn_registry_immutable()")
    op.drop_table("warehouse_grn_counters")
    op.drop_table("warehouse_material_owners")

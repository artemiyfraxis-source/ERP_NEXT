"""Fill every table of a migrated database with a few rows, at whatever revision it stands (test/CI helper only).

    cd backend; ERP_DATABASE_URL=… python -m tests.migrations.seed_any_revision [--rows 6]

The migrations job of CI seeds the database at head-1 with this, then runs the last migration over real rows. The
demo seeder and the API can't do it: their code is at head, the schema one revision behind. So the schema is
reflected and filled column by column: foreign keys take rows of their parent, CHECK `col IN (…)` takes its first
value, and the stock tables get consistent quantities (ПРИХОД > СПИСАНО + РЕЗЕРВ ≥ what kitting, techcards and
«Со склада» hold on the lot), so the integrity snapshot of a seeded database is clean.
Never point it at a real database: it only inserts, but the rows are made up.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Column, MetaData, Table, create_engine, inspect, null, select
from sqlalchemy import types as sqltypes
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.schema import sort_tables

ROWS = 6
# `status IN ('open', …)` on SQLite, `((status)::text = ANY ((ARRAY['open'::character varying, …` on PostgreSQL: the
# first word is the column, the first quoted literal an allowed value.
_CHECK_IN = re.compile(r"(\w+).*?'([^']*)'", re.DOTALL)
_IN_LIST = re.compile(r"\bIN\s*\(|=\s*ANY\s*\(", re.IGNORECASE)
# A one-value `source IN ('kaiten')` (0137) PostgreSQL keeps as `((source)::text = 'kaiten'::text)` — no IN, no ANY.
_SINGLE_EQ = re.compile(r"^[\s(]*(\w+)\)?(?:::[\w ]+?)?\s*=\s*'[^']*'(?:::[\w ]+)?[\s)]*$")
_CHECK_NULL_OR = re.compile(r"\s*\(*\s*(\w+)\s+IS\s+NULL\s*\)?\s+OR\b", re.IGNORECASE)
_CHECK_NULL = re.compile(r"^[\s(]*(\w+)\s+IS\s+NULL[\s)]*$", re.IGNORECASE)
# `(tab = 'prov' AND cable_kind IN (…)) OR (tab <> 'prov' AND cable_kind IS NULL)` (0131): the column stays empty on a
# row of any other value of the first column (the seeder never picks the one value that needs it).
_CHECK_NULL_BRANCH = re.compile(r"\bOR\b.*?<>.*?\bAND\s*\(?\s*(\w+)\s+IS\s+NULL", re.IGNORECASE | re.DOTALL)
_WAREHOUSES = ("ЦАР", "ЖУК")
# Stock quantities per lot row, and what each holder takes of one lot: 3 holders × 1 ≤ РЕЗЕРВ 20 ≤ balance.
_LOT_QUANTITIES = {
    "inbound": lambda i: Decimal(100 + i), "written_off": lambda i: Decimal(10), "reserved": lambda i: Decimal(20),
}
_HOLDER_QUANTITY = {
    ("fulfillment_lines", "reserved"),
    ("fulfillment_kit_reserves", "quantity"),
    ("procurement_stock_covers", "quantity"),
}


def seed(engine: Engine, rows: int = ROWS) -> dict[str, int]:
    """Insert `rows` rows into every empty table; returns rows inserted per table."""
    metadata = MetaData()
    metadata.reflect(bind=engine)
    inserted: dict[str, int] = {}
    with engine.begin() as connection:
        checks = _check_values(connection, metadata)
        inventory_era = _inventory_era(metadata)
        catalogs = {"equipment_units", "equipment_machines", "equipment_measuring_units", "equipment_support_units",
                    "equipment_applicators", "equipment_dies", "equipment_tools", "equipment_instrument_bindings"}
        units = metadata.tables.get("equipment_units")
        links = metadata.tables.get("equipment_unit_links")
        cutover = units is not None and links is not None and any(
            fk.referred_table.name == "equipment_units" for fk in links.foreign_key_constraints)
        for table in _fill_order(metadata):
            if table.name == "alembic_version" or _has_rows(connection, table):
                continue
            if inventory_era and table.name in _INVENTORY_ERA_TABLES:
                continue
            if table.name in catalogs:
                if table.name == "equipment_units" and cutover:
                    _seed_catalog_units(connection, metadata, rows, inserted)
                continue
            parents = _parent_values(connection, table)
            lots = _lot_ids(connection, metadata) if "lot_id" in table.c and not table.c.lot_id.foreign_keys else []
            count = 1 if table.name in _SINGLE_ROW_TABLES else min(rows, _ROW_CAPS.get(table.name, rows))
            if not count:
                continue
            values = [_row(table, index, parents, lots, checks, engine.dialect.name) for index in range(count)]
            if table.name == "tech_operation_records" and units is not None and not cutover:
                # At additive 0177 all equipment still lives here; the cutover must have valid source units.
                for index, value in enumerate(values):
                    value.update(tab="ob", equipment_model_id=parents["equipment_model_id"][index % len(parents["equipment_model_id"] )],
                                 fields={"Тип": "Оборудование", "Наименование": "Тестовый станок", "Инв. номер": f"ОБ.{index + 1:03}"})
            connection.execute(table.insert(), values)
            inserted[table.name] = len(values)
    return inserted


def _seed_catalog_units(connection, metadata, rows, inserted):
    """Seed a valid reciprocal identity/payload pair; instrument bindings require explicit review."""
    models = list(connection.scalars(select(metadata.tables["equipment_models"].c.id)))
    units, machines = metadata.tables["equipment_units"], metadata.tables["equipment_machines"]
    for index in range(rows):
        identifier = uuid.uuid4() if connection.dialect.name == "postgresql" else uuid.uuid4().hex
        model = models[index % len(models)]
        connection.execute(units.insert().values(id=identifier, model_id=model, catalog="ob", ob_id=identifier))
        connection.execute(machines.insert().values(id=identifier, equipment_model_id=model, tab="ob",
            display_text="Тестовый станок", normalized_search="тестовый станок", source_sheet="", sort_key=str(index),
            fields={"Тип": "Оборудование", "Наименование": "Тестовый станок", "Инв. номер": f"ОБ.{index + 1:03}"}))
    inserted[units.name] = inserted[machines.name] = rows


# 0170–0171 named a setup machine by its inventory number; 0172 converts only a number of exactly one БД.ОБ unit and
# stops otherwise. The seeder makes no БД.ОБ units, so at those revisions the machine-keyed setup tables stay empty.
_INVENTORY_ERA_TABLES = frozenset({"equipment_setup_recipe_versions", "equipment_setup_tasks",
                                   "equipment_setup_trial_measurements", "equipment_setup_transitions"})


def _inventory_era(metadata: MetaData) -> bool:
    tasks = metadata.tables.get("equipment_setup_tasks")
    if tasks is None or "machine_id" not in tasks.c:
        return False
    kind = tasks.c.machine_id.type
    # A Uuid column reflects as UUID on PostgreSQL and CHAR(32) on SQLite; 0170 made it VARCHAR(128).
    return isinstance(kind, sqltypes.String) and getattr(kind, "length", None) != 32


def _fill_order(metadata: MetaData) -> list[Table]:
    """Parents before children; the lots also before the tables that hold reserves on them by a plain `lot_id`."""
    lots = metadata.tables.get("warehouse_lots")
    extra = [
        (lots, table) for table in metadata.tables.values()
        if lots is not None and table is not lots and "lot_id" in table.c and not table.c.lot_id.foreign_keys
    ]
    # 0217 ties a measuring unit and its instrument binding both ways (deferred FKs); the seeder fills neither, so the
    # reciprocal key is left out of the order instead of making SQLAlchemy drop the whole cycle with a warning.
    return sort_tables(metadata.tables.values(), extra_dependencies=extra, skip_fn=lambda fk:
        (fk.parent.table.name == "equipment_units" and fk.column.table.name != "equipment_models")
        or (fk.parent.table.name == "equipment_measuring_units"
            and fk.column.table.name == "equipment_instrument_bindings"))


def _has_rows(connection: Connection, table: Table) -> bool:
    return connection.execute(select(1).select_from(table).limit(1)).first() is not None


def _check_values(connection: Connection, metadata: MetaData) -> dict[tuple[str, str], str | None]:
    """CHECK `status IN ('open', …)` → ('table', 'status'): 'open'; CHECK `col IS NULL OR (…)` on a nullable column →
    None (the column is left empty, e.g. «restored from» of a plain save)."""
    inspector = inspect(connection)
    found: dict[tuple[str, str], str | None] = {}
    for table in metadata.tables.values():
        for check in inspector.get_check_constraints(table.name):
            text = str(check.get("sqltext", ""))
            empty = _CHECK_NULL.match(text) or _CHECK_NULL_OR.match(text) or _CHECK_NULL_BRANCH.search(text)
            if empty and empty.group(1) in table.c and table.c[empty.group(1)].nullable:
                found[(table.name, empty.group(1))] = None
                continue
            match = _CHECK_IN.search(text)
            if match and match.group(1) in table.c and (_IN_LIST.search(text) or _SINGLE_EQ.match(text)):
                found[(table.name, match.group(1))] = match.group(2)
    return found


def _parent_values(connection: Connection, table: Table) -> dict[str, list[Any]]:
    """column → the parent values a row may point at (each foreign key, composite ones as aligned columns)."""
    values: dict[str, list[Any]] = {}
    # A column shared by a single and a composite key (0199: configurations.unit_id → units and, with reference_id, →
    # references) takes the composite key's aligned values — set order must not decide which parent wins.
    for key in sorted(table.foreign_key_constraints, key=lambda key: (len(key.elements), key.name or "")):
        parent = key.referred_table
        if parent is table:
            continue  # self reference (a correction of an earlier row): left NULL
        referred = [element.column for element in key.elements]
        rows = connection.execute(select(*referred).order_by(*referred)).all()
        for position, element in enumerate(key.elements):
            values[element.parent.name] = [row[position] for row in rows]
    return values


def _lot_ids(connection: Connection, metadata: MetaData) -> list[Any]:
    lots = metadata.tables.get("warehouse_lots")
    if lots is None:
        return []
    return list(connection.execute(select(lots.c.id).order_by(lots.c.id)).scalars())


def _row(table: Table, index: int, parents: dict[str, list[Any]], lots: list[Any],
         checks: dict[tuple[str, str], str | None], dialect: str) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for column in table.columns:
        if _database_assigns(table, column, dialect):
            continue
        name = column.name
        if (table.name, name) in {("warehouse_scan_jobs", "capture_settings"), ("production_vswr_measurements", "repeat_set")}:
            # Reflection forgets JSON(none_as_null=True); omission keeps SQL NULL (a repeat set needs its repeat, 0200).
            continue
        if (table.name, name) in _OVER_CHECK:
            row[name] = _OVER_CHECK[(table.name, name)]
            continue
        if checks.get((table.name, name), "") is None:  # `col IS NULL OR …` wins over a parent to point at
            row[name] = None
            continue
        if name in parents:
            choices = parents[name]
            if not choices:
                if not column.nullable:
                    raise RuntimeError(f"{table.name}.{name}: the parent table has no rows to point at")
                row[name] = None
            else:
                row[name] = choices[index % len(choices)]
            continue
        if column.foreign_keys:  # self reference
            row[name] = None
            continue
        if name == "lot_id" and lots:
            row[name] = lots[index % len(lots)]
            continue
        if (table.name, name) in checks and (table.name, name) not in _SHAPED_VALUES:
            row[name] = checks[(table.name, name)]
            continue
        row[name] = _value(table.name, column, index)
    return row


def _database_assigns(table: Table, column: Column, dialect: str) -> bool:
    """An integer surrogate key the database numbers itself (SERIAL / IDENTITY / the SQLite rowid)."""
    if not column.primary_key or not isinstance(column.type, sqltypes.Integer):
        return False
    if column.identity is not None or column.server_default is not None:
        return True
    return dialect == "sqlite" and len(table.primary_key.columns) == 1


# Pairs a CHECK ties together that the column-by-column fill can't see: identity_documents.scope takes 'all' from its
# `IN (…)` list, and `scope = 'all'` demands an empty scope_value (0100_identity_documents); fulfillment_lines.absent_at
# is left empty (`absent_at IS NULL OR …`), and «who» goes with «when» (0106_fulfillment_line_absent).
# finished_goods.before_erp is 0: the acts of a КБ (0128) sum to its «принято» moves less what was shipped before ERP,
# and row i of the moves and of the acts both take the i-th КБ and i + 1 pieces, so the sums meet only without it.
_COUPLED_VALUES: dict[tuple[str, str], Any] = {
    ("identity_documents", "scope_value"): "",
    ("fulfillment_lines", "absent_by"): None,
    ("finished_goods", "before_erp"): Decimal(0),
    # Σ партий ГРН строки отправки = её набранному (0173): column-by-column fill can't line a lot up with its line, so
    # seeded lines pick nothing and `shipment_line_lots` stays empty (_ROW_CAPS).
    ("shipment_lines", "picked"): Decimal(0),
    # A VSWR verdict without a norm (the seeder leaves norm_vswr_max empty) is a manual one (0142_production_vswr).
    ("production_vswr_measurements", "verdict_manual"): True,
    # A scan job's recognition answer exists only once it is done; the seeder's job is pending (0145_warehouse_scan_jobs).
    ("warehouse_scan_jobs", "result"): "",
    ("warehouse_scan_jobs", "kind"): "scan",
    ("warehouse_scan_jobs", "vna_name"): "",
    ("warehouse_scan_jobs", "capture_file_name"): "",
    # «Who» goes with «when» (0162_pack_decision_no_upd): a seeded card is not decided, a shipment has no no-УПД permit.
    ("shipping_project_addressees", "pack_decided_by"): "",
    ("shipping_project_addressees", "pack_decided_at"): None,
    ("shipments", "no_upd_approved_by"): "",
    ("shipments", "no_upd_approved_at"): None,
    # A разрешительный ПИН's hash goes with its date (0163_approval_pin): a seeded credential has none.
    ("identity_credentials", "approval_pin_hash"): None,
    ("identity_credentials", "approval_pin_set_at"): None,
    # A plain journal event has no piece counts (0166_kb_progress).
    **{("production_plan_events", f"progress_{name}"): None
       for name in ("run", "started", "done", "qc", "passed")},
}
# Values the column-by-column fill would leave empty by a CHECK's «other value → NULL» branch, although the row takes
# the one value that needs them: a seeded tech record is `tab = 'ter'` (the first literal of the БД.ТЕР card CHECKs,
# 0115), and a БД.ТЕР row is always in a list (0155_ter_lug_kind).
_OVER_CHECK: dict[tuple[str, str], Any] = {
    # 0190: the generic sample job is TSPL; marking evidence belongs only to gdi_png jobs.
    ("warehouse_label_jobs", "format"): "tspl",
    **{("warehouse_label_jobs", name): None for name in (
        "png", "target_machine_id", "target_printer", "width_mm", "height_mm", "orientation",
        "batch_id", "sequence", "attempt", "claim_token_id", "receipt",
    )},
    # A stock journal row has at most one target. Leave the newer alternatives empty;
    # record_id is filled from its parent (or NULL where the historical CHECK requires it).
    **{("reference_stock_links", name): None for name in ("equipment_unit_id", "heat_shrink_id", "covering_id")},
    ("tech_operation_records", "ter_kind"): "contact",
    # The last state seen and its time are set together (0211); the CHECK leaves the state empty, so the time too.
    ("production_machine_snapshots", "last_state_at"): None,
    ("warehouse_scan_jobs", "instrument_id"): None,
    # Before 0167 arbitrary statuses were allowed; ACTIVE now needs all evidence despite nullable-date CHECKs.
    ("production_instruments", "status"): "Действует",
    ("production_instruments", "kind"): "Средство измерений",
    ("production_instruments", "quantity"): 1,
    ("production_instruments", "verification_number"): "TEST-PV",
    ("production_instruments", "verified_at"): datetime(2026, 9, 1, tzinfo=timezone.utc),
    ("production_instruments", "verification_due"): datetime(2027, 9, 1, tzinfo=timezone.utc),
    # 0217: the single СИ registry's custom fields are a JSON object (CHECK json_type = 'object'), not the list the
    # generic fill gives a plural-named JSON column.
    ("production_instruments", "custom_fields"): {},
    # 0178_rf_interfaces: an unconfirmed «unknown» row needs no kit, points or source; interfaces stay unconfirmed.
    ("production_rf_details", "role"): "unknown",
    ("production_rf_details", "confirmed"): False,
    ("production_rf_details", "kit_definition"): "",
    ("production_rf_details", "max_points"): None,
    ("production_rf_details", "f_min_ghz"): None,
    ("production_rf_details", "norm_id"): "",  # 0193: only adapters/cables carry an assigned norm
    ("production_rf_interfaces", "position"): 1,
    ("production_rf_interfaces", "f_confirmed"): False,
    # 0181_vna_sessions: terminal rows hold no active instrument/command lease, even when parents repeat.
    # Generic counters already start at 1, versions at 1, and binary content is nonempty and below the size cap.
    ("production_vna_sessions", "state"): "recorded",
    ("production_vna_commands", "status"): "done",
    ("production_vna_commands", "claimed_at"): None,
    # 0205_vna_bench_sessions: a seeded session is the ОТК kind; only a bench session holds a calibration.
    ("production_vna_sessions", "purpose"): "assembly",
    **{("production_vna_sessions", name): None for name in ("calibrated_at", "profile_hash", "calibration_check_id")},
    ("production_vna_sessions", "calibration_temps"): null(),   # SQL NULL, not the JSON value null
    # 0182_vswr_traces: preserve the legacy manual verdict without claiming a multi-trace evaluation.
    ("production_vswr_measurements", "complete"): None,
    ("production_vswr_measurements", "evidence_key"): None,
    ("production_vswr_measurements", "port_mapping"): None,
    ("production_vswr_measurements", "required_ends"): None,
    ("production_vswr_measurements", "end_results"): None,
    ("production_vswr_traces", "name"): "S11",
    ("production_vswr_traces", "quantity"): "vswr",
    ("production_vswr_traces", "points"): 2,
    # Display frequencies are MHz; full-resolution scalar/complex evidence uses Hz (reflection coefficient 0).
    ("production_vswr_traces", "trace"): [[1000.0, 1.0], [2000.0, 1.0]],
    ("production_vswr_traces", "samples"): [[1e9, 1.0], [2e9, 1.0]],
    ("production_vswr_traces", "complex_samples"): [[1e9, 0.0, 0.0], [2e9, 0.0, 0.0]],
    # 0219_arinst_control: a seeded capability is an unverified, not-ready Planar row (`source IN (…)` of the readiness
    # CHECK would otherwise give 'arinst'); an identity association is an Arinst UID (serial = 'arinst:' || UID).
    ("warehouse_vna_control", "source"): "s2vna",
    ("warehouse_vna_control", "hardware_uid"): "",
    ("warehouse_vna_identities", "source"): "arinst",
}
# Values a CHECK shapes beyond `IN (…)`: an internal project number is «ВП-…» (0135_internal_projects), unique; the
# label printer settings are bounded ranges and a numeric `dpi IN (203, 300)` (0138_warehouse_labels).
_LABEL_TEMPLATE_KINDS = ("lot", "intake")
_LABEL_FORMATS = ("58x30", "58x40", "100x50")
_LABEL_SETTINGS = {"width_mm": 58, "height_mm": 30, "dpi": 300, "gap_mm": 2, "density": 12, "speed": 2,
                   "offset_x_mm": 0, "offset_y_mm": 0}
_SHAPED_VALUES: dict[tuple[str, str], Any] = {
    # 0218: generic registry rows are legacy ones (ck_grn_new_identity); source_kind must be exactly 'legacy'.
    ("warehouse_grn_registry", "is_legacy"): lambda i: True,
    ("warehouse_grn_registry", "source_kind"): lambda i: "legacy",
    ("warehouse_grn_counters", "full_year"): lambda i: 2000 + i,  # 0218: CHECK full_year BETWEEN 2000 AND 9999.
    ("receiving_lines", "revision_root_id"): lambda i: None,  # No fabricated UUID lineage without a revision.
    # 0218 rejects the same raw GRN on unrelated receipts. Generic "grn-0" in each
    # table used to fabricate exactly that conflict; give independent fixtures distinct identities.
    **{(table, "grn"): (lambda prefix: lambda i: f"{prefix}-{i}")(prefix) for table, prefix in (
        ("warehouse_lots", "legacy-stock"), ("receiving_lines", "legacy-receiving"),
        ("finished_goods", "legacy-good"), ("finished_goods_acts", "legacy-act"),
    )},
    # Exact resource reservations have positive windows; setup transitions follow version 1 (0170).
    ("production_resource_reservations", "finish"): lambda i: datetime(2026, 9, 1, 10, tzinfo=timezone.utc) + timedelta(days=i),
    ("equipment_setup_transitions", "version"): lambda i: i + 2,
    # A seeded claim holds a person: a machine is a БД.ОБ unit (0172), which the seeder doesn't make.
    **{(table, column): value for table in ("production_resource_reservations", "production_active_resources")
       for column, value in (("resource_kind", lambda i: "person"), ("resource_key", lambda i: str(uuid.uuid4())))},
    # A full source snapshot must contain a nonempty array (0168); never real source data.
    ("requests_calc_compositions", "rows"): lambda i: [{
        "source_row": 2, "name": f"Synthetic component {i}", "item_type": "Провод", "unit": "м",
        "unit_source": "derived_known", "quantity": "1", "price": None, "price_with_vat": None,
        "link": "", "country": "", "raw": {"unit": ""}, "issues": [],
        "request_id": None, "request_link_state": "unlinked",
    }],
    ("purchasing_internal_projects", "number"): lambda i: f"ВП-{i + 1:03d}",
    **{("warehouse_label_settings", name): (lambda value: lambda i: value)(value) for name, value in _LABEL_SETTINGS.items()},
    # A VSWR band is from < to and its peak lies inside (0142_production_vswr_measurements).
    ("production_vswr_measurements", "band_from_mhz"): lambda i: Decimal(1),
    ("production_vswr_measurements", "band_to_mhz"): lambda i: Decimal(8800),
    ("production_vswr_measurements", "max_vswr_mhz"): lambda i: Decimal(1000),
    # One saved label template per kind × format, UNIQUE (0143_warehouse_label_templates): row i takes its own pair.
    ("warehouse_label_templates", "kind"): lambda i: _LABEL_TEMPLATE_KINDS[i // len(_LABEL_FORMATS)],
    ("warehouse_label_templates", "format"): lambda i: _LABEL_FORMATS[i % len(_LABEL_FORMATS)],
    # The model API address is '' or http(s):// (0151_upd_llm_recognition); the key is made up.
    ("warehouse_upd_llm_settings", "base_url"): lambda i: "https://llm.example.test/v1",
    # A machine snapshot speaks bridge protocol 1 only (0193_machines).
    ("production_machine_snapshots", "proto"): lambda i: 1,
    # One association per Arinst UID, its serial derived from it (0219_arinst_control, both UNIQUE).
    ("warehouse_vna_identities", "hardware_uid"): lambda i: f"{i + 1}-0-0",
    ("warehouse_vna_identities", "serial"): lambda i: f"arinst:{i + 1}-0-0",
}
# Shaped values win over a CHECK's first `IN (…)` value; a table with a UNIQUE of shaped pairs gets no more rows than
# there are pairs.
_ROW_CAPS = {"warehouse_label_templates": len(_LABEL_TEMPLATE_KINDS) * len(_LABEL_FORMATS), "shipment_line_lots": 0,
              # A per-side chain (seq, running total) the generic filler cannot build; empty is a valid ledger.
              "production_instrument_matings": 0, "production_mating_batches": 0,
              # Each verdict is tied to its numbers by kind-specific CHECKs (0195_vna_check_journal).
              "production_vna_checks": 0,
              # A command's times follow its status and a machine command needs its command (0196 / 0197): the generic
              # filler sets every time and no command; empty is a valid queue.
              "production_machine_writes": 0,
              # A manual job's fields follow its status and count source (0202_manual_jobs); an issue needs its job,
              # lot and write-off. Empty is a valid journal.
              "production_manual_jobs": 0, "production_manual_job_issues": 0,
              # A bench measurement's kind, target, standard, object and record are tied by CHECKs (0205); empty is valid.
              "production_vna_bench_items": 0,
              # A print job's times, attempt and lease follow its status (0214_warehouse_document_devices); empty is valid.
              "warehouse_document_print_jobs": 0}
# One row per installation (CHECK `id = 1`): seeded with a single row (the label print agent's heartbeat, 0141, too).
_SINGLE_ROW_TABLES = frozenset({"warehouse_label_settings", "warehouse_label_agent", "warehouse_upd_llm_settings"})


def _value(table: str, column: Column, index: int) -> Any:
    name, kind = column.name, column.type
    if (table, name) in _COUPLED_VALUES:
        return _COUPLED_VALUES[(table, name)]
    if (table, name) in _SHAPED_VALUES:
        return _SHAPED_VALUES[(table, name)](index)
    if table == "warehouse_lots" and name in _LOT_QUANTITIES:
        return _LOT_QUANTITIES[name](index)
    if (table, name) in _HOLDER_QUANTITY:
        return Decimal(1)
    if isinstance(kind, sqltypes.Boolean):
        return False
    if isinstance(kind, sqltypes.Uuid):
        return uuid.uuid4() if kind.as_uuid else uuid.uuid4().hex
    if isinstance(kind, sqltypes.DateTime):
        return datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc) + timedelta(days=index)
    if isinstance(kind, sqltypes.Date):
        return date(2026, 9, 1) + timedelta(days=index)
    if isinstance(kind, sqltypes.Time):
        return time(9, index % 60)
    if isinstance(kind, sqltypes.Interval):
        return timedelta(minutes=index + 1)
    if isinstance(kind, sqltypes.JSON):
        return [] if name.endswith(("ids", "s")) else {}
    if isinstance(kind, sqltypes.Enum) and kind.enums:
        return kind.enums[0]
    if isinstance(kind, sqltypes.Integer):
        return 1 if name == "version" else index + 1
    if isinstance(kind, sqltypes.Float):
        return float(index + 1)
    if isinstance(kind, sqltypes.Numeric):
        return Decimal(index + 1)
    if isinstance(kind, sqltypes.LargeBinary):
        return f"bytes-{index}".encode()
    if isinstance(kind, sqltypes.String):
        length = getattr(kind, "length", None)
        if name == "warehouse" and (length is None or length >= 3):
            return _WAREHOUSES[index % len(_WAREHOUSES)]
        if length == 32 and (name == "id" or name.endswith("_id")):
            return uuid.uuid4().hex  # a Uuid column as SQLite reflects it (CHAR(32))
        if length == 64 and name.endswith("sha256"):
            return hashlib.sha256(f"{table}.{name}-{index}".encode()).hexdigest()  # CHECK length(…) = 64
        return _fit(f"{name}-{index}", f"-{index}", length)
    raise RuntimeError(f"{table}.{name}: no seed value for type {kind!r}")


def _fit(value: str, suffix: str, length: int | None) -> str:
    if length is None or len(value) <= length:
        return value
    return (value[: max(length - len(suffix), 0)] + suffix)[-length:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=int, default=ROWS)
    parser.add_argument("--database-url", default=os.getenv("ERP_DATABASE_URL", ""))
    args = parser.parse_args(argv)
    if not args.database_url:
        print("Set ERP_DATABASE_URL", file=sys.stderr)
        return 2
    engine = create_engine(args.database_url)
    try:
        inserted = seed(engine, args.rows)
    finally:
        engine.dispose()
    print(f"seeded {len(inserted)} tables, {sum(inserted.values())} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

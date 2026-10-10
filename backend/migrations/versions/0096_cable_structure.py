"""Структурная база кабелей (владелец, 10.10.2026: «наведи порядок в базах коаксиалов, монтажных проводов и сечений»).

New tables only; nothing existing changes (БД.ПРОВ / СПР.КАБ rows, `gost_awg`, `cable_kind`, D1–D3 edited in ERP keep
their data and keep being read by the ETK generator, the harness catalogue and the warehouse):
- `wire_gauges` — «Сечения AWG ↔ мм²»: AWG (or none for a ГОСТ-only size), section, conductor Ø, strands. Seeded with
  the SUPPLYMAP СПР.КАБ AWG table plus 0,07 / 0,14 мм² (МГТФ, НВ-4). `gost_awg` gets the whole-AWG rows it lacks
  (an existing entry is left as it is).
- `hookup_wires` — «Монтажные провода»: one row per БД.ПРОВ wire (by its mark), conductor = FK to `wire_gauges`
  (RESTRICT), series, insulation, outer Ø, voltage, temperature. Filled from the БД.ПРОВ rows that are in the
  монтажные list now (rule + manual moves) and whose size the table knows; the rest stay unstructured on the screen.
- `coax_cables` + `coax_cable_layers` / `_connectors` / `_markers` / `_attenuation` — «Коаксиальные кабели» cards.
  Empty here: `python -m app.modules.references.load_cables` loads `backend/data/cables/coax-cables.json` (idempotent,
  run by `deploy/update.sh`).

Downgrade drops the new tables with their rows; `gost_awg` entries added here stay (they are the same map values).
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0096_cable_structure"
down_revision: str | None = "0095_identity_leave_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_tables() -> None:
    op.create_table(
        "wire_gauges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("awg", sa.String(8), nullable=True),
        sa.Column("section_mm2", sa.Numeric(10, 4), nullable=False),
        sa.Column("conductor_d_mm", sa.Numeric(8, 3), nullable=True),
        sa.Column("strands", sa.String(64), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("awg", name="ux_wire_gauges_awg"),
        sa.UniqueConstraint("section_mm2", name="ux_wire_gauges_section"),
        sa.CheckConstraint("section_mm2 > 0", name="ck_wire_gauges_section_positive"),
        sa.CheckConstraint("conductor_d_mm IS NULL OR conductor_d_mm > 0", name="ck_wire_gauges_conductor_positive"),
        sa.CheckConstraint("awg IS NULL OR awg <> ''", name="ck_wire_gauges_awg_not_blank"),
    )
    op.create_table(
        "hookup_wires",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mark", sa.String(512), nullable=False),
        sa.Column("mark_key", sa.String(512), nullable=False),
        sa.Column("series", sa.String(255), nullable=False),
        sa.Column("gauge_id", sa.Uuid(), nullable=False),
        sa.Column("insulation", sa.String(512), nullable=False, server_default=""),
        sa.Column("outer_d_mm", sa.Numeric(8, 3), nullable=True),
        sa.Column("voltage_v", sa.Integer(), nullable=True),
        sa.Column("temp_min_c", sa.Integer(), nullable=True),
        sa.Column("temp_max_c", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["gauge_id"], ["wire_gauges.id"], name="fk_hookup_wires_gauge_id", ondelete="RESTRICT"),
        sa.UniqueConstraint("mark_key", name="ux_hookup_wires_mark_key"),
        sa.CheckConstraint("series <> ''", name="ck_hookup_wires_series_not_blank"),
        sa.CheckConstraint("outer_d_mm IS NULL OR outer_d_mm > 0", name="ck_hookup_wires_outer_positive"),
        sa.CheckConstraint("voltage_v IS NULL OR voltage_v > 0", name="ck_hookup_wires_voltage_positive"),
        sa.CheckConstraint("temp_min_c IS NULL OR temp_max_c IS NULL OR temp_min_c < temp_max_c",
                           name="ck_hookup_wires_temp_range"),
    )
    op.create_index("ix_hookup_wires_gauge_id", "hookup_wires", ["gauge_id"])
    op.create_table(
        "coax_cables",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("name_key", sa.String(255), nullable=False),
        sa.Column("family", sa.String(255), nullable=False, server_default=""),
        sa.Column("span_from_mhz", sa.Numeric(12, 3), nullable=True),
        sa.Column("span_to_ghz", sa.Numeric(10, 3), nullable=True),
        sa.Column("span_note", sa.String(2000), nullable=False, server_default=""),
        sa.Column("fc_ghz", sa.Numeric(8, 3), nullable=True),
        sa.Column("fc_note", sa.String(255), nullable=False, server_default=""),
        sa.Column("max_operating_ghz", sa.Numeric(8, 3), nullable=True),
        sa.Column("max_operating_note", sa.String(500), nullable=False, server_default=""),
        sa.Column("impedance_ohm", sa.Numeric(8, 2), nullable=True),
        sa.Column("velocity_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("capacitance_pf_m", sa.Numeric(8, 2), nullable=True),
        sa.Column("max_power_w", sa.Numeric(10, 2), nullable=True),
        sa.Column("max_power_at_mhz", sa.Numeric(12, 3), nullable=True),
        sa.Column("shielding_db", sa.Numeric(8, 2), nullable=True),
        sa.Column("bend_static_mm", sa.Numeric(8, 2), nullable=True),
        sa.Column("bend_dynamic_mm", sa.Numeric(8, 2), nullable=True),
        sa.Column("temp_min_c", sa.Numeric(6, 1), nullable=True),
        sa.Column("temp_max_c", sa.Numeric(6, 1), nullable=True),
        sa.Column("voltage_max_v", sa.Numeric(10, 1), nullable=True),
        sa.Column("weight_g_m", sa.Numeric(10, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("param_notes", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.String(64), nullable=False, server_default=""),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name_key", name="ux_coax_cables_name_key"),
        sa.CheckConstraint("name <> ''", name="ck_coax_cables_name_not_blank"),
        sa.CheckConstraint("span_from_mhz IS NULL OR span_from_mhz > 0", name="ck_coax_cables_span_from_positive"),
        sa.CheckConstraint("span_to_ghz IS NULL OR span_to_ghz > 0", name="ck_coax_cables_span_to_positive"),
        sa.CheckConstraint("span_from_mhz IS NULL OR span_to_ghz IS NULL OR span_from_mhz < span_to_ghz * 1000",
                           name="ck_coax_cables_span_order"),
        sa.CheckConstraint("fc_ghz IS NULL OR fc_ghz > 0", name="ck_coax_cables_fc_positive"),
        sa.CheckConstraint("max_operating_ghz IS NULL OR max_operating_ghz > 0", name="ck_coax_cables_operating_positive"),
        sa.CheckConstraint("capacitance_pf_m IS NULL OR capacitance_pf_m > 0", name="ck_coax_cables_capacitance_positive"),
        sa.CheckConstraint("max_power_w IS NULL OR max_power_w > 0", name="ck_coax_cables_power_positive"),
        sa.CheckConstraint("max_power_at_mhz IS NULL OR max_power_at_mhz > 0", name="ck_coax_cables_power_freq_positive"),
        sa.CheckConstraint("impedance_ohm IS NULL OR impedance_ohm > 0", name="ck_coax_cables_impedance_positive"),
        sa.CheckConstraint("velocity_pct IS NULL OR (velocity_pct > 0 AND velocity_pct <= 100)",
                           name="ck_coax_cables_velocity_pct"),
        sa.CheckConstraint("shielding_db IS NULL OR shielding_db > 0", name="ck_coax_cables_shielding_positive"),
        sa.CheckConstraint("bend_static_mm IS NULL OR bend_static_mm > 0", name="ck_coax_cables_bend_static_positive"),
        sa.CheckConstraint("bend_dynamic_mm IS NULL OR bend_dynamic_mm > 0", name="ck_coax_cables_bend_dynamic_positive"),
        sa.CheckConstraint("temp_min_c IS NULL OR temp_max_c IS NULL OR temp_min_c < temp_max_c",
                           name="ck_coax_cables_temp_range"),
        sa.CheckConstraint("voltage_max_v IS NULL OR voltage_max_v > 0", name="ck_coax_cables_voltage_positive"),
        sa.CheckConstraint("weight_g_m IS NULL OR weight_g_m > 0", name="ck_coax_cables_weight_positive"),
    )

    def child(name: str, *columns, constraints=()):
        op.create_table(
            name,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("cable_id", sa.Uuid(), nullable=False),
            *columns,
            sa.ForeignKeyConstraint(["cable_id"], ["coax_cables.id"], name=f"fk_{name}_cable_id", ondelete="CASCADE"),
            *constraints,
        )

    child(
        "coax_cable_layers",
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("material", sa.String(512), nullable=False, server_default=""),
        sa.Column("diameter_mm", sa.Numeric(8, 3), nullable=True),
        constraints=(
            sa.UniqueConstraint("cable_id", "layer", name="ux_coax_cable_layers_cable_layer"),
            sa.CheckConstraint("layer IN ('core', 'dielectric', 'shield', 'jacket')", name="ck_coax_cable_layers_layer"),
            sa.CheckConstraint("diameter_mm IS NULL OR diameter_mm > 0", name="ck_coax_cable_layers_diameter_positive"),
        ),
    )
    child(
        "coax_cable_connectors",
        sa.Column("family", sa.String(128), nullable=False),
        sa.Column("max_ghz", sa.Numeric(8, 3), nullable=True),
        sa.Column("max_note", sa.String(255), nullable=False, server_default=""),
        sa.Column("span_to_ghz", sa.Numeric(8, 3), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        constraints=(
            sa.UniqueConstraint("cable_id", "family", name="ux_coax_cable_connectors_cable_family"),
            sa.CheckConstraint("family <> ''", name="ck_coax_cable_connectors_family_not_blank"),
            sa.CheckConstraint("max_ghz IS NULL OR max_ghz > 0", name="ck_coax_cable_connectors_max_positive"),
            sa.CheckConstraint("span_to_ghz IS NULL OR span_to_ghz > 0", name="ck_coax_cable_connectors_span_positive"),
        ),
    )
    child(
        "coax_cable_markers",
        sa.Column("freq_mhz", sa.Numeric(12, 3), nullable=True),
        sa.Column("vswr_max", sa.Numeric(6, 3), nullable=True),
        sa.Column("vswr_ref_assembly", sa.Numeric(6, 3), nullable=True),
        sa.Column("note", sa.String(1000), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        constraints=(
            sa.UniqueConstraint("cable_id", "freq_mhz", name="ux_coax_cable_markers_cable_freq"),
            sa.CheckConstraint("freq_mhz IS NULL OR freq_mhz > 0", name="ck_coax_cable_markers_freq_positive"),
            sa.CheckConstraint("vswr_max IS NULL OR vswr_max >= 1", name="ck_coax_cable_markers_vswr"),
            sa.CheckConstraint("vswr_ref_assembly IS NULL OR vswr_ref_assembly >= 1", name="ck_coax_cable_markers_vswr_ref"),
        ),
    )
    child(
        "coax_cable_attenuation",
        sa.Column("freq_mhz", sa.Numeric(12, 3), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("value", sa.Numeric(12, 4), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("ref_length_m", sa.Numeric(10, 4), nullable=False),
        sa.Column("temp_c", sa.Numeric(6, 1), nullable=True),
        sa.Column("loss_db_m", sa.Numeric(12, 4), nullable=False),
        sa.Column("derived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("interpolated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(1000), nullable=False, server_default=""),
        sa.Column("basis", sa.String(500), nullable=False, server_default=""),
        constraints=(
            sa.UniqueConstraint("cable_id", "freq_mhz", "kind", "source", name="ux_coax_cable_attenuation_point"),
            sa.CheckConstraint("freq_mhz > 0", name="ck_coax_cable_attenuation_freq_positive"),
            sa.CheckConstraint("kind IN ('nominal', 'typical', 'max')", name="ck_coax_cable_attenuation_kind"),
            sa.CheckConstraint("value > 0", name="ck_coax_cable_attenuation_value_positive"),
            sa.CheckConstraint("unit IN ('dB/m', 'dB/100m', 'dB/km', 'dB/100ft', 'dB/ft')", name="ck_coax_cable_attenuation_unit"),
            sa.CheckConstraint("ref_length_m > 0", name="ck_coax_cable_attenuation_length_positive"),
            sa.CheckConstraint("loss_db_m > 0", name="ck_coax_cable_attenuation_loss_positive"),
        ),
    )


# SUPPLYMAP СПР.КАБ AWG table (AWG · ГОСТ · Ø жилы) + ГОСТ-only 0,07 / 0,14; strands where every БД.ПРОВ wire of
# the AWG agrees (7×0,101 for 30 AWG, 7×0,127 for 28 AWG); the rest is the owner's to fill.
GAUGES = (
    ("36", "0.01", "0.13", ""), ("34", "0.02", "0.16", ""), ("32", "0.03", "0.20", ""),
    ("30", "0.05", "0.26", "7×0,101"), ("", "0.07", "", ""), ("28", "0.08", "0.32", "7×0,127"),
    ("26", "0.12", "0.41", ""), ("", "0.14", "", ""), ("24", "0.2", "0.51", ""), ("22", "0.35", "0.64", ""),
    ("20", "0.5", "0.81", ""), ("18", "0.75", "1.02", ""), ("17", "1", "1.15", ""), ("16", "1.5", "1.29", ""),
    ("14", "2.5", "1.63", ""), ("12", "4", "2.05", ""), ("10", "6", "2.59", ""), ("8", "10", "3.26", ""),
    ("6", "16", "4.12", ""), ("4", "25", "5.19", ""), ("2", "35", "6.54", ""), ("1/0", "50", "8.25", ""),
    ("2/0", "70", "9.27", ""), ("3/0", "95", "10.40", ""),
)

_gauges = sa.table(
    "wire_gauges", sa.column("id", sa.Uuid()), sa.column("awg", sa.String()), sa.column("section_mm2", sa.Numeric(10, 4)),
    sa.column("conductor_d_mm", sa.Numeric(8, 3)), sa.column("strands", sa.String()), sa.column("position", sa.Integer()),
)
_hookups = sa.table(
    "hookup_wires", sa.column("id", sa.Uuid()), sa.column("mark", sa.String()), sa.column("mark_key", sa.String()),
    sa.column("series", sa.String()), sa.column("gauge_id", sa.Uuid()), sa.column("insulation", sa.String()),
    sa.column("outer_d_mm", sa.Numeric(8, 3)), sa.column("voltage_v", sa.Integer()), sa.column("temp_min_c", sa.Integer()),
    sa.column("temp_max_c", sa.Integer()), sa.column("updated_at", sa.DateTime(timezone=True)),
)
_records = sa.table(
    "tech_operation_records", sa.column("tab", sa.String()), sa.column("display_text", sa.String()),
    sa.column("fields", sa.JSON()), sa.column("sort_key", sa.String()),
)
_entries = sa.table(
    "reference_entries", sa.column("id", sa.Uuid()), sa.column("kind", sa.String()), sa.column("key", sa.String()),
    sa.column("normalized_key", sa.String()), sa.column("value", sa.String()), sa.column("position", sa.Integer()),
)


def _seed_gauges(bind) -> list:
    from app.modules.references.cable_structure import WireGauge

    gauges = []
    for position, (awg, section, diameter, strands) in enumerate(GAUGES):
        gauge = WireGauge(uuid4(), awg or None, Decimal(section), Decimal(diameter) if diameter else None, strands, position)
        gauges.append(gauge)
    bind.execute(_gauges.insert(), [
        {"id": g.id, "awg": g.awg, "section_mm2": g.section_mm2, "conductor_d_mm": g.conductor_d_mm,
         "strands": g.strands, "position": g.position} for g in gauges
    ])
    # gost_awg: add the whole-AWG sections it lacks; an existing entry (an admin's AWG) stays.
    existing = {row.normalized_key for row in bind.execute(sa.select(_entries.c.normalized_key).where(_entries.c.kind == "gost_awg"))}
    position = bind.execute(sa.select(sa.func.max(_entries.c.position)).where(_entries.c.kind == "gost_awg")).scalar()
    position = -1 if position is None else position
    missing = []
    for gauge in gauges:
        key = format(gauge.section_mm2.normalize(), "f")
        if gauge.awg and gauge.awg.isdigit() and key not in existing:
            position += 1
            missing.append({"id": uuid4(), "kind": "gost_awg", "key": key.replace(".", ","), "normalized_key": key,
                            "value": gauge.awg, "position": position})
    if missing:
        bind.execute(_entries.insert(), missing)
    return gauges


def _structure_hookups(bind, gauges) -> None:
    """БД.ПРОВ rows in the монтажные list (the rule of `cables.auto_kind`, or a manual move) whose size the table
    knows → `hookup_wires`. Twins of one mark: the first row by sort key wins."""
    from app.modules.references.cable_structure import hookup_from_parse, parse_hookup
    from app.modules.references.cables import CableKind, auto_kind
    from app.modules.references.domain import cable_key

    def mark_of(fields, display):
        return str((fields or {}).get("Марка") or display or "").strip()

    overrides = {row.normalized_key: row.value for row in bind.execute(
        sa.select(_entries.c.normalized_key, _entries.c.value).where(_entries.c.kind == "cable_kind"))}
    dia_keys = set()
    for row in bind.execute(sa.select(_records.c.fields, _records.c.display_text).where(_records.c.tab == "kab_coax")):
        fields = row.fields or {}
        first = next(iter(fields.values()), "") if fields else ""
        key = cable_key(fields.get("Кабель", first) or row.display_text)
        if key:
            dia_keys.add(key)
    now = datetime.now(timezone.utc)
    seen: set[str] = set()
    rows = []
    for row in bind.execute(sa.select(_records.c.fields, _records.c.display_text)
                            .where(_records.c.tab == "prov").order_by(_records.c.sort_key)):
        fields = {str(k): str(v or "") for k, v in (row.fields or {}).items()}
        mark = mark_of(fields, row.display_text)
        key = cable_key(mark)
        if not key or key in seen:
            continue
        kind = overrides.get(key) or auto_kind(fields, mark, frozenset(dia_keys))[0].value
        if kind != CableKind.WIRE.value:
            continue
        wire = hookup_from_parse(mark, parse_hookup(mark, fields, gauges))
        if wire is None:
            continue
        seen.add(key)
        rows.append({"id": wire.id, "mark": wire.mark, "mark_key": wire.mark_key, "series": wire.series,
                     "gauge_id": wire.gauge_id, "insulation": wire.insulation, "outer_d_mm": wire.outer_d_mm,
                     "voltage_v": wire.voltage_v, "temp_min_c": wire.temp_min_c, "temp_max_c": wire.temp_max_c,
                     "updated_at": now})
    if rows:
        bind.execute(_hookups.insert(), rows)


def upgrade() -> None:
    _create_tables()
    bind = op.get_bind()
    gauges = _seed_gauges(bind)
    _structure_hookups(bind, gauges)


def downgrade() -> None:
    for table in ("coax_cable_attenuation", "coax_cable_markers", "coax_cable_connectors", "coax_cable_layers"):
        op.drop_table(table)
    op.drop_table("coax_cables")
    op.drop_index("ix_hookup_wires_gauge_id", table_name="hookup_wires")
    op.drop_table("hookup_wires")
    op.drop_table("wire_gauges")

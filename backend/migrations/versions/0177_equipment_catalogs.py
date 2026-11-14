"""Add six independently owned equipment catalogs; the legacy source remains authoritative until 0178."""

from alembic import op
import sqlalchemy as sa

revision = "0177_equipment_catalogs"
down_revision = "0176_vna_capture_jobs"
branch_labels = depends_on = None
DESTRUCTIVE = False

# Frozen schema, independent of future application mappings.
CATALOGS = {
    "ob": "equipment_machines", "ob_measuring": "equipment_measuring_units",
    "ob_support": "equipment_support_units", "ob_applicators": "equipment_applicators",
    "ob_dies": "equipment_dies", "ob_tools": "equipment_tools",
}


def upgrade():
    sqlite = op.get_bind().dialect.name == "sqlite"
    owner_check = " OR ".join(
        "(" + " AND ".join([f"catalog = '{tab}'", f"{tab}_id IS NOT NULL", f"{tab}_id = id",
                            *(f"{other}_id IS NULL" for other in CATALOGS if other != tab)]) + ")"
        for tab in CATALOGS)
    op.create_table("equipment_units",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("model_id", sa.Uuid(), sa.ForeignKey("equipment_models.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("catalog", sa.String(16), nullable=False),
        *(sa.Column(f"{tab}_id", sa.Uuid(), *([sa.ForeignKey(f"{table}.id", name=f"fk_equipment_units_{tab}",
                                                       deferrable=True, initially="DEFERRED")] if sqlite else []))
          for tab, table in CATALOGS.items()),
        sa.UniqueConstraint("id", "catalog", "model_id", name="uq_equipment_units_owner"),
        sa.CheckConstraint(owner_check, name="ck_equipment_units_one_owner"))
    for tab, table in CATALOGS.items():
        op.create_table(table,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("tab", sa.String(16), nullable=False),
            sa.Column("equipment_model_id", sa.Uuid(), nullable=False),
            sa.Column("display_text", sa.String(2000), nullable=False),
            sa.Column("normalized_search", sa.Text(), nullable=False),
            sa.Column("source_sheet", sa.String(255), nullable=False),
            sa.Column("sort_key", sa.String(2000), nullable=False),
            sa.Column("fields", sa.JSON(), nullable=False),
            sa.Column("mirror_key", sa.String(64), nullable=False, server_default=""),
            sa.Column("edited_in_erp", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.CheckConstraint(f"tab = '{tab}'", name=f"ck_{table}_catalog"),
            sa.ForeignKeyConstraint(["id", "tab", "equipment_model_id"],
                ["equipment_units.id", "equipment_units.catalog", "equipment_units.model_id"],
                name=f"fk_{table}_owner", deferrable=True, initially="DEFERRED"))
        op.create_index(f"ix_{table}_sort", table, ["sort_key", "id"])
        op.create_index(f"ix_{table}_model", table, ["equipment_model_id"])
    if not sqlite:
        for tab, table in CATALOGS.items():
            op.create_foreign_key(f"fk_equipment_units_{tab}", "equipment_units", table, [f"{tab}_id"], ["id"],
                                  deferrable=True, initially="DEFERRED")
    op.create_table("equipment_instrument_bindings",
        sa.Column("unit_id", sa.Uuid(), sa.ForeignKey("equipment_units.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("production_instruments.id", ondelete="RESTRICT"),
                  nullable=False, unique=True))


def downgrade():
    bind = op.get_bind()
    tables = ("equipment_instrument_bindings", *CATALOGS.values(), "equipment_units")
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql("LOCK TABLE " + ", ".join(tables) + " IN ACCESS EXCLUSIVE MODE")
    populated = [table for table in tables if bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()]
    if populated:
        raise RuntimeError("Cannot remove populated equipment catalogs: " + ", ".join(populated))
    op.drop_table("equipment_instrument_bindings")
    if bind.dialect.name == "postgresql":
        for tab in CATALOGS:
            op.drop_constraint(f"fk_equipment_units_{tab}", "equipment_units", type_="foreignkey")
    for table in CATALOGS.values():
        op.drop_table(table)
    op.drop_table("equipment_units")

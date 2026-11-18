"""Append-only per-end mating ledger and nullable RF resource."""

import re

from alembic import op
import sqlalchemy as sa

revision = "0189_mating_cycles"
down_revision = "0188_instrument_labels"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def upgrade() -> None:
    op.add_column("production_rf_details", sa.Column("mating_resource", sa.Integer(),
        sa.CheckConstraint("mating_resource IS NULL OR mating_resource > 0", name="ck_rf_details_mating_resource"), nullable=True))
    op.create_table("production_mating_batches",
        sa.Column("source", sa.String(200), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("length(trim(source)) > 0", name="ck_mating_batch_source"))
    op.create_table("production_instrument_matings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("production_instruments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("end", sa.String(1), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("source", sa.String(200), nullable=True),
        sa.Column("actor", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end IN ('A','B')", name="ck_mating_end"),
        sa.CheckConstraint("delta <> 0", name="ck_mating_delta"),
        sa.CheckConstraint("kind IN ('measurement','calibration','manual','initial','correction')", name="ck_mating_kind"),
        sa.CheckConstraint("delta > 0 OR kind = 'correction'", name="ck_mating_negative"),
        sa.CheckConstraint("kind NOT IN ('initial','correction') OR length(trim(note)) > 0", name="ck_mating_note"))
    op.create_index("ix_mating_instrument_created", "production_instrument_matings", ["instrument_id", "created_at"])
    op.create_index("ix_mating_source", "production_instrument_matings", ["source"])
    op.create_index("uq_mating_initial_end", "production_instrument_matings", ["instrument_id", "end"], unique=True,
                    sqlite_where=sa.text("kind = 'initial'"), postgresql_where=sa.text("kind = 'initial'"))
    # Owner's vendor evidence 17.11: ADP1A-35* has 3000 cycles. The frozen adapter JSON
    # identifies ADP1A-35M-35F / -35F-35F, but contains no N/III resource table: never guess it.
    bind = op.get_bind()
    known = re.compile(r"(?<![\w-])ADP1A-35[A-Z0-9-]+(?![\w-])", re.I)
    for instrument_id, name, article in bind.execute(sa.text("SELECT id, name, doc_designation FROM production_instruments")):
        if not known.search(f"{article or ''} {name}"):
            continue
        if bind.execute(sa.text("SELECT 1 FROM production_rf_details WHERE instrument_id = :id"), {"id": instrument_id}).first():
            bind.execute(sa.text("UPDATE production_rf_details SET mating_resource = 3000 WHERE instrument_id = :id"), {"id": instrument_id})
        else:
            bind.execute(sa.text("INSERT INTO production_rf_details "
                "(instrument_id, version, role, confirmed, source, f_min_ghz, max_points, kit_definition, mating_resource) "
                "VALUES (:id, 1, 'unknown', false, '', NULL, NULL, '', 3000)"), {"id": instrument_id})


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM production_instrument_matings LIMIT 1")).first():
        raise RuntimeError("Нельзя отменить миграцию: есть история сочленений")
    op.drop_table("production_instrument_matings")
    op.drop_table("production_mating_batches")
    with op.batch_alter_table("production_rf_details") as batch:
        batch.drop_constraint("ck_rf_details_mating_resource", type_="check")
        batch.drop_column("mating_resource")

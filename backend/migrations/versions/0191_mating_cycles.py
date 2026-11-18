"""Append-only per-side mating ledger of instruments and a nullable RF mating resource."""

import re

from alembic import op
import sqlalchemy as sa

revision = "0191_mating_cycles"
down_revision = "0190_godex_marking"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

# Frozen copy of MATING_CHECKS in mating_infrastructure.py as of this revision: never import app code here, and
# change the model's checks only through a new migration. The chain makes a negative or forked total impossible.
MATING_CHECKS = {
    "ck_mating_side": "side IN ('A','B')",
    "ck_mating_seq": "seq >= 1",
    "ck_mating_delta": "delta <> 0",
    "ck_mating_total": "total_after >= 0",
    "ck_mating_first": "seq > 1 OR total_after = delta",
    "ck_mating_kind": "kind IN ('measurement','calibration','manual','initial','correction')",
    "ck_mating_negative": "delta > 0 OR kind = 'correction'",
    "ck_mating_initial": "kind <> 'initial' OR seq = 1",
    "ck_mating_note": "kind NOT IN ('initial','correction') OR length(trim(note)) > 0",
    "ck_mating_source": "(kind IN ('measurement','calibration') AND source IS NOT NULL) OR "
                        "(kind IN ('manual','initial','correction') AND source IS NULL)",
}


def upgrade() -> None:
    with op.batch_alter_table("production_rf_details") as batch:
        batch.add_column(sa.Column("mating_resource", sa.Integer(), nullable=True))
        batch.create_check_constraint("ck_rf_details_mating_resource", "mating_resource IS NULL OR mating_resource > 0")
    op.create_table(
        "production_mating_batches",
        sa.Column("source", sa.String(200), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(source)) > 0", name="ck_mating_batch_source"),
        sa.CheckConstraint("length(payload_hash) = 64", name="ck_mating_batch_hash"),
    )
    op.create_table(
        "production_instrument_matings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("production_instruments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("side", sa.String(1), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("total_after", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("source", sa.String(200), sa.ForeignKey("production_mating_batches.source", ondelete="RESTRICT"), nullable=True),
        sa.Column("actor", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("instrument_id", "side", "seq", name="uq_mating_chain"),
        *(sa.CheckConstraint(sql, name=name) for name, sql in MATING_CHECKS.items()),
    )
    op.create_index("ix_mating_instrument_created", "production_instrument_matings", ["instrument_id", "created_at"])
    op.create_index("ix_mating_source", "production_instrument_matings", ["source"])
    # Owner's vendor evidence 17.11: Planar ADP1A-35* adapters are rated for 3000 matings. Other series have
    # no confirmed figure in the shop's data, so they stay «Ресурс не указан» until entered on the card.
    bind = op.get_bind()
    known = re.compile(r"(?<![\w-])ADP1A-35[A-Z0-9-]+(?![\w-])", re.I)
    for instrument_id, name, article in bind.execute(sa.text("SELECT id, name, doc_designation FROM production_instruments")).all():
        if not known.search(f"{article or ''} {name}"):
            continue
        if bind.execute(sa.text("SELECT 1 FROM production_rf_details WHERE instrument_id = :id"), {"id": instrument_id}).first():
            bind.execute(sa.text("UPDATE production_rf_details SET mating_resource = 3000 "
                                 "WHERE instrument_id = :id AND mating_resource IS NULL"), {"id": instrument_id})
        else:
            bind.execute(sa.text(
                "INSERT INTO production_rf_details "
                "(instrument_id, version, role, confirmed, source, f_min_ghz, max_points, kit_definition, mating_resource) "
                "VALUES (:id, 1, 'unknown', :no, '', NULL, NULL, '', 3000)"), {"id": instrument_id, "no": False})


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM production_instrument_matings")).first():
        raise RuntimeError("Нельзя отменить миграцию: есть история сочленений")
    op.drop_index("ix_mating_source", table_name="production_instrument_matings")
    op.drop_index("ix_mating_instrument_created", table_name="production_instrument_matings")
    op.drop_table("production_instrument_matings")
    op.drop_table("production_mating_batches")
    with op.batch_alter_table("production_rf_details") as batch:
        batch.drop_constraint("ck_rf_details_mating_resource", type_="check")
        batch.drop_column("mating_resource")

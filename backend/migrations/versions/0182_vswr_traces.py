"""Atomic multi-trace VSWR evidence; historical measurements are not re-evaluated."""

from alembic import op
import sqlalchemy as sa

revision = "0182_vswr_traces"
down_revision = "0181_vna_sessions"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

TABLE = "production_vswr_measurements"
TRACES = "production_vswr_traces"
FILES = "production_vswr_files"
LEGACY_VERDICT = (
    "verdict_manual OR (norm_vswr_max IS NOT NULL AND ((verdict = 'pass' AND max_vswr <= norm_vswr_max)"
    " OR (verdict = 'fail' AND max_vswr > norm_vswr_max)))"
)


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("evidence_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("port_mapping", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("required_ends", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("end_results", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("complete", sa.Boolean(), nullable=True))
        batch.create_unique_constraint("uq_production_vswr_serial_evidence", ["serial_id", "evidence_key"])
        batch.create_check_constraint("ck_production_vswr_evidence_key", "evidence_key IS NULL OR length(evidence_key) = 64")
        batch.create_check_constraint("ck_production_vswr_evaluation", (
            "complete IS NULL OR (evidence_key IS NOT NULL AND port_mapping IS NOT NULL"
            " AND required_ends IS NOT NULL AND end_results IS NOT NULL)"
        ))
        batch.create_check_constraint("ck_production_vswr_complete", "complete IS NULL OR complete OR verdict = 'fail'")
        batch.drop_constraint("ck_production_vswr_auto_verdict", type_="check")
        batch.create_check_constraint("ck_production_vswr_auto_verdict", (
            "verdict_manual OR (complete IS FALSE AND verdict = 'fail') OR "
            "(norm_vswr_max IS NOT NULL AND ((verdict = 'pass' AND max_vswr <= norm_vswr_max)"
            " OR (verdict = 'fail' AND max_vswr > norm_vswr_max)))"
        ))
    op.create_table(
        TRACES,
        sa.Column("measurement_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("name", sa.String(8), nullable=False, primary_key=True),
        sa.Column("quantity", sa.String(16), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("trace", sa.JSON(), nullable=False),
        sa.Column("samples", sa.JSON(), nullable=False),
        sa.Column("complex_samples", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["measurement_id"], [f"{TABLE}.id"], ondelete="RESTRICT",
                                name="fk_production_vswr_trace_measurement"),
        sa.CheckConstraint("name IN ('S11', 'S22', 'S21', 'S12', 'A:S11', 'A:S22', 'B:S11', 'B:S22')", name="ck_production_vswr_trace_name"),
        sa.CheckConstraint("points >= 2", name="ck_production_vswr_trace_points"),
        sa.CheckConstraint(
            "(name IN ('S11', 'S22', 'A:S11', 'A:S22', 'B:S11', 'B:S22') AND quantity = 'vswr') OR "
            "(name IN ('S21', 'S12') AND quantity = 'magnitude')", name="ck_production_vswr_trace_quantity",
        ),
    )
    op.create_table(
        FILES,
        sa.Column("measurement_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("assembly_end", sa.String(1), nullable=False, primary_key=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("file_meta", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["measurement_id"], [f"{TABLE}.id"], ondelete="RESTRICT",
                                name="fk_production_vswr_file_measurement"),
        sa.CheckConstraint("assembly_end IN ('A', 'B')", name="ck_production_vswr_file_end"),
        sa.CheckConstraint("length(trim(file_name)) > 0", name="ck_production_vswr_end_file_name"),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 2097152", name="ck_production_vswr_end_file_size"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if (connection.scalar(sa.text(f"SELECT count(*) FROM {TRACES}"))
            or connection.scalar(sa.text(f"SELECT count(*) FROM {FILES}"))
            or connection.scalar(sa.text(f"SELECT count(*) FROM {TABLE} WHERE complete IS NOT NULL"))):
        raise RuntimeError("Откат удалит многотрассовые замеры КСВН — сначала сохраните данные")
    op.drop_table(TRACES)
    op.drop_table(FILES)
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint("uq_production_vswr_serial_evidence", type_="unique")
        for name in ("evidence_key", "evaluation", "complete", "auto_verdict"):
            batch.drop_constraint(f"ck_production_vswr_{name}", type_="check")
        batch.create_check_constraint("ck_production_vswr_auto_verdict", LEGACY_VERDICT)
        for column in ("evidence_key", "port_mapping", "required_ends", "end_results", "complete"):
            batch.drop_column(column)

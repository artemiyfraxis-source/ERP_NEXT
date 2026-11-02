"""Persist full calculation composition without creating purchase obligations."""

from alembic import op
import sqlalchemy as sa

revision = "0168_requests_calc_compositions"
down_revision = "0167_instrument_verification_pending"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def upgrade():
    op.create_table(
        "requests_calc_compositions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project", sa.String(128), nullable=False, unique=True),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_by", sa.Uuid(), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_requests_calc_compositions_version"),
        sa.CheckConstraint("length(trim(project)) > 0", name="ck_requests_calc_compositions_project"),
        sa.CheckConstraint("length(trim(source_id)) > 0", name="ck_requests_calc_compositions_source"),
        sa.CheckConstraint("json_array_length(rows) IS NOT NULL AND json_array_length(rows) BETWEEN 1 AND 5000",
                           name="ck_requests_calc_compositions_rows"),
    )


def downgrade():
    # Composition is source evidence; never silently erase an imported snapshot.
    if op.get_bind().execute(sa.text("SELECT count(*) FROM requests_calc_compositions")).scalar():
        raise RuntimeError("Cannot drop saved calculation evidence; restore a prior copy instead.")
    op.drop_table("requests_calc_compositions")

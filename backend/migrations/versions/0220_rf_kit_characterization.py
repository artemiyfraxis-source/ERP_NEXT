"""Physical SMA-MF06 standards and immutable measured revisions; no identities or curves are seeded."""

from alembic import op
import sqlalchemy as sa

revision = "0220_rf_kit_characterization"
down_revision = "0219_arinst_control"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

STANDARDS = "production_rf_kit_standards"
REVISIONS = "production_rf_standard_characterizations"
TRIGGER = "rf_standard_characterization_immutable"
MESSAGE = "Ревизии мер неизменяемы: сохраните новую ревизию"
OLD_KIT_CHECK = "kit_definition = '' OR (role = 'solt_kit' AND kit_definition = '6650F27-full') OR (role = 'load_kit' AND kit_definition = 'SMA-MF06-loads')"
KIT_CHECK = "kit_definition = '' OR (role = 'solt_kit' AND kit_definition IN ('6650F27-full','SMA-MF06-characterized')) OR (role = 'load_kit' AND kit_definition = 'SMA-MF06-loads')"
GENERATION_CHECK = "(kit_definition = 'SMA-MF06-characterized' AND characterization_generation > 0 AND characterization_hash IS NOT NULL AND length(characterization_hash) = 64) OR (kit_definition <> 'SMA-MF06-characterized' AND characterization_generation = 0 AND characterization_hash IS NULL)"


def upgrade():
    with op.batch_alter_table("production_rf_details") as batch:
        batch.add_column(sa.Column("characterization_generation", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("characterization_hash", sa.String(64), nullable=True))
        batch.drop_constraint("ck_rf_details_kit", type_="check")
        batch.create_check_constraint("ck_rf_details_kit", KIT_CHECK)
        batch.create_check_constraint("ck_rf_details_characterization", GENERATION_CHECK)
    op.create_table(STANDARDS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kit_instrument_id", sa.Uuid(), sa.ForeignKey("production_instruments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("piece_label", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("gender", sa.String(8), nullable=False),
        sa.Column("gender2", sa.String(8), nullable=True),
        sa.Column("f_min_hz", sa.Float(), nullable=False),
        sa.Column("f_max_hz", sa.Float(), nullable=False),
        sa.Column("current_revision_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.UniqueConstraint("kit_instrument_id", "piece_label", name="uq_rf_kit_piece_label"),
        sa.CheckConstraint("length(trim(piece_label)) BETWEEN 1 AND 100 AND piece_label = trim(piece_label)", name="ck_rf_kit_piece_label"),
        sa.CheckConstraint("kind IN ('OPEN','SHORT','LOAD','THRU')", name="ck_rf_kit_piece_kind"),
        sa.CheckConstraint("gender IN ('male','female')", name="ck_rf_kit_piece_gender"),
        sa.CheckConstraint("(kind = 'THRU' AND gender2 IS NOT NULL AND gender2 = gender) OR (kind <> 'THRU' AND gender2 IS NULL)", name="ck_rf_kit_piece_gender2"),
        sa.CheckConstraint("f_min_hz >= 0 AND f_max_hz > f_min_hz AND f_max_hz <= 6000000000", name="ck_rf_kit_piece_band"),
        sa.CheckConstraint("NOT (gender = 'female' AND kind IN ('OPEN','SHORT')) OR f_max_hz <= 3000000000", name="ck_rf_kit_piece_female_band"),
        sa.CheckConstraint("version >= 1", name="ck_rf_kit_piece_version"),
    )
    op.create_table(REVISIONS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("standard_id", sa.Uuid(), sa.ForeignKey(f"{STANDARDS}.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(3), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("grid_hash", sa.String(64), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("f_min_hz", sa.Float(), nullable=False),
        sa.Column("f_max_hz", sa.Float(), nullable=False),
        sa.Column("raw_artifact_id", sa.Uuid(), sa.ForeignKey("production_vna_artifacts.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("provenance", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("validation", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.UniqueConstraint("standard_id", "revision", name="uq_rf_standard_revision"),
        sa.UniqueConstraint("standard_id", "sha256", name="uq_rf_standard_sha256"),
        sa.UniqueConstraint("standard_id", "id", name="uq_rf_standard_revision_owner"),
        sa.CheckConstraint("revision >= 1", name="ck_rf_standard_revision"),
        sa.CheckConstraint("format IN ('s1p','s2p')", name="ck_rf_standard_format"),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 2097152", name="ck_rf_standard_content"),
        sa.CheckConstraint("length(sha256) = 64 AND length(grid_hash) = 64", name="ck_rf_standard_hashes"),
        sa.CheckConstraint("points BETWEEN 2 AND 200000", name="ck_rf_standard_points"),
        sa.CheckConstraint("f_min_hz >= 0 AND f_max_hz > f_min_hz AND f_max_hz <= 6000000000", name="ck_rf_standard_band"),
    )
    with op.batch_alter_table(STANDARDS) as batch:
        batch.create_foreign_key("fk_rf_kit_current_revision", REVISIONS,
                                 ["id", "current_revision_id"], ["standard_id", "id"], ondelete="RESTRICT")
    _protect_revisions()


def _protect_revisions():
    # Same dialect-specific RAISE pattern as the existing immutable production journals.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(f"CREATE FUNCTION {TRIGGER}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                             f"BEGIN RAISE EXCEPTION '{MESSAGE}'; END; $$")
        bind.exec_driver_sql(f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE OR DELETE ON {REVISIONS} "
                             f"FOR EACH ROW EXECUTE FUNCTION {TRIGGER}()")
    elif bind.dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            bind.exec_driver_sql(f"CREATE TRIGGER {TRIGGER}_{action.lower()} BEFORE {action} ON {REVISIONS} "
                                 f"BEGIN SELECT RAISE(ABORT, '{MESSAGE}'); END")
    else:
        raise RuntimeError("Защита ревизий поддерживается только для PostgreSQL и SQLite")


def downgrade():
    bind = op.get_bind()
    for table in (REVISIONS, STANDARDS):
        if bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("Откат запрещён: сохранены меры или ревизии набора")
    if bind.execute(sa.text("SELECT 1 FROM production_rf_details WHERE characterization_generation <> 0 LIMIT 1")).first():
        raise RuntimeError("Откат запрещён: набор активирован")
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(f"DROP TRIGGER {TRIGGER} ON {REVISIONS}")
        bind.exec_driver_sql(f"DROP FUNCTION {TRIGGER}()")
    else:
        for action in ("update", "delete"):
            bind.exec_driver_sql(f"DROP TRIGGER {TRIGGER}_{action}")
    with op.batch_alter_table(STANDARDS) as batch:
        batch.drop_constraint("fk_rf_kit_current_revision", type_="foreignkey")
    op.drop_table(REVISIONS)
    op.drop_table(STANDARDS)
    with op.batch_alter_table("production_rf_details") as batch:
        batch.drop_constraint("ck_rf_details_characterization", type_="check")
        batch.drop_constraint("ck_rf_details_kit", type_="check")
        batch.drop_column("characterization_hash")
        batch.drop_column("characterization_generation")
        batch.create_check_constraint("ck_rf_details_kit", OLD_KIT_CHECK)

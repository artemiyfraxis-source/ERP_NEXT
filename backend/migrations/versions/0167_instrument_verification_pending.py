"""Allow explicit unverified instruments without inventing verification evidence."""

from alembic import op
import sqlalchemy as sa

revision = "0167_instrument_verification_pending"
down_revision = "0166_kb_progress"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

TABLE = "production_instruments"
COLUMNS = {
    "verification_number": sa.String(255),
    "verified_at": sa.DateTime(timezone=True),
    "verification_due": sa.DateTime(timezone=True),
}
CHECKS = {
    "ck_production_instruments_status": "status IN ('Действует', 'Не используется', 'Поверка не подтверждена')",
    "ck_production_instruments_active_verification": "status <> 'Действует' OR (verification_number IS NOT NULL AND length(trim(verification_number)) > 0 AND verified_at IS NOT NULL AND verification_due IS NOT NULL)",
    "ck_production_instruments_unverified": "status <> 'Поверка не подтверждена' OR (verification_number IS NULL AND verified_at IS NULL AND verification_due IS NULL)",
    "ck_production_instruments_verification_dates": "verified_at IS NULL OR verification_due IS NULL OR verification_due >= verified_at",
}


def upgrade():
    # Validate before SQLite's table rebuild too: do not rewrite or silently repair existing evidence.
    invalid = " OR ".join(f"NOT ({condition})" for condition in CHECKS.values())
    if op.get_bind().execute(sa.text(f"SELECT count(*) FROM {TABLE} WHERE {invalid}")).scalar():
        raise RuntimeError("Existing instruments violate verification constraints; reconcile their evidence before migration.")
    with op.batch_alter_table(TABLE) as batch:
        for name, kind in COLUMNS.items():
            batch.alter_column(name, existing_type=kind, nullable=True)
        for name, condition in CHECKS.items():
            batch.create_check_constraint(name, condition)


def downgrade():
    missing = " OR ".join(f"{name} IS NULL" for name in COLUMNS)
    if op.get_bind().execute(sa.text(
        f"SELECT count(*) FROM {TABLE} WHERE status = 'Поверка не подтверждена' OR {missing}",
    )).scalar():
        raise RuntimeError("Cannot downgrade while instruments have unknown verification evidence; restore a prior copy.")
    with op.batch_alter_table(TABLE) as batch:
        for name in CHECKS:
            batch.drop_constraint(name, type_="check")
        for name, kind in COLUMNS.items():
            batch.alter_column(name, existing_type=kind, nullable=False)

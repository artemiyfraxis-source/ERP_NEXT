"""Instrument kinds, parent links and stored documents; preserve existing evidence."""

from alembic import op
import sqlalchemy as sa

revision = "0175_instrument_kinds_files"
down_revision = "0174_supplier_item_rules"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

TABLE = "production_instruments"
OLD_CHECKS = {
    "ck_production_instruments_status": "status IN ('Действует', 'Не используется', 'Поверка не подтверждена')",
    "ck_production_instruments_active_verification": "status <> 'Действует' OR (verification_number IS NOT NULL AND length(trim(verification_number)) > 0 AND verified_at IS NOT NULL AND verification_due IS NOT NULL)",
    "ck_production_instruments_unverified": "status <> 'Поверка не подтверждена' OR (verification_number IS NULL AND verified_at IS NULL AND verification_due IS NULL)",
    "ck_production_instruments_verification_dates": "verified_at IS NULL OR verification_due IS NULL OR verification_due >= verified_at",
}
CHECKS = {
    **OLD_CHECKS,
    "ck_production_instruments_active_verification": "kind <> 'Средство измерений' OR " + OLD_CHECKS["ck_production_instruments_active_verification"],
    "ck_production_instruments_kind": "kind IN ('Средство измерений', 'Принадлежность')",
    "ck_production_instruments_parent": "parent_id IS NULL OR parent_id <> id",
    "ck_production_instruments_quantity": "quantity >= 1",
    "ck_production_instruments_measuring_quantity": "kind <> 'Средство измерений' OR quantity = 1",
    "ck_production_instruments_accessory": "kind <> 'Принадлежность' OR (parent_id IS NOT NULL AND verification_number IS NULL AND verified_at IS NULL AND verification_due IS NULL AND status IN ('Действует', 'Не используется'))",
}
NEW_COLUMNS = ("kind", "parent_id", "serial_number", "manufacturer", "doc_designation", "quantity", "storage_location", "warehouse_grn", "warehouse_writeoff_id")


def upgrade():
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("kind", sa.String(32), nullable=False, server_default="Средство измерений"))
        batch.add_column(sa.Column("parent_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("warehouse_writeoff_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_production_instruments_parent", TABLE, ["parent_id"], ["id"], ondelete="RESTRICT")
        for name, length in (("serial_number", 128), ("manufacturer", 255), ("storage_location", 255), ("warehouse_grn", 32)):
            batch.add_column(sa.Column(name, sa.String(length), nullable=False, server_default=""))
        batch.add_column(sa.Column("doc_designation", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"))
        for name in OLD_CHECKS:
            batch.drop_constraint(name, type_="check")
        for name, condition in CHECKS.items():
            batch.create_check_constraint(name, condition)
    op.create_table(
        "production_instrument_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey(f"{TABLE}.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), sa.ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False),
        sa.CheckConstraint("kind IN ('Паспорт', 'Свидетельство о поверке', 'Руководство', 'Прочее')", name="ck_production_instrument_files_kind"),
        sa.CheckConstraint("length(trim(file_name)) > 0", name="ck_production_instrument_files_name"),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 20971520", name="ck_production_instrument_files_size"),
    )
    op.create_index("ix_production_instrument_files_instrument_id", "production_instrument_files", ["instrument_id"])


def downgrade():
    invalid = " OR ".join(f"NOT ({condition})" for condition in OLD_CHECKS.values())
    if op.get_bind().execute(sa.text(f"SELECT count(*) FROM {TABLE} WHERE {invalid}")).scalar():
        raise RuntimeError("Нельзя отменить миграцию: записи СИ нарушают прежние требования к поверке.")
    # A downgrade must not silently discard uploaded passports or accessory inventory.
    if op.get_bind().execute(sa.text("SELECT count(*) FROM production_instrument_files")).scalar():
        raise RuntimeError("Нельзя отменить миграцию: сохранены документы СИ.")
    if op.get_bind().execute(sa.text(f"SELECT count(*) FROM {TABLE} WHERE kind = 'Принадлежность'")).scalar():
        raise RuntimeError("Нельзя отменить миграцию: зарегистрированы принадлежности СИ.")
    metadata = " OR ".join(f"{name} <> ''" for name in (
        "serial_number", "manufacturer", "doc_designation", "storage_location", "warehouse_grn",
    ))
    if op.get_bind().execute(sa.text(
        f"SELECT count(*) FROM {TABLE} WHERE {metadata} OR quantity <> 1 "
        "OR parent_id IS NOT NULL OR warehouse_writeoff_id IS NOT NULL"
    )).scalar():
        raise RuntimeError("Нельзя отменить миграцию: сохранены дополнительные сведения или связи СИ.")
    op.drop_table("production_instrument_files")
    with op.batch_alter_table(TABLE) as batch:
        for name in CHECKS:
            batch.drop_constraint(name, type_="check")
        batch.drop_constraint("fk_production_instruments_parent", type_="foreignkey")
        for name in NEW_COLUMNS:
            batch.drop_column(name)
        for name, condition in OLD_CHECKS.items():
            batch.create_check_constraint(name, condition)

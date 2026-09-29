"""Вход по PIN и «Режим админа» (SUPPLYMAP Portal.gs portalLogin / portalRevealPin_ / portalToggleAdmin).

- `identity_credentials.pin_hash` — PIN (6–8 цифр) хэшем Argon2id; пусто — PIN не назначен.
- `identity_users.seen_at` — первый успешный вход (`portal_seen_<email>`): после него «узнать PIN» закрыто.
  Заполняется по журналу: кто уже входил в ERP (`identity.login_succeeded`), тот считается входившим.
- `identity_sessions.admin_mode_until` — режим админа этой сессии включён до этого момента.
- `identity_settings` — переключатели владельца (`pin_reveal` = «узнать PIN при первом входе»), по умолчанию выкл.

Revision ID: 0062_identity_pin_admin_mode
Revises: 0061_procurement_traceability
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0066_identity_pin_admin_mode"
down_revision: str | None = "0065_procurement_supplier_order_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("identity_credentials") as batch:
        batch.add_column(sa.Column("pin_hash", sa.String(512), nullable=True))
    with op.batch_alter_table("identity_users") as batch:
        batch.add_column(sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("identity_sessions") as batch:
        batch.add_column(sa.Column("admin_mode_until", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "identity_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.String(255), nullable=False),
    )
    op.execute(
        "UPDATE identity_users SET seen_at = ("
        " SELECT MIN(audit_events.occurred_at) FROM audit_events"
        " WHERE audit_events.event_type = 'identity.login_succeeded'"
        " AND audit_events.aggregate_id = identity_users.id)"
    )


def downgrade() -> None:
    op.drop_table("identity_settings")
    with op.batch_alter_table("identity_sessions") as batch:
        batch.drop_column("admin_mode_until")
    with op.batch_alter_table("identity_users") as batch:
        batch.drop_column("seen_at")
    with op.batch_alter_table("identity_credentials") as batch:
        batch.drop_column("pin_hash")

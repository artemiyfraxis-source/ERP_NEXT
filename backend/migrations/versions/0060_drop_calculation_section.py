"""Раздел «Расчёт и КП» (legacy ERP_RECOVERED) снят с ERP: он в другом блоке. Удалить его строки доступа.

Revision ID: 0060_drop_calculation_section
Revises: 0059_receiving_quality_details
"""

from alembic import op


revision = "0060_drop_calculation_section"
down_revision = "0059_receiving_quality_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM identity_role_access WHERE section = 'calculation'")
    op.execute("DELETE FROM identity_user_access WHERE section = 'calculation'")


def downgrade() -> None:
    # Строки доступа — правки администратора поверх ролей; удалённые не восстановить, раздела больше нет.
    pass

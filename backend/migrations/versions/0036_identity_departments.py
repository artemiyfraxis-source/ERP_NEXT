"""The department list of the «Сотрудники» screen (ID-8): the list the administrator saves, one row per
department in dropdown order. The table starts empty — until someone saves a list, the departments people
already have are offered, as SUPPLYMAP staffDepts_ does with an unset ScriptProperty. Users' department
values are not rewritten here: renaming a department is an explicit, audited action."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0036_identity_departments"
down_revision: str | None = "0035_procurement_replacement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "identity_departments",
        sa.Column("position", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(255), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("identity_departments")

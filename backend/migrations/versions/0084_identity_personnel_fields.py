"""«Личная карточка», кадровые поля (owner 04.10.2026; native, no SUPPLYMAP source).

- `identity_users.personnel_number` — табельный номер (FRX-0017), unique when set; NULL = not filled.
- `identity_users.job_title` / `qualification` — должность, категория / квалификация (free text, '' = not filled).
- `identity_users.job_since` — «в должности с».
- `identity_users.manager_id` — непосредственный руководитель (another employee); erasing that employee clears it.
- `identity_departments.head_user_id` — руководитель отдела, optional.

Existing rows get empty values: nothing to carry over."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0084_identity_personnel_fields"
down_revision: str | None = "0082_project_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("identity_users") as batch:
        batch.add_column(sa.Column("personnel_number", sa.String(32), nullable=True))
        batch.add_column(sa.Column("job_title", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("qualification", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("job_since", sa.Date(), nullable=True))
        batch.add_column(sa.Column("manager_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_identity_users_manager_id", "identity_users", ["manager_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_index("ux_identity_users_personnel_number", ["personnel_number"], unique=True)
        batch.create_index("ix_identity_users_manager_id", ["manager_id"])
    with op.batch_alter_table("identity_departments") as batch:
        batch.add_column(sa.Column("head_user_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_identity_departments_head_user_id", "identity_users", ["head_user_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("identity_departments") as batch:
        batch.drop_constraint("fk_identity_departments_head_user_id", type_="foreignkey")
        batch.drop_column("head_user_id")
    with op.batch_alter_table("identity_users") as batch:
        batch.drop_index("ix_identity_users_manager_id")
        batch.drop_index("ux_identity_users_personnel_number")
        batch.drop_constraint("fk_identity_users_manager_id", type_="foreignkey")
        batch.drop_column("manager_id")
        batch.drop_column("job_since")
        batch.drop_column("qualification")
        batch.drop_column("job_title")
        batch.drop_column("personnel_number")

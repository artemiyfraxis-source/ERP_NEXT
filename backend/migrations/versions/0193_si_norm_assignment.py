"""Assigned norm of a coax adapter / test cable: `production_rf_details.norm_id` (owner 18.11.2026).

The passport, the label and the VNA tool use only the norm assigned to the item — an id of backend/data/rf-norms.json,
checked against the catalog when it is written (the app's RfRepository.save). The database keeps the id on adapters and
test cables only. Every existing row starts unassigned (''): «норма не назначена», no verdict, until quality assigns
the reviewed proposals (python -m app.workflows.si_norm_assignment). Not destructive.
"""

from alembic import op
import sqlalchemy as sa

revision = "0193_si_norm_assignment"
down_revision = "0192_operation_template_sides"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

# Frozen copy of rf_infrastructure.NORM_CHECK as of this revision.
NORM_CHECK = "norm_id = '' OR (role IN ('adapter','test_cable') AND norm_id = trim(norm_id))"


def upgrade() -> None:
    with op.batch_alter_table("production_rf_details") as batch:
        batch.add_column(sa.Column("norm_id", sa.String(64), nullable=False, server_default=""))
        batch.create_check_constraint("ck_rf_details_norm", NORM_CHECK)


def downgrade() -> None:
    with op.batch_alter_table("production_rf_details") as batch:
        batch.drop_constraint("ck_rf_details_norm", type_="check")
        batch.drop_column("norm_id")

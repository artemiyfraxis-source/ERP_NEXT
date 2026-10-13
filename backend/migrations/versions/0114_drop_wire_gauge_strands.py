"""«Сечения AWG ↔ мм²»: столбец `wire_gauges.strands` (скрутка у сечения) удалён (владелец 13.10.2026).

Скрутка — свойство провода, а не сечения (с 12.10 она в ячейке «Жила: проволок × диаметр, мм» строки БД.ПРОВ); экран
столбец сечения не показывал, читали его только API и загрузка «Сечения AWG ↔ мм²», которые его больше не знают.

Значения не теряются: непустые скрутки сечений до удаления столбца записываются одним событием журнала
`references.wire_gauge_strands_dropped` (id, AWG, сечение, скрутка); пустой столбец — события нет. Downgrade
возвращает столбец и значения из этого события.

DESTRUCTIVE = False: ни одна строка не удаляется, содержимое столбца сохранено в журнале.
"""

from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID, uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0114_drop_wire_gauge_strands"
down_revision: str | None = "0113_task_launch_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

EVENT = "references.wire_gauge_strands_dropped"
SYSTEM = UUID(int=0)

_audit = sa.table(
    "audit_events", sa.column("id", sa.Uuid()), sa.column("aggregate_id", sa.Uuid()), sa.column("event_type", sa.String()),
    sa.column("actor_id", sa.Uuid()), sa.column("actor_name", sa.String()), sa.column("actor_department", sa.String()),
    sa.column("occurred_at", sa.DateTime(timezone=True)), sa.column("details", sa.JSON()),
)


def upgrade() -> None:
    bind = op.get_bind()
    gauges = sa.table("wire_gauges", sa.column("id", sa.Uuid()), sa.column("awg", sa.String()),
                      sa.column("section_mm2", sa.Numeric(10, 4)), sa.column("strands", sa.String()))
    kept = [{"id": str(row.id), "awg": row.awg or "", "section_mm2": str(row.section_mm2), "strands": row.strands}
            for row in bind.execute(sa.select(gauges).where(sa.func.trim(gauges.c.strands) != "").order_by(gauges.c.section_mm2))]
    if kept:
        bind.execute(_audit.insert().values(
            id=uuid4(), aggregate_id=SYSTEM, event_type=EVENT, actor_id=SYSTEM, actor_name="миграция 0114",
            actor_department="System", occurred_at=datetime.now(timezone.utc), details={"gauges": kept},
        ))
    with op.batch_alter_table("wire_gauges") as batch:
        batch.drop_column("strands")


def downgrade() -> None:
    with op.batch_alter_table("wire_gauges") as batch:
        batch.add_column(sa.Column("strands", sa.String(64), nullable=False, server_default=""))
    bind = op.get_bind()
    details = bind.execute(sa.select(_audit.c.details).where(_audit.c.event_type == EVENT)
                           .order_by(_audit.c.occurred_at.desc())).scalars().first()
    gauges = sa.table("wire_gauges", sa.column("id", sa.Uuid()), sa.column("strands", sa.String()))
    for item in (details or {}).get("gauges", []):
        bind.execute(gauges.update().where(gauges.c.id == UUID(item["id"])).values(strands=item["strands"]))

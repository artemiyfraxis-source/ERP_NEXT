"""Чертёж жгута: у позиции не больше одного привязанного чертежа — правило в базе (владелец, 10.10.2026).

До сих пор «один чертёж на позицию» держал только код (`harness_api._current` брал самый новый, «Вернуть» проверял
наличие перед сменой типа) — два одновременных «Вернуть» или два первых сохранения могли оставить у позиции два
привязанных чертежа. Здесь частичный уникальный индекс `ux_techcard_kitset_documents_linked_harness`:
(project, upper(item_index)) среди строк КД с типом `application/vnd.fraxis.harness+json`.

Перед индексом — дубли, если они уже есть: у позиции остаётся привязанным самый новый (`uploaded_at`, при равенстве —
больший id; его же читал `_current`), более старые переводятся в отвязанные (`…harness-unlinked+json`) — строка и
байты остаются, в карточке у них «Вернуть». Ни одна строка не удаляется. Что переведено — в вывод миграции и в журнал
(`techcards.kitset_harness_unlinked`, причина в details). Группировка по позиции — Python `upper()`, она не мягче
`upper()` базы, так что индекс после неё строится всегда.

Откат снимает индекс; отвязанные миграцией чертежи остаются отвязанными (их возвращают кнопкой «Вернуть»).
"""

from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID, uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0090_harness_one_linked_drawing"
down_revision: str | None = "0089_level_view_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ux_techcard_kitset_documents_linked_harness"
LINKED = "application/vnd.fraxis.harness+json"  # kitset_api.HARNESS_CONTENT_TYPE
UNLINKED = "application/vnd.fraxis.harness-unlinked+json"  # kitset_api.HARNESS_UNLINKED_CONTENT_TYPE
_WHERE = sa.text(f"content_type = '{LINKED}'")

_documents = sa.table(
    "techcard_kitset_documents",
    sa.column("id", sa.Uuid()), sa.column("project", sa.String()), sa.column("item_index", sa.String()),
    sa.column("file_name", sa.String()), sa.column("content_type", sa.String()),
    sa.column("uploaded_at", sa.DateTime(timezone=True)),
)
_audit = sa.table(
    "audit_events",
    sa.column("id", sa.Uuid()), sa.column("aggregate_id", sa.Uuid()), sa.column("event_type", sa.String()),
    sa.column("actor_id", sa.Uuid()), sa.column("actor_name", sa.String()), sa.column("actor_department", sa.String()),
    sa.column("occurred_at", sa.DateTime(timezone=True)), sa.column("details", sa.JSON()),
)


def _uuid(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_documents.c.id, _documents.c.project, _documents.c.item_index, _documents.c.file_name,
                  _documents.c.uploaded_at)
        .where(_documents.c.content_type == LINKED)
    ).all()
    positions: dict[tuple[str, str], list] = {}
    for row in rows:
        positions.setdefault((row.project, str(row.item_index or "").upper()), []).append(row)
    now = datetime.now(timezone.utc)
    events = []
    for (project, index), linked in sorted(positions.items()):
        if len(linked) < 2:
            continue
        linked.sort(key=lambda row: (row.uploaded_at, str(_uuid(row.id))), reverse=True)
        kept, older = linked[0], linked[1:]
        for row in older:
            bind.execute(
                _documents.update()
                .where(_documents.c.id == row.id, _documents.c.content_type == LINKED)
                .values(content_type=UNLINKED)
            )
            events.append({
                "id": uuid4(), "aggregate_id": _uuid(row.id), "event_type": "techcards.kitset_harness_unlinked",
                "actor_id": UUID(int=0), "actor_name": "Миграция 0089", "actor_department": "Система",
                "occurred_at": now, "details": {
                    "project": project, "item_index": row.item_index, "file": row.file_name,
                    "document_id": str(_uuid(row.id)), "kept_document_id": str(_uuid(kept.id)),
                    "reason": "У позиции было несколько привязанных чертежей — оставлен самый новый",
                },
            })
            _say(f"Чертёж жгута {project} {row.item_index}: «{row.file_name}» ({_uuid(row.id)}) отвязан, "
                 f"привязанным остался {_uuid(kept.id)}")
    if events:
        bind.execute(_audit.insert(), events)
    _say(f"Чертежи жгута: отвязано лишних {len(events)}, позиций с привязанным чертежом {len(positions)}")
    op.create_index(
        INDEX, "techcard_kitset_documents", ["project", sa.text("upper(item_index)")],
        unique=True, sqlite_where=_WHERE, postgresql_where=_WHERE,
    )


def _say(text: str) -> None:
    """В вывод миграции (на Windows вывод в трубу идёт в ANSI — символ вне неё не должен валить миграцию)."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "backslashreplace").decode("ascii"))


def downgrade() -> None:
    op.drop_index(INDEX, table_name="techcard_kitset_documents")

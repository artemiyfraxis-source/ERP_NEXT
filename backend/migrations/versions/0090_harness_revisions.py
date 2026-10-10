"""Чертёж жгута: история версий — таблица `techcard_harness_revisions` (владелец, 10.10.2026).

До сих пор чертёж сохранялся поверх себя в строке КД, а версией был хэш байтов. Теперь каждое сохранение дописывает
версию (номер растёт, строки не меняются и не удаляются); номер версии — то, на чём проверяется одновременная правка.
Правила в базе:
- версия принадлежит существующей строке КД (FK, ON DELETE RESTRICT — чертёж с историей не удалить);
- номер > 0 и единственный у чертежа (UNIQUE(document_id, revision)) — два сохранения на одной версии не проходят оба;
- sha256 полный (64 знака), размер ≥ 0, вид — сохранение / загрузка / восстановление / начальная, «восстановлена из»
  только у восстановления и только из более ранней.

Заполнение: каждый чертёж жгута (привязанный и отвязанный, у которого есть байты) получает версию 1 «начальная» —
его нынешние байты, автор и время из строки КД. Строки КД не меняются, ничего не удаляется. Счёт — в вывод миграции.
Чертёж без байтов (такого быть не должно) пропускается и называется в выводе: его первая правка запишет начальную
версию сама.

Откат снимает таблицу (история теряется, чертежи в КД остаются как есть).
"""

from datetime import datetime
import hashlib
from typing import Sequence
from uuid import UUID, uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0090_harness_revisions"
down_revision: str | None = "0089_harness_one_linked_drawing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "techcard_harness_revisions"
# harness.schema.HARNESS_CONTENT_TYPE / HARNESS_UNLINKED_CONTENT_TYPE
DRAWING_TYPES = ("application/vnd.fraxis.harness+json", "application/vnd.fraxis.harness-unlinked+json")

_documents = sa.table(
    "techcard_kitset_documents",
    sa.column("id", sa.Uuid()), sa.column("project", sa.String()), sa.column("item_index", sa.String()),
    sa.column("content_type", sa.String()), sa.column("content", sa.LargeBinary()),
    sa.column("uploaded_at", sa.DateTime(timezone=True)), sa.column("uploaded_by", sa.String()),
)


def upgrade() -> None:
    revisions = op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("restored_from", sa.Integer(), nullable=True),
        sa.Column("saved_by", sa.String(255), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["techcard_kitset_documents.id"], ondelete="RESTRICT",
                                name="fk_techcard_harness_revisions_document_id"),
        sa.UniqueConstraint("document_id", "revision", name="uq_techcard_harness_revisions_document_revision"),
        sa.CheckConstraint("revision > 0", name="ck_techcard_harness_revisions_revision_positive"),
        sa.CheckConstraint("size >= 0", name="ck_techcard_harness_revisions_size"),
        sa.CheckConstraint("length(content_sha256) = 64", name="ck_techcard_harness_revisions_sha256"),
        sa.CheckConstraint("kind IN ('saved', 'imported', 'restored', 'initial')",
                           name="ck_techcard_harness_revisions_kind"),
        sa.CheckConstraint(
            "restored_from IS NULL OR (kind = 'restored' AND restored_from > 0 AND restored_from < revision)",
            name="ck_techcard_harness_revisions_restored_from",
        ),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_documents.c.id, _documents.c.project, _documents.c.item_index, _documents.c.content,
                  _documents.c.uploaded_at, _documents.c.uploaded_by)
        .where(_documents.c.content_type.in_(DRAWING_TYPES))
        .order_by(_documents.c.project, _documents.c.item_index, _documents.c.uploaded_at)
    ).all()
    initial = []
    for row in rows:
        if row.content is None:
            _say(f"Чертёж жгута {row.project} {row.item_index} ({row.id}) без байтов — начальная версия не записана")
            continue
        content = bytes(row.content)
        initial.append({
            "id": uuid4(), "document_id": row.id if isinstance(row.id, UUID) else UUID(str(row.id)), "revision": 1,
            "content": content, "content_sha256": hashlib.sha256(content).hexdigest(), "size": len(content),
            "kind": "initial", "restored_from": None, "saved_by": row.uploaded_by or "",
            "saved_at": row.uploaded_at if isinstance(row.uploaded_at, datetime) else datetime.fromisoformat(str(row.uploaded_at)),
        })
    if initial:
        bind.execute(revisions.insert(), initial)
    _say(f"Чертежи жгута: строк КД {len(rows)}, начальных версий записано {len(initial)}")


def _say(text: str) -> None:
    """В вывод миграции (на Windows вывод в трубу идёт в ANSI — символ вне неё не должен валить миграцию)."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "backslashreplace").decode("ascii"))


def downgrade() -> None:
    op.drop_table(TABLE)

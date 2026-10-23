"""Производитель отдельно от артикула (owner 23.10.2026): «XHP-4@JST» → article «XHP-4», manufacturer «JST».

«можем вырезать производителя из артикула и писать его отдельно … дели старые партии тоже». Kits, BOMs, the reference
DB and receiving hold the bare article, so the lots of the old warehouse that carry «@MAKER» (with «##6000» or
«упаковка 6000 шт» on some) did not match them by article.

- `warehouse_article_split_log` — one row per rewritten lot (lot_id PK, FK → warehouse_lots ON DELETE RESTRICT): the
  article, manufacturer and description before and after. The lot gets the bare article; the maker only into an EMPTY
  manufacturer (a filled one is kept); a note after «@MAKER,» and the pack size go to «Описание» (appended once); the
  lot version grows by one, so a form opened before the migration can't save the old article back.
- `warehouse_article_split_line_log` — free kit requests (no project — lines taken from a lot in «Выдача со склада»)
  copied the lot's article into the line: such a line (bound to the lot, article = the lot's old one) gets the new one
  too, its request version grows. Lines of a project's kit carry the spec row's article — the key the techcard reserve
  and «Свободно/В комплектации» match the spec by — and stay as they are.
- Left as they are on purpose: journals and snapshots (audit log, outbox events, `reference_stock_links`, write-offs —
  they have no article column, label print jobs' TSPL), receiving lines (the purchase's own text), the techcard reserve
  rows (`fulfillment_kit_reserves.article` = the spec row's article) and the purchasing items.

The split is a frozen copy of `app/shared/article_maker.py` as of this revision (a migration never imports app code
that may change): only a trailing «@token» of 2..20 letters, digits, spaces, «&», «-» is a maker; «@MOLEX SH12501-7Y
china (на тесты)» stays. Rows are read once, the log is written in chunks, the rewrite is set-based SQL over the log
(PostgreSQL and SQLite alike).

Not destructive: nothing is deleted, every rewritten value is kept in the logs. DESTRUCTIVE = False. Downgrade puts
the old values back from the logs (a value someone edited since is left alone), bumps the versions again and drops
the logs.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import NamedTuple, Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0146_split_article_maker"
down_revision: str | None = "0145_warehouse_scan_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False
LOG = "warehouse_article_split_log"
LINE_LOG = "warehouse_article_split_line_log"
_CHUNK = 500
_MANUFACTURER_MAX = 255
_DESCRIPTION_MAX = 4096


# --- frozen copy of app/shared/article_maker.py (23.10.2026) -------------------------------------------------------
_PACK_WORDS = re.compile(r"[\s,;]*упаковк[аеиу]\s*(?:по\s*)?(\d[\d\s]*?)\s*шт\.?\s*\Z", re.IGNORECASE)
_PACK_HASH = re.compile(r"\s*##\s*(\d+)\s*\Z")
_MAKER = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё &\-]{0,18}[0-9A-Za-zА-Яа-яЁё]")
_LETTER = re.compile(r"[A-Za-zА-Яа-яЁё]")
_LATIN = re.compile(r"[A-Za-z]")
_TWINS = str.maketrans("АВЕКМНОРСТХУаеорсху", "ABEKMHOPCTXYaeopcxy")
_ALIASES = {"CONFLY": "CONNFLY", "CONDAR": "SCONDAR", "CHINA": "Китай", "КИТАЙ": "Китай"}


class _Split(NamedTuple):
    article: str
    maker: str
    pack_qty: int | None
    note: str


def _normalize_maker(value: str) -> str:
    text = " ".join(str(value or "").split()).strip(" .,;:")
    if not text:
        return ""
    if _LATIN.search(text):
        text = text.translate(_TWINS)
    text = text.upper()
    return _ALIASES.get(text, text)


def _pack(digits: str) -> int | None:
    number = re.sub(r"\s", "", digits)
    return int(number) if number.isdigit() and int(number) > 0 else None


def _split(text: object) -> _Split:
    value = str(text or "").strip()
    pack: int | None = None
    note = ""
    maker = ""
    words = _PACK_WORDS.search(value)
    if words and words.start() > 0:
        pack = _pack(words.group(1))
        value = value[: words.start()].rstrip()
    at = value.rfind("@")
    if at > 0:
        head, tail = value[:at], value[at + 1:]
        name, _, rest = tail.partition(",")
        name = name.strip().rstrip(".;:")
        if "@" not in head and head.strip() and _MAKER.fullmatch(name) and _LETTER.search(name):
            maker = _normalize_maker(name)
            note = " ".join(rest.split())
            value = head.rstrip()
    hashed = _PACK_HASH.search(value)
    if hashed and hashed.start() > 0:
        pack = pack or _pack(hashed.group(1))
        value = value[: hashed.start()].rstrip()
    return _Split(value, maker, pack, note)


def _extras(split: _Split) -> str:
    parts = [split.note] if split.note else []
    if split.pack_qty:
        parts.append(f"упаковка {split.pack_qty} шт")
    return "; ".join(parts)


def _with_extras(description: str, extras: str) -> str:
    current = str(description or "").strip()
    if not extras or extras.casefold() in current.casefold():
        return current
    return f"{current}; {extras}" if current else extras
# --- end of the frozen copy ----------------------------------------------------------------------------------------


_lots = sa.table(
    "warehouse_lots",
    sa.column("id", sa.Uuid()), sa.column("version", sa.Integer()), sa.column("article", sa.String()),
    sa.column("manufacturer", sa.String()), sa.column("description", sa.String()),
)
_log = sa.table(
    LOG,
    sa.column("lot_id", sa.Uuid()), sa.column("old_article", sa.String()), sa.column("new_article", sa.String()),
    sa.column("old_manufacturer", sa.String()), sa.column("new_manufacturer", sa.String()),
    sa.column("old_description", sa.Text()), sa.column("new_description", sa.Text()),
    sa.column("migrated_at", sa.DateTime(timezone=True)),
)


def _plan(row) -> dict | None:
    """The lot's new values, or None when the split changes nothing."""
    old_article, old_manufacturer = row.article or "", row.manufacturer or ""
    old_description = row.description or ""
    split = _split(old_article)
    if not split.article or (not split.maker and not _extras(split)):
        return None  # no maker, pack or note: the article stays exactly as written (spaces included)
    new_manufacturer = old_manufacturer if old_manufacturer.strip() else split.maker[:_MANUFACTURER_MAX]
    new_description = _with_extras(old_description, _extras(split))
    if len(new_description) > _DESCRIPTION_MAX:
        new_description = old_description
    if (split.article, new_manufacturer, new_description) == (old_article, old_manufacturer, old_description):
        return None
    return {
        "lot_id": row.id, "old_article": old_article, "new_article": split.article,
        "old_manufacturer": old_manufacturer, "new_manufacturer": new_manufacturer,
        "old_description": old_description, "new_description": new_description,
    }


def upgrade() -> None:
    op.create_table(
        LOG,
        sa.Column(
            "lot_id", sa.Uuid(),
            sa.ForeignKey("warehouse_lots.id", ondelete="RESTRICT", name="fk_warehouse_article_split_log_lot_id"),
            primary_key=True,
        ),
        sa.Column("old_article", sa.String(1024), nullable=False),
        sa.Column("new_article", sa.String(1024), nullable=False),
        sa.Column("old_manufacturer", sa.String(255), nullable=False),
        sa.Column("new_manufacturer", sa.String(255), nullable=False),
        sa.Column("old_description", sa.Text(), nullable=False),
        sa.Column("new_description", sa.Text(), nullable=False),
        sa.Column("migrated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        LINE_LOG,
        # A line deleted later (its request edited or removed) takes its log row with it: nothing to restore.
        sa.Column(
            "line_id", sa.Uuid(),
            sa.ForeignKey("fulfillment_lines.id", ondelete="CASCADE", name="fk_warehouse_article_split_line_log_line"),
            primary_key=True,
        ),
        sa.Column(
            "lot_id", sa.Uuid(),
            sa.ForeignKey("warehouse_lots.id", ondelete="RESTRICT", name="fk_warehouse_article_split_line_log_lot"),
            nullable=False,
        ),
        sa.Column("old_article", sa.String(255), nullable=False),
        sa.Column("new_article", sa.String(255), nullable=False),
        sa.Column("migrated_at", sa.DateTime(timezone=True), nullable=False),
    )
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    # Every lot is read (hundreds of rows): the frozen rule, not a LIKE prefilter, decides which ones change.
    lots = bind.execute(
        sa.select(_lots.c.id, _lots.c.article, _lots.c.manufacturer, _lots.c.description).order_by(_lots.c.id)
    ).all()
    plans = [plan for row in lots if (plan := _plan(row)) is not None]
    for start in range(0, len(plans), _CHUNK):
        bind.execute(_log.insert(), [{**plan, "migrated_at": now} for plan in plans[start:start + _CHUNK]])

    # The lots: every column from its log row, only while the lot still holds the article the plan was made from.
    bind.execute(sa.text(f"""
        UPDATE warehouse_lots SET
            article = (SELECT s.new_article FROM {LOG} s WHERE s.lot_id = warehouse_lots.id),
            manufacturer = (SELECT s.new_manufacturer FROM {LOG} s WHERE s.lot_id = warehouse_lots.id),
            description = (SELECT s.new_description FROM {LOG} s WHERE s.lot_id = warehouse_lots.id),
            version = version + 1
        WHERE id IN (SELECT s.lot_id FROM {LOG} s WHERE s.old_article = warehouse_lots.article)
    """))

    # Lines of free requests that copied the lot's article.
    bind.execute(sa.text(f"""
        INSERT INTO {LINE_LOG} (line_id, lot_id, old_article, new_article, migrated_at)
        SELECT l.id, l.lot_id, l.article, s.new_article, :now
        FROM fulfillment_lines l
        JOIN {LOG} s ON s.lot_id = l.lot_id
        JOIN fulfillment_requests r ON r.id = l.request_id
        WHERE r.project_key IS NULL AND s.new_article <> s.old_article AND TRIM(l.article) = TRIM(s.old_article)
    """), {"now": now})
    bind.execute(sa.text(f"""
        UPDATE fulfillment_requests SET version = version + 1
        WHERE id IN (SELECT l.request_id FROM fulfillment_lines l JOIN {LINE_LOG} g ON g.line_id = l.id)
    """))
    bind.execute(sa.text(f"""
        UPDATE fulfillment_lines
        SET article = (SELECT g.new_article FROM {LINE_LOG} g WHERE g.line_id = fulfillment_lines.id)
        WHERE id IN (SELECT g.line_id FROM {LINE_LOG} g)
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(f"""
        UPDATE fulfillment_requests SET version = version + 1
        WHERE id IN (
            SELECT l.request_id FROM fulfillment_lines l JOIN {LINE_LOG} g ON g.line_id = l.id
            WHERE l.article = g.new_article
        )
    """))
    bind.execute(sa.text(f"""
        UPDATE fulfillment_lines
        SET article = (SELECT g.old_article FROM {LINE_LOG} g WHERE g.line_id = fulfillment_lines.id)
        WHERE id IN (SELECT g.line_id FROM {LINE_LOG} g WHERE g.new_article = fulfillment_lines.article)
    """))
    # Each column back only where it still holds the migrated value: a later hand edit is kept.
    for column in ("manufacturer", "description", "article"):
        bind.execute(sa.text(f"""
            UPDATE warehouse_lots
            SET {column} = (SELECT s.old_{column} FROM {LOG} s WHERE s.lot_id = warehouse_lots.id)
            WHERE id IN (SELECT s.lot_id FROM {LOG} s WHERE s.new_{column} = warehouse_lots.{column})
        """))
    bind.execute(sa.text(f"UPDATE warehouse_lots SET version = version + 1 WHERE id IN (SELECT lot_id FROM {LOG})"))
    op.drop_table(LINE_LOG)
    op.drop_table(LOG)

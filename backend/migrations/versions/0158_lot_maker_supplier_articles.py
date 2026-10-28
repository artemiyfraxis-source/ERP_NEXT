"""Три артикула у партии склада (owner 28.10.2026: «"Артикул производителя" — артикул, с которым позиция вышла с
завода; "Артикул поставщика" — артикул, под которым позиция в продаже у поставщика; "Артикул номенклатуры" — наш
общепринятый артикул»).

- `warehouse_lots.article` stays as it is: it IS «Артикул номенклатуры» (techcards, requests, kitting match on it).
- `warehouse_lots.maker_article` — «Артикул производителя», '' — not known.
- `warehouse_lots.supplier_article` — «Артикул поставщика», '' — not known.

Backfill — only what is certain, everything else stays '':
* `maker_article = article` for a lot that came through «Приёмка» (a `source_receiving_ids` entry is a receiving line,
  «<uuid>» or «<uuid>:r<n>») whose procurement line is bound to a purchasing nomenclature item with a part number, and
  the lot's article IS that part number (the same `code_key`: spaces, dashes, dots, case and look-alikes dropped);
* `supplier_article` for a lot accepted with a delivery whose УПД reading is kept (`warehouse_receipts.scan_file` →
  `warehouse_receipt_files.recognition`) when exactly one supplier code («Код товара») stands on the reading's rows
  printed with the lot's «Наим. по УПД» (the same text, spaces collapsed, case folded).
No lot is renamed, no row is added or removed. Non-destructive: DESTRUCTIVE = False. Downgrade drops the two columns.
"""

import json
import re
from typing import Sequence
from uuid import UUID

from alembic import op
import sqlalchemy as sa


revision: str = "0158_lot_maker_supplier_articles"
down_revision: str | None = "0157_warehouse_scan_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

RECEIPT_SCAN_PATH = "/api/stock/receipts/files/"
LIMIT = 255
# Frozen copy of `procurement.document_match.code_key` as of 28.10.2026 (what purchasing_items.part_key holds).
_HOMOGLYPHS = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")


def _code_key(value) -> str:
    return re.sub(r"[\s\-‐‑‒–—_./\\]", "", str(value or "")).upper().translate(_HOMOGLYPHS)


def _plain(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def _uuid(text) -> UUID | None:
    try:
        return UUID(str(text).split(":", 1)[0])
    except (ValueError, AttributeError):
        return None


lots = sa.table(
    "warehouse_lots",
    sa.column("id", sa.Uuid()), sa.column("article", sa.String()), sa.column("source_receiving_ids", sa.JSON()),
    sa.column("receipt_id", sa.Uuid()), sa.column("upd_name", sa.Text()),
    sa.column("maker_article", sa.String()), sa.column("supplier_article", sa.String()),
)
receiving = sa.table("receiving_lines", sa.column("id", sa.Uuid()), sa.column("procurement_line_id", sa.Uuid()))
line_items = sa.table("purchasing_line_items", sa.column("procurement_line_id", sa.Uuid()), sa.column("item_id", sa.Uuid()))
items = sa.table(
    "purchasing_items", sa.column("id", sa.Uuid()), sa.column("part_number", sa.String()), sa.column("part_key", sa.String()),
)
receipts = sa.table("warehouse_receipts", sa.column("id", sa.Uuid()), sa.column("scan_file", sa.String()))
files = sa.table("warehouse_receipt_files", sa.column("id", sa.Uuid()), sa.column("recognition", sa.Text()))


def _maker_keys(bind) -> dict[UUID, set[str]]:
    """receiving line id → the code keys of the part number of its purchasing item."""
    found: dict[UUID, set[str]] = {}
    rows = bind.execute(
        sa.select(receiving.c.id, items.c.part_number, items.c.part_key)
        .select_from(receiving.join(line_items, line_items.c.procurement_line_id == receiving.c.procurement_line_id)
                     .join(items, items.c.id == line_items.c.item_id))
    )
    for line_id, part_number, part_key in rows:
        keys = {key for key in (_code_key(part_number), _code_key(part_key)) if key}
        if keys:
            found.setdefault(line_id, set()).update(keys)
    return found


def _supplier_codes(bind) -> dict[UUID, dict[str, set[str]]]:
    """receipt id → printed row name (plain) → the supplier codes of the rows printed so."""
    found: dict[UUID, dict[str, set[str]]] = {}
    rows = bind.execute(
        sa.select(receipts.c.id, receipts.c.scan_file).where(receipts.c.scan_file.like(RECEIPT_SCAN_PATH + "%"))
    )
    for receipt_id, link in rows:
        file_id = _uuid(link[len(RECEIPT_SCAN_PATH):])
        if file_id is None:
            continue
        recognition = bind.execute(sa.select(files.c.recognition).where(files.c.id == file_id)).scalar()
        try:
            lines = json.loads(recognition or "")["lines"]
        except (ValueError, KeyError, TypeError):
            continue
        by_name: dict[str, set[str]] = {}
        for line in lines if isinstance(lines, list) else ():
            if not isinstance(line, dict):
                continue
            name = _plain(line.get("name"))
            code = " ".join(str(line.get("supplier_code") or "").split())
            if name:
                by_name.setdefault(name, set()).add(code)
        if by_name:
            found[receipt_id] = by_name
    return found


def upgrade() -> None:
    with op.batch_alter_table("warehouse_lots") as batch:
        batch.add_column(sa.Column("maker_article", sa.String(LIMIT), nullable=False, server_default=""))
        batch.add_column(sa.Column("supplier_article", sa.String(LIMIT), nullable=False, server_default=""))
    bind = op.get_bind()
    maker_keys = _maker_keys(bind)
    supplier_codes = _supplier_codes(bind)
    rows = bind.execute(
        sa.select(lots.c.id, lots.c.article, lots.c.source_receiving_ids, lots.c.receipt_id, lots.c.upd_name)
    ).all()
    for lot_id, article, sources, receipt_id, upd_name in rows:
        values: dict[str, str] = {}
        key = _code_key(article)
        if key and len(str(article).strip()) <= LIMIT:
            if isinstance(sources, str):
                try:
                    sources = json.loads(sources)
                except ValueError:
                    sources = []
            for source in sources if isinstance(sources, list) else ():
                line_id = _uuid(source)
                if line_id is not None and key in maker_keys.get(line_id, ()):
                    values["maker_article"] = str(article).strip()
                    break
        codes = supplier_codes.get(receipt_id, {}).get(_plain(upd_name)) if receipt_id and _plain(upd_name) else None
        if codes and len(codes) == 1:
            [code] = codes
            if code and len(code) <= LIMIT:
                values["supplier_article"] = code
        if values:
            bind.execute(sa.update(lots).where(lots.c.id == lot_id).values(**values))


def downgrade() -> None:
    with op.batch_alter_table("warehouse_lots") as batch:
        batch.drop_column("supplier_article")
        batch.drop_column("maker_article")

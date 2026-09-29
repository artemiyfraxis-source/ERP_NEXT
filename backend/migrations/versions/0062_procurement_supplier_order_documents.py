"""Документ запроса поставщику в хранилище ERP (SupplierRequest.gs: копия шаблона-Google Doc на заказ).

Файл лежит в `procurement_supplier_order_documents` (байты, имя «PO-NNN от ДД.ММ.ГГГГ.html», папка
«Запросы поставщикам/год/месяц/поставщик»), как УПД отгрузки в `shipping_files`. Уже выданным заказам
документ собирается из сохранённого текста письма — снимок правил на момент миграции, без импорта кода модуля.
"""

import html
import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0062_procurement_supplier_order_documents"
down_revision: str | None = "0061_procurement_traceability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONTHS = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)
_CSS = (
    "@page{size:A4;margin:2.54cm}"
    "body{font-family:Arial,Helvetica,sans-serif;font-size:11pt;line-height:1.15;color:#000;"
    "max-width:16cm;margin:2.54cm auto;background:#fff}"
    "p{margin:0;white-space:pre-wrap;tab-size:4}"
    "@media print{body{margin:0;max-width:none}}"
)


def _folder(day, supplier: str) -> str:
    name = " ".join(re.sub(r'[\\/:*?"<>|]+', " ", supplier or "").split()) or "Без поставщика"
    return f"Запросы поставщикам/{day.year}/{day.month:02d} {_MONTHS[day.month - 1]}/{name}"


def _html(title: str, letter: str) -> bytes:
    paragraphs = "".join(f"<p>{html.escape(line) if line else '&nbsp;'}</p>" for line in letter.split("\n"))
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>{paragraphs}</body></html>"
    ).encode("utf-8")


def upgrade() -> None:
    documents = op.create_table(
        "procurement_supplier_order_documents",
        sa.Column(
            "order_number",
            sa.String(32),
            sa.ForeignKey("procurement_supplier_orders.order_number", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("folder", sa.String(1024), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    orders = sa.table(
        "procurement_supplier_orders",
        sa.column("order_number", sa.String),
        sa.column("order_date", sa.Date),
        sa.column("supplier", sa.String),
        sa.column("buyer_name", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("letter", sa.Text),
    )
    rows = []
    for order in op.get_bind().execute(sa.select(orders)).mappings():
        title = f"{order['order_number']} от {order['order_date']:%d.%m.%Y}"
        content = _html(title, order["letter"] or "")
        rows.append({
            "order_number": order["order_number"],
            "file_name": f"{title}.html",
            "folder": _folder(order["order_date"], order["supplier"]),
            "content_type": "text/html; charset=utf-8",
            "size": len(content),
            "content": content,
            "created_by": order["buyer_name"],
            "created_at": order["created_at"],
        })
    if rows:
        op.bulk_insert(documents, rows)


def downgrade() -> None:
    op.drop_table("procurement_supplier_order_documents")

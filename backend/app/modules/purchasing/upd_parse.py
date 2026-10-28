"""The УПД registry's side of the ERP's document reader: the parser's verdicts of the registry and a reading as the
registry's columns (money in kopecks).

The reader itself — the prompt, the kinds (УПД, СФ, ТН+СФ, ТОРГ-12, акт, М-15, КСФ), normalization and the document
checks, ported from Roller_Buyer's Google parser — moved to `app.shared.doc_reader.closing` (Витя, 26.10.2026: one
parser for the warehouse scan and the registry); its names are re-exported here for the registry's callers. Pure: the
calls live in `app/workflows/upd_pdf_parse.py`, the writes in `upd_pdf.py` and `upd_scans.py`."""

from __future__ import annotations

from typing import Any

from app.shared.doc_reader.closing import (  # noqa: F401 — re-exported for the registry's callers
    CHECK,
    KIND_LABELS,
    OK,
    OTHER,
    OWN_INN,
    TAG_LABELS,
    TEXT_LAYER_LIMIT,
    LineMoney,
    ParsedDoc,
    ParsedLine,
    closing_doc,
    decimal,
    file_amount,
    file_tag,
    is_service_line,
    kopecks,
    normalize,
    parse_date,
    parse_num,
    parse_vat_rate,
    prompt,
    repair_line,
)

# The registry's waiting verdicts (upd_checks reads both as «waiting») and a parse that did not happen.
QUEUED = "В очереди"
PARSING = "Разбирается"
ERROR = "Ошибка"


def header_values(doc: ParsedDoc) -> dict[str, Any]:
    """The document's parser columns of `purchasing_upd_documents` (money in kopecks)."""
    return {
        "kind": KIND_LABELS[doc.kind], "issues": "; ".join(doc.issues), "number": doc.number[:128],
        "doc_date": doc.doc_date, "seller_name": doc.seller_name[:512], "seller_inn": doc.seller_inn[:32],
        "seller_kpp": doc.seller_kpp[:32], "buyer_inn": doc.buyer_inn[:32], "basis": doc.basis,
        "payment_doc": doc.payment_doc, "total_net": kopecks(doc.total_net), "total_vat": kopecks(doc.total_vat),
        "total_gross": kopecks(doc.total_gross), "file_amount": kopecks(doc.file_amount),
        "line_count": len(doc.lines), "status": doc.status,
    }


def line_values(line: ParsedLine) -> dict[str, Any]:
    """One line's columns of `purchasing_upd_lines` (unit price in rubles, sums in kopecks)."""
    return {
        "seq": line.seq, "name": line.name, "article": line.article, "product_type_code": line.product_type_code[:64],
        "unit_code": line.unit_code[:16], "unit": line.unit[:64], "quantity": decimal(line.qty),
        "price": decimal(line.price), "net": kopecks(line.net), "excise": line.excise[:64],
        "vat_rate": line.vat_rate[:16], "vat": kopecks(line.vat), "gross": kopecks(line.gross),
        "country_code": line.country_code[:16], "country": line.country[:128], "declaration": line.declaration[:255],
        "is_service": line.is_service, "issues": "; ".join(line.issues), "fixes": "; ".join(line.fixes),
    }

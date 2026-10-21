"""Roller_Buyer → ERP purchasing: suppliers, nomenclature, supplier aliases, reference prices, the UPD registry, Kaiten
invoices with their files, the buyer's invoice ↔ UPD decisions (stage of Витя, 13.10.2026).

    ERP_DATABASE_URL=… python -m app.modules.purchasing.rb_import --snapshot <copy of buyer.db> --data-dir <DATA_DIR> [--apply]

`--snapshot` must be a COPY of Roller_Buyer's database (made with the SQLite backup API or with the -wal beside it):
it is opened read-only, and the live `Roller_Buyer-data\\buyer.db` is refused. `--data-dir` is Roller_Buyer's DATA_DIR:
the UPD PDFs (upd\\), EDO XML (edo\\) and Kaiten files (kaiten\\) are read from it, never written. Without `--apply`
everything is done in a transaction that is rolled back — the counts say what would happen.

Re-running is safe: suppliers match by INN, then by name key; items by part number key, then by name + unit; documents
and invoices by their keys. The parser's fields are refreshed, the buyer's own word in ERP (a corrected UPD header,
«Проверено», a chosen supplier or project) is kept. Reference prices of a source are replaced as a whole. Our own
company (INN 5040191181) is never imported as a supplier."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from . import suppliers as supplier_repo
from .infrastructure import (
    DocumentFileRow,
    InvoiceFileRow,
    InvoiceRow,
    ItemAliasRow,
    ItemRow,
    PriceRefRow,
    SupplierNameRow,
    SupplierRow,
    UpdDocumentRow,
    UpdInvoiceLinkRow,
    UpdLineRow,
)
from .items import alias_key, guess_category, part_key
from .upd_sellers import OWN_INNS

_UNITS = {"m": "м", "pcs": "шт", "pack": "уп", "kg": "кг", "l": "л", "set": "компл"}
_CATEGORIES = {"cable", "connector", "ekb", "other"}
_HEADER_KEYS = {
    "number": "number", "date": "date", "sellerName": "seller_name", "sellerInn": "seller_inn", "sellerKpp": "seller_kpp",
    "buyerInn": "buyer_inn", "totalNet": "total_net", "totalVat": "total_vat", "totalGross": "total_gross",
}
_LIVE_DB = re.compile(r"Roller_Buyer-data[\\/]+buyer\.db$", re.IGNORECASE)
ACTOR = Actor(id=uuid5(NAMESPACE_URL, "erp:purchasing:roller-buyer-import"), name="Импорт из Roller_Buyer", department="")


def _id(kind: str, value: Any) -> UUID:
    """Stable id of a row made from a Roller_Buyer row: the same id on every re-run."""
    return uuid5(NAMESPACE_URL, f"roller-buyer:{kind}:{value}")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _moment(value: Any, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        moment = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return fallback
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


@dataclass
class Report:
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    notes: list[str] = field(default_factory=list)

    def add(self, key: str, amount: int = 1) -> None:
        self.counts[key] += amount

    def lines(self) -> list[str]:
        return [f"{key}: {value}" for key, value in sorted(self.counts.items())] + self.notes[:40]


class Importer:
    def __init__(self, session: Session, snapshot: sqlite3.Connection, data_dir: Path, now: datetime):
        self.session, self.rb, self.data_dir, self.now = session, snapshot, data_dir, now
        self.report = Report()
        self.suppliers: dict[int, UUID | None] = {}
        self.items: dict[int, UUID] = {}
        self.files: dict[str, UUID] = {
            sha: file_id for sha, file_id in session.execute(select(DocumentFileRow.sha256, DocumentFileRow.id))
        }

    def rows(self, sql: str) -> list[sqlite3.Row]:
        return self.rb.execute(sql).fetchall()

    def columns(self, table: str) -> set[str]:
        return {row[1] for row in self.rb.execute(f"pragma table_info({table})")}

    # ---- directories -------------------------------------------------------------------------------------------

    def import_suppliers(self) -> None:
        by_inn = {row.inn: row.id for row in self.session.scalars(select(SupplierRow)) if row.inn}
        by_key = {row.name_key: row.supplier_id for row in self.session.scalars(select(SupplierNameRow))}
        by_key.update({row.name_key: row.id for row in self.session.scalars(select(SupplierRow))})
        for rb in self.rows("select * from suppliers order by id"):
            inn = supplier_repo.normalize_inn(rb["inn"]) if rb["inn"] else None
            if inn in OWN_INNS:
                self.suppliers[rb["id"]] = None
                self.report.add("поставщики: пропущены (наш ИНН)")
                continue
            if inn and not supplier_repo.inn_valid(inn):
                self.report.notes.append(f"поставщик «{rb['name']}»: ИНН {inn} не проходит проверку — без ИНН")
                inn = None
            key = supplier_repo.supplier_key(rb["name"])
            found = by_inn.get(inn) if inn else None
            if found is None and key in by_key:
                other = self.session.get(SupplierRow, by_key[key])
                if other is not None and (not inn or not other.inn or other.inn == inn):
                    found = other.id
            if found is not None:
                self.suppliers[rb["id"]] = found
                self.report.add("поставщики: совпали с уже заведёнными")
                continue
            name = _clean(rb["name"])
            if key in by_key:  # the same name, another INN: a different organisation
                name = f"{name} (ИНН {inn})"
            categories = [c for c in json.loads(rb["categories"] or "[]") if c in supplier_repo.CATEGORIES]
            notes = "\n".join(part for part in (_clean(rb["contacts"]), str(rb["notes"] or "").strip()) if part)
            row = supplier_repo.create(self.session, {
                "name": name, "inn": inn, "categories": categories, "is_key": bool(rb["is_key"]),
                "site": rb["site"] or None, "notes": (f"Из Roller_Buyer. {notes}" if notes else "Из Roller_Buyer"),
            }, ACTOR.id, self.now)
            by_key[row.name_key] = row.id
            if inn:
                by_inn[inn] = row.id
            self.suppliers[rb["id"]] = row.id
            self.report.add("поставщики: заведены")

    def import_items(self) -> None:
        by_part: dict[str, UUID] = {}
        by_name: dict[tuple[str, str], UUID] = {}
        for row in self.session.scalars(select(ItemRow)):
            if row.part_key:
                by_part[row.part_key] = row.id
            by_name.setdefault((alias_key(row.name), row.unit), row.id)
        new_rows: list[dict] = []
        for rb in self.rows("select * from items order by id"):
            name = _clean(rb["name"])[:512]
            if not name:
                continue
            unit = _UNITS.get(rb["unit"], rb["unit"])
            part = _clean(rb["part_number"])[:255] or None
            key = part_key(part)
            found = by_part.get(key) if key else by_name.get((alias_key(name), unit))
            if found is not None:
                self.items[rb["id"]] = found
                self.report.add("номенклатура: совпала с уже заведённой")
                continue
            item_id = _id("item", rb["id"])
            category = rb["category"] if rb["category"] in _CATEGORIES else guess_category(name, rb["type"])
            new_rows.append({
                "id": item_id, "name": name, "part_number": part, "part_key": key,
                "manufacturer": _clean(rb["manufacturer"])[:255] or None, "category": category,
                "item_type": _clean(rb["type"])[:128] or None, "unit": unit, "notes": None, "archived_at": None,
                "version": 1, "created_at": _moment(rb["created_at"], self.now), "created_by": None,
                "updated_at": None, "updated_by": None,
            })
            if key:
                by_part[key] = item_id
            else:
                by_name[(alias_key(name), unit)] = item_id
            self.items[rb["id"]] = item_id
        for start in range(0, len(new_rows), 500):
            self.session.execute(insert(ItemRow.__table__), new_rows[start:start + 500])
        self.report.add("номенклатура: заведена", len(new_rows))

    def import_aliases(self) -> None:
        taken = {(row.supplier_id, row.alias_key) for row in self.session.scalars(select(ItemAliasRow))}
        for rb in self.rows("select * from item_aliases order by id"):
            item_id = self.items.get(rb["item_id"])
            supplier_id = self.suppliers.get(rb["supplier_id"])
            key = alias_key(rb["key"])
            if item_id is None or not key or (supplier_id, key) in taken:
                self.report.add("названия у поставщиков: пропущены (уже есть или без позиции)")
                continue
            taken.add((supplier_id, key))
            self.session.add(ItemAliasRow(
                id=_id("alias", rb["id"]), item_id=item_id, supplier_id=supplier_id, alias_key=key[:512],
                alias_text=_clean(rb["key"])[:512], created_at=self.now, created_by=None,
            ))
            self.report.add("названия у поставщиков: добавлены")
        self.session.flush()

    def import_price_refs(self) -> None:
        sources = [row["source"] for row in self.rows("select distinct source from price_refs")]
        if sources:
            removed = self.session.execute(delete(PriceRefRow).where(PriceRefRow.source.in_(sources))).rowcount
            self.report.add("справочные цены: заменены старые", removed or 0)
        values = []
        for rb in self.rows("select * from price_refs order by id"):
            item_id = self.items.get(rb["item_id"])
            if item_id is None:
                self.report.add("справочные цены: без позиции — пропущены")
                continue
            values.append({
                "id": _id("price", rb["id"]), "item_id": item_id, "supplier_id": self.suppliers.get(rb["supplier_id"]),
                "supplier_name": _clean(rb["supplier_name"])[:512], "offered_part": _clean(rb["offered_part"])[:255],
                "offered_manufacturer": _clean(rb["offered_manufacturer"])[:255], "currency": rb["currency"],
                "unit_price4": int(rb["unit_price4"]), "vat_included": bool(rb["vat_included"]),
                "quantity": _decimal(rb["qty"]), "lead_time": _clean(rb["lead_time"])[:128], "source": rb["source"],
                "source_row": rb["source_row"], "imported_at": _moment(rb["imported_at"], self.now),
            })
        for start in range(0, len(values), 500):
            self.session.execute(insert(PriceRefRow.__table__), values[start:start + 500])
        self.report.add("справочные цены: загружены", len(values))

    # ---- files ---------------------------------------------------------------------------------------------------

    def stored_file(self, relative: str, name: str) -> UUID | None:
        path = (self.data_dir / relative.replace("\\", "/")).resolve()
        if self.data_dir.resolve() not in path.parents or not path.is_file():
            self.report.add("файлы: нет на диске")
            return None
        content = path.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        if sha in self.files:
            self.report.add("файлы: уже в базе")
            return self.files[sha]
        file_id = _id("file", sha)
        self.session.execute(insert(DocumentFileRow.__table__).values(
            id=file_id, sha256=sha, file_name=name[:1024], size=len(content), content=content, created_at=self.now,
        ))
        self.files[sha] = file_id
        self.report.add("файлы: загружены")
        self.report.add("файлы: байт", len(content))
        return file_id

    # ---- the UPD registry ----------------------------------------------------------------------------------------

    def import_documents(self) -> None:
        existing = {row.id: row for row in self.session.scalars(select(UpdDocumentRow))}
        lines_by_doc: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for line in self.rows("select * from upd_lines order by file_id, source, seq, id"):
            lines_by_doc[line["file_id"]].append(line)
        for rb in self.rows("select * from upd_documents order by file_id"):
            doc_id = rb["file_id"]
            if rb["origin"] == "edo":
                file_id = self.stored_file(rb["path"], rb["file_name"]) if rb["path"] else None
                folder = "edo"
            else:
                file_id = self.stored_file(f"upd/{rb['pdf_file']}", rb["file_name"]) if rb["pdf_file"] else None
                folder = rb["path"] or ""
            manual = json.loads(rb["manual_header"]) if rb["manual_header"] else None
            parsed = {
                "origin": rb["origin"], "file_name": rb["file_name"] or doc_id, "folder": folder[:255], "md5": rb["md5"] or "",
                "file_id": file_id, "status": rb["google_status"], "kind": rb["kind"] or "", "tag": rb["tag"] or "",
                "issues": rb["issues"] or "", "model": rb["model"] or "", "number": rb["number"] or "",
                "doc_date": _day(rb["date"]), "seller_name": rb["seller_name"] or "", "seller_inn": rb["seller_inn"] or "",
                "seller_kpp": rb["seller_kpp"] or "", "buyer_inn": rb["buyer_inn"] or "", "basis": rb["basis"] or "",
                "payment_doc": rb["payment_doc"] or "", "total_net": rb["total_net"], "total_vat": rb["total_vat"],
                "total_gross": rb["total_gross"], "file_amount": rb["file_amount"], "line_count": rb["line_count"] or 0,
                "dup_of": rb["dup_of"], "drive_url": rb["drive_url"] or "", "parser_updated": rb["google_updated"] or "",
                "synced_at": _moment(rb["synced_at"], self.now), "twin_of": rb["twin_of"],
            }
            own = {
                "manual_header": {_HEADER_KEYS[k]: v for k, v in manual.items() if k in _HEADER_KEYS} if manual else None,
                "manual_saved_at": _moment(rb["manual_saved_at"], self.now) if rb["manual_saved_at"] else None,
                "manual_based_on": rb["manual_based_on"],
                "verified_at": _moment(rb["verified_at"], self.now) if rb["verified_at"] else None,
                "verified_parser_updated": rb["verified_google_updated"],
                "supplier_id": self.suppliers.get(rb["supplier_id"]),
            }
            row = existing.get(doc_id)
            if row is None:
                row = UpdDocumentRow(id=doc_id, created_at=self.now, **parsed, **own)
                self.session.add(row)
                self.report.add("УПД: добавлены")
            else:
                for key, value in parsed.items():
                    setattr(row, key, value)
                # The buyer's word given in ERP stays; Roller_Buyer's fills only what ERP does not have.
                if row.manual_header is None and own["manual_header"] is not None:
                    row.manual_header, row.manual_saved_at, row.manual_based_on = (
                        own["manual_header"], own["manual_saved_at"], own["manual_based_on"])
                if row.verified_at is None and own["verified_at"] is not None:
                    row.verified_at, row.verified_parser_updated = own["verified_at"], own["verified_parser_updated"]
                if row.supplier_id is None:
                    row.supplier_id = own["supplier_id"]
                self.report.add("УПД: обновлены")
            self.session.flush()
            self.import_lines(doc_id, lines_by_doc.get(doc_id, []))

    def import_lines(self, doc_id: str, lines: list[sqlite3.Row]) -> None:
        by_source: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for line in lines:
            by_source[line["source"]].append(line)
        has_manual = self.session.scalar(
            select(UpdLineRow.id).where(UpdLineRow.document_id == doc_id, UpdLineRow.source == "manual").limit(1)
        ) is not None
        for source, rows in by_source.items():
            if source == "manual" and has_manual:
                continue  # corrected in ERP: kept
            self.session.execute(delete(UpdLineRow).where(UpdLineRow.document_id == doc_id, UpdLineRow.source == source))
            # The parser sometimes numbers two different lines alike («1», «1»): then the lines are numbered in order.
            renumber = len({row["seq"] for row in rows}) != len(rows)
            if renumber:
                self.report.add("УПД: документы с перенумерованными строками")
            values = []
            for position, rb in enumerate(rows, start=1):
                values.append({
                    "id": _id("upd-line", rb["id"]), "document_id": doc_id, "source": source,
                    "seq": position if renumber else rb["seq"], "name": rb["name"] or "", "article": (rb["article"] or "")[:255],
                    "article_key": part_key(rb["article"]), "name_key": alias_key(rb["name"])[:512],
                    "product_type_code": rb["product_type_code"] or "", "unit_code": rb["unit_code"] or "",
                    "unit": rb["unit"] or "", "quantity": _decimal(rb["qty"]), "price": _decimal(rb["price"]),
                    "net": rb["net"], "excise": rb["excise"] or "", "vat_rate": rb["vat_rate"] or "", "vat": rb["vat"],
                    "gross": rb["gross"], "country_code": rb["country_code"] or "", "country": rb["country"] or "",
                    "declaration": rb["declaration"] or "", "is_service": bool(rb["is_service"]),
                    "issues": rb["issues"] or "", "fixes": rb["fixes"] or "",
                })
            if values:
                self.session.execute(insert(UpdLineRow.__table__), values)
            self.report.add("УПД: строки", len(values))

    # ---- invoices ------------------------------------------------------------------------------------------------

    def import_invoices(self) -> dict[int, UUID]:
        has_text = {"text", "lines"} <= self.columns("invoices")
        existing = {row.external_id: row for row in self.session.scalars(select(InvoiceRow).where(InvoiceRow.source == "kaiten"))}
        ids: dict[int, UUID] = {}
        for rb in self.rows("select * from invoices order by id"):
            values = {
                "title": rb["title"], "board_title": rb["board_title"] or "", "column_title": rb["column_title"] or "",
                "status": rb["status"], "card_created": _moment(rb["card_created"], self.now),
                "card_updated": _moment(rb["card_updated"], self.now), "number": rb["number"], "doc_date": _day(rb["date"]),
                "seller_name": rb["seller_name"], "seller_inn": rb["seller_inn"],
                "supplier_id": self.suppliers.get(rb["supplier_id"]), "total": rb["total"], "vat": rb["vat"],
                "vat_free": bool(rb["vat_free"]), "payment_numbers": (rb["payment_numbers"] or "")[:512],
                "payment_date": _day(rb["payment_date"]), "payment_sum": rb["payment_sum"],
                "parsed_file": rb["parsed_file_id"], "parse_issues": rb["parse_issues"] or "",
                "text": (rb["text"] or "") if has_text else "", "lines": json.loads(rb["lines"] or "[]") if has_text else [],
                "synced_at": _moment(rb["synced_at"], self.now),
            }
            row = existing.get(rb["external_id"])
            if row is None:
                row = InvoiceRow(id=_id("invoice", rb["external_id"]), source="kaiten", external_id=rb["external_id"], **values)
                self.session.add(row)
                self.report.add("счета: добавлены")
            else:
                for key, value in values.items():
                    setattr(row, key, value)
                self.report.add("счета: обновлены")
            self.session.flush()
            ids[rb["id"]] = row.id
            self.session.execute(delete(InvoiceFileRow).where(InvoiceFileRow.invoice_id == row.id))
            for seq, item in enumerate(json.loads(rb["files"] or "[]")):
                file_id = self.stored_file(item["path"], item["name"]) if item.get("path") else None
                self.session.execute(insert(InvoiceFileRow.__table__).values(
                    id=_id("invoice-file", f"{rb['external_id']}:{item['id']}"), invoice_id=row.id,
                    external_id=str(item["id"])[:64], name=str(item["name"])[:1024],
                    kind=item["kind"] if item.get("kind") in ("invoice", "payment", "other") else "other",
                    size=int(item.get("size") or 0), seq=seq, file_id=file_id,
                ))
                self.report.add("счета: файлы")
        return ids

    def import_links(self, invoices: dict[int, UUID]) -> None:
        taken = {(row.upd_id, row.invoice_id) for row in self.session.scalars(select(UpdInvoiceLinkRow))}
        for rb in self.rows("select * from upd_invoice_links order by id"):
            invoice_id = invoices.get(rb["invoice_id"])
            if invoice_id is None or (rb["upd_file_id"], invoice_id) in taken:
                continue
            self.session.add(UpdInvoiceLinkRow(
                id=_id("link", rb["id"]), upd_id=rb["upd_file_id"], invoice_id=invoice_id, decision=rb["decision"],
                decided_at=_moment(rb["decided_at"], self.now), decided_by=None,
            ))
            self.report.add("счёт ↔ УПД: решения закупщика")
        self.session.flush()

    def run(self) -> Report:
        self.import_suppliers()
        self.import_items()
        self.import_aliases()
        self.import_price_refs()
        self.import_documents()
        self.import_links(self.import_invoices())
        try:  # the registry's own pass: suppliers by the corrected seller INN, twins (upd_registry.py)
            from .upd_registry import relink_documents
        except ImportError:
            self.report.notes.append("сверка реестра (поставщики, дубли) не выполнена: нет upd_registry")
        else:
            relinked = relink_documents(self.session, self.now)
            self.report.notes.append(f"сверка реестра: {relinked}")
        return self.report


def resolve_suppliers(session: Session, snapshot: sqlite3.Connection) -> dict[int, UUID | None]:
    """Read-only: the ERP supplier of each Roller_Buyer supplier, matched as `Importer.import_suppliers` does (INN, then
    the name key when the INNs do not contradict). None — our own company, or not in ERP yet (run this import first)."""
    by_inn = {row.inn: row.id for row in session.scalars(select(SupplierRow)) if row.inn}
    by_key = {row.name_key: row.supplier_id for row in session.scalars(select(SupplierNameRow))}
    by_key.update({row.name_key: row.id for row in session.scalars(select(SupplierRow))})
    found: dict[int, UUID | None] = {}
    for rb in snapshot.execute("select id, name, inn from suppliers order by id"):
        inn = supplier_repo.normalize_inn(rb["inn"]) if rb["inn"] else None
        if inn in OWN_INNS:
            found[rb["id"]] = None
            continue
        if inn and not supplier_repo.inn_valid(inn):
            inn = None
        match = by_inn.get(inn) if inn else None
        key = supplier_repo.supplier_key(rb["name"])
        if match is None and key in by_key:
            other = session.get(SupplierRow, by_key[key])
            if other is not None and (not inn or not other.inn or other.inn == inn):
                match = other.id
        found[rb["id"]] = match
    return found


def resolve_items(session: Session, snapshot: sqlite3.Connection) -> dict[int, UUID]:
    """Read-only: the ERP nomenclature item of each Roller_Buyer item, matched as `Importer.import_items` does (part
    number key, else name + unit); an item missing in ERP is left out."""
    by_part: dict[str, UUID] = {}
    by_name: dict[tuple[str, str], UUID] = {}
    for row in session.scalars(select(ItemRow)):
        if row.part_key:
            by_part[row.part_key] = row.id
        by_name.setdefault((alias_key(row.name), row.unit), row.id)
    found: dict[int, UUID] = {}
    for rb in snapshot.execute("select id, name, part_number, unit from items order by id"):
        own = session.get(ItemRow, _id("item", rb["id"]))
        if own is not None:
            found[rb["id"]] = own.id
            continue
        key = part_key(_clean(rb["part_number"])[:255] or None)
        unit = _UNITS.get(rb["unit"], rb["unit"])
        match = by_part.get(key) if key else by_name.get((alias_key(_clean(rb["name"])[:512]), unit))
        if match is not None:
            found[rb["id"]] = match
    return found


def open_snapshot(path: Path) -> sqlite3.Connection:
    if _LIVE_DB.search(str(path)):
        raise SystemExit("Это живая база Roller_Buyer — нужна копия (npm run backup или SQLite backup API)")
    if not path.is_file():
        raise SystemExit(f"Нет файла {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    if connection.execute("pragma integrity_check").fetchone()[0] != "ok":
        raise SystemExit("Копия Roller_Buyer не проходит integrity_check")
    return connection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Roller_Buyer → ERP: поставщики, номенклатура, цены, УПД, счета")
    parser.add_argument("--snapshot", required=True, type=Path, help="копия buyer.db")
    parser.add_argument("--data-dir", required=True, type=Path, help="DATA_DIR Roller_Buyer (только чтение)")
    parser.add_argument("--apply", action="store_true", help="записать (без флага — пробный прогон с откатом)")
    args = parser.parse_args(argv)
    from app.database import build_engine, build_purchasing_uow_factory

    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        print("Задайте ERP_DATABASE_URL", file=sys.stderr)
        return 2
    snapshot = open_snapshot(args.snapshot)
    now = datetime.now(timezone.utc)
    uow = build_purchasing_uow_factory(build_engine(database_url))()
    with uow:
        report = Importer(uow.session, snapshot, args.data_dir, now).run()
        uow.audit.add(AuditEvent(
            id=_id("audit", now.isoformat()), aggregate_id=ACTOR.id, event_type="purchasing.roller_buyer_imported",
            actor=ACTOR, occurred_at=now, details={key: str(value) for key, value in report.counts.items()},
        ))
        if args.apply:
            uow.commit()
    print("\n".join(report.lines()))
    print("ЗАПИСАНО" if args.apply else "пробный прогон: ничего не записано (добавьте --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

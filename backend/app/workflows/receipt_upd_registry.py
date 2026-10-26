"""Every УПД the warehouse takes in lands in purchasing's «Реестр УПД» (owner 26.10.2026, relaying Витя: «сваливать всё
в реестр, что будем принимать — он сам разберётся с агентом»).

A «Поставка» (warehouse_receipts) keeps its УПД as a stored file (`scan_file` → warehouse_receipt_files). Once a line of
the delivery is accepted — the goods are really taken — the file goes to the registry:

* an ЭДО file (XML / Diadoc ZIP, kept as it came) — `upd_registry.import_edo`, an ordinary «edo» document; sent again
  it reports «unchanged» or a twin;
* a paper scan — `upd_scans.import_scan`, origin «scan», one document per stored file («wh-<file uuid>»): the header as
  the storekeeper saved it on the delivery (`scan_document`), the lines from the model's reading kept with the file.

Idempotent: a scan document is rewritten only when the delivery's header or the reading changed. A push that fails is
logged and never fails the acceptance (`push_after_accept`); the next accepted line or the backfill retries.

Backfill of the deliveries taken in before (each with a УПД file and at least one accepted line):

    deploy/erp.sh cli python -m app.workflows.receipt_upd_registry --dry-run   # что попадёт в реестр, без записи
    deploy/erp.sh cli python -m app.workflows.receipt_upd_registry --apply     # записать (повтор безвреден)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.modules.purchasing import upd_registry, upd_scans
from app.modules.purchasing.upd_sellers import seller_name_key
from app.modules.warehouse.infrastructure import StockLotRow, WarehouseReceiptFileRow, WarehouseReceiptRow
from app.modules.warehouse.receipts_application import scan_file_id
from app.modules.warehouse.scan_reading import ScanReading, reading_from_json
from app.shared.clock import moscow_now
from app.shared.identity import Actor

EDO_KINDS = ("application/xml", "application/zip")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReceiptFile:
    id: UUID
    file_name: str
    content_type: str
    recognition: str


def _day(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def _agree(a: str, b: str) -> bool:
    return bool(a and b and (a == b or a in b or b in a))


def scan_document(receipt: Mapping[str, Any], file: ReceiptFile, reading: ScanReading | None) -> upd_scans.ScanDocument:
    """The registry's header of a scanned УПД from the delivery as the storekeeper saved it — every column of
    warehouse_receipts comes in `receipt`, so a new field of the delivery is mapped here and nowhere else. The seller's
    INN is the one read off the scan when the scan names the same seller (the storekeeper may have retyped it)."""
    supplier = str(receipt.get("supplier") or "").strip()
    invoice = str(receipt.get("invoice_number") or "").strip()
    basis = f"Счёт № {invoice}" + (f" от {_day(receipt.get('invoice_date'))}" if receipt.get("invoice_date") else "")
    found = reading.found if reading is not None else None
    inn = ""
    if found is not None and found.inn and (
        not supplier or not found.supplier or _agree(seller_name_key(found.supplier), seller_name_key(supplier))
    ):
        inn = found.inn
    lines = tuple(
        upd_scans.ScanLine(
            name=line.name, article=line.article or line.supplier_code,
            unit=line.invoice_unit if line.invoice_quantity is not None else line.unit,
            quantity=line.invoice_quantity if line.invoice_quantity is not None else line.quantity,
            number=line.number,
        )
        for line in (reading.lines if reading is not None else ())
    )
    return upd_scans.ScanDocument(
        file_key=str(file.id), file_name=file.file_name, folder=f"Склад {receipt.get('warehouse') or ''}".strip(),
        number=str(receipt.get("upd_number") or "").strip(), day=receipt.get("upd_date"), seller_name=supplier,
        seller_inn=inn, basis=basis if invoice else "", engine=reading.engine if reading is not None else "",
        warning=reading.warning if reading is not None else "", lines=lines,
    )


def _content(warehouse_factory: Callable, file_id: UUID) -> Callable[[], bytes]:
    def read() -> bytes:
        with warehouse_factory() as uow:
            return uow.session.scalar(
                select(WarehouseReceiptFileRow.content).where(WarehouseReceiptFileRow.id == file_id)
            ) or b""

    return read


def _receipt_facts(warehouse_factory: Callable, receipt_id: UUID) -> tuple[dict[str, Any], ReceiptFile, int] | str:
    """The delivery's columns, its file (without the bytes) and its accepted lines; a reason in words when nothing
    is to be sent."""
    with warehouse_factory() as uow:
        session = uow.session
        row = session.get(WarehouseReceiptRow, receipt_id)
        if row is None:
            return "поставка не найдена"
        receipt = {column.name: getattr(row, column.name) for column in WarehouseReceiptRow.__table__.columns}
        file_id = scan_file_id(row.scan_file) if row.scan_file else None
        if file_id is None:
            return "у поставки нет файла УПД"
        found = session.execute(
            select(WarehouseReceiptFileRow.id, WarehouseReceiptFileRow.file_name, WarehouseReceiptFileRow.content_type,
                   WarehouseReceiptFileRow.recognition).where(WarehouseReceiptFileRow.id == file_id)
        ).first()
        if found is None:
            return "файл УПД поставки не найден"
        lots = session.scalar(select(func.count()).select_from(StockLotRow).where(StockLotRow.receipt_id == receipt_id))
    return receipt, ReceiptFile(found.id, found.file_name, found.content_type, found.recognition or ""), lots or 0


def push_receipt(
    warehouse_factory: Callable, purchasing_factory: Callable, receipt_id: UUID, actor: Actor, now: datetime,
    *, apply: bool = True,
) -> dict[str, Any]:
    """One delivery's УПД file into the registry. `{receipt_id, status, …}`: skipped (`reason`) | created | updated |
    unchanged | twin | error (`message`). `apply=False` — the same run rolled back (the backfill's dry run)."""
    facts = _receipt_facts(warehouse_factory, receipt_id)
    if isinstance(facts, str):
        return {"receipt_id": str(receipt_id), "status": "skipped", "reason": facts}
    receipt, file, lots = facts
    if lots == 0:
        return {"receipt_id": str(receipt_id), "status": "skipped", "reason": "ни одна строка поставки не принята"}
    with purchasing_factory() as uow:
        if file.content_type in EDO_KINDS:
            report = upd_registry.import_edo(uow, [(file.file_name, _content(warehouse_factory, file.id)())], actor, now)
            files = report["files"]
            done = [item for item in files if item.get("document_id")]
            result: dict[str, Any] = {
                "status": done[0]["status"] if done else "error",
                "document_id": done[0]["document_id"] if done else None,
            }
            if not done:
                result["message"] = "; ".join(str(item.get("message") or "") for item in files) or "в файле нет УПД"
        else:
            reading = reading_from_json(file.recognition)
            result = upd_scans.import_scan(
                uow, scan_document(receipt, file, reading), _content(warehouse_factory, file.id), actor, now,
            )
        if apply and result["status"] != "error":
            uow.commit()
    return {"receipt_id": str(receipt_id), "file": str(file.id), "origin": "edo" if file.content_type in EDO_KINDS
            else "scan", **result}


def push_after_accept(
    warehouse_factory: Callable | None, purchasing_factory: Callable | None, receipt_id: UUID, actor: Actor,
    now: datetime,
) -> dict[str, Any] | None:
    """The hook of «Принять»: never raises — the lot is on stock whatever the registry says."""
    if warehouse_factory is None or purchasing_factory is None:
        return None
    try:
        result = push_receipt(warehouse_factory, purchasing_factory, receipt_id, actor, now)
    except Exception:  # noqa: BLE001 — the registry is purchasing's business; the acceptance is done
        log.exception("УПД поставки %s не попал в реестр УПД — повторится при следующем «Принять»", receipt_id)
        return None
    if result["status"] == "error":
        log.warning("УПД поставки %s не попал в реестр УПД: %s", receipt_id, result.get("message"))
    return result


def backfill(
    warehouse_factory: Callable, purchasing_factory: Callable, actor: Actor, now: datetime, *, apply: bool,
) -> list[dict[str, Any]]:
    """Every delivery with a УПД file and at least one accepted line, oldest first; each in its own transaction, a
    failure of one is reported and the rest go on. Run again — nothing is written twice."""
    with warehouse_factory() as uow:
        ids = list(uow.session.scalars(
            select(WarehouseReceiptRow.id).where(
                WarehouseReceiptRow.scan_file != "",
                select(StockLotRow.id).where(StockLotRow.receipt_id == WarehouseReceiptRow.id).exists(),
            ).order_by(WarehouseReceiptRow.created_at, WarehouseReceiptRow.id)
        ))
    results = []
    for receipt_id in ids:
        try:
            results.append(push_receipt(warehouse_factory, purchasing_factory, receipt_id, actor, now, apply=apply))
        except Exception as error:  # noqa: BLE001 — one broken delivery must not stop the rest
            results.append({"receipt_id": str(receipt_id), "status": "error", "message": str(error)})
    return results


def format_report(results: list[dict[str, Any]], *, applied: bool) -> str:
    counts: dict[str, int] = {}
    lines = []
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
        detail = item.get("document_id") or item.get("reason") or ""
        message = f" — {item['message']}" if item.get("message") else ""
        lines.append(f"{item['receipt_id']}  {item.get('origin', ''):4}  {item['status']:9}  {detail}{message}")
    summary = ", ".join(f"{status}: {count}" for status, count in sorted(counts.items())) or "поставок с файлом УПД нет"
    lines.append(f"Итого {len(results)} — {summary}.")
    lines.append("Записано." if applied else "Проверка без записи. Записать — та же команда с --apply.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="УПД принятых поставок склада → «Реестр УПД» закупки.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="только показать, что попадёт в реестр (по умолчанию)")
    mode.add_argument("--apply", action="store_true", help="записать; повторный запуск ничего не дублирует")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import build_engine, build_purchasing_uow_factory, build_warehouse_uow_factory

    engine = build_engine(url)
    try:
        results = backfill(
            build_warehouse_uow_factory(engine), build_purchasing_uow_factory(engine),
            Actor(id=uuid.uuid4(), name="перенос УПД склада в реестр", department=""), moscow_now(), apply=args.apply,
        )
    finally:
        engine.dispose()
    print(format_report(results, applied=args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())

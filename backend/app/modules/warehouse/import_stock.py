"""WH-IMPORT CLI: SUPPLYMAP warehouse export → ERP lots, with a reconciliation report.

    ERP_DATABASE_URL=... python -m app.modules.warehouse.import_stock --dir ../data/import --dry-run

Exit code: 0 — everything reconciled; 1 — some rows were not taken or differ (see the report);
2 — the export cannot be read or ERP_DATABASE_URL is not set.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.database import build_engine, build_warehouse_uow_factory

from .application import ImportLegacyStockCommand, LegacyStockImportResult, UnitOfWork, import_legacy_stock
from .stock_export import STOCK_SHEETS, WRITEOFF_SHEET, StockExport, StockExportError, read_stock_export

DEFAULT_DIRECTORY = Path(__file__).resolve().parents[4] / "data" / "import"


@dataclass(frozen=True)
class TotalLine:
    warehouse: str
    unit: str
    sheet_balance: Decimal
    erp_balance: Decimal
    unreadable_rows: int = 0

    @property
    def ok(self) -> bool:
        return self.sheet_balance == self.erp_balance and not self.unreadable_rows


@dataclass(frozen=True)
class GrnLine:
    warehouse: str
    grn: str
    unit: str
    sheet_balance: Decimal | None  # None: the sheet value itself could not be read
    erp_balance: Decimal


@dataclass
class Reconciliation:
    totals: list[TotalLine] = field(default_factory=list)
    grn_count: int = 0
    grn_mismatches: list[GrnLine] = field(default_factory=list)
    journal_checked: int = 0
    journal_mismatches: list[tuple[str, Decimal, Decimal]] = field(default_factory=list)  # ГРН, журнал, СПИСАНО
    stock_rejected: int = 0
    conflicts: int = 0

    @property
    def ok(self) -> bool:
        return not (self.grn_mismatches or self.stock_rejected or self.conflicts) and all(t.ok for t in self.totals)


def reconcile(export: StockExport, result: LegacyStockImportResult) -> Reconciliation:
    """Compare every sheet row (taken or not) with the ERP lot at its key, per warehouse+unit and per ГРН."""
    sheet_rows = [(lot.key, lot.unit, lot.balance) for lot in export.lots]
    sheet_rows += [((r.sheet, r.grn, r.package), r.unit, r.balance)
                   for r in export.rejected if r.sheet in STOCK_SHEETS]
    totals: dict[tuple[str, str], list] = defaultdict(lambda: [Decimal("0"), Decimal("0"), 0])
    by_grn: dict[tuple[str, str], list] = {}
    for key, unit, balance in sheet_rows:
        lot = result.erp_lots.get(key)
        erp = lot.balance if lot is not None else Decimal("0")
        line = totals[(key[0], unit)]
        line[1] += erp
        if balance is None:
            line[2] += 1
        else:
            line[0] += balance
        grn = by_grn.setdefault((key[0], key[1]), [unit, Decimal("0"), Decimal("0")])
        grn[1] = None if balance is None or grn[1] is None else grn[1] + balance
        grn[2] += erp
    report = Reconciliation(
        totals=[TotalLine(w, u, s, e, bad) for (w, u), (s, e, bad) in sorted(totals.items())],
        grn_count=len(by_grn),
        grn_mismatches=[GrnLine(w, g, unit, sheet, erp) for (w, g), (unit, sheet, erp) in sorted(by_grn.items())
                        if sheet != erp],
        stock_rejected=len([r for r in export.rejected if r.sheet in STOCK_SHEETS]) + len(result.rejected),
        conflicts=len(result.conflicts),
    )
    # The journal is history, not the source of quantities; this only shows where it disagrees with СПИСАНО.
    net: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    for row in export.writeoffs:
        if row.is_posted:
            net[row.key] += -row.quantity if row.is_return else row.quantity
    for lot in export.lots:
        if lot.key in net or lot.written_off:
            report.journal_checked += 1
            if net.get(lot.key, Decimal("0")) != lot.written_off:
                report.journal_mismatches.append((lot.grn, net.get(lot.key, Decimal("0")), lot.written_off))
    return report


def _n(value: Decimal | None) -> str:
    if value is None:
        return "?"
    text = f"{value.normalize():f}" if value else "0"
    return text.replace(".", ",")


def format_report(export: StockExport, result: LegacyStockImportResult, check: Reconciliation) -> str:
    mode = "записано в базу" if result.committed else "ПРОБНЫЙ ПРОГОН — в базу ничего не записано"
    lines = [f"Импорт склада SUPPLYMAP, выгрузка {export.exported_at or '?'}: {mode}", "", "Листы:"]
    for name, sheet in export.sheets.items():
        lines.append(f"  {name}: строк {sheet.rows_total}, пустых {sheet.blank_rows}")
    lines += ["", "Партии:",
              f"  создано {len(result.created)}, уже в ERP без изменений {len(result.unchanged)}, "
              f"расходятся с ERP {len(result.conflicts)}, отклонено {check.stock_rejected}"]
    rejected = [(r.sheet, r.row_number, r.grn, r.reason) for r in export.rejected if r.sheet in STOCK_SHEETS]
    rejected += [(row.sheet, row.row_number, row.grn, reason) for row, reason in result.rejected]
    for sheet, number, grn, reason in rejected:
        lines.append(f"  отклонено {sheet}:{number} {grn or '—'}: {reason}")
    for row, reason in result.conflicts:
        lines.append(f"  не изменено {row.sheet}:{row.row_number} {row.grn}: {reason}")
    for row in result.reserve_not_transferred:
        lines.append(f"  резерв {_n(row.reserved)} {row.unit} не перенесён: {row.grn} ({row.sheet}:{row.row_number})")

    lines += ["", "Сверка БАЛАНС по складам (лист / ERP):"]
    for total in check.totals:
        mark = "совпадает" if total.ok else "РАСХОДИТСЯ"
        unread = f", нечитаемых строк {total.unreadable_rows}" if total.unreadable_rows else ""
        lines.append(f"  {total.warehouse}, {total.unit}: {_n(total.sheet_balance)} / {_n(total.erp_balance)} — {mark}{unread}")
    if not check.totals:
        lines.append("  строк нет")
    lines.append(f"Сверка по ГРН: совпало {check.grn_count - len(check.grn_mismatches)} из {check.grn_count}")
    for line in check.grn_mismatches:
        lines.append(f"  РАСХОДИТСЯ {line.warehouse} {line.grn}: лист {_n(line.sheet_balance)} / ERP {_n(line.erp_balance)} {line.unit}")

    journal = export.sheets.get(WRITEOFF_SHEET)
    journal_rejected = [r for r in export.rejected if r.sheet == WRITEOFF_SHEET]
    lines += ["", f"Журнал {WRITEOFF_SHEET} (история, количества не двигает): строк {journal.rows_total if journal else 0}",
              f"  в историю новых партий {result.history_imported}, партия уже была в ERP {result.history_for_existing_lots}, "
              f"без партии {len(result.history_without_lot)}, отклонено {len(journal_rejected)}"]
    for row in result.history_without_lot:
        lines.append(f"  без партии {WRITEOFF_SHEET}:{row.row_number} {row.warehouse} {row.grn}")
    for row in journal_rejected:
        lines.append(f"  отклонено {WRITEOFF_SHEET}:{row.row_number} {row.grn}: {row.reason}")
    lines.append(f"  сверка с СПИСАНО (проведённые, возврат со знаком минус): совпало "
                 f"{check.journal_checked - len(check.journal_mismatches)} из {check.journal_checked}")
    for grn, journal_net, written_off in check.journal_mismatches:
        lines.append(f"  журнал {_n(journal_net)} ≠ СПИСАНО {_n(written_off)}: {grn}")

    lines += ["", "Не перенесённые колонки (поля партии ждут SK-6):"]
    for name, sheet in export.sheets.items():
        if sheet.skipped_columns:
            listed = ", ".join(f"{header} ({filled} заполнено)" for header, filled in sheet.skipped_columns.items())
            lines.append(f"  {name}: {listed}")
    if result.manual_numbers:
        lines += ["", "Ручная нумерация OLD продолжится после импортированных: " + ", ".join(
            f"{year} → с {number + 1:07d}" for year, number in sorted(result.manual_numbers.items()))]
    lines += ["", "ИТОГ: " + ("всё сошлось" if check.ok else "есть расхождения — см. выше")]
    return "\n".join(lines)


def run_import(export: StockExport, uow: UnitOfWork, *, dry_run: bool, now: datetime) -> LegacyStockImportResult:
    return import_legacy_stock(ImportLegacyStockCommand(
        lots=tuple(export.lots),
        writeoffs=tuple(export.writeoffs),
        source=f"SUPPLYMAP {export.exported_at}".strip(),
        now=now,
        dry_run=dry_run,
        reconcile_keys=tuple((r.sheet, r.grn, r.package) for r in export.rejected if r.sheet in STOCK_SHEETS),
    ), uow)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Импорт остатков склада из выгрузки SUPPLYMAP (WH-IMPORT)")
    parser.add_argument("--dir", default=str(DEFAULT_DIRECTORY), help="папка с ЦАР.csv, ЖУК.csv, СПИСАНИЕ.csv и _manifest.json")
    parser.add_argument("--dry-run", action="store_true", help="только отчёт сверки, без записи в базу")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    try:
        export = read_stock_export(args.dir)
    except StockExportError as error:
        print(f"Выгрузка не читается: {error}", file=sys.stderr)
        return 2
    engine = build_engine(database_url)
    try:
        result = run_import(export, build_warehouse_uow_factory(engine)(), dry_run=args.dry_run,
                            now=datetime.now(timezone.utc))
    finally:
        engine.dispose()
    check = reconcile(export, result)
    print(format_report(export, result, check))
    return 0 if check.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

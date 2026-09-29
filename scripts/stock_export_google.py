#!/usr/bin/env python
"""Выгрузка склада SUPPLYMAP (ЦАР, ЖУК, СПИСАНИЕ) из Google в формат WH-IMPORT — только чтение.

То же, что `whExportRead` (DbMirror.gs): лист целиком отображаемыми значениями (`getDisplayValues` над
`getDataRange`), но сервисным аккаунтом через Sheets API, без clasp. Пишет `ЦАР.csv`, `ЖУК.csv`,
`СПИСАНИЕ.csv` и `_manifest.json` — их читает `python -m app.modules.warehouse.import_stock`.

    cd backend
    .venv/bin/python ../scripts/stock_export_google.py --key <sa.json> --out ../data/import
    ERP_DATABASE_URL=... .venv/bin/python -m app.modules.warehouse.import_stock --dir ../data/import --dry-run

В Google ничего не пишется: ключ открывается со scope spreadsheets.readonly.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

WAREHOUSE_SPREADSHEET = "19w4wmGYYylpYQD053itm0KC8KkwotlNN0fmkJUOjQaU"  # SUPPLYMAP CONFIG.SS_WAREHOUSE
SHEETS = ("ЦАР", "ЖУК", "СПИСАНИЕ")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", help="JSON-ключ сервисного аккаунта (иначе ERP_GOOGLE_SERVICE_ACCOUNT_JSON)")
    parser.add_argument("--sheet", default=WAREHOUSE_SPREADSHEET)
    parser.add_argument("--out", required=True, help="папка выгрузки (живые данные — не в git)")
    args = parser.parse_args()

    from app.modules.requests.google_sheets import GoogleSheetsCalcReader, load_service_account

    raw = Path(args.key).read_text(encoding="utf-8") if args.key else os.getenv("ERP_GOOGLE_SERVICE_ACCOUNT_JSON", "")
    title, sheets = GoogleSheetsCalcReader(load_service_account(raw)).read_sheets(args.sheet, SHEETS)
    missing = [name for name in SHEETS if name not in sheets]
    if missing:
        raise SystemExit(f"В «{title}» нет листов: {', '.join(missing)}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "exportedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": f"https://docs.google.com/spreadsheets/d/{args.sheet}", "title": title, "sheets": {},
    }
    for name in SHEETS:
        rows = sheets[name]
        with (out / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)
        manifest["sheets"][name] = {"file": f"{name}.csv", "rows": len(rows)}
        print(f"{name}: {len(rows)} строк")
    (out / "_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Выгрузка: {out}")


if __name__ == "__main__":
    main()

"""Read-only check of the Google wiring the server runs with: ``python -m app.modules.requests.google_check``.

Uses the same environment and adapter as the app (`reader_from_env`): signs in and reads the titles of the
SUPPLYMAP spreadsheets. Writes nothing to Google (КД lives in the ERP database since 29.09). Prints
one line per check and never the key; exit code 1 when a check fails. The GitHub workflow
`google-access.yml` runs it with the repository secrets — the same values a server deploy will get.
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import quote

from .application import CalcSheetError
from .google_sheets import ENV_VAR, SHEETS_API, reader_from_env

# SUPPLYMAP Config.gs: Запросы, Лист закупки, Лист ожидания. Only titles are read.
SUPPLYMAP_SHEETS = {
    "Запросы": "1xjAH9Wzu7EoKZHjPkuhS7TeOiqIBsfZvyNKjiJnO2go",
    "Лист закупки": "1MBrPPwOfRxnczvfytT6kV5Hem-YJd-r8YjYbO4w2Nl8",
    "Лист ожидания": "1ObPHscfUeMexBps_a_VQjZGrE64Uk6eJ8IqnhbPFinM",
}


def check_sheets(extra: list[str]) -> list[tuple[bool, str]]:
    if not os.getenv(ENV_VAR, "").strip():
        return [(False, f"{ENV_VAR} не задан — расчёты и таблицы не читаются")]
    try:
        reader = reader_from_env()
    except CalcSheetError as error:
        return [(False, str(error))]
    results = [(True, f"ключ сервисного аккаунта {reader.account.client_email} прочитан")]
    targets = list(SUPPLYMAP_SHEETS.items()) + [(f"таблица {item}", item) for item in extra]
    for label, spreadsheet_id in targets:
        try:
            meta = reader._get(f"{SHEETS_API}/{quote(spreadsheet_id, safe='')}", [("fields", "properties.title")])
            title = str((meta.get("properties") or {}).get("title") or "")
            results.append((True, f"{label}: читается («{title}»)"))
        except CalcSheetError as error:
            results.append((False, f"{label}: {error}"))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка доступа ERP к Google (только чтение).")
    parser.add_argument("--sheet", action="append", default=[], help="ещё одна таблица (ID) для проверки чтения")
    args = parser.parse_args(argv)
    results = check_sheets(args.sheet)
    for ok, text in results:
        print(("OK   " if ok else "FAIL ") + text)
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())

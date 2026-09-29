#!/usr/bin/env python
"""Завести производственные проекты из графика цеха («Производственный план v.0.1», лист ЗАГРУЗ) в ERP.

Два шага, чтобы ERP-стенду не был нужен ключ Google:

    cd backend
    # 1. Снимок: лист графика + расчёт каждого проекта («<№> Расчет» на Диске), только чтение.
    .venv/bin/python ../scripts/plan_graph_import.py snapshot --key <sa.json> --out graph.json
    # 2. Заведение в базу ERP_DATABASE_URL тем же API, что кнопки ERP (в процессе, без запущенного сервера).
    ERP_DATABASE_URL=sqlite+pysqlite:///demo.db .venv/bin/python ../scripts/plan_graph_import.py apply graph.json

apply повторяет путь человека в ERP:
- «Создать проект из расчёта» (POST /api/techcards/kitsets/load со ссылкой на расчёт и дедлайном) —
  карточка проекта, строка реестра, очередь отгрузки, запись «Дедлайн» в журнале; расчёт читается из снимка;
- проект без расчёта — строкой реестра (PUT /api/production/plan/projects-snapshot, реестр дополняется,
  чужие строки не трогаются) и записью «Дедлайн»;
- статус графика: ХОЛД/СТОП/ОЖИД ИНФ/ОЖИД КОМПЛ/ПАУЗА — «Блокер» журнала, ОТГРУЖЕН — терминальный
  «Отгружено» в реестре, остальное — текст статуса реестра; комментарий графика — «Комментарий» журнала;
  В РАБОТЕ / ГОТОВО·НА ОТГР·ОТК / К ЗАПУС или «КОМПЛ НАБР» / «ТЕХ.КАР» — отметка «Этап КБ» на каждой КБ.
Повторный apply не дублирует: карточки, которые уже есть, не перечитываются (кроме --refresh), одинаковые
записи журнала не пишутся второй раз. В Google ничего не пишется.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

GRAPH_SPREADSHEET = "1DZ_H3vIVseQ3LKC8FgrhfezM-4JHznZUFiKvLWhqap0"  # «Производственный план v.0.1»
GRAPH_SHEET = "ЗАГРУЗ"
DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"

COLUMNS = {
    "comment": "Комментарий", "status": "Статус", "ready": "% Готовн", "in_plan": "В плане",
    "project": "№ Проекта", "customer": "Наименование организции", "supplied": "ЕСТЬ ДАВАЛ",
    "kit_checked": "ПРОВ КОМПЛ", "kit_present": "КОМПЛ ЕСТЬ", "zkp": "ЗКП", "kit_arrival": "Дата прихода комплек.",
    "techcard": "ТЕХ.КАР", "test": "Тест С/Б", "kit_picked": "КОМПЛ НАБР", "deadline": "Дедлайн",
}
# Статусы графика → блокеры журнала плана (PLAN_BLOCKERS); написание в графике плавает.
BLOCKER_ALIASES = {
    "ХОЛД": "ХОЛД", "СТОП": "СТОП", "ОЖД ИНФ": "ОЖИД ИНФ", "ОЖИД ИНФ": "ОЖИД ИНФ",
    "ОЖИД КОМПЛ": "ОЖИД КОМПЛ", "ПАУЗА": "ПАУЗА",
}
SHIPPED = "ОТГРУЖЕН"
# Этап КБ (KIT_STAGES) по статусу и галочкам графика — ручной отметкой «Этап КБ», как мастер ставит в плане.
STOCK_STATUSES = {"ГОТОВО", "НА ОТГР", "ОТК"}
TERMINAL_STATUS = "Отгружено"


# ── Разбор графика ────────────────────────────────────────────────────────────────────────────


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _date(value: object) -> str | None:
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", _clean(value))
    if not match:
        return None
    try:
        return date(int(match[3]), int(match[2]), int(match[1])).isoformat()
    except ValueError:
        return None


def graph_statuses(value: object) -> list[str]:
    return [_clean(part).rstrip(".").upper() for part in str(value or "").split(",") if _clean(part)]


def parse_graph(grid: list[list[str]]) -> list[dict]:
    """Строки графика под шапкой с «№ Проекта» до первой полностью пустой строки."""
    header_row = next((i for i, row in enumerate(grid) if COLUMNS["project"] in [_clean(c) for c in row]), None)
    if header_row is None:
        raise SystemExit(f"В листе нет шапки с «{COLUMNS['project']}»")
    header = [_clean(cell) for cell in grid[header_row]]
    index = {key: header.index(label) for key, label in COLUMNS.items() if label in header}
    rows = []
    for number, row in enumerate(grid[header_row + 1:], start=header_row + 2):
        if not any(_clean(cell) for cell in row):
            break
        cell = lambda key: _clean(row[index[key]]) if key in index and index[key] < len(row) else ""  # noqa: E731
        rows.append({
            "row": number, "project_raw": cell("project"), "customer": cell("customer"),
            "comment": cell("comment"), "status": cell("status"), "ready": cell("ready"),
            "in_plan": cell("in_plan") == "TRUE", "supplied": cell("supplied"),
            "kit_arrival": cell("kit_arrival"), "deadline": _date(cell("deadline")),
            "flags": {key: cell(key) == "TRUE" for key in ("kit_checked", "kit_present", "zkp", "techcard", "test", "kit_picked")},
        })
    return rows


# ── Снимок (Google, только чтение) ───────────────────────────────────────────────────────────


def _calc_key(title: str) -> str:
    from app.modules.production.plan import normalize_project_number

    head = re.split(r"(?i)\s*[-–]?\s*(?:актуальн\S*\s+)?расч", title, maxsplit=1)[0]
    return normalize_project_number(head)


def pick_calc(project: str, files: list[dict]) -> tuple[dict | None, list[str]]:
    """Файл расчёта проекта: название «<№> Расчет»; из нескольких — точное «<№> Расчет», иначе свежий."""
    matches = [
        item for item in files
        if _calc_key(item["name"]) == project and not re.search(r"(?i)не\s*актуальн|стар|пример", item["name"])
    ]
    if not matches:
        # Допроект (228К.20.2) без своего файла считается в расчёте родителя (228К.20 Расчет).
        parent = project.rsplit(".", 1)[0] if project.count(".") >= 2 else ""
        chosen, others = pick_calc(parent, files) if parent else (None, [])
        return ({**chosen, "parent": parent} if chosen else None), others
    exact = [item for item in matches if re.fullmatch(r"(?i)\S+\s+расч[её]т", item["name"].strip())]
    pool = exact or matches
    chosen = max(pool, key=lambda item: item.get("modifiedTime", ""))
    return chosen, [item["name"] for item in matches if item is not chosen]


def snapshot(args: argparse.Namespace) -> None:
    import httpx

    from app.modules.production.plan import normalize_project_number
    from app.modules.requests.google_sheets import GoogleSheetsCalcReader, load_service_account

    raw = Path(args.key).read_text(encoding="utf-8") if args.key else os.getenv("ERP_GOOGLE_SERVICE_ACCOUNT_JSON", "")
    account = load_service_account(raw)
    sheets = GoogleSheetsCalcReader(account)
    _title, graph = sheets.read_sheets(args.sheet, (args.tab,))
    if args.tab not in graph:
        raise SystemExit(f"В таблице нет листа «{args.tab}»")
    rows = parse_graph(graph[args.tab])

    token = httpx.post(account.token_uri, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": account.assertion(int(time.time()), DRIVE_SCOPE),
    }, timeout=30).raise_for_status().json()["access_token"]
    files, page = [], None
    while True:
        params = {
            "q": "(name contains 'Расчет' or name contains 'Расчёт' or name contains 'расчет') and "
                 f" mimeType='{SHEET_MIME}' and trashed=false",
            "fields": "nextPageToken,files(id,name,modifiedTime)", "pageSize": 1000,
            "includeItemsFromAllDrives": "true", "supportsAllDrives": "true", "corpora": "allDrives",
        }
        if page:
            params["pageToken"] = page
        body = httpx.get(DRIVE_FILES, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        body = body.raise_for_status().json()
        files += body.get("files") or []
        page = body.get("nextPageToken")
        if not page:
            break

    calcs: dict[str, dict] = {}
    for row in rows:
        project = normalize_project_number(row["project_raw"])
        row["project"] = project
        if not project:
            continue
        chosen, others = pick_calc(project, files)
        if chosen is None:
            continue
        row["calc"] = {"id": chosen["id"], "name": chosen["name"], "others": others, "parent": chosen.get("parent", "")}
        if chosen["id"] not in calcs:
            try:
                title, calc_sheets = sheets.read_sheets(chosen["id"], ("ЗКЗ", "СПЯ"))
                calcs[chosen["id"]] = {"title": title, "sheets": calc_sheets}
            except Exception as error:  # noqa: BLE001 — проект заведётся строкой реестра
                row["calc"]["error"] = str(error)
    Path(args.out).write_text(json.dumps({
        "source": f"https://docs.google.com/spreadsheets/d/{args.sheet}", "sheet": args.tab,
        "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": rows, "calcs": calcs,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    found = sum(1 for row in rows if row.get("calc") and not row["calc"].get("error"))
    print(f"строк графика: {len(rows)}, с расчётом: {found}, снимок: {args.out}")


# ── Заведение в ERP ───────────────────────────────────────────────────────────────────────────


class SnapshotSheets:
    """`CalcSheetsReader` над снимком: ERP читает расчёт так же, как из Google, только без сети."""

    def __init__(self, calcs: dict[str, dict]):
        self.calcs = calcs

    def read_sheets(self, spreadsheet_id: str, names: tuple[str, ...]):
        from app.modules.requests.application import CalcSheetError

        calc = self.calcs.get(spreadsheet_id)
        if calc is None:
            raise CalcSheetError("Расчёта нет в снимке графика", "access")
        return calc["title"], {name: calc["sheets"][name] for name in names if name in calc["sheets"]}


def plan_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Одна строка на проект. Дубль в графике (проект и в работе, и отгружен) — берётся строка не
    «ОТГРУЖЕН»; комментарий второй строки уходит отдельной записью журнала."""
    by_project: dict[str, dict] = {}
    skipped = []
    for row in rows:
        if not row.get("project"):
            if row["comment"] or row["customer"]:
                skipped.append(f"строка {row['row']}: нет № проекта ({row['customer'] or row['comment'][:40]})")
            continue
        current = by_project.get(row["project"])
        if current is None:
            by_project[row["project"]] = {**row, "extra_notes": []}
            continue
        shipped_now = SHIPPED in graph_statuses(current["status"])
        keep, other = (row, current) if shipped_now and SHIPPED not in graph_statuses(row["status"]) else (current, row)
        merged = {**keep, "extra_notes": current["extra_notes"]}
        if other["comment"]:
            merged["extra_notes"].append(f"{other['comment']} (строка графика «{other['status']}»)")
        by_project[row["project"]] = merged
    return list(by_project.values()), skipped


def graph_note(row: dict) -> str:
    facts = []
    if row["ready"] and row["ready"] != "0%":
        facts.append(f"готовность {row['ready']}")
    if row["supplied"] in {"ДА", "Ч/Т"}:
        facts.append(f"давальческое {row['supplied']}")
    if row["kit_arrival"]:
        facts.append(f"приход комплектации {row['kit_arrival']}")
    if row["in_plan"]:
        facts.append("в плане")
    tail = f" [график: {', '.join(facts)}]" if facts else ""
    return (row["comment"] or f"Статус графика: {row['status']}") + tail


def graph_stage(row: dict) -> str:
    statuses = set(graph_statuses(row["status"]))
    if SHIPPED in statuses:
        return ""
    if statuses & STOCK_STATUSES:
        return "КБ на складе"
    if "В РАБОТЕ" in statuses:
        return "В работе"
    if row["flags"].get("kit_picked") or "К ЗАПУС" in statuses:
        return "КБ набрана"
    if row["flags"].get("techcard"):
        return "ТК готова"
    return ""


def apply(args: argparse.Namespace) -> None:
    os.environ.setdefault("ERP_COOKIE_SECURE", "0")
    if not os.getenv("ERP_DATABASE_URL"):
        raise SystemExit("Задайте ERP_DATABASE_URL (база, куда заводить)")
    from fastapi.testclient import TestClient

    from app.main import app
    from app.modules.production.plan import PLAN_KINDS
    from app.modules.techcards.kitsets import project_number_valid

    data = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    rows, skipped = plan_rows(data["rows"])
    app.state.kitset_sheets_reader = SnapshotSheets(data["calcs"])
    client = TestClient(app, base_url="http://127.0.0.1")
    login = client.post("/api/auth/login", json={"login": args.login, "password": args.password})
    if login.status_code != 200:
        raise SystemExit(f"Вход {args.login}: {login.status_code} {login.text[:200]}")

    def call(method: str, path: str, **kw):
        headers = {"x-csrf-token": client.cookies.get("erp_csrf", ""), "Idempotency-Key": str(uuid.uuid4())}
        response = client.request(method, path, headers=headers, **kw)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path} → {response.status_code} {response.text[:300]}")
        return response.json() if response.content else {}

    report = {"loaded": [], "registry_only": [], "failed": [], "skipped": skipped, "notes": 0, "blocks": 0, "stages": 0}
    kitsets = {item["project"] for item in call("GET", "/api/techcards/kitsets")["items"]}
    for row in rows:
        calc = row.get("calc") or {}
        if not calc.get("id") or calc.get("error") or calc["id"] not in data["calcs"]:
            continue
        if row["project"] in kitsets and not args.refresh:
            continue
        number = row["project"] if project_number_valid(row["project"]) else ""
        try:
            result = call("POST", "/api/techcards/kitsets/load", json={
                "url": f"https://docs.google.com/spreadsheets/d/{calc['id']}/edit",
                "number": number, "deadline": row["deadline"],
            })["res"]
            row["loaded_as"] = result["project"]
            report["loaded"].append(f"{row['project']}: КБ {result['assemblies']}, строк {result['lines']} ← {calc['name']}")
        except RuntimeError as error:
            report["failed"].append(f"{row['project']} (расчёт {calc['name']}): {error}")

    # Реестр: дополнить своими строками, чужие не трогать.
    registry = {}
    with app.state.production_uow_factory() as uow:
        for item in uow.plan_projects.list():
            registry[item.project] = {
                "project": item.project, "customer": item.customer,
                "opened_on": item.opened_on.isoformat() if item.opened_on else None,
                "deadline": item.deadline.isoformat() if item.deadline else None,
                "status": item.status, "terminal": item.terminal,
            }
    for row in rows:
        statuses = graph_statuses(row["status"])
        terminal = SHIPPED in statuses
        current = registry.get(row["project"], {})
        if row["project"] not in registry and not row.get("loaded_as"):
            report["registry_only"].append(row["project"])
        registry[row["project"]] = {
            "project": row["project"], "customer": row["customer"] or current.get("customer") or "—",
            "opened_on": current.get("opened_on") or date.today().isoformat(),
            "deadline": row["deadline"], "terminal": terminal,
            "status": TERMINAL_STATUS if terminal else row["status"],
        }
    call("PUT", "/api/production/plan/projects-snapshot", json={"projects": list(registry.values())})

    # Журнал плана: дедлайн, блокер, комментарий — без повторов при повторном запуске.
    plan = {item["project"]: item for item in call("GET", "/api/production/plan")["rows"]}
    journal: dict[str, list[dict]] = {}
    with app.state.production_uow_factory() as uow:
        for event in uow.plan_events.list():
            journal.setdefault(event.project, []).append({"kind": event.kind, "text": event.text, "blocker": event.blocker, "deadline": event.deadline})
    for row in rows:
        events = journal.get(row["project"], [])
        post = lambda payload: call("POST", "/api/production/plan/events", json={"project": row["project"], **payload})  # noqa: E731
        try:
            if row["deadline"] and not any(e["kind"] == PLAN_KINDS["deadline"] and e["deadline"] and e["deadline"].isoformat() == row["deadline"] for e in events):
                post({"kind": "deadline", "deadline": row["deadline"], "text": "из графика цеха"})
            blocker = next((BLOCKER_ALIASES[s] for s in graph_statuses(row["status"]) if s in BLOCKER_ALIASES), "")
            text = graph_note(row)
            active = (plan.get(row["project"]) or {}).get("blocker")
            if blocker and not (active and active["type"] == blocker):
                post({"kind": "block", "blocker": blocker, "text": text})
                report["blocks"] += 1
            elif not blocker and not any(e["text"] == text for e in events):
                post({"kind": "note", "text": text})
                report["notes"] += 1
            stage = graph_stage(row)
            kits = (plan.get(row["project"]) or {}).get("kits") or []
            targets = [kit["item_index"] for kit in kits if not (kit["manual"] and kit["status"] == stage)]
            if stage and not kits and (plan.get(row["project"]) or {}).get("stage") != stage:
                targets = [""]
            for item_index in targets if stage else []:
                post({"kind": "stage", "item_index": item_index, "value": stage, "text": f"из графика: {row['status']}"})
                report["stages"] += 1
            for note in row["extra_notes"]:
                if not any(e["text"] == note for e in events):
                    post({"kind": "note", "text": note})
                    report["notes"] += 1
        except RuntimeError as error:
            report["failed"].append(f"{row['project']} журнал: {error}")

    print(f"Проектов в графике: {len(rows)}")
    print(f"Заведено из расчёта: {len(report['loaded'])}")
    for line in report["loaded"]:
        print("  " + line)
    print(f"Только строкой реестра (расчёта нет): {len(report['registry_only'])} — {', '.join(report['registry_only'])}")
    print(f"Блокеров: {report['blocks']}, комментариев: {report['notes']}, отметок этапа КБ: {report['stages']}")
    for line in report["skipped"]:
        print("Пропущено: " + line)
    for line in report["failed"]:
        print("ОШИБКА: " + line)
    if report["failed"]:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot", help="прочитать график и расчёты из Google в JSON")
    snap.add_argument("--key", help="JSON-ключ сервисного аккаунта (иначе ERP_GOOGLE_SERVICE_ACCOUNT_JSON)")
    snap.add_argument("--sheet", default=GRAPH_SPREADSHEET)
    snap.add_argument("--tab", default=GRAPH_SHEET)
    snap.add_argument("--out", required=True)
    run = sub.add_parser("apply", help="завести проекты из снимка в базу ERP_DATABASE_URL")
    run.add_argument("snapshot")
    run.add_argument("--login", default="preview")
    run.add_argument("--password", default="Preview-2026!")
    run.add_argument("--refresh", action="store_true", help="перечитать расчёт и у уже заведённых карточек")
    args = parser.parse_args()
    (snapshot if args.command == "snapshot" else apply)(args)


if __name__ == "__main__":
    main()

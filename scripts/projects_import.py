#!/usr/bin/env python
"""Производственные проекты SUPPLYMAP → ERP: реестр, наборы из расчёта, журнал плана, сверка с листом цеха.

Два шага, чтобы базе ERP не был нужен ключ Google:

    cd backend
    # 1. Снимок (только чтение Google): «ERP — Производство» (Проекты, Журнал проектов, Наборы), расчёт
    #    каждого набора по его ссылке РАСЧЁТ и лист цеха «ЗАГРУЗ» («Производственный план v.0.1»).
    .venv/bin/python ../scripts/projects_import.py snapshot --key <sa.json> --out projects.json
    # 2. Заведение в базу ERP_DATABASE_URL (в процессе, сервер можно не останавливать).
    ERP_DATABASE_URL=sqlite+pysqlite:///erp.db .venv/bin/python ../scripts/projects_import.py apply projects.json

apply по шагам, как это лежит в SUPPLYMAP:
1. Наборы — «Создать проект из расчёта» (POST /api/techcards/kitsets/load) по ссылке РАСЧЁТ набора; расчёт
   читается из снимка. Карточка, строка реестра, очередь отгрузки — тем же путём, что кнопка.
2. Реестр «Проекты» — строками реестра плана (№, заказчик, дата, дедлайн, статус); чужие строки ERP остаются.
3. «Журнал проектов» — записями журнала плана как есть: дата, кто, вид, блокер, текст, КБ, значение.
4. Сверка с листом цеха — порт `planShopSync_` / `diagPlanSyncFromSheet` (ProdPlan.gs): этапы КБ только вперёд,
   блокер, снятие блокера, дедлайн, комментарии — записями журнала от «план цеха <дата>»; проекта нет в
   реестре — заводится строкой (№ + заказчик).
Повторный apply ничего не дублирует. В Google ничего не пишется.
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

PROD_SPREADSHEET = "1cxYr4lVHUI0RNw3eBn5cKOTus5SdYAJeA043LCUBj0w"  # «ERP — Производство» (SUPPLYMAP SS_PROD)
PROD_SHEETS = ("Проекты", "Журнал проектов", "Наборы")
SHOP_SPREADSHEET = "1DZ_H3vIVseQ3LKC8FgrhfezM-4JHznZUFiKvLWhqap0"  # PLAN_SHOP_SHEET_ID_
SHOP_SHEET = "ЗАГРУЗ"

# planShopSync_: статус листа цеха → этап КБ, до которого КБ дотягиваются (назад не двигаем).
SHOP_STAGE = {"ОТГРУЖЕН": "Отгружено", "ГОТОВО": "КБ на складе", "ОТК": "В работе",
              "В РАБОТЕ": "В работе", "К ЗАПУСК": "КБ набрана"}
SHOP_NOISE = re.compile(r"^(ОТГРУЗКА|ПРОВЕРКА)$", re.I)
SHOP_ALIASES = {"К ЗАПУС": "К ЗАПУСК", "ОЖД ИНФ": "ОЖИД ИНФ", "ОЖИД КОМП": "ОЖИД КОМПЛ"}


def _flat(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _table(grid: list[list[str]]) -> list[dict[str, str]]:
    """Строки листа словарями по шапке (первая строка), пустые строки — прочь."""
    if not grid:
        return []
    header = [_flat(cell).upper() for cell in grid[0]]
    return [
        {name: (row[i] if i < len(row) else "").strip() for i, name in enumerate(header) if name}
        for row in grid[1:] if any(str(cell).strip() for cell in row)
    ]


def _day(value: object) -> date | None:
    match = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})", _flat(value))
    if not match:
        return None
    try:
        return date(int(match[3]), int(match[2]), int(match[1]))
    except ValueError:
        return None


def _moment(value: object) -> datetime | None:
    from app.shared.clock import MOSCOW

    match = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", _flat(value))
    if not match:
        return None
    return datetime(int(match[3]), int(match[2]), int(match[1]), int(match[4] or 0), int(match[5] or 0),
                    int(match[6] or 0), tzinfo=MOSCOW)


def spreadsheet_id(url: str) -> str:
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", str(url or ""))
    return match[1] if match else ""


# ── Снимок (Google, только чтение) ───────────────────────────────────────────────────────────


def snapshot(args: argparse.Namespace) -> None:
    from app.modules.requests.google_sheets import GoogleSheetsCalcReader, load_service_account

    raw = Path(args.key).read_text(encoding="utf-8") if args.key else os.getenv("ERP_GOOGLE_SERVICE_ACCOUNT_JSON", "")
    reader = GoogleSheetsCalcReader(load_service_account(raw))
    _title, prod = reader.read_sheets(args.prod, PROD_SHEETS)
    missing = [name for name in PROD_SHEETS if name not in prod]
    if missing:
        raise SystemExit(f"В «ERP — Производство» нет листов: {', '.join(missing)}")
    _title, shop = reader.read_sheets(args.shop, (SHOP_SHEET,))
    calcs: dict[str, dict] = {}
    errors, tried = [], set()
    for row in _table(prod["Наборы"]):
        calc_id = spreadsheet_id(row.get("РАСЧЁТ", ""))
        if not calc_id or calc_id in tried:
            continue
        tried.add(calc_id)
        for attempt in range(6):
            try:
                title, sheets = reader.read_sheets(calc_id, ("ЗКЗ", "СПЯ"))
                calcs[calc_id] = {"title": title, "sheets": sheets}
                break
            except Exception as error:  # noqa: BLE001 — проект останется строкой реестра
                if "429" in str(error) and attempt < 5:
                    time.sleep(15 * (attempt + 1))  # квота Sheets API: 60 чтений в минуту
                    continue
                errors.append(f"{row.get('№ ПРОЕКТА')}: {error}")
                break
    Path(args.out).write_text(json.dumps({
        "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prod": prod, "shop": shop.get(SHOP_SHEET) or [], "calcs": calcs,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"Проекты: {len(_table(prod['Проекты']))}, журнал: {len(_table(prod['Журнал проектов']))}, "
          f"расчётов наборов: {len(calcs)}, лист цеха: {len(shop.get(SHOP_SHEET) or [])} строк → {args.out}")
    for line in errors:
        print("Расчёт не прочитан: " + line)


# ── Сверка с листом цеха (planShopSync_) ─────────────────────────────────────────────────────


def shop_status(value: object) -> str:
    text = re.sub(r"[.\s]+$", "", _flat(value).upper())
    return SHOP_ALIASES.get(text, text)


def shop_rows(grid: list[list[str]]) -> list[dict]:
    """diagPlanSyncFromSheet: строки под шапкой с «№ Проекта» и «Статус», у которых есть №."""
    header_index = next((i for i, row in enumerate(grid) if "№ Проекта" in row and "Статус" in row), None)
    if header_index is None:
        raise SystemExit("В «ЗАГРУЗ» не найдена шапка «№ Проекта» / «Статус»")
    header = grid[header_index]

    def column(label: str) -> int:
        return header.index(label) if label in header else -1

    number, status, comment, deadline = column("№ Проекта"), column("Статус"), column("Комментарий"), column("Дедлайн")
    customer = next((i for i, h in enumerate(header) if re.match(r"^Наименование орган", str(h).strip(), re.I)), -1)
    cell = lambda row, i: row[i] if 0 <= i < len(row) else ""  # noqa: E731
    return [{
        "no": cell(row, number), "status": cell(row, status), "comment": cell(row, comment),
        "deadline": cell(row, deadline), "customer": cell(row, customer),
    } for row in grid[header_index + 1:] if str(cell(row, number)).strip()]


def shop_sync(sheet: list[dict], portal: list[dict]) -> dict:
    """Порт planShopSync_: записи журнала, которые приведут план к листу цеха. Чистая.
    portal — строки плана: {no, terminal, stage, kbs: [{index, status}], blocker: {type, text}|None,
    deadline: date|None, texts: [тексты журнала]}."""
    from app.modules.production.plan import KIT_STAGES, PLAN_BLOCKERS, normalize_project_number as key

    by_no: dict[str, dict] = {}
    for row in sheet:
        k = key(row["no"])
        if not k:
            continue
        o = by_no.setdefault(k, {"no": _flat(row["no"]), "customer": _flat(row["customer"]), "live": [], "done": [],
                                 "comments": [], "live_comments": [], "deadlines": []})
        statuses = [shop_status(part) for part in str(row["status"] or "").split(",") if shop_status(part)]
        # Строка «ОТГРУЖЕН» рядом с живой строкой — частичная отгрузка: этап и блокер берём с живых строк.
        done = "ОТГРУЖЕН" in statuses
        bag = o["done"] if done else o["live"]
        bag.extend(s for s in statuses if s not in bag)
        comment = _flat(row["comment"])
        if comment and not SHOP_NOISE.match(comment) and comment not in o["comments"]:
            o["comments"].append(comment)
        if comment and not done and comment not in o["live_comments"]:
            o["live_comments"].append(comment)
        if _day(row["deadline"]):
            o["deadlines"].append((_day(row["deadline"]), done))
    for o in by_no.values():
        o["statuses"] = o["live"] or o["done"]

    entries, seen, not_in_sheet = [], set(), []
    for p in portal:
        k = key(p["no"])
        s = by_no.get(k)
        if s is None:
            if not p["terminal"] and p["kbs"]:
                not_in_sheet.append(f"{p['no']} ({p['stage']})")
            continue
        seen.add(k)
        add = lambda entry: entries.append({"project": p["no"], **entry})  # noqa: E731
        target = max((KIT_STAGES.index(SHOP_STAGE[st]) for st in s["statuses"] if st in SHOP_STAGE), default=-1)
        why_stage = "по листу цеха: " + ", ".join(s["statuses"])
        if not p["terminal"] and target > 0 and not p["kbs"] and KIT_STAGES.index(p["stage"]) < target:
            add({"kind": "stage", "item_index": "", "value": KIT_STAGES[target], "text": why_stage})
        if not p["terminal"] and target > 0:
            for kb in p["kbs"]:
                if KIT_STAGES.index(kb["status"]) < target:
                    add({"kind": "stage", "item_index": kb["index"], "value": KIT_STAGES[target], "text": why_stage})
        blocker = next((b for b in PLAN_BLOCKERS if b in s["statuses"]), "")
        why = " · ".join(s["live_comments"] or s["comments"]) or ", ".join(s["statuses"])
        # Новый блокер уносит комментарии листа своим текстом — отдельными записями их не дублируем.
        block_new = bool(blocker) and (not p["blocker"] or p["blocker"]["type"] != blocker)
        if block_new:
            add({"kind": "block", "blocker": blocker, "text": why})
        elif not blocker and p["blocker"]:
            add({"kind": "unblock", "text": why_stage})
        live = [d for d, done in s["deadlines"] if not done]
        deadline = (live or [d for d, _ in s["deadlines"]] or [None])[0]
        if deadline and deadline != p["deadline"]:
            add({"kind": "deadline", "deadline": deadline, "text": "из листа цеха «ЗАГРУЗ»"})
        known = "\n".join([_flat(t) for t in p["texts"]] + [_flat((p["blocker"] or {}).get("text"))])
        if not block_new:
            for comment in s["comments"]:
                if comment not in known:
                    add({"kind": "note", "text": comment})
    missing = [{"no": o["no"], "customer": o["customer"], "text": f"{o['no']} — {', '.join(o['statuses'])}"}
               for k, o in by_no.items() if k not in seen]
    return {"entries": entries, "missing": missing, "not_in_sheet": not_in_sheet}


# ── Заведение в ERP ───────────────────────────────────────────────────────────────────────────


class SnapshotSheets:
    """`CalcSheetsReader` над снимком: ERP читает расчёт так же, как из Google, только без сети."""

    def __init__(self, calcs: dict[str, dict]):
        self.calcs = calcs

    def read_sheets(self, spreadsheet_id: str, names: tuple[str, ...]):
        from app.modules.requests.application import CalcSheetError

        calc = self.calcs.get(spreadsheet_id)
        if calc is None:
            raise CalcSheetError("Расчёта нет в снимке", "access")
        return calc["title"], {name: calc["sheets"][name] for name in names if name in calc["sheets"]}


def apply(args: argparse.Namespace) -> None:
    os.environ.setdefault("ERP_COOKIE_SECURE", "0")
    if not os.getenv("ERP_DATABASE_URL"):
        raise SystemExit("Задайте ERP_DATABASE_URL (база, куда заводить)")
    from fastapi.testclient import TestClient

    from app.main import app
    from app.modules.production.plan import PLAN_KINDS, PlanEvent, PlanProjectSnapshot, normalize_project_number
    from app.modules.production.plan_application import (
        AddPlanEventCommand, ReplacePlanProjectsCommand, add_plan_event, replace_plan_projects,
    )
    from app.modules.techcards.kitsets import project_number_valid
    from app.shared.clock import moscow_now
    from app.shared.identity import Actor

    data = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    projects = _table(data["prod"]["Проекты"])
    journal = _table(data["prod"]["Журнал проектов"])
    kit_rows = _table(data["prod"]["Наборы"])
    production = app.state.production_uow_factory
    now = moscow_now()
    importer = Actor(id=uuid.uuid4(), name="перенос из SUPPLYMAP", department="")
    report: dict[str, list[str]] = {"loaded": [], "failed": [], "created": []}
    counts = {"journal": 0, "shop": 0}

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

    # 1. Наборы — из расчёта по ссылке набора.
    existing = {normalize_project_number(item["project"]) for item in call("GET", "/api/techcards/kitsets")["items"]}
    kitsets: dict[str, dict] = {}
    for row in kit_rows:
        kitsets.setdefault(row.get("№ ПРОЕКТА", ""), row)
    for number, row in kitsets.items():
        calc_id = spreadsheet_id(row.get("РАСЧЁТ", ""))
        if not number or (normalize_project_number(number) in existing and not args.refresh):
            continue
        if calc_id not in data["calcs"]:
            report["failed"].append(f"{number}: расчёт не прочитан при снимке")
            continue
        try:
            result = call("POST", "/api/techcards/kitsets/load", json={
                "url": row["РАСЧЁТ"], "number": number if project_number_valid(number) else "",
            })["res"]
            report["loaded"].append(f"{result['project']}: КБ {result['assemblies']}, строк {result['lines']}")
        except RuntimeError as error:
            report["failed"].append(f"{number}: {error}")

    # 2. Реестр «Проекты»: строки SUPPLYMAP поверх своих, чужие строки ERP остаются.
    with production() as uow:
        registry = {item.project: item for item in uow.plan_projects.list()}
    for row in projects:
        project = normalize_project_number(row.get("№ ПРОЕКТА"))
        if not project:
            continue
        current = registry.get(project)
        registry[project] = PlanProjectSnapshot(
            project=project, customer=row.get("ЗАКАЗЧИК") or (current.customer if current else "") or "—",
            opened_on=_day(row.get("ДАТА")) or (current.opened_on if current else None),
            deadline=_day(row.get("ДЕДЛАЙН")), status=row.get("СТАТУС", ""), terminal=False,
        )
    replace_plan_projects(ReplacePlanProjectsCommand(tuple(registry.values()), importer, now), production())

    # 3. «Журнал проектов» — как есть, с датой и автором записи.
    with production() as uow:
        have = {(e.project, e.occurred_at.replace(tzinfo=None, microsecond=0) if e.occurred_at else None,
                 e.kind, e.text, e.item_index) for e in uow.plan_events.list()}
        for row in journal:
            project = normalize_project_number(row.get("№ ПРОЕКТА"))
            occurred = _moment(row.get("ДАТА"))
            if not project or occurred is None:
                report["failed"].append(f"журнал: строка без № или даты — {row}")
                continue
            kind = row.get("ВИД", "")
            key = (project, occurred.replace(tzinfo=None), kind, row.get("ТЕКСТ", ""), row.get("КБ", ""))
            if key in have:
                continue
            deadline = None
            if kind == PLAN_KINDS["deadline"]:
                match = re.search(r"→\s*(\d{1,2}\.\d{1,2}\.\d{4})", row.get("ТЕКСТ", ""))
                deadline = _day(match[1]) if match else None
            uow.plan_events.add(PlanEvent(
                project=project, occurred_at=occurred, who=row.get("КТО", ""), kind=kind,
                blocker=row.get("БЛОКЕР", ""), text=row.get("ТЕКСТ", ""), deadline=deadline,
                item_index=row.get("КБ", ""), value=row.get("ЗНАЧЕНИЕ", ""),
            ))
            have.add(key)
            counts["journal"] += 1
        uow.commit()

    # 4. Сверка с листом цеха (diagPlanSyncFromSheet): недостающие — в реестр, затем записи журнала.
    sheet = shop_rows(data["shop"])
    shop = Actor(id=uuid.uuid4(), name=f"план цеха {now.date().isoformat()}", department="")

    def portal() -> list[dict]:
        with production() as uow:
            texts: dict[str, list[str]] = {}
            for event in uow.plan_events.list():
                texts.setdefault(normalize_project_number(event.project), []).append(event.text)
        return [{
            "no": row["project"], "terminal": row["terminal"], "stage": row["stage"],
            "kbs": [{"index": kit["item_index"], "status": kit["status"]} for kit in row["kits"]],
            "blocker": row["blocker"], "deadline": date.fromisoformat(row["deadline"]) if row["deadline"] else None,
            "texts": texts.get(normalize_project_number(row["project"]), []),
        } for row in call("GET", "/api/production/plan")["rows"]]

    result = shop_sync(sheet, portal())
    if result["missing"]:
        with production() as uow:
            registry = {item.project: item for item in uow.plan_projects.list()}
        for item in result["missing"]:
            project = normalize_project_number(item["no"])
            registry[project] = PlanProjectSnapshot(project=project, customer=item["customer"] or "—", opened_on=now.date())
            report["created"].append(project)
        replace_plan_projects(ReplacePlanProjectsCommand(tuple(registry.values()), shop, now), production())
        result = shop_sync(sheet, portal())
    with production() as uow:
        known = tuple(item.project for item in uow.plan_projects.list())
    for entry in result["entries"]:
        try:
            add_plan_event(AddPlanEventCommand(
                project=entry["project"], kind=entry["kind"], blocker=entry.get("blocker", ""),
                text=entry.get("text", ""), deadline=entry.get("deadline"), known_projects=known,
                actor=shop, now=moscow_now(), item_index=entry.get("item_index", ""), value=entry.get("value", ""),
            ), production())
            counts["shop"] += 1
        except Exception as error:  # noqa: BLE001 — одна запись не роняет сверку
            report["failed"].append(f"{entry['project']} {entry['kind']}: {error}")

    rows = call("GET", "/api/production/plan")["rows"]
    print(f"Наборы из расчёта: заведено {len(report['loaded'])} (всего в SUPPLYMAP {len(kitsets)})")
    for line in report["loaded"]:
        print("  " + line)
    print(f"Реестр SUPPLYMAP: {len(projects)} проектов; журнал: перенесено {counts['journal']} из {len(journal)}")
    print(f"Лист цеха: записей журнала {counts['shop']}"
          + (f", заведено в реестр: {', '.join(report['created'])}" if report["created"] else ""))
    if result["not_in_sheet"]:
        print("В плане, но нет в листе цеха: " + ", ".join(result["not_in_sheet"]))
    print(f"В плане цеха ERP: {len(rows)} проектов")
    for line in report["failed"]:
        print("ОШИБКА: " + line)
    if report["failed"]:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot", help="прочитать проекты SUPPLYMAP, расчёты и лист цеха в JSON")
    snap.add_argument("--key", help="JSON-ключ сервисного аккаунта (иначе ERP_GOOGLE_SERVICE_ACCOUNT_JSON)")
    snap.add_argument("--prod", default=PROD_SPREADSHEET)
    snap.add_argument("--shop", default=SHOP_SPREADSHEET)
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

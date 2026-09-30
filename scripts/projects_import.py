#!/usr/bin/env python
"""Один производственный проект SUPPLYMAP → ERP командой, по запросу. Владелец 30.09.2026: все проекты снесены
(миграция 0078_wipe_projects_once), дальше проекты заводит он сам в ERP («Создать проект из расчёта») или по одному
через чат. Сами ни обновление, ни сервер проекты не грузят.

    cd backend
    ERP_DATABASE_URL=sqlite+pysqlite:///real.db .venv/bin/python ../scripts/projects_import.py load 708К.4 --key <sa.json>
    # на ПК владельца одной строкой: powershell -ExecutionPolicy Bypass -File scripts\\load-project.ps1 708К.4

Google только читается: «ERP — Производство» (Проекты, Журнал проектов, Наборы, Техкарты), расчёт проекта по его
ссылке РАСЧЁТ (ЗКЗ, СПЯ и листы КБn) и лист цеха «ЗАГРУЗ» («Производственный план v.0.1»). Для каждого номера, как
это лежит в SUPPLYMAP:
1. Набор — «Создать проект из расчёта» (`load_kitset`): карточка, строка реестра, очередь отгрузки.
2. Реестр «Проекты» — строка плана (№, заказчик, дата, дедлайн, статус).
3. «Журнал проектов» — записи журнала плана как есть: дата, кто, вид, блокер, текст, КБ, значение.
4. Сверка с листом цеха — порт `planShopSync_` / `diagPlanSyncFromSheet` (ProdPlan.gs): этапы КБ только вперёд,
   блокер, снятие блокера, дедлайн, комментарии — записями журнала от «план цеха <дата>»; проекта нет в
   реестре — заводится строкой (№ + заказчик).
5. Отгруженное по листу цеха (`sync_shipped`), затем ТК: выданные в SUPPLYMAP переносятся, остальным сборкам —
   генератор, как кнопка «Создать» (`app.workflows.techcards_all` только для этого проекта).
Проект, который уже есть в ERP (карточка или строка реестра), не трогается. Полностью отгруженный по листу цеха не
загружается.
"""

from __future__ import annotations

import argparse
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


def _wall(value: datetime | None) -> datetime | None:
    """Московское «настенное» время без зоны: SQLite отдаёт его как записано, PostgreSQL — в UTC с зоной."""
    from app.shared.clock import MOSCOW

    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(MOSCOW).replace(tzinfo=None)
    return value.replace(microsecond=0)


def spreadsheet_id(url: str) -> str:
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", str(url or ""))
    return match[1] if match else ""


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


def shipped_projects(sheet: list[dict]) -> set[str]:
    """Проекты листа цеха, у которых все строки «ОТГРУЖЕН». Владелец 29.09: отгруженное в ERP не переносить.
    Частичная отгрузка (рядом есть живая строка) остаётся."""
    from app.modules.production.plan import normalize_project_number as key

    statuses: dict[str, list[str]] = {}
    for row in sheet:
        if key(row["no"]):
            statuses.setdefault(key(row["no"]), []).append(
                ",".join(shop_status(part) for part in str(row["status"] or "").split(",")))
    return {k for k, values in statuses.items() if all("ОТГРУЖЕН" in value for value in values)}


# Сколько уже уехало, из комментария строки: «Отгрузили 154 шт», «ОТГРУЗКА  60000». На строке «ОТГРУЖЕН» годится любое
# «отгруз…», на живой — только прошедшее «отгрузил…» («Отгрузили 50 шт. Придут разъемы…»): «ОТГРУЗКА 600» там — план.
_QTY = r"\s+(\d{1,3}(?:[ \u00a0]\d{3})+|\d+)"
SHIPPED_DONE = re.compile(r"отгруз\w*" + _QTY, re.I)
SHIPPED_LIVE = re.compile(r"отгрузил\w*" + _QTY, re.I)


def partial_shipments(sheet: list[dict]) -> dict[str, dict]:
    """Частичные отгрузки листа цеха (у проекта есть и живые строки): проект → {"no", "total", "texts"}.
    total — сумма чисел из комментариев; None — строка «ОТГРУЖЕН» есть, а сколько уехало, не написано. Чистая."""
    from decimal import Decimal

    from app.modules.production.plan import normalize_project_number as key

    found: dict[str, dict] = {}
    shipped = shipped_projects(sheet)
    for row in sheet:
        k = key(row["no"])
        if not k or k in shipped:
            continue
        done = "ОТГРУЖЕН" in [shop_status(part) for part in str(row["status"] or "").split(",")]
        comment = _flat(row["comment"])
        match = (SHIPPED_DONE if done else SHIPPED_LIVE).search(comment)
        if not done and not match:
            continue
        o = found.setdefault(k, {"no": _flat(row["no"]), "total": Decimal("0"), "texts": [], "unknown": False})
        o["texts"].append(comment or ", ".join(shop_status(p) for p in str(row["status"]).split(",")))
        if match:
            o["total"] += Decimal(re.sub(r"\s", "", match[1]))
        else:
            o["unknown"] = True
    return {k: {"no": o["no"], "texts": o["texts"], "total": None if o["unknown"] and not o["total"] else o["total"]}
            for k, o in found.items()}


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


TECHCARDS_SHEET = "Техкарты"


def _retrying(read):
    """Квота Sheets API — 60 чтений в минуту: на 429 ждём и повторяем."""
    for attempt in range(6):
        try:
            return read()
        except Exception as error:  # noqa: BLE001 — только 429 повторяем, остальное наверх
            if "429" in str(error) and attempt < 5:
                time.sleep(15 * (attempt + 1))
                continue
            raise
    return None


def plan_rows(state, today: date) -> list[dict]:
    """Строки плана цеха для сверки с листом цеха — то же, что GET /api/production/plan, без HTTP и входа."""
    from app.workflows.production_plan import build_production_plan

    def read(factory, reader):
        if factory is None:
            return ()
        with factory() as uow:
            return tuple(reader(uow))

    production = state.production_uow_factory
    techcards = state.techcards_uow_factory
    rows = build_production_plan(
        cards=read(techcards, lambda uow: uow.techcards.list()),
        tasks=read(production, lambda uow: uow.production.list(limit=10_000)),
        fulfillment=read(getattr(state, "fulfillment_uow_factory", None), lambda uow: uow.fulfillment.list(limit=10_000)),
        goods=read(getattr(state, "shipping_uow_factory", None), lambda uow: uow.goods.list(limit=10_000)),
        requests=read(getattr(state, "uow_factory", None), lambda uow: uow.requests.recent(10_000)),
        procurement=read(getattr(state, "procurement_uow_factory", None), lambda uow: uow.procurement.list(limit=10_000)),
        projects=read(production, lambda uow: uow.plan_projects.list()),
        events=read(production, lambda uow: uow.plan_events.list()),
        today=today, kitsets=read(techcards, lambda uow: uow.kitsets.list()),
    )
    return [{
        "project": row.project, "terminal": row.terminal, "stage": row.stage.status,
        "kits": [{"item_index": kit.item_index, "status": kit.status} for kit in row.stage.kits],
        "blocker": None if row.journal.blocker is None else {
            "type": row.journal.blocker.type, "text": row.journal.blocker.text,
        },
        "deadline": row.deadline,
    } for row in rows]


def load_projects(numbers: list[str], prod: dict[str, list[list[str]]], shop_grid: list[list[str]], reader, state, *,
                  now: datetime, generator=None) -> dict:
    """Номера — в ERP шагами 1–5 (см. модуль). reader — Google Sheets (`read_sheets`), им читаются расчёты;
    generator — форма генератора ТК (`techcards_all.node_generator`), None — ТК только из SUPPLYMAP.
    → {"loaded", "skipped", "failed", "notes", "techcards": строки итога}."""
    from app.modules.production.plan import PLAN_KINDS, PlanEvent, PlanProjectSnapshot, normalize_project_number as key
    from app.modules.production.plan_application import (
        AddPlanEventCommand, ReplacePlanProjectsCommand, add_plan_event, replace_plan_projects,
    )
    from app.modules.techcards.kitsets import project_key, project_number_valid
    from app.shared.identity import Actor
    from app.workflows.project_kitset import LoadKitsetCommand, load_kitset, read_calc_by_link
    from app.workflows.techcards_all import GeneratorUnavailable, format_report, generate, import_supplymap

    wanted: dict[str, str] = {}
    for number in numbers:
        if key(number):
            wanted.setdefault(key(number), number.strip())
    projects = [row for row in _table(prod.get("Проекты") or []) if key(row.get("№ ПРОЕКТА")) in wanted]
    journal = [row for row in _table(prod.get("Журнал проектов") or []) if key(row.get("№ ПРОЕКТА")) in wanted]
    kit_rows: dict[str, dict] = {}
    for row in _table(prod.get("Наборы") or []):
        kit_rows.setdefault(key(row.get("№ ПРОЕКТА")), row)
    sheet = shop_rows(shop_grid) if shop_grid else []
    shipped = shipped_projects(sheet)
    importer = Actor(id=uuid.uuid4(), name="перенос из SUPPLYMAP", department="")
    report: dict[str, list[str]] = {"loaded": [], "skipped": [], "failed": [], "notes": [], "techcards": []}
    for k in sorted(wanted.keys() & shipped):
        report["skipped"].append(f"{wanted[k]}: отгружен по листу цеха «ЗАГРУЗ» — не загружаю")
    production = state.production_uow_factory
    techcards = state.techcards_uow_factory
    # Проект, уже заведённый в ERP (владельцем или прошлой загрузкой), не трогается: его правят в ERP.
    with techcards() as uow:
        existing = {key(item.project) for item in uow.kitsets.list()}
    with production() as uow:
        existing |= {key(item.project) for item in uow.plan_projects.list()}
    for k in sorted((set(wanted) - shipped) & existing):
        report["skipped"].append(f"{wanted[k]}: уже есть в ERP — не трогаю")
    active = set(wanted) - shipped - existing
    if not active:
        return report

    # 1. Набор — из расчёта по ссылке РАСЧЁТ, тем же путём, что кнопка «Создать проект из расчёта».
    queue = getattr(state, "kitset_name_queue", None)
    for k in sorted(active & kit_rows.keys()):
        row = kit_rows[k]
        number = row.get("№ ПРОЕКТА", "")
        try:
            data = _retrying(lambda: read_calc_by_link(row.get("РАСЧЁТ", ""), lambda: reader))
            result = load_kitset(LoadKitsetCommand(
                data=data, number=number if project_number_valid(number) else "", deadline=None, ship={},
                actor=importer, now=now,
            ), techcards, production, getattr(state, "shipping_uow_factory", None),
                queue.enqueue if queue is not None else None)
        except Exception as error:  # noqa: BLE001 — проект пробуется снова при следующем прогоне
            report["failed"].append(f"{number}: расчёт не прочитан — {error}")
            continue
        report["loaded"].append(f"{result.project}: КБ {result.assemblies}, строк {result.lines}")

    # 2. Реестр «Проекты»: строка SUPPLYMAP поверх той, что завела карточка; остальные строки ERP остаются.
    if projects:
        with production() as uow:
            registry = {item.project: item for item in uow.plan_projects.list()}
        for row in projects:
            project = key(row.get("№ ПРОЕКТА"))
            if project not in active:
                continue
            current = registry.get(project)
            registry[project] = PlanProjectSnapshot(
                project=project, customer=row.get("ЗАКАЗЧИК") or (current.customer if current else "") or "—",
                opened_on=_day(row.get("ДАТА")) or (current.opened_on if current else None),
                deadline=_day(row.get("ДЕДЛАЙН")), status=row.get("СТАТУС", ""), terminal=False,
            )
        replace_plan_projects(ReplacePlanProjectsCommand(tuple(registry.values()), importer, now), production())

    # 3. «Журнал проектов» — как есть, с датой и автором записи; повтор не дублирует.
    journal_added = 0
    with production() as uow:
        have = {(e.project, _wall(e.occurred_at), e.kind, e.text, e.item_index) for e in uow.plan_events.list()}
        for row in journal:
            project = key(row.get("№ ПРОЕКТА"))
            occurred = _moment(row.get("ДАТА"))
            if project not in active:
                continue
            if occurred is None:
                report["notes"].append(f"журнал {row.get('№ ПРОЕКТА')}: строка без даты — пропущена")
                continue
            kind = row.get("ВИД", "")
            entry = (project, _wall(occurred), kind, row.get("ТЕКСТ", ""), row.get("КБ", ""))
            if entry in have:
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
            have.add(entry)
            journal_added += 1
        uow.commit()

    # 4. Сверка с листом цеха (diagPlanSyncFromSheet) — только строки этих проектов.
    shop = Actor(id=uuid.uuid4(), name=f"план цеха {now.date().isoformat()}", department="")
    sheet = [row for row in sheet if key(row["no"]) in active]

    def portal() -> list[dict]:
        with production() as uow:
            texts: dict[str, list[str]] = {}
            for event in uow.plan_events.list():
                texts.setdefault(key(event.project), []).append(event.text)
        return [{**row, "no": row["project"], "kbs": [{"index": kit["item_index"], "status": kit["status"]}
                                                      for kit in row["kits"]],
                 "texts": texts.get(key(row["project"]), [])}
                for row in plan_rows(state, now.date()) if key(row["project"]) in active]

    result = shop_sync(sheet, portal())
    if result["missing"]:
        with production() as uow:
            registry = {item.project: item for item in uow.plan_projects.list()}
        for item in result["missing"]:
            project = key(item["no"])
            registry[project] = PlanProjectSnapshot(project=project, customer=item["customer"] or "—", opened_on=now.date())
        replace_plan_projects(ReplacePlanProjectsCommand(tuple(registry.values()), shop, now), production())
        result = shop_sync(sheet, portal())
    with production() as uow:
        known = tuple(item.project for item in uow.plan_projects.list())
    shop_added = 0
    for entry in result["entries"]:
        try:
            add_plan_event(AddPlanEventCommand(
                project=entry["project"], kind=entry["kind"], blocker=entry.get("blocker", ""),
                text=entry.get("text", ""), deadline=entry.get("deadline"), known_projects=known,
                actor=shop, now=now, item_index=entry.get("item_index", ""), value=entry.get("value", ""),
            ), production())
            shop_added += 1
        except Exception as error:  # noqa: BLE001 — одна запись не роняет сверку
            report["notes"].append(f"{entry['project']} {entry['kind']}: {error}")
    report["notes"].append(f"журнал: перенесено {journal_added}, из листа цеха {shop_added}")

    # Что теперь в ERP: без расчёта в «Наборы» — только строка реестра; нигде нет — не загружен.
    with techcards() as uow:
        cards = {key(item.project): item.project for item in uow.kitsets.list()}
    with production() as uow:
        registered = {key(item.project) for item in uow.plan_projects.list()}
    for k in sorted(active - cards.keys() - kit_rows.keys()):
        if k in registered:
            report["notes"].append(f"{wanted[k]}: в «Наборы» нет расчёта — заведена только строка реестра")
        else:
            report["failed"].append(f"{wanted[k]}: номера нет ни в «Наборы», ни в «Проекты», ни в листе цеха")

    # 5. Частичные отгрузки этих проектов по листу цеха — в готовую продукцию, затем их ТК.
    if shop_grid:
        _done, skipped = sync_shipped(shop_grid, state, shop, now, only=active)
        report["notes"] += [f"не перенесено: {line}" for line in skipped]
    only = {project_key(cards[k]) for k in active if k in cards}
    if only:
        tc_actor = Actor(id=uuid.uuid4(), name="перенос из SUPPLYMAP", department="")
        if prod.get(TECHCARDS_SHEET):
            moved = import_supplymap(prod[TECHCARDS_SHEET], techcards, actor=tc_actor, now=now, apply=True, only=only)
            report["techcards"].append(format_report("Выданные ТК из SUPPLYMAP", moved, apply=True, done_title="Перенесено"))
        else:
            report["notes"].append(f"в «ERP — Производство» нет листа «{TECHCARDS_SHEET}» — выданные ТК не перенесены")
        try:
            built = generate(state, generator() if generator else _no_generator(), reader, actor=Actor(
                id=uuid.uuid4(), name="генерация ТК при загрузке проекта", department=""), now=now, apply=True, only=only)
        except GeneratorUnavailable as error:
            report["notes"].append(f"ТК генератором не собраны: {error} — кнопка «Создать» в карточке проекта")
        else:
            report["techcards"].append(format_report("Генерация ТК", built, apply=True, done_title="Собрано и выдано ЭТК"))
    return report


def _no_generator():
    from app.workflows.techcards_all import GeneratorUnavailable

    raise GeneratorUnavailable("генератор не передан")


def load(args: argparse.Namespace) -> int:
    """Номера из аргументов — в базу ERP_DATABASE_URL (сервер можно не останавливать)."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (база, куда заводить)", file=sys.stderr)
        return 2
    raw = args.key or os.getenv("ERP_GOOGLE_SERVICE_ACCOUNT_JSON", "")  # JSON-ключ или путь к нему
    if not raw.strip():
        print("Ключ Google не задан (--key или ERP_GOOGLE_SERVICE_ACCOUNT_JSON)", file=sys.stderr)
        return 2
    from types import SimpleNamespace

    from app.database import configure_database
    from app.modules.requests.google_sheets import GoogleSheetsCalcReader, load_service_account
    from app.shared.clock import moscow_now
    from app.workflows.techcards_all import node_generator

    reader = GoogleSheetsCalcReader(load_service_account(raw))
    _title, prod = _retrying(lambda: reader.read_sheets(args.prod, PROD_SHEETS + (TECHCARDS_SHEET,)))
    _title, shop = _retrying(lambda: reader.read_sheets(args.shop, (SHOP_SHEET,)))
    holder = SimpleNamespace(state=SimpleNamespace())
    engine = configure_database(holder, url)
    try:
        report = load_projects(list(args.numbers), prod, shop.get(SHOP_SHEET) or [], reader, holder.state,
                               now=moscow_now(), generator=node_generator)
    finally:
        engine.dispose()
    for line in report["loaded"]:
        print("Загружен: " + line)
    for line in report["skipped"]:
        print("Пропущен: " + line)
    for line in report["notes"]:
        print("  " + line)
    for block in report["techcards"]:
        print(block)
    for line in report["failed"]:
        print("НЕ ЗАГРУЖЕН: " + line)
    return 1 if report["failed"] else 0


def sync_shipped(shop_grid: list[list[str]], state, actor, now, only: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Отгруженное по листу цеха — в базу. Полностью отгруженные проекты уходят (владелец 29.09: отгруженное не
    нужно): набор каскадом, как «Удалить проект» в карточке, без набора — строка реестра. Частичные отгрузки —
    в готовую продукцию «отгружено до ERP», если КБ у проекта одна (у нескольких не угадать, какая уехала).
    Проект, у которого в ERP уже есть своё движение готовой продукции или отправка, не трогается. Повтор ничего
    не меняет. only — только эти проекты (`normalize_project_number`). → (что сделано, что не перенесено)."""
    from app.modules.production.plan import normalize_project_number as key
    from app.modules.production.plan_application import ReplacePlanProjectsCommand, replace_plan_projects
    from app.modules.shipping.application import DropProjectCommand, ShipBeforeErpCommand, drop_project, ship_before_erp
    from app.workflows.project_kitset import delete_kitset

    sheet = shop_rows(shop_grid)
    shipped = shipped_projects(sheet)
    partial = partial_shipments(sheet)
    if only is not None:
        shipped &= only
        partial = {k: item for k, item in partial.items() if k in only}
    done: list[str] = []
    skipped: list[str] = []
    with state.shipping_uow_factory() as uow:
        goods: dict[str, list] = {}
        for good in uow.goods.list(limit=1_000_000):
            goods.setdefault(key(good.project), []).append(good)
        in_erp = {k for k, rows in goods.items()
                  if uow.goods.in_shipments([g.id for g in rows]) or any(g.accepted > g.before_erp for g in rows)}

    with state.techcards_uow_factory() as uow:
        kitsets = [item.project for item in uow.kitsets.list() if key(item.project) in shipped]
    for project in kitsets:
        if key(project) in in_erp:
            skipped.append(f"{project}: отгружен по листу цеха, но в ERP уже есть приёмка или отправка — не удаляю")
            continue
        delete_kitset(project, actor, now, state.techcards_uow_factory, state.production_uow_factory,
                      state.shipping_uow_factory)
        done.append(f"{project}: отгружен по листу цеха — убран из ERP")
    # Готовая продукция без движения, оставшаяся без карточки, — тоже прочь (`shipGpDropProject_`).
    for k in sorted(shipped - in_erp - {key(p) for p in kitsets}):
        for name in sorted({good.project for good in goods.get(k, [])}):
            result = drop_project(DropProjectCommand(name, actor, now), state.shipping_uow_factory())
            if result.deleted:
                done.append(f"{name}: отгружен по листу цеха — готовая продукция без карточки убрана ({result.deleted})")
    with state.production_uow_factory() as uow:
        projects = list(uow.plan_projects.list())
    kept = [item for item in projects if key(item.project) not in shipped or key(item.project) in in_erp]
    if len(kept) != len(projects):
        replace_plan_projects(ReplacePlanProjectsCommand(tuple(kept), actor, now), state.production_uow_factory())
        gone = {key(p) for p in kitsets}
        done.extend(f"{item.project}: отгружен по листу цеха — строка реестра убрана"
                    for item in projects if item not in kept and key(item.project) not in gone)

    for k, item in sorted(partial.items()):
        rows = goods.get(k, [])
        why = " · ".join(item["texts"])
        if item["total"] is None:
            skipped.append(f"{item['no']}: частично отгружен, количество в листе цеха не написано ({why})")
        elif not rows:
            skipped.append(f"{item['no']}: частично отгружен ({item['total']} шт), в ERP нет готовой продукции проекта")
        elif len(rows) > 1:
            skipped.append(f"{item['no']}: частично отгружен ({item['total']} шт), КБ {len(rows)} — "
                           "какая уехала, не ясно, отметить вручную")
        elif item["total"] > rows[0].before_erp:
            good = ship_before_erp(ShipBeforeErpCommand(rows[0].project, rows[0].item_index, item["total"], actor, now),
                                   state.shipping_uow_factory())
            done.append(f"{good.project} {good.item_index}: отгружено до ERP {item['total']} из {good.ordered} ({why})")
    return done, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("load", help="проект(ы) по номеру из Google — в базу ERP_DATABASE_URL, по запросу")
    run.add_argument("numbers", nargs="+", help="№ проекта, как в «Наборы» (например 708К.4)")
    run.add_argument("--key", help="JSON-ключ сервисного аккаунта (иначе ERP_GOOGLE_SERVICE_ACCOUNT_JSON)")
    run.add_argument("--prod", default=PROD_SPREADSHEET)
    run.add_argument("--shop", default=SHOP_SPREADSHEET)
    return load(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())

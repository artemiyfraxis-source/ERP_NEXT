#!/usr/bin/env python
"""Seed the ERP_NEXT demo instance through the HTTP API only (run once on a fresh, migrated DB).

    cd backend; <venv python> ../scripts/demo/seed_demo.py   (API running on 8021 over ERP_DATABASE_URL)

Every step is wrapped: a failure is printed and the rest keeps going.
"""
import json
import os
import subprocess
import sys
import traceback
import uuid
from datetime import date, timedelta

import httpx

BASE = os.environ.get("DEMO_BASE", "http://127.0.0.1:8021")
BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend")
DB_URL = os.environ.get("ERP_DATABASE_URL") or f"sqlite+pysqlite:///{BACKEND}/demo.db"
PW = "Demo-2026!"
TODAY = date.today()
D = lambda n: str(TODAY + timedelta(days=n))  # noqa: E731
MSK = "г. Москва, ул. 6-я Радиальная, д. 17"
RAM = "г. Раменское, ул. 100-й Свирской Дивизии, д. 52"

COUNT: dict[str, int] = {}
FAILS: list[str] = []


def bump(key: str, n: int = 1) -> None:
    COUNT[key] = COUNT.get(key, 0) + n


class Client:
    def __init__(self, login="preview", password="Preview-2026!"):
        self.login = login
        self.h = httpx.Client(base_url=BASE, timeout=120)
        r = self.h.post("/api/auth/login", json={"login": login, "password": password})
        if r.status_code != 200:
            raise RuntimeError(f"login {login}: {r.status_code} {r.text[:200]}")

    def call(self, method, path, **kw):
        headers = {"x-csrf-token": self.h.cookies.get("erp_csrf", ""), "Idempotency-Key": str(uuid.uuid4())}
        for attempt in range(3):
            try:
                return self.h.request(method, path, headers=headers, **kw)
            except httpx.TransportError:
                if attempt == 2:
                    raise

    def ok(self, method, path, tag=None, **kw):
        try:
            r = self.call(method, path, **kw)
        except Exception as error:  # noqa: BLE001
            FAILS.append(f"{method} {path}: transport {error!r}")
            print(f"  FAIL {method} {path}: transport {error!r}")
            return None
        try:
            body = r.json() if r.content else {}
        except Exception:  # noqa: BLE001
            body = r.text
        if r.status_code >= 400:
            msg = f"{method} {path} -> {r.status_code} {str(body)[:260]}"
            FAILS.append(msg)
            print("  FAIL " + msg)
            return None
        if tag:
            bump(tag)
        return body


def step(title):
    def deco(fn):
        def run(*a, **kw):
            print(f"== {title}")
            try:
                return fn(*a, **kw)
            except Exception as error:  # noqa: BLE001
                FAILS.append(f"STEP {title}: {error!r}")
                print(f"  STEP FAILED {title}: {error!r}")
                traceback.print_exc()
        return run
    return deco


def outbox() -> None:
    """The outbox worker is a separate process: drain it so hand-offs, receipts and notifications land."""
    env = dict(os.environ, ERP_DATABASE_URL=DB_URL)
    r = subprocess.run([sys.executable, "-m", "app.outbox_worker", "--once"],
                       cwd=BACKEND, env=env, capture_output=True, text=True)
    if r.returncode:
        print("  outbox worker:", r.stderr[-300:])


S: dict = {}  # shared state between steps

# ── 1. Staff ────────────────────────────────────────────────────────────────────────────────────────


@step("1. Staff")
def staff():
    adm = S["adm"]
    people = [
        ("petrov.buyer", "Петров Сергей", "Снабжение", "buyer", ""),
        ("sidorova.wh", "Сидорова Мария", "Склад", "receiver", "ЦАР"),
        ("kuznetsov.wh", "Кузнецов Игорь", "Склад", "receiver", "ЖУК"),
        ("ivanov.master", "Иванов Павел", "Производство", "master", "ЦАР"),
        ("smirnova.op", "Смирнова Елена", "Производство", "staff", "ЦАР"),
        ("volkov.op", "Волков Дмитрий", "Производство", "staff", "ЦАР"),
        ("orlova.eng", "Орлова Анна", "Технологический отдел", "engineer", ""),
        ("belov.head", "Белов Андрей", "Производство", "prodhead", ""),
        ("lebedeva.qc", "Лебедева Ольга", "ОТК", "qc", "ЦАР"),
    ]
    for login, name, dept, role, site in people:
        adm.ok("POST", "/api/auth/staff", "staff", json=dict(
            login=login, email=f"{login.replace('.', '_')}@fraxis-demo.ru", name=name, department=dept,
            role=role, site=site, password=PW))
    # Роль qc -> уровень «Бригадир», у которого «ОТК» только на просмотр (после уровней доступа 0086): правку ОТК
    # даём личным исключением, иначе приборы и приёмка ОТК в демо отвечают 403.
    access = adm.ok("GET", "/api/auth/access")
    qc_user = next((u for u in (access or {}).get("users", []) if u["login"] == "lebedeva.qc"), None)
    if qc_user:
        adm.ok("PUT", f"/api/auth/access/users/{qc_user['id']}", json={"access": {"quality": "edit"}})
    for login, *_ in people:
        S[login] = Client(login, PW)


# ── 2. Requests ─────────────────────────────────────────────────────────────────────────────────────


def rq(name, typ, qty, unit, proj, price, *, purpose="Обеспечение производства", cat="ОП", qd="Да",
       point=MSK, dl=7, pl=21, comment=None):
    d = dict(purpose=purpose, delivery_point=point, name=name, item_type=typ, quantity=str(qty), unit=unit,
             quality_document=qd, procurement_deadline=D(dl), planned_date=D(pl), category=cat,
             price_with_vat=price)
    if proj:
        d["project_reference"] = proj
    if comment:
        d["comment"] = comment
    return d


@step("2. Requests")
def requests_():
    eng = S["orlova.eng"]
    items = [
        rq("Провод МГТФ 0.35 мм² белый", "Комплектующие", 200, "м", "614К.1", "38.00", dl=3, pl=10),
        rq("Провод НВ-4 0.5 мм² красный", "Комплектующие", 150, "м", "614К.1", "24.50", dl=3, pl=10),
        rq("Клемма JST XH 2.54 мм, 4 pin, розетка", "Комплектующие", 120, "шт", "614К.1", "9.80", dl=4, pl=12),
        rq("Разъём Molex Micro-Fit 3.0, 12 pin", "Комплектующие", 40, "шт", "614К.1", "65.00", dl=5, pl=14),
        rq("Термоусадочная трубка 3:1, 6 мм, чёрная", "Расходники", 60, "м", "614К.1", "42.00", point=RAM, dl=5, pl=14),
        rq("Кабель КСПВГ 4х0.5", "Комплектующие", 300, "м", "621К.2", "31.00", dl=7, pl=18),
        rq("Разъём DB-9 вилка на кабель", "Комплектующие", 25, "шт", "621К.2", "118.00", dl=8, pl=20),
        rq("Стяжки нейлоновые 200х3.6", "Расходники", 10, "уп", "621К.2", "210.00", point=RAM, dl=9, pl=21),
        rq("Кембрики маркировочные 2.5 мм", "Расходники", 5, "уп", "614К.1", "380.00", dl=10, pl=21),
        rq("Обжимной инструмент для клемм JST", "Инструмент", 2, "шт", "", "4500.00",
           purpose="Хоз деятельность", cat="Общепром", qd="Нет", dl=14, pl=30),
        rq("Флюс-гель ФТС, 10 мл", "Расходники", 6, "шт", "", "150.00",
           purpose="Расходники", cat="Общепром", qd="Нет", dl=14, pl=30),
    ]
    r = eng.ok("POST", "/api/requests", json={"requests": items})
    if r:
        bump("requests", len(r["requests"]))


@step("2b. Hand-off to procurement")
def handoff():
    buyer = S["petrov.buyer"]
    rows = S["adm"].ok("GET", "/api/requests")["requests"]
    ids = [r["id"] for r in rows if not r["name"].startswith(("Обжимной", "Флюс"))]
    if buyer.ok("POST", "/api/requests/handoff", json={"request_ids": ids}):
        bump("handoff", len(ids))
    outbox()


# ── 3. Procurement ──────────────────────────────────────────────────────────────────────────────────

SUPPLIER = {
    "Провод МГТФ": ("ООО «Кабельторг»", "31.15", "КТ-4471"),
    "Провод НВ-4": ("ООО «Кабельторг»", "20.08", "КТ-4471"),
    "Кабель КСПВГ": ("ООО «Кабельторг»", "25.41", "КТ-4471"),
    "Клемма JST": ("ООО «ЧипДип»", "8.03", "ЧД-90215"),
    "Разъём Molex": ("ООО «ЧипДип»", "53.28", "ЧД-90215"),
    "Разъём DB-9": ("ООО «ЧипДип»", "96.72", "ЧД-90215"),
    "Термоусадочная": ("ООО «Электро-Сервис»", "34.43", "ЭС-0533"),
    "Стяжки": ("ООО «Электро-Сервис»", "172.13", "ЭС-0533"),
}


def _prefix(name: str):
    return next(k for k in SUPPLIER if name.startswith(k))


@step("3. Procurement")
def procurement():
    buyer = S["petrov.buyer"]
    # «В закупку» нажал закупщик — строки уже у него «В работе» (взять их не нужно).
    lines = {}
    for l in buyer.ok("GET", "/api/procurement/mine")["lines"]:
        try:
            lines[_prefix(l["calculated_name"])] = l
        except StopIteration:
            pass
    take = ["Провод МГТФ", "Провод НВ-4", "Клемма JST", "Разъём Molex", "Термоусадочная",
            "Кабель КСПВГ", "Разъём DB-9", "Стяжки"]
    missing = [k for k in take if k not in lines]
    if missing:
        FAILS.append(f"procurement: not in buyer's «В работе» after handoff: {missing}")
        return
    bump("taken", len(take))
    ver = {lines[k]["id"]: lines[k]["version"] for k in take}
    S["pline"] = {k: lines[k]["id"] for k in take}
    for k in take:  # «Стяжки» get a supplier but no request: «В работе», group «ООО «Электро-Сервис»»
        l = lines[k]
        r = buyer.ok("PATCH", f"/api/procurement/{l['id']}/facts", "facts", json={
            "version": ver[l["id"]], "supplier": SUPPLIER[k][0],
            "actual_quantity": l["calculated_quantity"], "actual_unit": l["calculated_unit"]})
        if r:
            ver[l["id"]] = r["line"]["version"]
    buyer.ok("POST", "/api/procurement/supplier-requests", "supplier_order_batches", json={"line_ids": [S["pline"][k] for k in take[:7]]})
    mine = {l["id"]: l for l in buyer.ok("GET", "/api/procurement/mine")["lines"]}

    def money(k, invoice_day):
        l = mine[S["pline"][k]]
        sup, price, inv = SUPPLIER[k]
        r = buyer.ok("PATCH", f"/api/procurement/{l['id']}/money", "money", json={
            "version": l["version"], "currency": "RUB", "price": price, "vat_rate": "22%",
            "invoice_item_name": l["calculated_name"], "invoice_number": inv, "invoice_date": D(invoice_day)})
        return r["line"] if r else None

    def invoice(l, arrival):
        r = buyer.ok("POST", f"/api/procurement/{l['id']}/invoice", "invoices", json={
            "version": l["version"],
            "invoice_url": f"https://files.example.com/invoices/{SUPPLIER[_prefix(l['calculated_name'])][2]}.pdf",
            "expected_arrival": D(arrival)})
        return r["line"] if r else None

    S["invoiced"] = {}
    for k, arrival in (("Провод МГТФ", 0), ("Провод НВ-4", 0), ("Клемма JST", 0), ("Разъём Molex", 3), ("Термоусадочная", 4)):
        l = money(k, -3)
        l = invoice(l, arrival) if l else None
        if l:
            S["invoiced"][k] = l
    first = S["invoiced"].get("Провод МГТФ")
    if first:
        buyer.ok("POST", f"/api/procurement/{first['id']}/paid", "paid", json={"version": first["version"]})
    bulk = [{"line_id": v["id"], "version": v["version"]} for k, v in S["invoiced"].items() if k != "Провод МГТФ"]
    if bulk and buyer.ok("POST", "/api/procurement/paid", json={"items": bulk}):
        bump("paid", len(bulk))
    # КСПВГ: invoice loaded, awaiting payment («В оплату»)
    l = money("Кабель КСПВГ", -1)
    if l:
        invoice(l, 8)
    # DB-9 stays «Жду счет», Стяжки «В работе», Кембрики «Новый»
    outbox()


# ── 4. Receiving ────────────────────────────────────────────────────────────────────────────────────


@step("4. Receiving")
def receiving():
    sid, kuz = S["sidorova.wh"], S["kuznetsov.wh"]
    queue = sid.ok("GET", "/api/receiving")["lines"]
    print("  receiving queue ЦАР:", [(l["article"][:20], l["status"]) for l in queue])
    spec = {  # article prefix -> (rack, series, fact qty, quality doc type)
        "Провод МГТФ": ("1F4", "МГ-2609", None, "Паспорт"),
        "Провод НВ-4": ("1F5", "НВ-0918", "148", "Паспорт"),  # 2 м short vs the invoice
        "Клемма JST": ("2B3", "JST-4402", None, "Сертификат"),
    }
    for l in queue:
        key = next((k for k in spec if l["article"].startswith(k)), None)
        if not key:
            continue  # Molex — leave in the queue («В пути»)
        rack, series, fact, qdt = spec[key]
        fact_q = fact or l["ordered_quantity"]
        r = sid.ok("POST", f"/api/receiving/{l['id']}/inspect", "inspected", json={
            "version": l["version"], "fact_quantity": fact_q, "document_quantity": l["ordered_quantity"],
            "shipping_document_url": f"https://files.example.com/upd/{series}.pdf",
            "quality_document_url": f"https://files.example.com/cert/{series}.pdf",
            "series": series, "manufacturer": "ОАО «Завод кабельных изделий»" if "Провод" in key else "JST Mfg.",
            "country": "Россия" if "Провод" in key else "Япония", "quality_document_type": qdt,
            "quality_document_number": f"{series}-К", "document_number": f"УПД-{series[-4:]}",
            "document_date": D(-1)})
        if not r:
            continue
        line = r["line"]
        if line["status"] == "Принято":
            sid.ok("POST", f"/api/receiving/{line['id']}/place", "placed",
                   json={"version": line["version"], "storage_location": rack})
        else:
            print("  inspect result status:", line["status"])
    # ЖУК: термоусадка arrives without quality paperwork -> waits in the queue
    for l in kuz.ok("GET", "/api/receiving")["lines"]:
        if l["article"].startswith("Термоусадочная"):
            kuz.ok("POST", f"/api/receiving/{l['id']}/inspect", "receiving_waiting", json={
                "version": l["version"], "fact_quantity": l["ordered_quantity"],
                "document_quantity": l["ordered_quantity"], "has_shipping_document": True})
    outbox()


# ── 5. Stock ────────────────────────────────────────────────────────────────────────────────────────


@step("5. Stock")
def stock():
    sid, kuz = S["sidorova.wh"], S["kuznetsov.wh"]
    tsar = [
        ("MOLEX-430250800", "MX-2611", "Терминал Molex 43025-0800 (Micro-Fit)", "Комплектующие", "шт", "2500", "3A2"),
        ("PVAM-1.5-BL", "ПВ-0815", "Провод ПВАМ 1,5 белый", "Комплектующие", "м", "800", "1F6"),
        ("PVAM-1.5-BK", "ПВ-0815", "Провод ПВАМ 1,5 чёрный", "Комплектующие", "м", "650", "1F6"),
        ("SMA-J-RG316", "SMA-0902", "Разъём SMA вилка под RG-316", "Комплектующие", "шт", "60", "2C1"),
        ("TUT-3-1-9", "ТУТ-0710", "Термоусадка 3:1, 9 мм, чёрная", "Расходники", "м", "120", "4A1"),
    ]
    zhuk = [
        ("GHR-04V-S", "JST-2601", "Клемма JST GHR-04V-S", "Комплектующие", "шт", "400", "1A1"),
        ("NYLON-200", "СТ-0611", "Стяжки нейлоновые 200х3.6 (пачка 100 шт)", "Расходники", "уп", "24", "2A3"),
        ("FLUX-FTS", "ФЛ-0501", "Флюс-гель ФТС, 10 мл", "Расходники", "шт", "18", "3B1"),
    ]

    def payload(wh, rows):
        return {"items": [dict(warehouse=wh, article=a, series=s, name=n, item_type=t, purpose="Склад", unit=u,
                               quantity=q, storage_location=loc, manufacturer="Demo") for a, s, n, t, u, q, loc in rows]}
    for c, wh, rows in ((sid, "ЦАР", tsar), (kuz, "ЖУК", zhuk)):
        r = c.ok("POST", "/api/stock/batches", json=payload(wh, rows))
        if r:
            bump("stock_batches", len(r["lots"]))
    lots = {l["article"]: l for l in sid.ok("GET", "/api/stock")["lots"]}
    a = lots.get("SMA-J-RG316")
    if a:
        sid.ok("POST", f"/api/stock/batches/{a['id']}/move", "stock_moves",
               json={"version": a["version"], "storage_location": "2C4"})
    b = lots.get("TUT-3-1-9")
    if b:
        sid.ok("POST", f"/api/stock/batches/{b['id']}/writeoff", "stock_writeoffs", json={
            "version": b["version"], "quantity": "6", "purpose": "614К.1", "reason": "Брак при усадке, акт № 12"})
    m = next((l for l in kuz.ok("GET", "/api/stock")["lots"] if l["article"] == "NYLON-200"), None)
    if m:
        kuz.ok("POST", f"/api/stock/batches/{m['id']}/move", "stock_moves",
               json={"version": m["version"], "storage_location": "2A4"})


# ── 6. Techcards ────────────────────────────────────────────────────────────────────────────────────

FIX = json.load(open(f"{BACKEND}/tests/techcards/fixtures/current-etk.json", encoding="utf-8"))["files"]


def _card(eng, project, index, name, kit_file, qty, *, issue):
    cfg = json.loads(json.dumps(FIX[kit_file]["payload"]["config"]))
    cfg.update(projectNo=project, assemblyIndex=index, assemblyName=name, partQty=qty)
    created = eng.ok("POST", "/api/techcards/projects", "techcards",
                     json={"project": project, "item_index": index, "product_name": name, "deadline": D(25)})
    if not created:
        return None
    built = eng.ok("POST", f"/api/techcards/projects/{created['id']}/build", "techcards_built",
                   json={"version": created["version"], "config": cfg})
    if not built:
        return created
    if issue:
        issued = eng.ok("POST", f"/api/techcards/projects/{created['id']}/issue", "techcards_issued",
                        json={"version": built["version"]})
        return issued or built
    return built


@step("6. Techcards")
def techcards():
    eng = S["orlova.eng"]
    S["card1"] = _card(eng, "614К.1", "КБ1", "Жгут питания стойки", "kit-720К.1.2-КБ1.json", 100, issue=True)
    S["card2"] = _card(eng, "614К.1", "КБ2", "Жгут питания блока Molex 12P", "kit-720К.-КБ1.json", 40, issue=True)
    S["card3"] = _card(eng, "621К.2", "КБ1", "Кабель интерфейсный КСПВГ / DB-9", "kit-345К.2-КБ1.json", 25, issue=False)
    _card(eng, "708К.2", "КБ1", "Кабель питания контроллера", "kit-708К.2-КБ1.json", 10, issue=True)
    # kitsets need a Google Sheets URL (kitsets/load reads a live calculation) — skipped, see report.


# ── 7. Production ───────────────────────────────────────────────────────────────────────────────────


@step("7. Production")
def production():
    m, sm, vk, qc = S["ivanov.master"], S["smirnova.op"], S["volkov.op"], S["lebedeva.qc"]
    ops = {o["name"]: o["id"] for o in m.ok("GET", "/api/production/operators")["operators"]}
    m.call("PUT", "/api/production/calendar", json={
        "holidays": ["2026-11-04"],
        "shift": {"weekdays": [0, 1, 2, 3, 4], "start": "09:00", "finish": "18:00",
                  "breaks": [["13:00", "14:00"]], "effective_hours": "8"},
        "absences": [{"operator_id": ops["Волков Дмитрий"], "from_date": D(12), "to_date": D(16), "kind": "Отпуск"}]})
    card1, card2 = S.get("card1"), S.get("card2")
    if not card1:
        return
    t1 = m.ok("POST", "/api/production/tasks", "tasks", json={"techcard_id": card1["id"], "quantity": 100, "deadline": D(20)})
    t2 = m.ok("POST", "/api/production/tasks", "tasks", json={"techcard_id": card2["id"], "quantity": 40, "deadline": D(25)}) if card2 else None
    if not t1:
        return
    task = t1["task"]
    codes = list(dict.fromkeys(o["code"] for tt in (t1, t2) if tt for o in tt["task"]["operations"]))
    for name, stars in (("Смирнова Елена", 5), ("Волков Дмитрий", 4)):
        m.call("PUT", f"/api/production/skills/{ops[name]}", json={"skills": [
            {"operation_code": c, "allowed": True, "stars": stars, "coefficient": "1"} for c in codes]})
    pv = m.ok("POST", f"/api/production/tasks/{task['id']}/schedule",
              json={"version": task["version"], "replan": False, "max_crew": 2})
    if pv:
        ap = m.ok("POST", f"/api/production/tasks/{task['id']}/schedule/apply", "task_scheduled", json={
            "version": task["version"], "replan": False, "max_crew": 2,
            "transfer_batch": pv["variants"][0]["transfer_batch"]})
        if ap:
            task = ap["task"]
    # task 2: scheduled too, then driven through the whole route (op by op) up to ОТК / ГП. A box handed in is taken by
    # the next operation at once: the master's «confirm» step is gone since 13.10 (POST …/confirm answers 405).
    task2 = None
    if t2:
        tk2 = t2["task"]
        pv2 = m.ok("POST", f"/api/production/tasks/{tk2['id']}/schedule",
                   json={"version": tk2["version"], "replan": False, "max_crew": 2})
        if pv2:
            ap2 = m.ok("POST", f"/api/production/tasks/{tk2['id']}/schedule/apply", "task_scheduled", json={
                "version": tk2["version"], "replan": False, "max_crew": 2,
                "transfer_batch": pv2["variants"][0]["transfer_batch"]})
            if ap2:
                tk2 = ap2["task"]
        prev = None
        for i, op in enumerate(tk2["operations"]):
            who = sm if i % 2 == 0 else vk
            ws = who.ok("POST", f"/api/production/tasks/{tk2['id']}/operations/{op['id']}/work/start", "work_sessions",
                        json={"inputs": [prev["number"]] if prev else [], "materials": []})
            if not ws:
                break
            dn = who.ok("POST", f"/api/production/work/{ws['session']['id']}/submit", "boxes",
                        json={"good_quantity": "40", "defect_quantity": "0"})
            if not dn:
                break
            prev = dn["box"]
        S["last_box2"] = prev
        if prev:
            iid = None
            ins2 = qc.ok("POST", "/api/production/instruments", "instruments", json={
                "inventory_number": "ИНВ-0143", "name": "Штангенциркуль ШЦ-150", "instrument_type": "Линейный",
                "verification_number": "СП-2026/0418", "verified_at": D(-45), "verification_due": D(320)})
            iid = (ins2 or {}).get("instrument", {}).get("id")
            r = qc.ok("POST", f"/api/production/boxes/{prev['id']}/qc", "qc_inspections", json={
                "version": prev["version"], "accepted": 39, "rejected": 1,
                "defect_character": "Несоответствие длины", "defect_description": "Длина за пределами допуска",
                "instrument_ids": [iid] if iid else []})
            print("  qc:", bool(r))
            if r and r.get("box"):
                S["last_box2"] = r["box"]
    # execution on task 1 (the fitter's «Мои задания» of the stage-3 demo): op1 done by Смирнова in two boxes (97 good,
    # 3 scrap); Волков takes the 60-box into op2 and hands in 60 — op2 has handed part, 37 more wait in op1's box;
    # op3 is split: Смирнова 40, Волков 60 («ваша доля 40 из 100», «можно начать: 40 шт»); op4 waits for op3.
    first = task["operations"][0]
    st = sm.ok("POST", f"/api/production/tasks/{task['id']}/operations/{first['id']}/work/start", "work_sessions",
               json={"inputs": [], "materials": []})
    if st:
        done = sm.ok("POST", f"/api/production/work/{st['session']['id']}/submit", "boxes", json={
            "good_quantity": "60", "defect_quantity": "2", "defect_character": "Обрыв жилы",
            "defect_description": "Брак при резке, 2 шт"})
        if done:
            S["box1"] = done["box"]
            st2 = sm.ok("POST", f"/api/production/tasks/{task['id']}/operations/{first['id']}/work/start", "work_sessions",
                        json={"inputs": [], "materials": []})
            if st2:
                sm.ok("POST", f"/api/production/work/{st2['session']['id']}/submit", "boxes", json={
                    "good_quantity": "37", "defect_quantity": "1", "defect_character": "Повреждение изоляции",
                    "defect_description": "Царапина изоляции при укладке"})
    second = task["operations"][1]
    box = S.get("box1")
    if box:
        ws = vk.ok("POST", f"/api/production/tasks/{task['id']}/operations/{second['id']}/work/start", "work_sessions",
                   json={"inputs": [box["number"]], "materials": []})
        if ws:
            vk.ok("POST", f"/api/production/work/{ws['session']['id']}/submit", "boxes",
                  json={"good_quantity": "60", "defect_quantity": "0"})
    fresh = next((item for item in (m.ok("GET", "/api/production/tasks") or {}).get("tasks", []) if item["id"] == task["id"]), None)
    if fresh and len(fresh["operations"]) > 2:
        task = fresh
        third = task["operations"][2]
        shared = m.ok("POST", f"/api/production/tasks/{task['id']}/operations/{third['id']}/share", "shares", json={
            "version": task["version"], "operator_id": ops["Смирнова Елена"], "quantity": 40, "requested_start": None})
        if shared:
            task = shared["task"]
    defects = m.ok("GET", "/api/production/defects")
    rows = (defects or {}).get("defects") or []
    if rows:
        d = sorted(rows, key=lambda x: x["number"])[0]
        m.ok("POST", f"/api/production/defects/{d['id']}/decide", "defect_decisions",
             json={"version": d["version"], "disposition": "Списание", "act_reference": "АКТ-БР-014"})
    m.ok("PUT", "/api/production/plan/projects-snapshot", json={"projects": [
        {"project": "614К.1", "customer": "ООО «Стойка-Про»", "opened_on": D(-20), "deadline": D(25), "status": "В работе", "terminal": False},
        {"project": "621К.2", "customer": "АО «ПромАвтоматика»", "opened_on": D(-10), "deadline": D(40), "status": "Новый", "terminal": False},
        {"project": "708К.2", "customer": "ООО «Контроллер»", "opened_on": D(-30), "deadline": D(15), "status": "В работе", "terminal": False}]})
    for ev in (
        {"project": "614К.1", "kind": "deadline", "deadline": D(25)},
        {"project": "614К.1", "kind": "note", "text": "Клиент согласовал замену разъёма на Molex Micro-Fit"},
        {"project": "621К.2", "kind": "block", "blocker": "ОЖИД КОМПЛ", "text": "Ждём КСПВГ и DB-9 от поставщика"},
        {"project": "614К.1", "kind": "stage", "item_index": "КБ1", "value": "В работе"},
        {"project": "614К.1", "kind": "tcReview", "item_index": "КБ1", "value": "проверена"},
        {"project": "708К.2", "kind": "stage", "item_index": "КБ1", "value": "КБ набрана"},
    ):
        m.ok("POST", "/api/production/plan/events", "plan_events", json=ev)
    S["task1"] = task


# ── 8. Fulfillment (issues) ─────────────────────────────────────────────────────────────────────────


@step("8. Fulfillment")
def fulfillment():
    sid, eng = S["sidorova.wh"], S["orlova.eng"]
    lots = {l["article"]: l for l in sid.ok("GET", "/api/stock")["lots"]}
    mgtf = next((l for a, l in lots.items() if a.startswith("Провод МГТФ")), None)
    jst = next((l for a, l in lots.items() if a.startswith("Клемма JST XH")), None)
    molex = lots.get("MOLEX-430250800")
    pvam = lots.get("PVAM-1.5-BL")
    print("  lots:", [a[:22] for a in lots])

    def make(purpose, lines, dl):
        r = eng.ok("POST", "/api/issues", "issues", json={"purpose": purpose, "deadline": D(dl), "lines": lines})
        return r["request"] if r else None

    def bind(req, pairs):
        for line, lot in zip(req["lines"], pairs):
            if lot is None:
                continue
            r = sid.ok("POST", f"/api/issues/{req['id']}/lines/{line['id']}/bind",
                       json={"version": req["version"], "lot_id": lot["id"]})
            if r:
                req = r["request"]
        return req

    def adv(req, path, **extra):
        r = sid.ok("POST", f"/api/issues/{req['id']}/{path}", json={"version": req["version"], **extra})
        return r["request"] if r else req

    r1 = make("614К.1 / КБ1", [
        {"article": "МГТФ", "name": "Провод МГТФ 0.35 мм² белый", "unit": "м", "requirement": "120"},
        {"article": "JST", "name": "Клемма JST XH 2.54 мм, 4 pin, розетка", "unit": "шт", "requirement": "100"}], 6)
    if r1:
        r1 = bind(r1, [mgtf, jst])
        r1 = adv(r1, "complete")
        r1 = adv(r1, "ready", handout_location="Полка выдачи 2")
        r1 = adv(r1, "issue")
        print("  issue 1 status:", r1["status"])
    r2 = make("614К.1 / КБ2", [
        {"article": "MOLEX-430250800", "name": "Терминал Molex 43025-0800 (Micro-Fit)", "unit": "шт", "requirement": "80"}], 8)
    if r2:
        r2 = bind(r2, [molex])
        r2 = adv(r2, "complete")
        r2 = adv(r2, "ready", handout_location="Полка выдачи 3")
        print("  issue 2 status:", r2["status"])
    r3 = make("708К.2 / КБ1", [
        {"article": "ПВАМ", "name": "Провод ПВАМ 1,5 белый", "unit": "м", "requirement": "400"},
        {"article": "SMA", "name": "Разъём SMA вилка под RG-316", "unit": "шт", "requirement": "20"}], 12)
    if r3:
        r3 = bind(r3, [pvam, None])
        print("  issue 3 status:", r3["status"])
    make("621К.2 / КБ1", [
        {"article": "КСПВГ", "name": "Кабель КСПВГ 4х0.5", "unit": "м", "requirement": "300"},
        {"article": "DB9", "name": "Разъём DB-9 вилка на кабель", "unit": "шт", "requirement": "25"}], 18)


# ── 9. Shipping ─────────────────────────────────────────────────────────────────────────────────────


@step("9. Shipping")
def shipping():
    wh = S["adm"]
    m = S["ivanov.master"]
    wh.ok("POST", "/api/shipping/project-queue", "queue", json={"project": "614К.1", "customer": "ООО «Стойка-Про»", "assemblies": [
        {"item_index": "КБ1", "name": "Жгут питания стойки", "run": "100", "article": "ЖП-614-1"},
        {"item_index": "КБ2", "name": "Жгут питания блока Molex 12P", "run": "40", "article": "ЖП-614-2"}]})
    wh.ok("POST", "/api/shipping/goods", "goods", json={
        "project": "708К.2", "item_index": "КБ1", "customer": "ООО «Контроллер»", "article": "КП-708-1",
        "name": "Кабель питания контроллера", "ordered": "10", "unit": "шт"})
    goods = {g["item_index"]: g for g in wh.ok("GET", "/api/shipping/goods")["goods"] if g["project"] == "614К.1"}
    g1, g2 = goods.get("КБ1"), goods.get("КБ2")
    print("  good sample:", json.dumps(g1, ensure_ascii=False)[:300])
    handed = False
    box = S.get("last_box2")
    if box and g2:
        h = wh.ok("POST", f"/api/production/boxes/{box['id']}/handoff", "handoffs",
                 json={"version": box.get("version", 1), "finished_good_id": g2["id"]})
        handed = bool(h)
        print("  handoff:", handed)
    # Склад ГП принимает только сданное цехом: КБ2 пришла передачей коробки после ОТК (39 из 40), КБ1 ещё в цеху
    # (этапы 2–4) — её «принять» нечего (422 «принять можно ещё 0»). Обе отгрузки — из принятой КБ2.
    goods = {g["item_index"]: g for g in wh.ok("GET", "/api/shipping/goods")["goods"] if g["project"] == "614К.1"}
    g2 = goods.get("КБ2")
    print("  КБ2 accepted:", (g2 or {}).get("accepted"))
    wh.ok("PUT", "/api/shipping/project-addressee", json={
        "project": "614К.1", "receiver": "ООО «Стойка-Про»", "shipping_point": "г. Тула, ул. Оружейная, 12",
        "contact": "Соколов А.", "phone": "+7 900 555-01-14", "terms": "ДОСТАВКА ТК", "carrier": "СДЭК",
        "upd_type": "ЭДО"})
    if g2 and handed:
        made = wh.ok("POST", "/api/shipping/shipments", "shipments", json={
            "customer": "ООО «Стойка-Про»", "receiver": "ООО «Стойка-Про»", "shipping_point": "г. Тула, ул. Оружейная, 12",
            "contact": "Соколов А.", "phone": "+7 900 555-01-14", "terms": "ДОСТАВКА ТК", "carrier": "СДЭК",
            "upd_type": "ЭДО", "lines": [{"good_id": g2["id"], "quantity": "20"}]})
        if made:
            sh = made["shipment"]
            print("  shipment 1:", sh["status"])
            s2 = wh.ok("POST", f"/api/shipping/shipments/{sh['id']}/ship", "shipments_shipped", json={"version": sh["version"]})
            if s2:
                print("  shipment 1 ->", s2["shipment"]["status"])
        made = wh.ok("POST", "/api/shipping/shipments", "shipments", json={
            "customer": "ООО «Стойка-Про»", "receiver": "ООО «Стойка-Про»", "shipping_point": "г. Тула, ул. Оружейная, 12",
            "terms": "САМОВЫВОЗ", "upd_type": "БУМАГА", "lines": [{"good_id": g2["id"], "quantity": "15"}]})
        if made:
            print("  shipment 2:", made["shipment"]["status"])


# ── 10. Notifications ───────────────────────────────────────────────────────────────────────────────


@step("10. Notifications")
def notifications():
    outbox()
    for login in ("preview", "petrov.buyer", "sidorova.wh", "ivanov.master", "orlova.eng"):
        c = S["adm"] if login == "preview" else S[login]
        r = c.ok("GET", "/api/notifications")
        n = len(r.get("notifications") or r.get("items") or []) if isinstance(r, dict) else "?"
        print(f"  notifications for {login}: {n}")


def main():
    S["adm"] = Client()
    for fn in (staff, requests_, handoff, procurement, receiving, stock, techcards, production, fulfillment, shipping, notifications):
        fn()
    print("\nCOUNTS:", json.dumps(COUNT, ensure_ascii=False))
    print(f"FAILURES ({len(FAILS)}):")
    for f in FAILS:
        print(" -", f)


if __name__ == "__main__":
    sys.exit(main())

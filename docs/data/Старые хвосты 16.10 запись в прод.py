"""Запись в прод 16.10.2026 — старые хвосты базы (владелец 16.10: «ты все решаешь сам и закрываешь»; «"примерно" не надо,
будем на деле проверять» — значения пишутся как есть, без «примерно»; как получено каждое — в журнале
docs/data/«Старые хвосты 16.10.csv» и «Старые хвосты 16.10.md»).

Путями самого приложения (`_edit_in`, как «Изменить» в «Базе данных»), строка ищется по естественному ключу (Марка,
Инв. номер, артикул контакта), у каждой ячейки старое значение: ячейку, которую поменяли после снимка, `_edit_in`
отклоняет целиком (строка не пишется), пустая ячейка пишется только пустой.
  1. БД.ПРОВ — МК 26-13 по техописанию Чувашкабеля (Ø 0,50, цвета, скрутка, масса, источник); МГТФ — цвет
     «натуральный»; ПВВС 4×0,35 — ПВХ, до 60 В, Беларускабель; FLEXICORE® 105 CY 0,6/1 кВ — 4 жилы.
  2. БД.ОБ — AM901 ×2: «Станок для зачистки чего?» → «Станок для зачистки провода» («Для базы» идёт следом),
     «Chenzhen» → «Shenzhen»; пишется, только если на старое «Для базы» никто не ссылается.
  3. БД.ТЕР — CTX50 34905 (349052447, 349056447): шаг 2 → 1,8 (Mini50 34967); JR-T-Female / JR-T-Male: материал без
     «(типично для клонов JR)».
  4. БД.КОАКС — «Источник данных»: «L1–L3 примерно: по …» → «L1–L3 по …» (только этот оборот, остальной текст тот же).

Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «уже так»"""
import json
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab, cable_key
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Старые хвосты базы 16.10.2026", department="System")
REASON = "старые хвосты базы, 16.10.2026 (владелец: «ты все решаешь сам и закрываешь»)"

MK_SRC = ("Чувашкабель, «Бортовые монтажные провода МК, МКЭ. Техническое описание», ТУ 16.К05-016-2003 — "
          "https://chuvkab.nt-rt.ru/images/manuals/mk_26_13.pdf")
MK_COLORS_OLD = "белый, жёлтый, зелёный, коричневый, красный, оранжевый, синий, чёрный"
MK_COLORS = "белый, натуральный, жёлтый, оранжевый, красный, синий, коричневый, чёрный"
MGTF_MULTI = "натуральный, белый, жёлтый, зелёный, коричневый, красный, оранжевый, синий, чёрный"
PVVS_SRC_OLD = ("rfopt.ru (карточка товара + общее описание серии pvvs); наружный диаметр и вес страницей не даны — оставлено "
                "пустым. Темп./напряжение — по общему описанию серии ПВВС, не по этой конкретной позиции — решено агентом — "
                "проверить")
PVVS_SRC = ("Беларускабель, ПВВС — https://fbns.ru/product/%D0%BF%D0%B2%D0%B2%D1%81-40-2/ («кабель для пожарной "
            "сигнализации при напряжении до 60 В переменного тока частотой 50 Гц», изоляция и оболочка — "
            "поливинилхлоридный пластикат, −40…+60 °C); https://rfopt.ru/pvvs-4h035")
FLEXI = "FLEXICORE® 105 CY 0,6/1 кВ"
FLEXI_SRC = "FLEXICORE, FLEXICORE 105 CY 0,6/1 кВ — https://flexicore.ru/products/flexicore-105-cy-06-1-kv"

# (Марка, поле, было, стало); «*» в «было» — текущее значение берётся из строки (длинные источники), см. SRC_OLD.
PROV = [
    ("МК 26-13 0,07", "Жила: проволок × диаметр, мм", "", "14×0,08"),
    ("МК 26-13 0,07", "Масса, г/м", "", "1,14"),
    ("МК 26-13 0,12", "Жила: проволок × диаметр, мм", "", "24×0,08"),
    ("МК 26-13 0,12", "Масса, г/м", "", "1,82"),
    ("МК 26-13 0,20", "Жила: проволок × диаметр, мм", "", "19×0,12"),
    ("МК 26-13 0,20", "Масса, г/м", "", "2,80"),
    ("МК 26-13 0,35", "Жила: проволок × диаметр, мм", "", "19×0,15"),
    ("МК 26-13 0,35", "Масса, г/м", "", "4,04"),
    ("МК 26-13 0,50", "Жила: проволок × диаметр, мм", "", "19×0,18"),
    ("МК 26-13 0,50", "Масса, г/м", "", "5,92"),
    ("МК 26-13 0,50", "Наружный диаметр, мм", "1,5", "1,4"),
    *((f"МК 26-13 {size}", "Цвета", MK_COLORS_OLD, MK_COLORS) for size in ("0,07", "0,12", "0,20", "0,35", "0,50")),
    *((f"МК 26-13 {size}", "Источник данных", "", MK_SRC) for size in ("0,07", "0,12", "0,20", "0,35", "0,50")),
    ("МГТФ 0,07", "Цвета", "белый", "натуральный"),
    ("МГТФ 0,12", "Цвета", MGTF_MULTI, "натуральный"),
    ("МГТФ 0,20", "Цвета", MGTF_MULTI, "натуральный"),
    ("МГТФ 0,35", "Цвета", MGTF_MULTI, "натуральный"),
    ("ПВВС 4×0,35", "Изоляция", "полиэтилен", "ПВХ пластикат"),
    ("ПВВС 4×0,35", "Материал изоляции жилы", "полиэтилен", "ПВХ пластикат"),
    ("ПВВС 4×0,35", "Материал оболочки", "полиэтилен", "ПВХ пластикат"),
    ("ПВВС 4×0,35", "Напряжение, В", "380 (перем.) / 700 (пост.) — по описанию серии, для конкретной позиции не подтверждено",
     "60 (перем.)"),
    ("ПВВС 4×0,35", "Производители", "", "Беларускабель"),
    ("ПВВС 4×0,35", "Источник данных", PVVS_SRC_OLD, PVVS_SRC),
    (FLEXI, "Число жил", "", "4"),
    (FLEXI, "Источник данных", "*", FLEXI_SRC),
]
FLEXI_SRC_START = "Обобщённые данные линейки FLEXICORE 105 CY 0,6/1кВ (LAPP-style, X10VV-F CY)"

AM_OLD_NAME, AM_NAME = "Станок для зачистки чего?", "Станок для зачистки провода"
AM_OLD_MAKER, AM_MAKER = "Chenzhen Aituo Automation Device", "Shenzhen Aituo Automation Device"
OB = [("ОБ.036.1",), ("ОБ.036.2",)]

JR_OLD = "латунь / фосфористая бронза, золочение (типично для клонов JR)"
TER = [  # (артикул REEL или BAG, поле, было, стало)
    ("349052447", "Шаг разьема", "2", "1,8"),
    ("349056447", "Шаг разьема", "2", "1,8"),
    ("JR-T-Female", "Материал контакта", JR_OLD, "латунь / фосфористая бронза, золочение"),
    ("JR-T-Male", "Материал контакта", JR_OLD, "латунь / фосфористая бронза, золочение"),
]
COAX_OLD, COAX_NEW = "L1–L3 примерно: ", "L1–L3 "

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
LOG = []


def log(table, key, field, old, new, status):
    LOG.append({"table": table, "key": key, "field": field, "old": old, "new": new, "status": status})
    short = lambda text: (str(text)[:90] + "…") if len(str(text)) > 90 else str(text)  # noqa: E731
    print(f"   {status}: {table} «{key}» {field}: «{short(old)}» → «{short(new)}»")


def records(uow, tab):
    return uow.tech_operations.list(tab=tab, query="", limit=100_000)


def edit_row(record_id, edits, table, key, quiet=False):
    with factory() as uow:
        try:
            _edit_in(record_id, tuple(edits), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            for item in edits:
                log(table, key, item.field, item.old, item.value, f"отказ ({error})")
            return False
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(record_id), "tab": table, "fields": [item.field for item in edits], "reason": REASON}, uow)
        for item in edits:
            if quiet:
                LOG.append({"table": table, "key": key, "field": item.field, "old": item.old, "new": item.value,
                            "status": "проверено" if dry else "записано"})
            else:
                log(table, key, item.field, item.old, item.value, "проверено" if dry else "записано")
        if not dry:
            uow.commit()
        return True


def cells(record, wanted, table, key):
    """FieldEdit per (field, old, new); a cell already at «new» is logged «уже так»."""
    edits = []
    for field, old, new in wanted:
        have = str(record.fields.get(field) or "").strip()
        if have == new:
            log(table, key, field, new, new, "уже так")
            continue
        if old == "*":
            old = have if have.startswith(FLEXI_SRC_START) else "\x00не тот текст"
        edits.append(FieldEdit(field, old, new))
    return edits


print("== 1. БД.ПРОВ")
with factory() as uow:
    wires = {cable_key(r.fields.get("Марка")): r for r in records(uow, TechOperationTab.WIRE)}
by_mark: dict[str, list] = {}
for mark, field, old, new in PROV:
    by_mark.setdefault(mark, []).append((field, old, new))
for mark, wanted in by_mark.items():
    record = wires.get(cable_key(mark))
    if record is None:
        log("БД.ПРОВ", mark, "-", "", "", "отказ (марки нет)")
        continue
    edits = cells(record, wanted, "БД.ПРОВ", mark)
    if edits:
        edit_row(record.id, edits, "БД.ПРОВ", mark)

print("== 2. БД.ОБ — AM901")
with factory() as uow:
    everything = [r for tab in TechOperationTab for r in records(uow, tab)]
equipment = {str(r.fields.get("Инв. номер") or "").strip(): r for r in everything if r.tab == TechOperationTab.EQUIPMENT}
old_base = f"AM901/{AM_OLD_NAME}"
linked = [r for r in everything if r.tab != TechOperationTab.EQUIPMENT and old_base in json.dumps(r.fields, ensure_ascii=False)]
for (inv,) in OB:
    record = equipment.get(inv)
    if record is None:
        log("БД.ОБ", inv, "-", "", "", "отказ (строки нет)")
        continue
    if linked:
        log("БД.ОБ", inv, "Наменование", AM_OLD_NAME, AM_NAME, f"отказ (на «{old_base}» ссылаются: {len(linked)})")
        continue
    if str(record.fields.get("Артикул") or "").strip() != "AM901":
        log("БД.ОБ", inv, "Артикул", record.fields.get("Артикул"), "AM901", "отказ (не AM901)")
        continue
    edits = cells(record, [("Наменование", AM_OLD_NAME, AM_NAME), ("Производитель", AM_OLD_MAKER, AM_MAKER)], "БД.ОБ", inv)
    if edits and edit_row(record.id, edits, "БД.ОБ", inv):
        log("БД.ОБ", inv, "Для базы", old_base, f"AM901/{AM_NAME}", "следом (формула артикул/наименование)")

print("== 3. БД.ТЕР")
terminals = [r for r in everything if r.tab == TechOperationTab.TERMINAL]
for article, field, old, new in TER:
    found = [r for r in terminals if article in {str(r.fields.get("terArticle") or "").strip(),
                                                 str(r.fields.get("Артикул контакта (BAG)") or "").strip()}]
    if len(found) != 1:
        log("БД.ТЕР", article, field, old, new, f"отказ (строк {len(found)})")
        continue
    edits = cells(found[0], [(field, old, new)], "БД.ТЕР", article)
    if edits:
        edit_row(found[0].id, edits, "БД.ТЕР", article)

print("== 4. БД.КОАКС — «L1–L3 примерно: »")
coax = [r for r in everything if r.tab == TechOperationTab.COAX]
touched, odd = 0, []
for record in coax:
    for field, value in record.fields.items():
        text = str(value or "")
        if "примерн" not in text.lower():
            continue
        new = text.replace(COAX_OLD, COAX_NEW)
        if "примерн" in new.lower():
            odd.append((record.fields.get("coaxArticle"), record.fields.get("coaxWire"), field))
            continue
        key = f"{record.fields.get('coaxArticle')} × {record.fields.get('coaxWire')}"
        if edit_row(record.id, [FieldEdit(field, text, new)], "БД.КОАКС", key, quiet=True):
            touched += 1
print(f"   строк: {touched}; с другим «примерно» (не тронуты): {len(odd)}", odd[:5])

counts: dict[str, int] = {}
for item in LOG:
    counts[f"{item['table']} {item['status'].split(' (')[0]}"] = counts.get(f"{item['table']} {item['status'].split(' (')[0]}", 0) + 1
print("ИТОГО", json.dumps(counts, ensure_ascii=False), "(проверка, ничего не записано)" if dry else "(записано)")
print("LOG " + json.dumps([item for item in LOG if item["table"] != "БД.КОАКС"], ensure_ascii=False, default=str))

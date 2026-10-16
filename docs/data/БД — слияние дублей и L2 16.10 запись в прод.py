"""Запись в прод 16.10.2026, днём (владелец: «продолжай сам, все реши сам») — пункты, оставленные утром владельцу.

  1. «RG1.37» и «RF 1.37» — один кабель в двух строках. Остаётся «RF 1.37»: так названы карточка коаксиального кабеля
     (`coax_cables`, псевдоним «RG1.37» у неё уже есть), 4 разъёма БД.КОАКС и строка таблицы D1–D3. Пустые ячейки строки
     «RF 1.37» (провода) дописываются из «RG1.37» (данные, 21 закупка, цена), «Поставщик» — объединение; строки «RG1.37»
     (провода и D1–D3) удаляются. Складские партии с «RG1.37» в артикуле — разъёмы (SMA, MMCX, FAKRA), их не трогаем.
  2. «КСПВГ 4x0,50» — на деле КДВВГ 4х0,50: ссылка строки и все её значения — карточка Паритета КДВВГ 4х0,50 (арт. 100780),
     цена закупки 75,6 ₽/м против 73,57 ₽/м у КДВВГ, а КСПВГ 4×0,50 Паритет не делает. В «КДВВГ 4х0,50» дописываются
     закупки, последняя цена и AWG; строка КСПВГ удаляется. Складских партий и ссылок нет.
  3. BNC-C58P и N-C58P (RUICHI, RG-58): L2 была записана приростом (L2 < L1). L1–L3 — длины от торца нарастающим итогом,
     поэтому L2 = L1 + L2: 5 + 2,5 = 7,5 и 3,5 + 3 = 6,5; L3 оставлена (оплётка 4,5 мм у обоих — как у обжимных BNC/N).

Каждая ячейка — со старым значением (`import_rows` по натуральному ключу, пустая ячейка — old = ""); строка удаляется, только если её поля
совпадают со снимком в этом же проходе; удаление пишет событие журнала со всеми полями (откат — из журнала).
Порядок: dry → `erp.sh backup-now` → apply → dry (всё «уже так»)."""
import json
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import TechOperationTab, cable_key
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 16.10.2026", department="System")
REASON = "слияние дублей / исправление L2, 16.10.2026 (владелец: «все реши сам»)"
mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()


def s(value):
    return str(value or "").strip()


def find(uow, tab, field, value):
    return [r for r in uow.tech_operations.list(tab=tab, query="", limit=100_000) if s(r.fields.get(field)) == value]


OWN_COLUMNS = ("Цена поставщика, ₽/м", "Дата цены")


def merge(tab, table, field, source_name, target_name, only=None, overwrite=None, note=""):
    """Empty target cells ← source (all, or `only`) through `import_rows` by the natural key with old = "" (a filled cell
    comes back refused), `overwrite` {field: new} over the current value, then the source row is deleted."""
    with factory() as uow:
        src, dst = find(uow, tab, field, source_name), find(uow, tab, field, target_name)
    if not src:
        print(f"   уже так: {tab.value} «{source_name}» нет")
        return
    if len(src) != 1 or len(dst) != 1:
        print(f"   отказ: {tab.value} «{source_name}» {len(src)} / «{target_name}» {len(dst)}")
        return
    src, dst = src[0], dst[0]
    print("   источник:", json.dumps(dict(src.fields), ensure_ascii=False))
    fill = {k: s(v) for k, v in src.fields.items()
            if k != field and s(v) and not s(dst.fields.get(k)) and (only is None or k in only)}
    old = {k: "" for k in fill}
    for k, v in (overwrite or {}).items():
        if s(dst.fields.get(k)) != v:
            fill[k], old[k] = v, s(dst.fields.get(k))
    if note:
        if s(src.fields.get("Источник данных")) and "Источник данных" not in (only or ()) and only is None:
            note += f" (её «Источник данных»: {s(src.fields.get('Источник данных'))})"
        fill["Источник данных"] = (s(dst.fields.get("Источник данных")) + note).lstrip("; ")
        old["Источник данных"] = s(dst.fields.get("Источник данных"))
    own = {k: fill.pop(k) for k in OWN_COLUMNS if k in fill}  # the owner's columns: not in the load, the row edit takes them
    for k in own:
        old.pop(k, None)
    if fill:
        with factory() as uow:
            report = import_rows(table, uow, [ImportRowIn(fields={field: target_name, **fill}, old=old)], ACTOR, now,
                                 dry_run=dry, source="erp").model_dump()
        if report["refused"] or report["created"]:
            print("   отказ, import:", report)
            return
    if own:
        with factory() as uow:
            try:
                _edit_in(dst.id, tuple(FieldEdit(k, "", v) for k, v in own.items()), now, uow)
            except Exception as error:  # noqa: BLE001 — a refusal stops the merge before the delete
                print(f"   отказ: {tab.value} «{target_name}»: {error}")
                return
            _audit("references.tech_record_edited", ACTOR, now, {
                "record_id": str(dst.id), "tab": tab.value, "fields": list(own), "reason": REASON}, uow)
            if not dry:
                uow.commit()
        fill.update(own)
    for k, v in fill.items():
        print(f"   {'проверено' if dry else 'записано'}: {tab.value} «{target_name}» {k}: «{old.get(k, '')}» → «{v}»")
    with factory() as uow:
        live = uow.tech_operations.get(src.id)
        if live is None or dict(live.fields) != dict(src.fields):
            print(f"   отказ: {tab.value} «{source_name}» поменялась")
            return
        print("   удаляется:", json.dumps({"id": str(live.id), "tab": tab.value, "sort_key": live.sort_key,
                                          "display_text": live.display_text}, ensure_ascii=False))
        if not dry:
            uow.tech_operations.delete(live.id)
            _audit("references.tech_record_removed", ACTOR, now, {
                "record_id": str(live.id), "tab": tab.value, "display_text": live.display_text, "fields": dict(live.fields),
                "reason": f"дубль «{target_name}»; {REASON}"}, uow)
            uow.commit()
        print(f"   {'проверено' if dry else 'записано'}: {tab.value} «{source_name}» удалена")


print("== 1. RG1.37 → RF 1.37")
with factory() as uow:
    rows = find(uow, TechOperationTab.WIRE, "Марка", "RG1.37")
    sup = s(rows[0].fields.get("Поставщик")) if rows else ""
merge(TechOperationTab.WIRE, "prov", "Марка", "RG1.37", "RF 1.37",
      overwrite={"Поставщик": sup} if sup else None,
      note="; 16.10.2026: слита строка-дубль «RG1.37» (так кабель назван в закупках) — её данные, закупки и цена перенесены "
           "сюда, «RG1.37» остаётся псевдонимом карточки коаксиального кабеля")
merge(TechOperationTab.KAB_COAX, "kab_coax", "Кабель", "RG1.37", "RF 1.37")

print("== 2. КСПВГ 4x0,50 → КДВВГ 4х0,50")
merge(TechOperationTab.WIRE, "prov", "Марка", "КСПВГ 4x0,50", "КДВВГ 4х0,50",
      only={"AWG", "Закупок", "Цена посл., ₽/м без НДС"},
      note="; 16.10.2026: слита строка «КСПВГ 4x0,50» — так покупатель записал этот кабель в 2 закупках (ссылка и все "
           "значения той строки — эта карточка КДВВГ 4х0,50, цена закупки 75,6 ₽/м; КСПВГ 4×0,50 у Паритета нет)")

print("== 3. L2 у BNC-C58P и N-C58P")
for article, old, new in (("BNC-C58P", "2,5", "7,5"), ("N-C58P", "3", "6,5")):
    with factory() as uow:
        rows = [r for r in find(uow, TechOperationTab.COAX, "Артикул", article)
                if cable_key(r.fields.get("Провод")) == cable_key("RG-58")]
        if len(rows) != 1:
            print(f"   отказ: «{article}» строк {len(rows)}")
            continue
        r = rows[0]
        if s(r.fields.get("L2")) == new:
            print(f"   уже так: «{article}» L2 = {new}")
            continue
        src = s(r.fields.get("Источник данных"))
        add = (f"16.10.2026: L2 {old} → {new} — была записана приростом (L2 < L1); L1–L3 от торца нарастающим итогом, "
               f"L2 = L1 + {old}; чертежа RUICHI нет — решено агентом, проверить")
        report = import_rows("coax", uow, [ImportRowIn(
            fields={"coaxArticle": s(r.fields.get("coaxArticle")), "coaxWire": s(r.fields.get("coaxWire")), "L2": new,
                    "Источник данных": f"{src}; {add}" if src else add},
            old={"L2": old, "Источник данных": src})], ACTOR, now, dry_run=dry, source="erp").model_dump()
        if report["refused"] or report["created"]:
            print(f"   отказ «{article}», import:", report)
            continue
        print(f"   {'проверено' if dry else 'записано'}: БД.КОАКС «{article}» L2: «{old}» → «{new}»")

"""Запись в прод 21.10.2026 — БД.КОАКС: свои строки у разъёмов склада, которые «Склад → база» вёл на чужую запись или никуда
(владелец 20.10, показал позиции склада: «ты все эти внес?»; правило «один артикул — одна строка»).

Что было: партии склада ЦАР SMA-JW2CG, SMA-JWB2-F своей строки в БД.КОАКС не имели; SMA-JWB3-F-1 имела, но без L1–L3,
изготовителя и ссылки; партия «ISMA-JB3 SMA-male RG402» по тексту артикула не находила строку ISMA-JB3 (наименование
партии бралось разбором кода). Журнал «Склад → база» (reference_stock_links) на проде был пуст: показанные владельцем
«привязки» — наименования партий, вычисленные из артикула (`infer_article_identity`: строка БД.КОАКС с тем же
артикулом → «БД.КОАКС», иначе разбор кода).

Что делает (всё — функциями приложения, как кнопка «В базу данных» на складе и правка партии):
  1. SMA-JWB2-F × RG-405 — новая строка, `link_stock_article` без record_id (событие журнала «Создана», партия OLD.26.0000062);
  2. SMA-JW2CG (провод пуст) — новая строка тем же путём (партия OLD.26.0000070);
  3. SMA-JWB3-F-1 × RG-402 — дозаполнение пустых ячеек, `link_stock_article` с record_id и правками (событие «Обновлена»,
     у каждой правки `old` = то, что стоит; не совпало — не пишется ничего);
  4. партия OLD.26.0000071: артикул «ISMA-JB3 SMA-male RG402» → «ISMA-JB3» (`warehouse.application.edit_lot_identity`,
     наименование и серия те же); движения склада, остатки, резервы не трогаются;
  5. остальные партии этих артикулов (и ISMA-KB2-F, ISMA-KB3A, у которых строка уже своя) — событие журнала «Привязана».
Режимы:
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry      < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  ... python - apply    < этот файл
  ... python - dry      < этот файл   → всё «уже сделано»
  ... python - rollback < этот файл   → удалить созданные строки, вернуть ячейки SMA-JWB3-F-1 и артикул партии;
                                       строки журнала «Склад → база» не удаляются (журнал только дописывается) —
                                       у удалённых записей он сам покажет «(удалена)».
Журнал ячеек и откат — «КОАКС — свои строки разъёмов склада 21.10 откат.csv» (пишется после apply).

СОСТОЯНИЕ 21.10.2026: записано на прод — свежая выгрузка БД.КОАКС совпала с 20.10 (608 строк, 0 изменений, партии те же);
dry → backup-now (/backups/daily/erp_2026-10-21_175653.dump) → apply → dry (всё «уже»). Ссылки проверены
/api/stock/article-info (все шесть артикулов → «БД.КОАКС») и /api/references/stock-links (13 строк журнала);
etk_refresh --all --dry-run: 27 карт, ревизий 0. Выводы и откат — рядом, «… 21.10 вывод …», «… 21.10 откат.csv»,
список артикулов склада — «Склад — коаксиальные разъёмы и свои строки БД.КОАКС 21.10.csv».
"""
import os
import sys
from uuid import UUID

from sqlalchemy import text

from app.database import build_engine, build_references_uow_factory, build_warehouse_uow_factory
from app.modules.references.application import (
    FieldEdit, LinkStockCommand, _clean_new_fields, _create_in, delete_record, link_stock_article,
)
from app.modules.references.domain import TechOperationTab, cable_key
from app.modules.warehouse.application import EditLotIdentityCommand, edit_lot_identity
from app.shared.clock import moscow_now
from app.shared.identity import Actor
from app.workflows.stock_article import read_stock_article

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Разъёмы склада → свои строки 21.10.2026", department="System")
mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply", "rollback"):
    raise SystemExit("режим: dry | apply | rollback")
engine = build_engine(os.environ["ERP_DATABASE_URL"])
refs = build_references_uow_factory(engine)
stock = build_warehouse_uow_factory(engine)
now = moscow_now()

HLT = "HenryTech (Hengliti)"
URL_JWB2F = "http://www.hltchips.com/product_detail.html?id=660"
URL_JWB3F = "http://www.hltchips.com/product_detail.html?id=661"
URL_JWB2CG = "http://www.hltchips.com/product_detail.html?id=561"
HS_ANGLE = ("L1 3 — аналог: Huber+Suhner 16_SMA-50-2-103/111 NH (SMA угловой на RG-405), строка БД.КОАКС — тот же ряд, "
            "угловой штекер на полужёсткий кабель, изготовитель другой (так же у «SMA-male R/A RG-405», S-125-085, "
            "S-125-141, SMA-50-3-3/111 NH); L2, L3 — пусто: ни у изготовителя, ни у аналога разделки нет")
ANGLE_PIN = ("Тип пина — пайка внутри корпуса: угловой разъём, жила паяется в контакт через окно корпуса под крышкой "
             "(правило БД.КОАКС, владелец 20.10)")
SEMI_SHIELD = ("Тип экрана — пайка: полужёсткий кабель, внешний проводник — трубка, паяется в хвостовик (как у соседних строк "
               "на этот кабель: Huber+Suhner / Coax Connectors Ltd); на странице изготовителя не сказано")

LOTS = {  # ГРН → (id партии на проде, артикул партии сейчас)
    "OLD.26.0000062": ("6f48470a-f730-47ca-afc1-a317b078f6ab", "SMA-JWB2-F"),
    "OLD.26.0000063": ("e7f2cd9d-effc-4bbc-8ba8-08b6295e0f2e", "SMA-JWB2-F"),
    "OLD.26.0000064": ("f47cee64-73e9-4ee6-a386-2d80b730e7bd", "ISMA-KB3A"),
    "OLD.26.0000065": ("28fcf766-1b07-4bec-b00d-bc489867adeb", "ISMA-KB3A"),
    "OLD.26.0000068": ("e7ccd5e6-d95a-46b1-9cad-a6d6146ee604", "SMA-JWB3-F-1"),
    "OLD.26.0000070": ("98d337c6-bcad-4860-b488-d3dddbe995f5", "SMA-JW2CG"),
    "OLD.26.0000071": ("1e6709e3-680b-4d06-94cb-70f33c847a7c", "ISMA-JB3 SMA-male RG402"),
    "OLD.26.0000073": ("88d7d415-d7d6-499c-ac58-eebc6b0aae1f", "ISMA-KB2-F"),
    "OLD.26.0000074": ("88141023-f1ea-4a2e-9051-be63d45d3882", "ISMA-KB2-F"),
    "OLD.26.0000075": ("bf91887e-bb3c-4b7d-af65-f2471ff282de", "ISMA-KB2-F"),
    "OLD.26.0000076": ("8dbdbd0f-a629-4079-8c1e-3ded2bbbc3af", "ISMA-KB2-F"),
    "OLD.26.0000077": ("1a34e44a-8e4b-4547-8c94-1ecd8dab3438", "ISMA-KB2-F"),
    "OLD.26.0000078": ("3e29ca3b-34a0-4a91-a988-9150450e6ccb", "ISMA-KB2-F"),
}

CREATE = [
    ("OLD.26.0000062", {
        "Артикул": "SMA-JWB2-F", "Тип/Серия": "SMA-male R/A", "Провод": "RG-405", "Производитель": HLT,
        "Серия": "SMA", "Пол": "штекер", "Полярность": "обычная", "Исполнение": "угловой",
        "L1": "3", "Тип пина": "пайка внутри корпуса", "Тип экрана": "пайка",
        "Волновое сопротивление, Ом": "50", "Частота до, ГГц": "12", "КСВ": "≤1,20",
        "Наименование": "Разъём SMA-JWB2-F (SMA, штекер, угловой, под полужёсткий кабель .086 / RG-405)",
        "Ссылка": URL_JWB2F,
        "Примечание": "склад ЦАР: ГРН OLD.26.0000062 и 063, ячейки 18C1A, 18C1B; у изготовителя — HL-SMA-JWB2-F",
        "Источник данных": (
            f"21.10.2026: изготовитель — Chengdu HenryTech (成都恒利泰, hltchips.com), страница HL-SMA-JWB2-F {URL_JWB2F}: "
            "«SMA公头弯式接.086电缆射频同轴连接器 DC-12G，驻波比≤1.20» — SMA-штекер угловой на кабель .086 (= RG-405), "
            "DC–12 ГГц, КСВ ≤ 1,20; «其特性阻抗为50Ω» — 50 Ом; артикул склада = артикул изготовителя без «HL-»; "
            f"Серия, Пол, Исполнение, Полярность — по той же строке; {ANGLE_PIN}; {SEMI_SHIELD}; {HS_ANGLE}; "
            "чертежа с разделкой у HenryTech нет (на странице только фото); HEX — нет (экран не обжимается); "
            "Заглубление — только у прямых; цены на сайте нет"),
    }),
    ("OLD.26.0000070", {
        "Артикул": "SMA-JW2CG", "Тип/Серия": "SMA-male R/A", "Провод": "", "Производитель": "",
        "Серия": "SMA", "Пол": "штекер", "Полярность": "обычная", "Исполнение": "угловой",
        "Тип пина": "пайка внутри корпуса",
        "Наименование": "Разъём SMA-JW2CG (SMA, штекер, угловой; кабель не установлен)",
        "Примечание": "склад ЦАР: ГРН OLD.26.0000070, ячейка 18C2D; текст партии «SMA-JW2CG»",
        "Источник данных": (
            "21.10.2026: склад ЦАР, текст партии «SMA-JW2CG»: артикул; «JW» = штекер угловой (как SMA-JW3, SMA-JWB2: "
            "J — штекер, W — угловой) → Тип/Серия, Серия, Пол, Исполнение; Полярность обычная — нет «RP»; "
            f"{ANGLE_PIN}. Изготовитель не установлен: у HenryTech (hltchips.com: SMA, ISMA, 2.92, 3.5 мм — 181 позиция) "
            f"артикула SMA-JW2CG нет; ближайший HL-SMA-JWB2CG ({URL_JWB2CG}: угловой на кабель .086, DC–18 ГГц, КСВ 1,30; "
            "чертёж LCSC C7420648 — 13,9 × 14,2 мм, разделки нет) отличается буквой «B» — не принят; партия лежит среди "
            "разъёмов HenryTech на RG-405 (ГРН OLD.26.0000062…078). Провод, Производитель, Тип экрана, L1–L3, частота, "
            "КСВ — пусто до сверки с самим разъёмом (есть ли хвостовик под .086)"),
    }),
]

JWB3_SOURCE_OLD = (
    "склад ЦАР, текст партии «SMA-JWB3-F-1»: артикул, пол, исполнение, провод; документа производителя на этот артикул не "
    "найдено — в базе только то, что на партии склада; 19.10.2026; 20.10.2026: дозаполнено — Полярность — по «Тип/Серия» "
    "(значения этого типа в базе); Волновое сопротивление, Ом — по серии «sma»: у всех заполненных строк серии одно "
    "значение; 20.10.2026: Тип пина — пайка внутри корпуса — угловой разъём: центральная жила паяется в контакт через окно "
    "корпуса, под крышкой; «обжимной» у продавца — про гильзу экрана, не про пин (владелец 20.10: «как у углового может "
    "быть обжим пина»)")
JWB3_NOTE_OLD = "склад ЦАР: ГРН OLD.26.0000068, ячейка 18E6B; текст партии «SMA-JWB3-F-1»"
EDIT = ("OLD.26.0000068", "SMA-JWB3-F-1", "RG-402", [
    FieldEdit("coaxMfr", "", HLT),
    FieldEdit("L1", "", "3"),
    FieldEdit("Тип экрана", "", "пайка"),
    FieldEdit("Частота до, ГГц", "", "12"),
    FieldEdit("КСВ", "", "≤1,30"),
    FieldEdit("Ссылка", "", URL_JWB3F),
    FieldEdit("Примечание", JWB3_NOTE_OLD, JWB3_NOTE_OLD + "; у изготовителя — HL-SMA-JWB3-F (без «-1»)"),
    FieldEdit("Источник данных", JWB3_SOURCE_OLD, JWB3_SOURCE_OLD + (
        f"; 21.10.2026: изготовитель — Chengdu HenryTech (hltchips.com), страница HL-SMA-JWB3-F {URL_JWB3F}: "
        "«SMA公头弯式接.141电缆射频同轴连接器 DC-12G，驻波比≤1.30» — SMA-штекер угловой на кабель .141 (= RG-402), "
        "DC–12 ГГц, КСВ ≤ 1,30 → Производитель, Частота, КСВ, Ссылка; что значит хвост «-1» артикула склада, на сайте не "
        f"сказано (у HenryTech так же HL-ISMA-JB3A-1) — данные взяты по HL-SMA-JWB3-F; {SEMI_SHIELD}; {HS_ANGLE}")),
])
LOT_RENAME = ("OLD.26.0000071", "ISMA-JB3 SMA-male RG402", "ISMA-JB3")
LINKS = [  # ГРН → (артикул строки, провод строки)
    ("OLD.26.0000063", "SMA-JWB2-F", "RG-405"),
    ("OLD.26.0000071", "ISMA-JB3", "RG-402"),
    *[(f"OLD.26.00000{n}", "ISMA-KB2-F", "RG-405") for n in range(73, 79)],
    ("OLD.26.0000064", "ISMA-KB3A", "RG-402"), ("OLD.26.0000065", "ISMA-KB3A", "RG-402"),
]


def coax_rows():
    with refs() as uow:
        return uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)


def find(rows, article, wire):
    return [r for r in rows if str(r.fields.get("Артикул", "")).strip().casefold() == article.casefold()
            and cable_key(str(r.fields.get("Провод", ""))) == cable_key(wire)]


def lot(grn):
    lot_id, _ = LOTS[grn]
    with stock() as uow:
        return uow.warehouse.get(UUID(lot_id))


def links_of(lot_id):
    with refs() as uow:
        return uow.stock_links.list(lot_id=UUID(lot_id), limit=100)


problems: list[str] = []
rows = coax_rows()
print(f"== БД.КОАКС: {len(rows)} строк")
for grn, (lot_id, article) in LOTS.items():
    item = lot(grn)
    if item is None:
        problems.append(f"{grn}: партии нет")
    elif item.grn != grn or item.article not in (article, LOT_RENAME[2] if grn == LOT_RENAME[0] else article):
        problems.append(f"{grn}: партия {item.grn} «{item.article}» — ждали «{article}»")

if mode == "rollback":
    for _, fields in CREATE:
        for record in find(rows, fields["Артикул"], fields["Провод"]):
            delete_record(record.id, ACTOR, now, refs(), reason="откат записи 21.10.2026 (разъёмы склада → свои строки)")
            print("   удалена строка:", fields["Артикул"], "×", fields["Провод"] or "—")
    grn, article, wire, edits = EDIT
    record = find(rows, article, wire)[0]
    back = tuple(FieldEdit(e.field, e.value, e.old) for e in edits if record.fields.get(e.field, "") == e.value)
    if back:
        link_stock_article(LinkStockCommand(article=read_stock_article(stock, UUID(LOTS[grn][0])), actor=ACTOR, now=now,
                                            record_id=record.id, tab=TechOperationTab.COAX, edits=back), refs())
        print(f"   {article}: возвращено ячеек {len(back)}")
    item = lot(LOT_RENAME[0])
    if item.article == LOT_RENAME[2]:
        edit_lot_identity(EditLotIdentityCommand(item.id, item.version, LOT_RENAME[1], item.name, item.series, ACTOR, now),
                          stock())
        print(f"   партия {LOT_RENAME[0]}: артикул «{LOT_RENAME[1]}» возвращён")
    raise SystemExit(0)

# --- проверка (dry и перед apply) ---
plan = []
for grn, fields in CREATE:
    have = find(rows, fields["Артикул"], fields["Провод"])
    plan.append(("create", grn, fields, have))
    print(f"   создать {fields['Артикул']} × {fields['Провод'] or '—'}: " + ("уже есть" if have else "строки нет — будет создана"))
    if not have:  # проба: та же запись внутри транзакции без commit — откатывается при выходе
        uow = refs()
        with uow:
            probe = _create_in(TechOperationTab.COAX, _clean_new_fields(TechOperationTab.COAX, fields), uow)
            print(f"     проба: «{probe.display_text}», полей {len(probe.fields)}, "
                  f"Артикул «{probe.fields.get('Артикул')}», Провод «{probe.fields.get('Провод')}», "
                  f"Производитель «{probe.fields.get('Производитель', '')}», coaxArticle «{probe.fields.get('coaxArticle')}», coaxWire «{probe.fields.get('coaxWire')}», coaxType «{probe.fields.get('coaxType')}», coaxMfr «{probe.fields.get('coaxMfr')}»")
grn, article, wire, edits = EDIT
target = find(rows, article, wire)
if len(target) != 1:
    problems.append(f"{article} × {wire}: строк {len(target)}, ждали 1")
else:
    current = target[0].fields
    done = all(current.get(e.field, "") == e.value for e in edits)
    for e in edits:
        state = "уже" if current.get(e.field, "") == e.value else ("ок" if current.get(e.field, "") == e.old else "ОТКАЗ: стоит другое")
        if state.startswith("ОТКАЗ") and not done:
            problems.append(f"{article}: {e.field} — стоит «{current.get(e.field, '')[:60]}»")
        print(f"   {article} · {e.field}: {state}")
item = lot(LOT_RENAME[0])
if item is None:
    print("== СТОП:"); [print("   ", p) for p in problems]; raise SystemExit(1)
print(f"   партия {LOT_RENAME[0]}: артикул «{item.article}»" + (" — уже переименована" if item.article == LOT_RENAME[2] else f" → «{LOT_RENAME[2]}»"))
with engine.connect() as connection:  # где ещё стоит старый текст артикула партии (спецификации, комплекты, ЭТК)
    columns = connection.execute(text(
        "select table_name, column_name from information_schema.columns where table_schema = 'public' "
        "and data_type in ('text', 'character varying', 'json', 'jsonb') and table_name not in "
        "('warehouse_lots', 'audit_events', 'reference_stock_links')")).all()
    for table, column in columns:
        count = connection.execute(text(f'select count(*) from "{table}" where cast("{column}" as text) like :q'),
                                   {"q": "%ISMA-JB3 SMA-male%"}).scalar()
        if count:
            print(f"   «ISMA-JB3 SMA-male…» встречается: {table}.{column} — {count}")
planned = {(f["Артикул"], f["Провод"]) for _, f in CREATE}
for grn, art, wire in LINKS:
    found = find(rows, art, wire)
    if not found and (art, wire) in planned:
        continue  # строку создаёт этот же запуск (шаг 1)
    if len(found) != 1:
        problems.append(f"привязка {grn}: строк {art} × {wire} — {len(found)}")
print(f"   журнал «Склад → база» по этим партиям: {sum(len(links_of(LOTS[g][0])) for g in LOTS)} строк")
if problems:
    print("== СТОП:"); [print("   ", p) for p in problems]
    raise SystemExit(1)
if mode == "dry":
    print("== dry: проблем нет, ничего не записано")
    raise SystemExit(0)

# --- apply ---
for kind, grn, fields, have in plan:
    if have:
        continue
    result = link_stock_article(LinkStockCommand(article=read_stock_article(stock, UUID(LOTS[grn][0])), actor=ACTOR, now=now,
                                                 tab=TechOperationTab.COAX, fields=fields), refs())
    print(f"== создана {result.record.id} {fields['Артикул']} × {fields['Провод'] or '—'}; журнал {result.entry.event.value} {grn}")
grn, article, wire, edits = EDIT
record = find(coax_rows(), article, wire)[0]
todo = tuple(e for e in edits if record.fields.get(e.field, "") != e.value)
result = link_stock_article(LinkStockCommand(article=read_stock_article(stock, UUID(LOTS[grn][0])), actor=ACTOR, now=now,
                                             record_id=record.id, tab=TechOperationTab.COAX, edits=todo), refs())
print(f"== {article}: правок {len(todo)}; журнал {result.entry.event.value if result.entry else 'без изменений'} {grn}")
item = lot(LOT_RENAME[0])
if item.article == LOT_RENAME[1]:
    updated = edit_lot_identity(EditLotIdentityCommand(item.id, item.version, LOT_RENAME[2], item.name, item.series, ACTOR, now),
                                stock())
    print(f"== партия {LOT_RENAME[0]}: артикул «{LOT_RENAME[1]}» → «{updated.article}», наименование «{updated.name}»")
rows = coax_rows()
for grn, art, wire in LINKS:
    record = find(rows, art, wire)[0]
    result = link_stock_article(LinkStockCommand(article=read_stock_article(stock, UUID(LOTS[grn][0])), actor=ACTOR, now=now,
                                                 record_id=record.id, tab=TechOperationTab.COAX), refs())
    print(f"== {grn} → {art} × {wire}: {result.entry.event.value if result.entry else 'уже привязана'}")
print("== apply: готово")

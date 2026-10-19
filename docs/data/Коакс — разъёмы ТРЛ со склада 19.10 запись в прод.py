"""Запись в прод 19.10.2026 — БД.КОАКС: разъёмы ТРЛ, которые лежат на складе ЦАР, а в базе их нет (владелец 19.10: «они не все в
базе есть, надо бы занести — только от ТРЛ которые»).

Как опознаны разъёмы ТРЛ: в партиях и в базе нет ни поставщика, ни производителя (склад — выгрузка, `manufacturer` пуст у
всех 597 партий). Опознаны по артикулу вида «I<серия>-K/J…» (ISMA-KB3A, ISMA-JB3 — в БД.КОАКС они уже стоят с
производителем «TRL»; подсказка владельца 19.10). На складе такой артикул, которого нет в базе, один — ISMA-KB2-F
(ГРН OLD.26.0000073…078, ЦАР 18C3A…F, 3 000 шт). ISMA-JB3 уже есть в базе (артикул склада «ISMA-JB3 SMA-male RG402»
находит его по слову артикула) — не пишется.

Что пишется (только то, что даёт документ; не выдумано ничего — пусто остаётся пустым):
  HL-ISMA-KB2-F, http://www.hltchips.com/product_detail.html?id=842 (чертёж и таблица: ISMA = SMA, розетка, под
  кабель 086/RG405, DC–6 ГГц, 50 Ом, КСВ ≤ 1,25, ресурс ≥ 500, корпус латунь с золотом, штифт бериллиевая бронза с золотом,
  длина 14 мм, шестигранник 6,5 мм, хвостовик Ø2,2 мм под пайку);
  «Заглубление контакта» SMA-розетки — как у всех SMA-розеток (radiolab, H+I+J = 1,88…2,41 мм, владелец 19.10).
НЕ пишется: L1–L3 (в документе нет разделки кабеля), HEX (экран паяный), цена.

Ключ строки — пара «артикул + провод» (`import_schema`), «Артикул» как на партии, существующие артикулы не переименованы.
Режимы: dry | apply. Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  ... python - apply < этот файл
  ... python - dry   < этот файл   → всё «без изменений»
Откат: удалить строку (ключ ISMA-KB2-F × RG-405) в «Базе данных» → БД.КОАКС; журнал — docs/data/«Коакс — разъёмы ТРЛ со склада 19.10.csv»."""
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Разъёмы ТРЛ 19.10.2026", department="System")
URL = "http://www.hltchips.com/product_detail.html?id=842"
DEPTH_URL = "https://www.radiolab.ru/ru/attach_file/smaradiolab.pdf"
ROWS = [{
    "coaxArticle": "ISMA-KB2-F", "coaxWire": "RG-405", "coaxType": "SMA-female", "coaxMfr": "TRL",
    "Тип пина": "пайка", "Тип экрана": "пайка",
    "Серия": "SMA", "Пол": "гнездо", "Полярность": "обычная", "Исполнение": "прямой", "Монтаж": "напаиваемый",
    "Волновое сопротивление, Ом": "50", "Частота до, ГГц": "6", "КСВ": "≤1,25",
    "Материал корпуса": "латунь", "Покрытие": "корпус: золото; центральный контакт: золото", "Габариты": "14 x 6,5 мм (шестигранник)",
    "Наименование": "Разъём ISMA-KB2-F (SMA-female, напаиваемый, на кабель RG-405 / 086)",
    "Ссылка": URL,
    "Примечание": "склад ЦАР: ГРН OLD.26.0000073…078, ячейки 18C3A…18C3F; разъём ТРЛ (артикул вида ISMA-K…); на сайте HL-ISMA-KB2-F",
    "Источник данных": "ТРЛ (по артикулу ISMA-K…); HLT/Hengliti HL-ISMA-KB2-F, " + URL + ": ISMA-розетка под кабель 086/RG405, DC–6 ГГц, 50 Ом, "
                       "КСВ ≤ 1,25, корпус латунь/золото, штифт бериллиевая бронза/золото, изолятор фторопласт, длина 14 мм, шестигранник 6,5 мм, "
                       "хвостовик Ø2,2 мм с отверстием под пайку; пайка пина и экрана — как у ISMA-KB3A (семейство KB, полужёсткий кабель); "
                       "L1–L3 в документе нет — не заполнены; 19.10.2026",
    "Заглубление контакта, мм": "1,88…2,41",
    "Заглубление контакта, источник":
        "radiolab.ru, SMA series connectors, габаритно-присоединительные размеры, розетка (мама): H + I + J = "
        "(1,88 + 0,00 + 0,00)…(1,98 + 0,25 + 0,18) = 1,88…2,41 мм — торец → REF (H) → изолятор (I) → контакт (J); "
        f"стандарт SMA (MIL-C-39012, IEC 169-15), общий для серии, не лист артикула; {DEPTH_URL}; 19.10.2026",
}]

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()


def call(dry_run: bool):
    return import_rows("coax", factory(), [ImportRowIn(fields=fields, old=None) for fields in ROWS], ACTOR, now,
                       dry_run=dry_run, source="erp").model_dump()


data = call(True)
print("== dry:", {k: (len(v) if isinstance(v, list) else v) for k, v in data.items()})
for key in ("created", "updated", "refused"):
    for item in data.get(key) or []:
        print("  ", key, item)
if mode == "apply":
    if len(data["created"]) != len(ROWS) or data["refused"]:
        raise SystemExit("СТОП — ожидалось создать ровно новые строки, отказов 0; ничего не записано")
    data = call(False)
    print("== apply:", {k: (len(v) if isinstance(v, list) else v) for k, v in data.items()})

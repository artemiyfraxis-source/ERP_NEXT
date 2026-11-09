"""Запись в прод 25.10.2026 — БД.КОАКС: две новые строки по документам изготовителя (владелец 25.10: добавить разъёмы
комплектов 708К., которых нет в базе). Третий разъём комплекта 552К.5 «N231» — не создаётся: это N-231L gsm-repiteri.ru,
строки N-231L × RG-316 / RG-174 уже есть (правило «один артикул — одна строка»), см. журнал CSV.

  1. 73403-6270 × LMR-100 — Molex, FAKRA SMB plug, прямой, обжим, код Z (Water Blue); партия склада OLD.26.0000516.
  2. 901-9610-3 × LMR-100 — Amphenol RF, SMA bulkhead jack (розетка с креплением на панель), обжим экрана, пайка контакта.

import_rows("coax") по (артикул, провод), old = {} — только создание; если строка с таким ключом уже есть, скрипт
останавливается (обновлений быть не должно). Журнал ячеек — «КОАКС — 73403-6270 и 901-9610-3 по документам 25.10.csv».

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → создано 0, без изменений 2
Откат: удалить две строки (карточка строки в БД.КОАКС → «Удалить»), бэкап до записи — в журнале CSV.
"""
import os
import sys

from app.database import build_engine, build_references_uow_factory
from app.modules.references.importers import import_rows
from app.modules.references.load_mirror import ACTOR
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")

MOLEX_PAGE = "https://www.molex.com/en-us/products/part-detail/734036270"
MOLEX_SD = ("https://www.molex.com/content/dam/molex/molex-dot-com/products/automated/en-us/salesdrawingpdf/734/73403/"
            "734036270_sd.pdf")
MOLEX_AS = ("https://www.molex.com/content/dam/molex/molex-dot-com/products/automated/en-us/applicationspecificationspdf/"
            "735/73597/AS-73597-045-001.pdf")
AMP_PAGE = "https://www.amphenolrf.com/en-us/part/901-9610-3/4428/"
AMP_C56 = "https://www.amphenolrf.com/en-us/assets/file/4065861011/"
AMP_DWG = "https://www.amphenolrf.com/en-us/assets/file/4065788941/"
SMA_DEPTH_SOURCE = (
    "MIL-STD-348B (2014), рис. 310-2, розетка SMA: H 1,88…1,98 (торец → REF) + G 0,00…0,25 (утопание гнезда) = "
    "1,88…2,23 мм; интерфейс — IEC 61169-15:2021; владелец 24.10.2026: «MIL-STD-348B важнее»; чертёж изготовителя "
    f"901-9610-3 rev J: «INTERFACE PER MIL-STD-348 SMA SERIES FIGURE 310.2» ({AMP_DWG}); 25.10.2026")

ROWS = [
    {
        "coaxArticle": "73403-6270", "coaxWire": "LMR-100",
        "Артикул": "73403-6270", "Провод": "LMR-100", "Тип/Серия": "FAKRA-male (тип Z)", "Производитель": "Molex",
        "Серия": "Fakra", "Пол": "штекер", "Полярность": "обычная", "Исполнение": "прямой",
        "L1": "2,84", "L2": "4,21", "L3": "8,41",
        "Тип пина": "обжим", "Тип экрана": "обжим", "Монтаж": "обжимной", "Центральный контакт": "обжим",
        "Крепление экрана": "обжимной", "HEX обжима, мм": "3,25",
        "Волновое сопротивление, Ом": "50", "Частота до, ГГц": "6",
        "Материал корпуса": "латунь", "Покрытие": "корпус: никель; центральный контакт: золото",
        "Наименование": ("Разъём 73403-6270 (FAKRA SMB, штекер, прямой, обжимной, код Z — Water Blue, 50 Ом; "
                         "кабель LMR-100, RG-174, RG-188, RG-316)"),
        "Ссылка": MOLEX_PAGE,
        "Примечание": ("склад: партия OLD.26.0000516 «II PLUS STR KE MOLEX RF Connectors / coaxial»; комплект 708К. "
                       "на LMR-100; по Molex тот же разъём идёт на RG-174, RG-188, RG-316 — строки под эти кабели не "
                       "заводились"),
        "Источник данных": (
            f"25.10.2026: Molex, страница 734036270 ({MOLEX_PAGE}): «FAKRA Plug, Straight, Crimp, RG-316 and RG-174 "
            "Cable, Brass Body, Nickel (Ni) Plated, Keying Code Z, Water Blue, 50 Ohms»; Frequency 6 GHz; Impedance "
            "50Ω; Gender Plug; Cable Attachment Crimp; Reverse Polar No; Wire/Cable Type «LMR-100, RG-174, RG-188, "
            f"RG-316» → Тип/Серия, Пол, Исполнение, Полярность, Частота, Провод; чертёж SD-73403-626 rev A3 ({MOLEX_SD}): "
            "таблица кодов «Z WATER BLUE 73403-6270», «SMB PLUG FAKRA STR. 50 OHMS», материалы «BODY: BRASS, PLATED: "
            "NICKEL; CENTER CONTACT: PHOSPOR BRONZE, PLATED: GOLD; CRIMP TUBE: BRASS» → Материал корпуса, Покрытие; "
            "«RECOMMENDED CABLE STRIP DIM.»: 2.84 REF (центральный проводник), 4.21 REF (до оплётки), 8.41 REF (до "
            "оболочки), все от торца кабеля → L1 2,84, L2 4,21, L3 8,41; инструкция AS-73597-045 rev B "
            f"«FAKRA II CABLE ASSEMBLY INSTRUCTIONS FOR 73403-626X» ({MOLEX_AS}): п. 2.3 «A. CRIMPING CONTACT PIN WITH "
            "HAND CRIMP TOOL — DANIELS AFM8 (Depth Setting 4), POSITIONER #K331-2 OR B. … WITH SOLDER» → Тип пина "
            "обжим (пайка допускается); п. 4 «SLIDE CRIMP TUBE OVER BRAID … CRIMP USING 3.25mm (.128) HEX DIE», "
            "клещи с матрицей 73415-4550 → Тип экрана обжим, HEX 3,25; КСВ, заглубление контакта, цена — в документах "
            "Molex не даны"),
    },
    {
        "coaxArticle": "901-9610-3", "coaxWire": "LMR-100",
        "Артикул": "901-9610-3", "Провод": "LMR-100", "Тип/Серия": "SMA-female bulkhead", "Производитель": "Amphenol RF",
        "Серия": "SMA", "Пол": "гнездо", "Полярность": "обычная", "Исполнение": "прямой",
        "L1": "2,8", "L2": "8,6", "L3": "15,5",
        "Тип пина": "пайка", "Тип экрана": "обжим", "Монтаж": "обжимной", "Центральный контакт": "пайка",
        "Крепление экрана": "обжимной", "HEX обжима, мм": "3,25",
        "Волновое сопротивление, Ом": "50", "Частота до, ГГц": "12,4",
        "Материал корпуса": "нержавеющая сталь", "Покрытие": "корпус: золото; центральный контакт: золото",
        "Габариты": ("длина не более 38,1 мм (1.500″); резьба 1/4-36UNS-2A; отверстие в панели Ø6,73 мм с лыской "
                     "6,10 мм (.265″ / .240″) — пересчитано из дюймов"),
        "Заглубление контакта, мм": "1,88…2,23", "Заглубление контакта, источник": SMA_DEPTH_SOURCE,
        "Наименование": ("Разъём 901-9610-3 (SMA, розетка, прямая, на панель с тыльной стороны, обжим экрана, пайка "
                         "контакта; кабель LMR-100A, RG-174, RG-188, RG-316)"),
        "Ссылка": AMP_PAGE,
        "Примечание": ("комплект 708К. на LMR-100; у Amphenol кабель назван «Times LMR-100A»; строки под RG-174 / RG-188 "
                       "/ RG-316 не заводились; на складе партий нет"),
        "Источник данных": (
            f"25.10.2026: Amphenol RF, страница 901-9610-3 ({AMP_PAGE}): «SMA Straight Crimp Jack RG-174 RG-188 RG-316 "
            "Times LMR-100A Bulkhead 50 Ohm»; Gender Jack; Orientation Straight; Panel Mounting Feature «Bulkhead - Rear "
            "Mount»; Frequency (Max GHz) 12.4; Impedance 50; Body Material Stainless Steel, Body Finish Gold; Contact "
            "Material Beryllium Copper, Contact Finish Gold; Insulator PTFE; Assembly Tool CTL-2 → Тип/Серия, Пол, "
            "Исполнение, Частота, Материал корпуса, Покрытие; Cable Type «… Times LMR-100A …» → Провод; "
            f"чертёж 901-9610-3 rev J ({AMP_DWG}): «FREQUENCY RANGE: DC - 12.4 GHz», «CRIMP USING TOOL 227-944. DIE SET "
            "227-1221-03 CAVITY A (.128 HEX)», 1.500 MAX, .250-36UNS-2A, отверстие Ø.265 / .240 → Габариты, HEX 3,25 "
            f"(.128″ × 25,4); инструкция Assembly Instructions C56 ({AMP_C56}), строка «901-9610-3, -3SF SMA Bulkhead "
            "Jack 174,179,187,188,316: a .610 (15.5), b .340 (8.6), c .110 (2.8); Hex Cavity for Outer Ferrule .128 "
            "(3.3)» — a от среза оболочки, b от среза оплётки, c от среза диэлектрика до торца жилы → L3 15,5, L2 8,6, "
            "L1 2,8; заголовок C56 «BRAID CRIMP-SOLDER CENTER CONTACT», шаг 3 «Contact must butt firmly against "
            "insulator washer while soldering» → Тип пина пайка, Тип экрана обжим (на странице «Contact Termination: "
            "Crimp» — по инструкции контакт паяется, взято по инструкции); LMR-100A в таблице C56 не назван, размеры "
            "взяты по строке артикула (RG-174/316, тот же диаметр по оболочке); КСВ, цена — у Amphenol не даны"),
    },
]

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()


def call(dry_run):
    return import_rows("coax", factory(), [ImportRowIn(fields=fields, old={}) for fields in ROWS], ACTOR, now,
                       dry_run=dry_run, source="erp").model_dump()


data = call(True)
print(f"== проверка: строк {len(ROWS)} → создано {len(data['created'])}, обновлено {len(data['updated'])}, "
      f"без изменений {data['unchanged']}, отказ {len(data['refused'])}")
for item in data["created"]:
    print("   создать:", item)
for item in data["updated"] + data["refused"]:
    print("   !", item)
if data["updated"] or data["refused"]:
    raise SystemExit("СТОП: строка с таким ключом уже есть или отказ — ничего не записано")
if mode == "apply" and data["created"]:
    data = call(False)
    print(f"== записано: создано {len(data['created'])}, обновлено {len(data['updated'])}, "
          f"без изменений {data['unchanged']}, отказ {len(data['refused'])}")
    for item in data["created"]:
        print("   создана:", item)
print("ИТОГО", "(проверка, ничего не записано)" if mode == "dry" else "(записано)")

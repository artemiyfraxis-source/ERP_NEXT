"""Запись в прод 15.10.2026 — монтажные провода UL3239 (8 типоразмеров) и UL3135 (22 AWG + 6 новых).

Владелец: «проработать» серии UL3239 и UL3135 (пустые колонки на скриншоте). Порядок (live-data protocol,
prod-reference-write):

  cd /opt/erp/app/deploy
  sudo -n bash erp.sh exec -T api python - dry   < этот файл
  sudo -n bash erp.sh backup-now
  sudo -n bash erp.sh exec -T api python - apply < этот файл
  sudo -n bash erp.sh exec -T api python - dry   < этот файл   -> «уже есть» / отказ «значение уже другое»

Ключ — «Марка». Пустые ячейки пишутся с old = "" (заполненное вернётся отказом); четыре форматные правки
UL3135 22AWG (Тип провода, Стандарт, Жила, Изоляция) — явно разрешены владельцем, их old — прежние значения.
Откатный журнал — docs/data/«Провода UL3239 UL3135 — запись 15.10.csv»."""
import json
import os
import sys

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import ColumnCommand, EditRecordCommand, FieldEdit, add_column, edit_record
from app.modules.references.domain import BASE_COLUMNS, TechOperationTab, cable_key
from app.modules.references.importers import import_rows
from app.modules.references.load_mirror import ACTOR
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")
PAYLOAD = json.loads(r'''
{
 "columns": [
  "Артикул поставщика",
  "Цена поставщика, ₽/м",
  "Дата цены",
  "Материал жилы",
  "Материал оболочки"
 ],
 "groups": [
  {
   "name": "UL3239: заполнение пустых ячеек (8 строк)",
   "new": false,
   "rows": [
    {
     "fields": {
      "Марка": "UL3239 24AWG",
      "Производители": "CnC Tech (Тайвань)",
      "Масса, г/м": "5,0",
      "Ссылка на datasheet": "https://atta.szlcsc.com/upload/public/pdf/source/20231008/B9E0930CC0C3F9BF59B4373637ECE412.pdf",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     },
     "old": {
      "Производители": "",
      "Масса, г/м": "",
      "Ссылка на datasheet": "",
      "Материал жилы": "",
      "Материал оболочки": ""
     }
    },
    {
     "fields": {
      "Марка": "UL3239 22AWG",
      "Производители": "CnC Tech (Тайвань)",
      "Масса, г/м": "6,5",
      "Ссылка на datasheet": "https://atta.szlcsc.com/upload/public/pdf/source/20231008/B9E0930CC0C3F9BF59B4373637ECE412.pdf",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     },
     "old": {
      "Производители": "",
      "Масса, г/м": "",
      "Ссылка на datasheet": "",
      "Материал жилы": "",
      "Материал оболочки": ""
     }
    },
    {
     "fields": {
      "Марка": "UL3239 20AWG",
      "Производители": "CnC Tech (Тайвань)",
      "Масса, г/м": "8,7",
      "Ссылка на datasheet": "https://atta.szlcsc.com/upload/public/pdf/source/20231008/B9E0930CC0C3F9BF59B4373637ECE412.pdf",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     },
     "old": {
      "Производители": "",
      "Масса, г/м": "",
      "Ссылка на datasheet": "",
      "Материал жилы": "",
      "Материал оболочки": ""
     }
    },
    {
     "fields": {
      "Марка": "UL3239 18AWG",
      "Производители": "CnC Tech (Тайвань)",
      "Масса, г/м": "12,0",
      "Ссылка на datasheet": "https://atta.szlcsc.com/upload/public/pdf/source/20231008/B9E0930CC0C3F9BF59B4373637ECE412.pdf",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     },
     "old": {
      "Производители": "",
      "Масса, г/м": "",
      "Ссылка на datasheet": "",
      "Материал жилы": "",
      "Материал оболочки": ""
     }
    },
    {
     "fields": {
      "Марка": "UL3239 16AWG",
      "Производители": "CnC Tech (Тайвань)",
      "Масса, г/м": "17,1",
      "Ссылка на datasheet": "https://atta.szlcsc.com/upload/public/pdf/source/20231008/B9E0930CC0C3F9BF59B4373637ECE412.pdf",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     },
     "old": {
      "Производители": "",
      "Масса, г/м": "",
      "Ссылка на datasheet": "",
      "Материал жилы": "",
      "Материал оболочки": ""
     }
    },
    {
     "fields": {
      "Марка": "UL3239 14AWG",
      "Производители": "CnC Tech (Тайвань)",
      "Масса, г/м": "25,3",
      "Ссылка на datasheet": "https://atta.szlcsc.com/upload/public/pdf/source/20231008/B9E0930CC0C3F9BF59B4373637ECE412.pdf",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     },
     "old": {
      "Производители": "",
      "Масса, г/м": "",
      "Ссылка на datasheet": "",
      "Материал жилы": "",
      "Материал оболочки": ""
     }
    },
    {
     "fields": {
      "Марка": "UL3239 12AWG",
      "Производители": "CnC Tech (Тайвань)",
      "Масса, г/м": "39,0",
      "Ссылка на datasheet": "https://atta.szlcsc.com/upload/public/pdf/source/20231008/B9E0930CC0C3F9BF59B4373637ECE412.pdf",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     },
     "old": {
      "Производители": "",
      "Масса, г/м": "",
      "Ссылка на datasheet": "",
      "Материал жилы": "",
      "Материал оболочки": ""
     }
    },
    {
     "fields": {
      "Марка": "UL3239 10AWG",
      "Производители": "CnC Tech (Тайвань)",
      "Масса, г/м": "60,0",
      "Ссылка на datasheet": "https://atta.szlcsc.com/upload/public/pdf/source/20231008/B9E0930CC0C3F9BF59B4373637ECE412.pdf",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     },
     "old": {
      "Производители": "",
      "Масса, г/м": "",
      "Ссылка на datasheet": "",
      "Материал жилы": "",
      "Материал оболочки": ""
     }
    }
   ]
  },
  {
   "name": "UL3135 22AWG: форматные правки (разрешены) и заполнение пустых",
   "new": false,
   "rows": [
    {
     "fields": {
      "Марка": "UL3135 22AWG",
      "Тип провода": "монтажный",
      "Стандарт": "UL 758; AWM Style 3135 (600 В)",
      "Жила: проволок × диаметр, мм": "60×0,08",
      "Изоляция": "силиконовая резина",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина",
      "Поставщик": "ПроДиэл (prodiel.ru)",
      "Артикул поставщика": "11317",
      "Цена поставщика, ₽/м": "20,07",
      "Дата цены": "15.10.2026",
      "Масса, г/м": "5,1"
     },
     "old": {
      "Тип провода": "монтажный (силиконовый)",
      "Стандарт": "UL3135 22AWG (prodiel.ru)",
      "Жила: проволок × диаметр, мм": "60×0,08 (луженая медь)",
      "Изоляция": "силикон (толщина 0,45мм)",
      "Материал жилы": "",
      "Материал оболочки": "",
      "Поставщик": "",
      "Артикул поставщика": "",
      "Цена поставщика, ₽/м": "",
      "Дата цены": "",
      "Масса, г/м": ""
     }
    }
   ]
  },
  {
   "name": "UL3135: новые типоразмеры 24, 20, 18, 16, 14, 12 AWG (HELUKABEL)",
   "new": true,
   "rows": [
    {
     "fields": {
      "Марка": "UL3135 24AWG",
      "Тип провода": "монтажный",
      "Стандарт": "UL 758; AWM Style 3135 (600 В)",
      "Производители": "HELUKABEL (Германия)",
      "Сечение, мм2": "0,205",
      "AWG": "24",
      "Жила: проволок × диаметр, мм": "1×0,5",
      "Число жил": "1",
      "Цвета": "чёрный, синий, коричневый, красный, белый, серый, фиолетовый, зелёный, жёлто-зелёный",
      "Наружный диаметр, мм": "2,1",
      "Изоляция": "силиконовая резина",
      "Экран": "нет",
      "Темп. от, °C": "-60",
      "Темп. до, °C": "200",
      "Напряжение, В": "600",
      "Масса, г/м": "6,3",
      "Ссылка на datasheet": "https://assets-cdn.helukabel.com/suppliers/Helukabel/documents/db/HELUKABEL_M47021_EN_GB.pdf",
      "Источник данных": "HELUKABEL, «SINGLE CORE UL Style 3135», датащит M47021 от 06.09.2021 — assets-cdn.helukabel.com/suppliers/Helukabel/documents/db/HELUKABEL_M47021_EN_GB.pdf: 1×0,5 мм, Ø 2,1 мм, масса 6,3 кг/км (Cu 1,9 кг/км), 600 В AC, −60…+200 °C, медь лужёная, силикон; shop.helukabel.com/en/HELUKABEL-SINGLE-CORE-UL-Style-3135-M47021/47021 (арт. 47021, чёрный)",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     }
    },
    {
     "fields": {
      "Марка": "UL3135 20AWG",
      "Тип провода": "монтажный",
      "Стандарт": "UL 758; AWM Style 3135 (600 В)",
      "Производители": "HELUKABEL (Германия)",
      "Сечение, мм2": "0,518",
      "AWG": "20",
      "Жила: проволок × диаметр, мм": "5×0,4",
      "Число жил": "1",
      "Цвета": "чёрный, синий, коричневый, красный, белый, серый, фиолетовый, зелёный, жёлто-зелёный",
      "Наружный диаметр, мм": "2,6",
      "Изоляция": "силиконовая резина",
      "Экран": "нет",
      "Темп. от, °C": "-60",
      "Темп. до, °C": "200",
      "Напряжение, В": "600",
      "Масса, г/м": "12,3",
      "Ссылка на datasheet": "https://assets-cdn.helukabel.com/suppliers/Helukabel/documents/db/HELUKABEL_M47021_EN_GB.pdf",
      "Источник данных": "HELUKABEL, «SINGLE CORE UL Style 3135», датащит M47021 от 06.09.2021 — assets-cdn.helukabel.com/suppliers/Helukabel/documents/db/HELUKABEL_M47021_EN_GB.pdf: 5×0,4 мм, Ø 2,6 мм, масса 12,3 кг/км (Cu 6,0 кг/км), 600 В AC, −60…+200 °C, медь лужёная, силикон; shop.helukabel.com/en/HELUKABEL-SINGLE-CORE-UL-Style-3135-M47021/47035 (арт. 47035, чёрный)",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     }
    },
    {
     "fields": {
      "Марка": "UL3135 18AWG",
      "Тип провода": "монтажный",
      "Стандарт": "UL 758; AWM Style 3135 (600 В)",
      "Производители": "HELUKABEL (Германия)",
      "Сечение, мм2": "0,823",
      "AWG": "18",
      "Жила: проволок × диаметр, мм": "7×0,4",
      "Число жил": "1",
      "Цвета": "чёрный, синий, коричневый, красный, белый, серый, фиолетовый, зелёный, жёлто-зелёный",
      "Наружный диаметр, мм": "2,9",
      "Изоляция": "силиконовая резина",
      "Экран": "нет",
      "Темп. от, °C": "-60",
      "Темп. до, °C": "200",
      "Напряжение, В": "600",
      "Масса, г/м": "15,5",
      "Ссылка на datasheet": "https://assets-cdn.helukabel.com/suppliers/Helukabel/documents/db/HELUKABEL_M47021_EN_GB.pdf",
      "Источник данных": "HELUKABEL, «SINGLE CORE UL Style 3135», датащит M47021 от 06.09.2021 — assets-cdn.helukabel.com/suppliers/Helukabel/documents/db/HELUKABEL_M47021_EN_GB.pdf: 7×0,4 мм, Ø 2,9 мм, масса 15,5 кг/км (Cu 8,6 кг/км), 600 В AC, −60…+200 °C, медь лужёная, силикон; shop.helukabel.com/en/HELUKABEL-SINGLE-CORE-UL-Style-3135-M47021/47042 (арт. 47042, чёрный)",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     }
    },
    {
     "fields": {
      "Марка": "UL3135 16AWG",
      "Тип провода": "монтажный",
      "Стандарт": "UL 758; AWM Style 3135 (600 В)",
      "Производители": "HELUKABEL (Германия)",
      "Сечение, мм2": "1,309",
      "AWG": "16",
      "Жила: проволок × диаметр, мм": "11×0,4",
      "Число жил": "1",
      "Цвета": "чёрный, синий, коричневый, красный, белый, серый, фиолетовый, зелёный, жёлто-зелёный",
      "Наружный диаметр, мм": "3,3",
      "Изоляция": "силиконовая резина",
      "Экран": "нет",
      "Темп. от, °C": "-60",
      "Темп. до, °C": "200",
      "Напряжение, В": "600",
      "Масса, г/м": "21,0",
      "Ссылка на datasheet": "https://assets-cdn.helukabel.com/suppliers/Helukabel/documents/db/HELUKABEL_M47021_EN_GB.pdf",
      "Источник данных": "HELUKABEL, «SINGLE CORE UL Style 3135», датащит M47021 от 06.09.2021 — assets-cdn.helukabel.com/suppliers/Helukabel/documents/db/HELUKABEL_M47021_EN_GB.pdf: 11×0,4 мм, Ø 3,3 мм, масса 21,0 кг/км (Cu 13,3 кг/км), 600 В AC, −60…+200 °C, медь лужёная, силикон; shop.helukabel.com/en/HELUKABEL-SINGLE-CORE-UL-Style-3135-M47021/47049 (арт. 47049, чёрный)",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     }
    },
    {
     "fields": {
      "Марка": "UL3135 14AWG",
      "Тип провода": "монтажный",
      "Стандарт": "UL 758; AWM Style 3135 (600 В)",
      "Производители": "HELUKABEL (Германия)",
      "Сечение, мм2": "2,081",
      "AWG": "14",
      "Жила: проволок × диаметр, мм": "17×0,4",
      "Число жил": "1",
      "Цвета": "чёрный, синий, коричневый, красный, белый, серый, фиолетовый, зелёный, жёлто-зелёный",
      "Наружный диаметр, мм": "3,6",
      "Изоляция": "силиконовая резина",
      "Экран": "нет",
      "Темп. от, °C": "-60",
      "Темп. до, °C": "200",
      "Напряжение, В": "600",
      "Масса, г/м": "29,7",
      "Ссылка на datasheet": "https://assets-cdn.helukabel.com/suppliers/Helukabel/documents/db/HELUKABEL_M47021_EN_GB.pdf",
      "Источник данных": "HELUKABEL, «SINGLE CORE UL Style 3135», датащит M47021 от 06.09.2021 — assets-cdn.helukabel.com/suppliers/Helukabel/documents/db/HELUKABEL_M47021_EN_GB.pdf: 17×0,4 мм, Ø 3,6 мм, масса 29,7 кг/км (Cu 20,5 кг/км), 600 В AC, −60…+200 °C, медь лужёная, силикон; shop.helukabel.com/en/HELUKABEL-SINGLE-CORE-UL-Style-3135-M47021/47056 (арт. 47056, чёрный)",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     }
    },
    {
     "fields": {
      "Марка": "UL3135 12AWG",
      "Тип провода": "монтажный",
      "Стандарт": "UL 758; AWM Style 3135 (600 В)",
      "Производители": "HELUKABEL (Германия)",
      "Сечение, мм2": "3,309",
      "AWG": "12",
      "Жила: проволок × диаметр, мм": "27×0,4",
      "Число жил": "1",
      "Цвета": "чёрный, синий, коричневый, красный, белый, серый, фиолетовый, зелёный, жёлто-зелёный",
      "Наружный диаметр, мм": "4,1",
      "Изоляция": "силиконовая резина",
      "Экран": "нет",
      "Темп. от, °C": "-60",
      "Темп. до, °C": "200",
      "Напряжение, В": "600",
      "Масса, г/м": "43,2",
      "Ссылка на datasheet": "https://assets-cdn.helukabel.com/suppliers/Helukabel/documents/db/HELUKABEL_M47021_EN_GB.pdf",
      "Источник данных": "HELUKABEL, «SINGLE CORE UL Style 3135», датащит M47021 от 06.09.2021 — assets-cdn.helukabel.com/suppliers/Helukabel/documents/db/HELUKABEL_M47021_EN_GB.pdf: 27×0,4 мм, Ø 4,1 мм, масса 43,2 кг/км (Cu 32,6 кг/км), 600 В AC, −60…+200 °C, медь лужёная, силикон; shop.helukabel.com/en/HELUKABEL-SINGLE-CORE-UL-Style-3135-M47021/47063 (арт. 47063, чёрный)",
      "Материал жилы": "медь лужёная",
      "Материал оболочки": "силиконовая резина"
     }
    }
   ]
  }
 ]
}
''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"

factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
SHEET = set(BASE_COLUMNS[TechOperationTab.WIRE])


def wires():
    with factory() as uow:
        return {cable_key(r.fields.get("Марка") or r.display_text): r for r in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100_000)}


records = wires()
have = {name for record in records.values() for name in record.fields}
for name in PAYLOAD["columns"]:
    if name in have:
        continue
    if dry:
        print(f"столбец «{name}» будет добавлен (apply)")
    else:
        add_column(ColumnCommand(TechOperationTab.WIRE, name, ACTOR, now), factory())
        print(f"столбец «{name}» добавлен")

total = {"created": 0, "updated": 0, "unchanged": 0, "refused": 0, "own_written": 0, "own_refused": 0}
for group in PAYLOAD["groups"]:
    rows, own_cells = [], []
    for row in group["rows"]:
        mark = row["fields"]["Марка"]
        exists = cable_key(mark) in records
        # a new row must not be there yet, a fix must find its row — never create a half row from a fix
        if exists != (not group["new"]):
            print(f"   пропуск «{mark}»: {'уже есть' if exists else 'нет такой строки'}")
            continue
        old = row.get("old") or {}
        sheet = {k: v for k, v in row["fields"].items() if k == "Марка" or k in SHEET}
        own = {k: v for k, v in row["fields"].items() if k != "Марка" and k not in SHEET}
        if len(sheet) > 1:
            rows.append(ImportRowIn(fields=sheet, old={k: v for k, v in old.items() if k in sheet} or None))
        if own:
            own_cells.append((mark, own, {k: old.get(k, "") for k in own}))
    counts = {key: 0 for key in total}
    if rows:
        data = import_rows("prov", factory(), rows, ACTOR, now, dry_run=dry, source="erp").model_dump()
        counts.update(created=len(data["created"]), updated=len(data["updated"]), unchanged=data["unchanged"],
                      refused=len(data["refused"]))
        for item in data["refused"]:
            print("   отказ:", item)
    fresh = wires() if not dry else records
    for mark, own, old in own_cells:
        record = fresh.get(cable_key(mark))
        if record is None:  # dry run: the row is created only by apply
            counts["own_written"] += len(own)
            continue
        stale = [k for k in own if str(record.fields.get(k, "")) != str(old[k])]
        if stale:
            counts["own_refused"] += len(own)
            print(f"   отказ «{mark}»: в ячейках {', '.join(stale)} уже другое значение — не тронуто")
            continue
        if not dry:
            try:
                edit_record(EditRecordCommand(record.id, tuple(FieldEdit(k, old[k], v) for k, v in own.items()), ACTOR, now), factory())
            except Exception as error:  # noqa: BLE001 — one record refused, the rest go on
                counts["own_refused"] += len(own)
                print(f"   отказ «{mark}»: {error}")
                continue
        counts["own_written"] += len(own)
    for key in total:
        total[key] += counts[key]
    print(f"== {group['name']}: строк {len(group['rows'])} → {counts}")
    records = wires()
print("ИТОГО", total, "(проверка, ничего не записано)" if dry else "(записано)")

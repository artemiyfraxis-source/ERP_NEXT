"""Удаление в проде 15.10.2026 — «Коаксиальные кабели» вне трёх поставщиков (владелец: «всё остальное можно сносить —
три основных наших поставщика я тебе отправил»: АМЭЛ, Крокс, gsm-repiteri).

Удаляются только записи без ссылок (БД.КОАКС «Провод», склад, закупки, приёмка, заявки, комплектация, комплекты, ЭТК —
поиск по марке без регистра/пробелов/дефисов, только чтение). Путь — репозиторий справочника и событие журнала, как у
чистки оборудования (`workflows/equipment_cleanup.py`: `tech_operations.delete` + `references.*_removed` со всеми
полями); у БД.ПРОВ/СПР.КАБ своего «Удалить» в интерфейсе нет. Запись удаляется, только если её поля совпадают со
снимком в DELETE (иначе — отказ «поменялась»). Порядок:

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → «нет записи» у всех

Откат — строки из docs/data/«Коакс кабели — удалено 15.10.csv» (все поля и ID) или копия базы до удаления."""
import json
import os
import sys

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import _audit
from app.modules.references.domain import TechOperationTab
from app.modules.references.load_mirror import ACTOR
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")
DELETE = json.loads(r'''[
 {
  "tab": "prov",
  "id": "79cd970c-db4c-4b35-b45d-d4796805d756",
  "key": "LMR-400 LSZH",
  "fields": {
   "Марка": "LMR-400 LSZH",
   "Тип провода": "коаксиальный",
   "Стандарт": "",
   "Производители": "Times Microwave LMR-400-LSZH (LS Cable & System)",
   "Сечение, мм2": "",
   "AWG": "",
   "Жила: проволок × диаметр, мм": "1×2,77 (медненный алюминий)",
   "Число жил": "1",
   "Цвета": "",
   "Наружный диаметр, мм": "10,29",
   "Изоляция": "ПЭ вспененный",
   "Экран": "фольга алюминиевая + оплётка медь лужёная",
   "Волновое сопротивление, Ом": "50",
   "Темп. от, °C": "-40",
   "Темп. до, °C": "85",
   "Напряжение, В": "",
   "Ток, А": "",
   "Масса, г/м": "100",
   "Закупок": "2",
   "Цена посл., ₽/м без НДС": "573,8",
   "Поставщик": "",
   "Ссылка на datasheet": "https://www.1-cable.ru/models/10605-6201.html",
   "Источник данных": "1-cable.ru (LS Cable & System) + типовой Times Microwave LMR-400-LSZH datasheet (темп./масса, т.к. на странице 1-cable.ru не даны); жила — алюминий с медным покрытием, аналогично «LMR-100» в базе; оболочка LSZH безгалогенная — решено агентом — проверить",
   "Категория": "",
   "Артикул поставщика": "",
   "Цена поставщика, ₽/м": "",
   "Дата цены": ""
  }
 },
 {
  "tab": "kab_coax",
  "id": "813a23f2-a521-4ad8-8f14-538466ef2527",
  "key": "LMR-400 LSZH",
  "fields": {
   "Кабель": "LMR-400 LSZH",
   "D1": "2,74",
   "D2": "7,24",
   "D3": "8,13"
  }
 },
 {
  "tab": "prov",
  "id": "f3021b01-7561-4c1a-8cbb-17c10898d1a4",
  "key": "КВСФ-75",
  "fields": {
   "Марка": "КВСФ-75",
   "Тип провода": "коаксиальный",
   "Стандарт": "ГОСТ",
   "Производители": "Камкабель, ОКБ КП",
   "Сечение, мм2": "",
   "AWG": "",
   "Жила: проволок × диаметр, мм": "1× (медь посеребрённая, сплошная)",
   "Число жил": "1",
   "Цвета": "",
   "Наружный диаметр, мм": "4,2",
   "Изоляция": "фторопласт Ф-4МБ",
   "Экран": "оплётка медь лужёная",
   "Волновое сопротивление, Ом": "75",
   "Темп. от, °C": "-60",
   "Темп. до, °C": "200",
   "Напряжение, В": "250",
   "Ток, А": "",
   "Масса, г/м": "",
   "Закупок": "2",
   "Цена посл., ₽/м без НДС": "491,8",
   "Поставщик": "",
   "Ссылка на datasheet": "https://cable.ru/cable/marka-kvsf_75.php",
   "Источник данных": "cable.ru карточка марки КВСФ-75: коаксиал для бортовых мультиплексных систем передачи, оплётка луж.медь, оболочка Ф-4Д+Ф-4МБ, 75±5 Ом, -60…+200°C, макс. 250В (30МГц и выше), ёмкость 65пФ/м, срок службы ≥20 лет; было в «Производители»: бортовые мультиплексные системы, Камкабель, ОКБ КП",
   "Категория": "",
   "Артикул поставщика": "",
   "Цена поставщика, ₽/м": "",
   "Дата цены": ""
  }
 }
]''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
counts = {"deleted": 0, "missing": 0, "refused": 0}
with factory() as uow:
    for item in DELETE:
        tab = TechOperationTab(item["tab"])
        record = next((r for r in uow.tech_operations.list(tab=tab, query="", limit=100_000) if str(r.id) == item["id"]), None)
        if record is None:
            counts["missing"] += 1
            print(f"   нет записи {item['tab']} «{item['key']}» ({item['id']})")
            continue
        name = record.fields.get("Марка") if tab == TechOperationTab.WIRE else record.fields.get("Кабель")
        if name != item["key"] or dict(record.fields) != item["fields"]:
            counts["refused"] += 1
            print(f"   отказ {item['tab']} «{item['key']}»: запись поменялась — не удалена")
            continue
        print("   " + json.dumps({"tab": record.tab.value, "id": str(record.id), "display_text": record.display_text,
                                   "sort_key": record.sort_key, "source_sheet": record.source_sheet,
                                   "mirror_key": record.mirror_key, "edited_in_erp": record.edited_in_erp,
                                   "fields": record.fields}, ensure_ascii=False))
        if not dry:
            uow.tech_operations.delete(record.id)
            _audit("references.coax_cable_removed", ACTOR, now, {
                "record_id": str(record.id), "tab": record.tab.value, "display_text": record.display_text,
                "fields": dict(record.fields), "reason": "вне каталогов АМЭЛ, Крокс, gsm-repiteri; ссылок нет (15.10.2026)"}, uow)
        counts["deleted"] += 1
    if not dry:
        uow.commit()
print("ИТОГО", counts, "(проверка, ничего не удалено)" if dry else "(удалено)")

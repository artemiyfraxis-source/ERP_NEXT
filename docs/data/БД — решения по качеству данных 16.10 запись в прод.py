"""Запись в прод 16.10.2026 — решения по открытым группам отчёта «База — качество данных 14.10.md» (владелец: «сделай
все сам»). Что уже сделано 15–16.10 и что оставлено без источника — в «База — решения по качеству данных 16.10.md».

Каждая ячейка — правка строки «Базы данных» тем же путём, что «Изменить» на экране (`_edit_in`), со старым значением:
ячейка, которую поменяли после снимка 16.10, приходит отказом и не пишется. Строка ищется по натуральному ключу таблицы,
никогда по ID: БД.КОАКС — «Артикул» + кабель (`cable_key`), БД.ТЕР — артикул контакта (REEL), БД.ОП — код операции,
БД.МАТ — «Наименование», БД.ОБ — «Инв. номер». Ключи (артикулы, кабели, коды, инвентарные номера) не меняются.
К строке, где есть столбец источника («Источник данных», у контактов «Источник высоты обжима»), дописывается, что и
почему поменяно. Событие журнала — `references.tech_record_edited` от имени «Решения по качеству данных 16.10.2026».

Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
      → «проверено» у всех ячеек, отказов 0
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «уже так»
Журнал ячеек для отката (таблица, ключ, поле, было, стало, источник) — «База — решения по качеству данных 16.10.csv»."""
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
ACTOR = Actor(id=UUID(int=0), name="Решения по качеству данных 16.10.2026", department="System")
REASON = "решения по качеству данных 14.10, 16.10.2026 (владелец: «сделай все сам»)"
STAMP = "16.10.2026, качество данных"

# [группа, таблица, ключ, поле, было, стало, источник]; ключ БД.КОАКС — «артикул × кабель» (кабель может быть пуст).
# Блок между r''' и ''' читается и генератором CSV-журнала — держать его чистым JSON.
DATA = json.loads(r'''[
 ["3", "coax", "S-A111L × RG-316", "Тип пина", "обжим", "пайка", "gsm-repiteri.ru/prodazha/razyomi/razyom-s-a111l (Ссылка строки): «монтаж центрального контакта осуществляется посредством пайки», в таблице «пайка»; та же пара на RG-174 уже «пайка»"],
 ["3", "coax", "S-A111F × RG-58", "Тип пина", "обжим", "пайка", "gsm-repiteri.ru 0297 (Ссылка строки): «Монтаж центрального контакта: пайка», «Припаять центральный контакт к жиле»"],
 ["3", "coax", "S-231F × RG-58", "Тип пина", "обжим", "пайка", "gsm-repiteri.ru/prodazha/razyomi/razyom-s-231f (Ссылка строки): «Монтаж центрального контакта: пайка»"],
 ["3", "coax", "N-212/8D × 8D-FB", "Тип экрана", "обжим", "", "gsm-repiteri.ru 0309 (Ссылка строки): «Прижимной монтаж оплетки», фиксация резьбовой гайкой; словарь «Тип экрана» — только обжим / пайка, прижимной = пусто (как у 55 прижимных строк)"],
 ["3", "coax", "N-112/8D × 8D-FB", "Тип экрана", "обжим", "", "gsm-repiteri.ru 0105 (Ссылка строки): «Крепление экрана» прижимной; словарь «Тип экрана» — только обжим / пайка, прижимной = пусто (как у 55 прижимных строк)"],
 ["3", "coax", "S1-211L × RG-174", "Тип экрана", "пайка", "обжим", "gsm-repiteri.ru 3420 (Ссылка строки): «Оплетка и хвостовая часть корпуса обжимаются специальными клещами (кримпером)», «Обжимной метод крепления к кабелю в сочетании с пайкой центральной жилы» (в таблице той же страницы «Монтаж оплетки: пайка» — противоречит); аналог Coax Connectors 32-054-B3-AD — SMB crimp jack"],
 ["3", "coax", "S1-211L × RG-174", "Крепление экрана", "пайка", "обжимной", "то же, gsm-repiteri.ru 3420"],
 ["3", "coax", "S1-211L × RG-316", "Тип экрана", "пайка", "обжим", "то же, gsm-repiteri.ru 3420"],
 ["3", "coax", "S1-211L × RG-316", "Крепление экрана", "пайка", "обжимной", "то же, gsm-repiteri.ru 3420"],
 ["доп.", "coax", "N-111C/8D × 8D-FB", "Тип пина", "пайка", "", "gsm-repiteri.ru/prodazha/razyomi/razyem-n-111c-8d (Ссылка строки): «центральный контакт - цанговый и не требует пайки», «Монтаж центрального контакта: цанга»; словарь «Тип пина» — только «пайка», цанга = пусто (pin_type, 24 строки «цанга»)"],
 ["17", "coax", "Fakra Z-Female × RG-174", "Тип/Серия", "Fakra-female", "FAKRA-female (тип Z)", "код ключа Z — в артикуле; запись как у 5 строк «FAKRA-female (тип Z)»"],
 ["17", "coax", "Fakra Z-Female × RG-316", "Тип/Серия", "Fakra-female", "FAKRA-female (тип Z)", "код ключа Z — в артикуле; запись как у 5 строк «FAKRA-female (тип Z)»"],
 ["17", "coax", "Fakra Type A Female × ", "Тип/Серия", "Fakra-female", "FAKRA-female (тип A)", "код ключа A — в артикуле («Type A»)"],
 ["15", "ter", "163303-6", "Тип контакта", "Male (Pin) — см. notes", "Male", "TE, даташит 163303-6: «Pin Contact, 17 – 13 AWG … Commercial MATE-N-LOK» (ответное гнездо — 163302-6); terGender уже Male"],
 ["15", "ter", "163555-6", "Тип контакта", "Male (Pin) — см. notes, отличается от типового Female у остальных строк листа", "Male", "TE, даташит 163555-6 (te.com.cn): «Pin Contact … 22 – 18 AWG … Commercial MATE-N-LOK»; RS: «Male Crimp Terminal, 22AWG Min, 18AWG Max»; terGender уже Male"],
 ["16", "ter", "1-962916-1", "Тип контакта", "", "Male (tab)", "TE, даташит 1-962916-1: «Tab Terminal, 17 – 13 AWG, 1 – 2.5 mm² Wire, 2.8 mm Tab Width … Timer Connector System», тип вывода — male; запись как у «Male (tab)»"],
 ["16", "ter", "SLBTAD-01T-M0.5", "Тип контакта", "", "Female", "Future Electronics: SLBTAD-01T-M0.5 в разделе «Pin and Socket Connectors → Socket Contacts»; у JST приставка S — гнездовой контакт (SPH, SXH, SLF…)"],
 ["16", "ter", "1241394-1", "Высота обжима изоляции, мм", "4,06 (О-crimp for single-wire-seal 963293-1, insulation-Ø 2,0-2,7 мм)", "4,06", "TE 114-18387 Rev G, Table 1 (SWS) — число из той же ячейки; уплотнитель и Ø изоляции перенесены в «Источник высоты обжима»"],
 ["16", "ter", "1564984-1", "Высота обжима изоляции, мм", "3,94 (О-crimp for single-wire-seal 963294-1, insulation-Ø 1,2-2,0 мм)", "3,94", "TE 114-18387 Rev G, Table 1 (SWS) — число из той же ячейки; уплотнитель и Ø изоляции перенесены в «Источник высоты обжима»"],
 ["14", "op", "BUY", "Время подготовки, сек", "", "0", "у операции нет времени вообще (Время операции 0,00); пустое время подготовки ЭТК и так считает нулём"],
 ["14", "op", "PREP1", "Время подготовки, сек", "", "0", "у операции нет времени вообще (Время операции 0,00); пустое время подготовки ЭТК и так считает нулём"],
 ["14", "op", "PREP2", "Время подготовки, сек", "", "0", "у операции нет времени вообще (Время операции 0,00); пустое время подготовки ЭТК и так считает нулём"],
 ["18", "mat", "ТТК 19/6,5мм, черная", "Коэф. усадки", "", "3:1", "по размерам из наименования: 19 / 6,5 = 2,9 → ряд 3:1 (в базе коэффициенты 2:1, 3:1, 4:1)"],
 ["18", "mat", "ТТК 24/8мм, черная", "Коэф. усадки", "", "3:1", "по размерам из наименования: 24 / 8 = 3 → 3:1"],
 ["18", "mat", "Припой ПОС61 1,0мм с флюсом", "Темп. от, °C", "183", "", "183–190 °C — интервал плавления ПОС61 (так и записано в «Источнике данных»), а не рабочий диапазон; перенесено в «Источник данных», у второго припоя (Sn60Pb39Cu1) поля тоже пусты"],
 ["18", "mat", "Припой ПОС61 1,0мм с флюсом", "Темп. до, °C", "190", "", "то же"],
 ["18", "mat", "Лента нетканая 20мм", "Ед. изм.", "", "шт", "все 11 остальных строк группы «лента» учитываются в шт (рулон); решено агентом — проверить по закупке"],
 ["20", "ob", "ОБ.010", "Потребление,вват/ч", "~400", "400", "«~» убран — число из той же ячейки (столбец пока никто не читает)"],
 ["20", "ob", "ОБ.012", "Потребление,вват/ч", "~1200", "1200", "«~» убран — число из той же ячейки"],
 ["20", "ob", "ОБ.017", "Потребление,вват/ч", "~500", "500", "«~» убран — число из той же ячейки"],
 ["20", "ob", "ОБ.018", "Потребление,вват/ч", "~1000", "1000", "«~» убран — число из той же ячейки"],
 ["20", "ob", "ОБ.020", "Потребление,вват/ч", "~2000", "2000", "«~» убран — число из той же ячейки"],
 ["20", "ob", "ОБ.021", "Потребление,вват/ч", "~3000", "3000", "«~» убран — число из той же ячейки"],
 ["20", "ob", "ОБ.023", "Потребление,вват/ч", "~1000", "1000", "«~» убран — число из той же ячейки"],
 ["20", "ob", "ОБ.035", "Потребление,вват/ч", "~750", "750", "«~» убран — число из той же ячейки"],
 ["20", "ob", "ОБ.027.1", "Потребление,вват/ч", "400, 750", "750", "из двух значений ячейки взято большее (предложение отчёта 14.10)"],
 ["20", "ob", "ОБ.027.2", "Потребление,вват/ч", "400, 750", "750", "из двух значений ячейки взято большее"],
 ["20", "ob", "ОБ.027.3", "Потребление,вват/ч", "400, 750", "750", "из двух значений ячейки взято большее"],
 ["20", "ob", "ОБ.027.4", "Потребление,вват/ч", "400, 750", "750", "из двух значений ячейки взято большее"]
]''')
# The source column a row gets its «what changed» note in (only if the row has that column).
NOTE_FIELD = {"coax": "Источник данных", "ter": "Источник высоты обжима", "mat": "Источник данных"}
NOTE_EXTRA = {
    "1241394-1": "высота обжима изоляции 4,06 — O-crimp уплотнителя 963293-1, Ø изоляции 2,0–2,7 мм (было в ячейке высоты)",
    "1564984-1": "высота обжима изоляции 3,94 — O-crimp уплотнителя 963294-1, Ø изоляции 1,2–2,0 мм (было в ячейке высоты)",
    "Припой ПОС61 1,0мм с флюсом": "температура плавления 183–190 °C (была в «Темп. от/до»)",
}
TABS = {"coax": TechOperationTab.COAX, "ter": TechOperationTab.TERMINAL, "op": TechOperationTab.OPERATION,
        "mat": TechOperationTab.MATERIAL, "ob": TechOperationTab.EQUIPMENT}
TABLE_RU = {"coax": "БД.КОАКС", "ter": "БД.ТЕР", "op": "БД.ОП", "mat": "БД.МАТ", "ob": "БД.ОБ"}

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
LOG = []


def log(table, key, field, old, new, status):
    LOG.append({"table": TABLE_RU[table], "key": key, "field": field, "old": old, "new": new, "status": status})
    print(f"   {status}: {TABLE_RU[table]} «{key}» {field}: «{old}» → «{new}»")


def text(fields, name):
    return str(fields.get(name) or "")


def matches(table, fields, key):
    if table == "coax":
        article, _, wire = key.partition(" × ")
        return (text(fields, "coaxArticle") or text(fields, "Артикул")).strip().casefold() == article.strip().casefold() \
            and cable_key(text(fields, "coaxWire") or text(fields, "Провод")) == cable_key(wire)
    if table == "ter":
        return (text(fields, "terArticle") or text(fields, "Артикул контакта (REEL)")).strip() == key
    if table == "op":
        return (text(fields, "opNumber") or text(fields, "Номер")).strip() == key
    if table == "mat":
        return text(fields, "Наименование").strip() == key
    return text(fields, "Инв. номер").strip() == key


rows: dict[tuple[str, str], list] = {}
for group, table, key, field, old, new, source in DATA:
    rows.setdefault((table, key), []).append((field, old, new, source))

with factory() as uow:
    listed = {table: uow.tech_operations.list(tab=tab, query="", limit=100_000) for table, tab in TABS.items()}

for (table, key), cells in rows.items():
    found = [record for record in listed[table] if matches(table, record.fields, key)]
    if len(found) != 1:
        for field, old, new, _ in cells:
            log(table, key, field, old, new, f"отказ (строк по ключу: {len(found)})")
        continue
    record = found[0]
    edits, changed = [], []
    for field, old, new, source in cells:
        current = text(record.fields, field)
        if current == new:
            log(table, key, field, current, new, "уже так")
        elif current != old:
            log(table, key, field, current, new, "отказ (значение уже другое)")
        else:
            edits.append(FieldEdit(field, old, new))
            changed.append((field, old, new, source))
    if not edits:
        continue
    note_field = NOTE_FIELD.get(table)
    if note_field and note_field in record.fields:
        parts = [f"«{field}» «{old}» → «{new}»: {source}" for field, old, new, source in changed]
        if key in NOTE_EXTRA:
            parts.append(NOTE_EXTRA[key])
        note_old = text(record.fields, note_field)
        edits.append(FieldEdit(note_field, note_old, (note_old + "; " if note_old else "") + f"{STAMP}: " + "; ".join(parts)))
    with factory() as uow:
        try:
            before, after = _edit_in(record.id, tuple(edits), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            for field, old, new, _ in changed:
                log(table, key, field, old, new, f"отказ ({error})")
            continue
        wrong = [field for field, _, new, _ in changed if text(after.fields, field) != new]
        if wrong:  # the row's own sync took the value back — never write such a row
            for field, old, new, _ in changed:
                log(table, key, field, old, new, f"отказ (после правки {', '.join(wrong)} не то)")
            continue
        twins = {name: (text(before.fields, name), text(after.fields, name)) for name in after.fields
                 if name not in {edit.field for edit in edits} and text(before.fields, name) != text(after.fields, name)}
        if twins:
            print("      следом:", json.dumps(twins, ensure_ascii=False))
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(before.id), "tab": before.tab.value, "fields": [edit.field for edit in edits], "reason": REASON,
        }, uow)
        for field, old, new, _ in changed:
            log(table, key, field, old, new, "проверено" if dry else "записано")
        if not dry:
            uow.commit()

totals: dict[str, int] = {}
for item in LOG:
    status = item["status"].split(" (")[0]
    totals[status] = totals.get(status, 0) + 1
print("ИТОГО", json.dumps(totals, ensure_ascii=False), "(проверка, ничего не записано)" if dry else "(записано)")
print("LOG " + json.dumps(LOG, ensure_ascii=False, default=str))

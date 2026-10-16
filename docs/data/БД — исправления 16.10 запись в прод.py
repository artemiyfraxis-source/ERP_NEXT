"""Запись в прод 16.10.2026 — исправление данных по ночной сверке (владелец 16.10: «реши проблемы», в том числе
перезапись заполненных ячеек, где источник говорит, что значение неверное).

Всё — путями самого приложения, со старым значением у каждой ячейки (ячейка, которую поменяли после снимка, приходит
отказом и не пишется) и событием журнала:
  1. СПР.КАБ D1–D3 — `set_coax_diameters_in` (как форма «Коаксиальные кабели»), старые D сверяются целиком.
  2. БД.ПРОВ — правка строки `_edit_in` (как «Изменить» в «Базе данных»), строка ищется по «Марке»; у монтажного
     провода со структурой структура идёт следом (МНВ-4 0,12: «Темп. до»).
  3. БД.КОАКС — N-112C/8D: кабель 10D-FB → 8D-FB (`_edit_in`); N-111C/8D «PicoCell 8D/FB» — пустые ячейки строки
     N-111C/8D × 8D-FB дописываются из двойника (`import_rows("coax")`, old = ""), двойник удаляется
     (`tech_operations.delete` + событие со всеми полями), только если его поля совпадают со снимком.
  4. Отгрузка ОТГ-20261015-02 — «Срок» 16.01.2026 → 16.10.2026 (`edit_shipment_head`, версия отгрузки сверяется).
  5. БД.ТЕР — у строк, где значение карточки контакта есть только в ключе (terInsOdFrom…), а столбец листа пуст,
     столбец получает то же значение (`_edit_in`). Иначе первая же правка строки стёрла бы ключ: синхронизация
     выводит ключи карточки из столбцов. Проверка: читатели (TerminalRecord) до и после дают одно и то же, и
     повторная синхронизация ничего не стирает — иначе строка не пишется.
  6. UL2725 28AWG — проверка, что строка уже в «Многожильных» (иначе `set_cable_kind`).

Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «уже так» / «отказ: уже поменяли»
Журнал ячеек (таблица, ключ, поле, было, стало, источник) — docs/data/«БД — исправления 16.10.csv»."""
import json
import os
import sys
from dataclasses import replace
from datetime import date
from uuid import UUID

from app.database import build_engine, build_references_uow_factory, build_shipping_uow_factory
from app.modules.references.application import (FieldEdit, SetCableKindCommand, SetCoaxDiametersCommand, _audit, _edit_in,
                                                 cable_overrides, set_cable_kind, set_coax_diameters_in)
from app.modules.references.cables import CableKind, cable_rows, diameter_index
from app.modules.references.domain import StaleRecordError, ReferenceValidationError, TechOperationTab, cable_key
from app.modules.references.importers import import_rows
from app.modules.references.technology import (TER_CARD_KEYS, TerminalRecord, _TECH_SCHEMA, _resolve_schema, contact_gender,
                                               schema_keys, sync_schema_fields, terminal_card_indexes)
from app.modules.shipping.application import EditHeadCommand, edit_shipment_head
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 16.10.2026", department="System")
REASON = "исправление по источнику, сверка 16.10.2026 (владелец: «реши проблемы»)"

DIA = json.loads(r'''[
 ["10D-FB", {"D1": "3,5", "D2": "10", "D3": "10,8"}, {"D1": "3,5", "D2": "9,7", "D3": "10,8"}],
 ["10D-FB PE", {"D1": "3,5", "D2": "10", "D3": "10,8"}, {"D1": "3,5", "D2": "9,7", "D3": "10,8"}],
 ["8D-FB Cu", {"D1": "2,56", "D2": "7,35", "D3": "8,25"}, {"D1": "2,6", "D2": "7,3", "D3": "8,1"}],
 ["RG-400", {"D1": "1", "D2": "2,95", "D3": "3,95"}, {"D1": "1", "D2": "2,95", "D3": "4,2"}],
 ["RG-142", {"D1": "0,94", "D2": "3", "D3": "3,95"}, {"D1": "0,94", "D2": "2,95", "D3": "4,25"}],
 ["RG-174", {"D1": "0,48", "D2": "1,52", "D3": "1,9"}, {"D1": "0,48", "D2": "1,52", "D3": "2,23"}],
 ["RG-178", {"D1": "0,25", "D2": "0,87", "D3": "1,3"}, {"D1": "0,31", "D2": "0,87", "D3": "1,3"}],
 ["RG-58", {"D1": "1", "D2": "2,95", "D3": "3,5"}, {"D1": "0,82", "D2": "2,95", "D3": "3,5"}],
 ["RG-59", {"D1": "0,81", "D2": "3,66", "D3": "4,28"}, {"D1": "0,58", "D2": "3,66", "D3": "4,28"}],
 ["RG-213 C/U", {"D1": "2,39", "D2": "7,25", "D3": "7,9"}, {"D1": "2,25", "D2": "7,25", "D3": "7,9"}]
]''')

KDVVG_NOTE = ("; 16.10.2026: «Напряжение» исправлено по карточке paritet.su: «Напряжение: до 300 В переменного тока "
              "частотой 10 кГц или 420 В постоянного тока» (было 200)")
PROV = json.loads(r'''[
 ["RG-316D", "Наружный диаметр, мм", "3,16", "2,9"],
 ["КДЭВГ 9х0,14", "Напряжение, В", "200", "300 переменный / 420 постоянный"],
 ["КСПВГ 4x0,50", "Напряжение, В", "200", "300 переменный / 420 постоянный"],
 ["КСПЭВ 2х0,50", "Сечение, мм2", "0,50", "0,2"],
 ["МНВ-4 0,12", "Темп. до, °C", "105", "70"],
 ["6XV1830-0EH10", "Материал оболочки", "силиконовая резина", "ПВХ пластикат"],
 ["6XV1830-0EH10", "Материал изоляции жилы", "силиконовая резина", "полиэтилен"]
]''')
SOURCE_APPEND = {
    "КДЭВГ 9х0,14": KDVVG_NOTE.replace("карточке paritet.su", "карточке paritet.su арт. 108490"),
    "КСПВГ 4x0,50": KDVVG_NOTE.replace("карточке paritet.su", "карточке paritet.su КДВВГ 4х0,50 арт. 100780 (ссылка строки, "
                                       "с неё взяты и остальные значения; КСПВГ 4х0,50 у Паритета нет)"),
    "КСПЭВ 2х0,50": ("; 16.10.2026: «Сечение» исправлено по той же карточке: «2 жил диаметром 0,5мм (сечением 0,2 мм.кв.)» — "
                     "в марке КСПВ/КСПЭВ диаметр жилы, а не сечение (было 0,50)"),
    "МНВ-4 0,12": ("; 16.10.2026: «Темп. до» исправлено по ТУ 16-505.928-76 (сборник ВНИИКП, "
                   "https://meganorm.ru/Data2/1/4293851/4293851304.htm): «от минус 50 до 70 °С и до 100 °С в течение 105 ч» "
                   "(было 105)"),
    "6XV1830-0EH10": ("; 16.10.2026: материалы исправлены по спецификации Siemens у дистрибьютора "
                      "(https://franklinempire.com/en/6xv1830-0eh10-6115-6xv18300eh10.html): Jacket «PVC», "
                      "Insulation «Polyethylene» (было «силиконовая резина» — так прочиталось «силикон-свободный»)"),
}
N112_NOTE = ("; 16.10.2026: кабель 10D-FB → 8D-FB по странице gsm-repiteri.ru 3903: «устанавливается на радиокабель "
             "8D-FB», «8D — совместимый кабель 8D-FB» (в таблице характеристик там же — 10D-FB, расходится с названием)")
PICO_ID = "75770bd0-987a-48fc-944c-420a6181a698"
PICO_COPY = ("Серия", "Полярность", "Исполнение", "Монтаж", "Центральный контакт", "Крепление экрана",
             "Волновое сопротивление, Ом", "Покрытие", "Наименование", "Ссылка", "Примечание")
SHIPMENT = ("ОТГ-20261015-02", date(2026, 1, 16), date(2026, 10, 16))

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
engine = build_engine(os.environ["ERP_DATABASE_URL"])
factory = build_references_uow_factory(engine)
shipping = build_shipping_uow_factory(engine)
now = moscow_now()
LOG = []


def log(table, key, field, old, new, status):
    LOG.append({"table": table, "key": key, "field": field, "old": old, "new": new, "status": status})
    print(f"   {status}: {table} «{key}» {field}: «{old}» → «{new}»")


def records(uow, tab):
    return uow.tech_operations.list(tab=tab, query="", limit=100_000)


def edit_row(record_id, edits, table, key):
    """`edit_record` without its own commit control: one row, one transaction; dry — not committed."""
    with factory() as uow:
        try:
            before, after = _edit_in(record_id, tuple(edits), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            for item in edits:
                log(table, key, item.field, item.old, item.value, f"отказ ({error})")
            return None, None
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(before.id), "tab": before.tab.value, "fields": [item.field for item in edits], "reason": REASON,
        }, uow)
        for item in edits:
            log(table, key, item.field, item.old, item.value, "проверено" if dry else "записано")
        if not dry:
            uow.commit()
        return before, after


print("== 1. СПР.КАБ D1–D3")
for cable, old, new in DIA:
    with factory() as uow:
        current = diameter_index(records(uow, TechOperationTab.KAB_COAX)).get(cable_key(cable))
        if current is None:
            log("СПР.КАБ", cable, "D1–D3", old, new, "отказ (строки нет)")
            continue
        have = {name: str(current.fields.get(name, "")).strip() for name in ("D1", "D2", "D3")}
        if have == new:
            log("СПР.КАБ", cable, "D1–D3", have, new, "уже так")
            continue
        try:
            set_coax_diameters_in(SetCoaxDiametersCommand(cable=cable, dia_id=current.id, old=old, value=new, actor=ACTOR,
                                                          now=now), uow, {"reason": REASON})
        except (StaleRecordError, ReferenceValidationError) as error:
            log("СПР.КАБ", cable, "D1–D3", have, new, f"отказ ({error})")
            continue
        for name in ("D1", "D2", "D3"):
            if old[name] != new[name]:
                log("СПР.КАБ", cable, name, old[name], new[name], "проверено" if dry else "записано")
        if not dry:
            uow.commit()

print("== 2. БД.ПРОВ")
with factory() as uow:
    wires = {cable_key(r.fields.get("Марка")): r for r in records(uow, TechOperationTab.WIRE)}
by_mark: dict[str, list] = {}
for mark, field, old, new in PROV:
    by_mark.setdefault(mark, []).append((field, old, new))
for mark, cells in by_mark.items():
    record = wires.get(cable_key(mark))
    if record is None:
        log("БД.ПРОВ", mark, "-", "", "", "отказ (марки нет)")
        continue
    edits = []
    for field, old, new in cells:
        if str(record.fields.get(field, "")).strip() == new:
            log("БД.ПРОВ", mark, field, new, new, "уже так")
        else:
            edits.append(FieldEdit(field, old, new))
    if edits and mark in SOURCE_APPEND:
        source = str(record.fields.get("Источник данных", ""))
        edits.append(FieldEdit("Источник данных", source, source + SOURCE_APPEND[mark]))
    if edits:
        edit_row(record.id, edits, "БД.ПРОВ", mark)
with factory() as uow:  # the structured монтажный wire follows its cells
    wire = uow.cable_structure.hookup(cable_key("МНВ-4 0,12"))
    print("   структура МНВ-4 0,12: temp_max_c =", wire.temp_max_c if wire else None)

print("== 3. БД.КОАКС")
with factory() as uow:
    coax = records(uow, TechOperationTab.COAX)
pair = [r for r in coax if r.fields.get("coaxArticle") == "N-112C/8D"]
if len(pair) == 1 and pair[0].fields.get("coaxWire") == "10D-FB":
    rec = pair[0]
    if any(r.fields.get("coaxArticle") == "N-112C/8D" and cable_key(r.fields.get("coaxWire")) == "8dfb" for r in coax):
        log("БД.КОАКС", "N-112C/8D × 10D-FB", "Провод", "10D-FB", "8D-FB", "отказ (пара на 8D-FB уже есть)")
    else:
        source = str(rec.fields.get("Источник данных", ""))
        edit_row(rec.id, [FieldEdit("Провод", "10D-FB", "8D-FB"), FieldEdit("coaxWire", "10D-FB", "8D-FB"),
                          FieldEdit("Источник данных", source, source + N112_NOTE)], "БД.КОАКС", "N-112C/8D")
else:
    log("БД.КОАКС", "N-112C/8D", "Провод", "10D-FB", "8D-FB",
        "уже так" if pair and all(cable_key(r.fields.get("coaxWire")) == "8dfb" for r in pair) else f"отказ (строк {len(pair)})")

pico = next((r for r in coax if str(r.id) == PICO_ID), None)
target = [r for r in coax if r.fields.get("coaxArticle") == "N-111C/8D" and cable_key(r.fields.get("coaxWire")) == "8dfb"]
if pico is None:
    log("БД.КОАКС", "N-111C/8D × PicoCell 8D/FB", "строка", "есть", "удалена", "уже так (строки нет)")
elif len(target) != 1:
    log("БД.КОАКС", "N-111C/8D × PicoCell 8D/FB", "строка", "есть", "удалена", f"отказ (строк N-111C/8D × 8D-FB: {len(target)})")
else:
    fill = {name: str(pico.fields.get(name) or "").strip() for name in PICO_COPY
            if str(pico.fields.get(name) or "").strip() and not str(target[0].fields.get(name) or "").strip()}
    note = "; двойник «PicoCell 8D/FB» (так кабель назван на gsm-repiteri.ru) слит в эту строку 16.10.2026"
    fill["Примечание"] = (fill.get("Примечание") or str(target[0].fields.get("Примечание") or "")) + note
    old = {name: str(target[0].fields.get(name) or "").strip() for name in fill}
    with factory() as uow:
        report = import_rows("coax", uow, [ImportRowIn(fields={"coaxArticle": "N-111C/8D", "coaxWire": "8D-FB", **fill}, old=old)],
                             ACTOR, now, dry_run=dry, source="erp").model_dump()
    status = "отказ" if report["refused"] or report["created"] else ("проверено" if dry else "записано")
    for name, value in fill.items():
        log("БД.КОАКС", "N-111C/8D × 8D-FB", name, old[name], value, status)
    if report["refused"] or report["created"]:
        print("   import:", report)
    else:
        with factory() as uow:
            live = uow.tech_operations.get(pico.id)
            if live is None or dict(live.fields) != dict(pico.fields):
                log("БД.КОАКС", "N-111C/8D × PicoCell 8D/FB", "строка", "есть", "удалена", "отказ (строка поменялась)")
            else:
                print("   удаляется:", json.dumps({"id": str(live.id), "sort_key": live.sort_key, "fields": live.fields},
                                                  ensure_ascii=False))
                if not dry:
                    uow.tech_operations.delete(live.id)
                    _audit("references.coax_connector_removed", ACTOR, now, {
                        "record_id": str(live.id), "tab": live.tab.value, "display_text": live.display_text,
                        "fields": dict(live.fields),
                        "reason": "двойник N-111C/8D × 8D-FB (кабель назван на сайте PicoCell 8D/FB), ссылок нет; " + REASON}, uow)
                    uow.commit()
                log("БД.КОАКС", "N-111C/8D × PicoCell 8D/FB", "строка", "есть", "удалена", "проверено" if dry else "записано")

print("== 4. Отгрузка")
number, old_day, new_day = SHIPMENT
with shipping() as uow:
    found = [s for s in uow.shipments.list(limit=100_000) if s.number == number]
if len(found) != 1:
    log("Отгрузки", number, "Срок", old_day, new_day, f"отказ (найдено {len(found)})")
elif found[0].deadline == new_day:
    log("Отгрузки", number, "Срок", new_day, new_day, "уже так")
elif found[0].deadline != old_day:
    log("Отгрузки", number, "Срок", found[0].deadline, new_day, "отказ (срок уже другой)")
else:
    shipment = found[0]
    with shipping() as uow:
        card = uow.addressees.get(shipment.lines[0].project) if shipment.lines else None
    print("   карточка проекта", shipment.lines[0].project, "plan_date =", card.plan_date if card else None)
    if dry:
        edited = shipment.edit_head({"deadline": new_day}, card)
        assert edited.deadline == new_day
    else:
        edit_shipment_head(EditHeadCommand(shipment_id=shipment.id, expected_version=shipment.version, actor=ACTOR, now=now,
                                           fields={"deadline": new_day}), shipping())
    log("Отгрузки", number, "Срок", old_day.isoformat(), new_day.isoformat(), "проверено" if dry else "записано")

print("== 5. БД.ТЕР — столбец карточки пуст, ключ заполнен")
keys = schema_keys("ter")
with factory() as uow:
    ter = records(uow, TechOperationTab.TERMINAL)
touched = 0
for record in ter:
    fields = record.fields
    columns = [name for name in fields if name not in keys]
    index = terminal_card_indexes(columns)
    edits = []
    for key in TER_CARD_KEYS:
        if key == "terGender" or index.get(key, -1) < 0:
            continue
        column = columns[index[key]]
        value = str(fields.get(key) or "").strip()
        if value and not str(fields.get(column) or "").strip():
            edits.append(FieldEdit(column, str(fields.get(column) or ""), value))
    gender = str(fields.get("terGender") or "").strip()
    type_index = _resolve_schema(_TECH_SCHEMA["ter"][0], columns).get("terType", -1)
    if gender and type_index >= 0 and contact_gender(fields.get(columns[type_index])) != gender \
            and not str(fields.get(columns[type_index]) or "").strip():
        edits.append(FieldEdit(columns[type_index], "", gender))  # as the import does: a gender alone becomes the type
    if not edits:
        continue
    after_fields = dict(fields, **{item.field: item.value for item in edits})
    synced = sync_schema_fields("ter", after_fields)
    if TerminalRecord.from_fields(fields) != TerminalRecord.from_fields(synced) or any(
            str(synced.get(key) or "") != str(fields.get(key) or "") for key in TER_CARD_KEYS if fields.get(key)):
        for item in edits:
            log("БД.ТЕР", fields.get("terArticle"), item.field, item.old, item.value, "отказ (читатели дали бы другое)")
        continue
    edit_row(record.id, edits, "БД.ТЕР", fields.get("terArticle") or record.display_text)
    touched += 1
print("   строк:", touched)

print("== 6. UL2725 28AWG")
with factory() as uow:
    wires_all = records(uow, TechOperationTab.WIRE)
    row = next(item for item in cable_rows(wires_all, records(uow, TechOperationTab.KAB_COAX),
                                           cable_overrides(uow.references.list()))
               if item.record is not None and cable_key(item.cable) == cable_key("UL2725 28AWG"))
print(f"   список: {row.kind.value} (авто {row.auto.value}, «{row.reason}», вручную: {row.manual})")
if row.kind != CableKind.MULTI:
    if not dry:
        set_cable_kind(SetCableKindCommand(record_id=row.record.id, kind=CableKind.MULTI, actor=ACTOR, now=now), factory())
    log("Список кабелей", "UL2725 28AWG", "список", row.kind.value, "multi", "проверено" if dry else "записано")

print("ИТОГО", json.dumps({status: sum(1 for item in LOG if item["status"] == status) for status in {i["status"] for i in LOG}},
                          ensure_ascii=False), "(проверка, ничего не записано)" if dry else "(записано)")
print("LOG " + json.dumps(LOG, ensure_ascii=False, default=str))

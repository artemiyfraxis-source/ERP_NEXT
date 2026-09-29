---
name: supplymap-schema
description: Справочник схемы трёх таблиц SUPPLYMAP (Запросы, Лист закупки, Лист ожидания) — колонки, выпадающие значения, ключ System ID. Use when adding fields, debugging column mapping, writing row transforms, or checking which column lives where. Triggers on "схема таблиц", "колонки", "supplymap schema", "какие поля", "System ID", "маппинг колонок", "структура листа".
---

# SUPPLYMAP — sheet schema

Three separate Google Sheets, joined by `System ID` (in all three, immutable join key).

**Source of truth — `Config.gs`**, not this file (full column lists here were duplicated and
became stale — no longer maintained here). See there:
- `SHEET_ORDER.{REQUESTS,PURCHASE,WAITLIST}` — target order and full set of columns for each sheet.
- `REQUEST_TO_PURCHASE_MAP` — mapping Запросы→Закупка.
- `REQUEST_FORM_FIELDS` — form fields, types, dropdowns, mandatory flag.
- `TRACE_COLUMNS` — traceability columns (GOST RV) by sheets.
- `JOURNAL_HEADERS` — Journal schema.

Read snapshot with `getDisplayValues`, not `getValues`. Columns match by **name**
(`normHeader_`: trim + collapse spaces/newlines + lowercase), not by position — can reorder,
case-insensitive. Headers with newlines come as `&#10;` → normalizer collapses them. Header standard
is protected by guard (`snapshotHeaders`/`headerGuard_`) — renaming reverts. Actual sizes (standard):
**ЗПР 24 · ЗКП 56 · Ожидание 34** columns.

## Запросы (`SS_REQUESTS`, "ЗПР") — pipeline entry
Filled ONLY via form/import (manual entry reverts). Auto-fields not in form:
`СТАТУС` (default "В работу" set by owner-tick), `Дата запроса`, `System ID`, `НАЗНАЧЕНИЕ`,
`ПРИОРИТЕТ`, `СУММА`, hidden `ЗАМОК`.
- **СТАТУС**: В работу (default) / В закупку (= transfer, set by button) / …
- **ПРИОРИТЕТ** (auto from DEADLINE, `PRIORITY_RULES`): Очень срочно ≤3 / Срочно ≤7 / Не срочно ≤14 /
  В свободное время >14. Recalculated at time of transfer.
- **ТИП ЗАДАЧИ**: Закупка / Аутсорс / Аутсорс (ПР). The «Проценка» option was removed 2026-08-27
  with the branch itself; shipment point is mandatory for every type.
- **НАЗНАЧЕНИЕ** = `base.NNN` (`nextDesignation_`). base — form choice (RND/FRX/…) or import
  project number. One request batch = ONE number; counter is global (max suffix for base +1).
- **ТИП** (combo, choice or manual): ЗИП / Оборудование / Расходники / Комплектующие / Инструмент /
  СИЗ / Услуга. Import writes type from bill of materials.
- **КАТЕГОРИЯ** (GOST RV): Общепром (default) / ИП / ОП. Ruled by PASSPORT: ОП→"Да" strictly,
  ИП→"Да" by default, Общепром→"Нет" (`applyPassportRule_`).
- **СУММА** = ЦЕНА С НДС × КОЛ-ВО (server, not entered in form).
- **КОНЧН ТОЧКА ОТГРУЗКИ** (mandatory) and **ПЛАНИРУЕМАЯ ДАТА ПОСТУПЛЕНИЯ**
  (mandatory) — transferred to Закупка.
- Sheet "ЗПР" uses Google "column type" → SpreadsheetApp does NOT see/set those dropdowns
  (getDataValidation returns null). They live in a Google **Table** («ЗПР», tableId 1165974176)
  and ARE editable via Sheets REST `updateTable` (send FULL columnProperties, only the target
  column changed, else the mask wipes other dropdowns; clear + restore basicFilter around it or
  the call 400s). One-offs: `diagRequestStatusTable` / `fixRequestStatusTableDropdown`
  (Triggers.gs). Dropdowns for select-fields synced by `syncDropdowns()`.

## Лист закупки (`SS_PURCHASE`) — buyer's desk
Split of **Calc/Fact**: on transfer, НАИМЕНОВАНИЕ/КОЛ-ВО/ЕД.ИЗМ/ЦЕНА/ЦЕНА С НДС/СУММА →
versions "(Расчет)"; buyer maintains "(факт)" manually (recalc `computeFact_`). НАИМЕНОВАНИЕ
is three-part: (Расчет) / (Сайт, auto from ССЫЛКА) / (Счет, OCR).
- **СТАТУС ЗАПРОСА** (own dictionary, forward-only movement `PUR_STATUS_ORDER`):
  Новый → В работе → Жду счет → В оплату → В пути → Карантин → Готово. "В работе" — button only
  "Взять в работу" (+employee ID); "В оплату" — auto only after invoice upload.
  Manual set/fill of these two reverts (`statusManualBlocked_`).
  **Off-line** (`PUR_STATUS_OFFLINE`, forward-only not enforced, to/from anytime):
  **Согласование** (approval before order — while in it, order not formed), **Отменено**
  (terminal). After "В оплату" stages В пути/Карантин/Готово — receiving (Лист ожидания).
- **ВАЛЮТА**: RUB / USD / CNY / … Switch to foreign currency → recalc fact-prices to RUB by CB +6.5%
  (`CURRENCY_MARKUP`), currency → RUB.
- **ПОСТАВЩИК** — from ССЫЛКА by `SUPPLIER_BY_LINK` (≠ ИЗГОТОВИТЕЛЬ, that's from site/manual).
- Traceability: from Запросы — АРТИКУЛ ПО КД, КАТЕГОРИЯ; at purchase stage — СТРАНА,
  ИЗГОТОВИТЕЛЬ, № СЕРТИФИКАТ/ДАТА, СЕРТИФИКАТ/ПАСПОРТ, ЗАМЕНА+РАЗРЕШЕНИЯ block.
- Owner-only (range-protection + code-revert): transfer columns + `PUR_LOCKED_EXTRA`
  (№ ЗАКАЗА/ЗАКАЗ/ДАТА ЗАКАЗА). ССЫЛКА/СТРАНА open. Employee ID — written by button.

## Лист ожидания (`SS_WAITLIST`) — receiving/warehouse
**TWO tabs**: ОЖ.ЦАР / ОЖ.ЖУК (`WAITLIST_TABS`), unified schema, routed by "ТОЧКА ОТГРУЗКИ".
All "read receiving" functions read both (`waitlistSheets_`/`waitlistRows_`). Both under guard.
Heavily automated (details — memory `[[supplymap-warehouse]]`/`[[supplymap-procurement-flow]]`):
- **Incoming control by button** (`runIncomingControl`): verdict is COMPUTED (`incomingControlVerdict_`)
  → РЕЗУЛЬТАТ/Status/incoming act (+nonconformity act on defect) + **auto-GRN** by designation.
- Receiving status-chip: `В пути / Принято / Возврат / Карантин` (`WAITLIST_STATUS_OPTIONS`), back-sync
  to Закупка by `baseSystemId_` (slice batch suffixes `-П`; backorder `-Д` — separate Закупка row).
- Waybill document columns — **УПД/М-15** (renamed from "УПД"; М-15 = consignment, same column).
  Typos from original: `Рассчет`, `НЕСООТВЕТСВИЯ` — match as-is.
- ФАКТ. ДАТА pulled to Закупка (`syncActualArrival_`), ОЖИД. ДАТА — from Закупка (`syncExpectedDateToWaitlist_`).
Full set of columns (batch/deadline/incoming/shortage/acts/GRN/storage location) — `SHEET_ORDER.WAITLIST` +
`TRACE_COLUMNS.WAITLIST`. Warehouse — separate file `SS_WAREHOUSE` (`Warehouse.gs`, aggregated by GRN).

## Проценка — removed
The terminal 5th sheet and everything around it (`SS_PROTSENKA`, `PRO_STATUS_*`, `Protsenka.gs`,
portal view `protsenka`) was deleted on 2026-08-27. The spreadsheet itself stays in Drive as an
archive; the code no longer knows about it.

## Запросы statuses (`СТАТУС`) and back-sync
Chip values: В работу (default) · В закупку (button) · Согласование · В пути · Закрыто · Отменено.
В работу/В закупку set by code; others "come alive" via back-sync from Закупка
(`PURCHASE_TO_REQUEST_STATUS`, owner-tick `syncRequestStatusFromPurchase_`): Отменено→Отменено,
Готово→Закрыто, В пути→В пути, Согласование→Согласование. Manual status edit reverts.

## Traceability Journal (separate sheet in Запросы table)
Schema `JOURNAL_HEADERS`: `ДАТА-ВРЕМЯ · ЭТАП · СОБЫТИЕ · System ID · НАЗНАЧЕНИЕ · НАИМЕНОВАНИЕ ·
ПРИОРИТЕТ · СТАТУС · ПОЛЬЗОВАТЕЛЬ`. Written only by code (`logEvents_`): "Создана (форма)",
"В закупку", "Взято в работу", "Статус → …". No manual entry. Setup —
`setupTraceability()` (append-only) / `setupJournal()` (rebuild). **Protected owner-only**
(`lockJournal`): non-owner entries go to buffer `Журнал.буфер`, owner-tick delivers.

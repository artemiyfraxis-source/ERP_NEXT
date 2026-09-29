---
name: supplymap-pipeline
description: Архитектура закупочного пайплайна SUPPLYMAP — движение заявки по трём таблицам, инварианты, слои кода, ограничение триггеров для standalone-скрипта над раздельными таблицами. Use when designing or changing move/sync logic, adding a pipeline stage, wiring triggers, or deciding where logic belongs. Triggers on "пайплайн", "перенос строки", "движение заявки", "триггеры", "onEdit", "supplymap pipeline", "архитектура", "upsert", "слои кода".
---

# SUPPLYMAP — pipeline and architecture

Full description → `README.md`. Here — invariants and solutions that are easy to break.

## Flow
`Запросы` —(button "Перенести в закупку")→ `Лист закупки` —(order placed)→ `Лист ожидания`.
Connection throughout via **`System ID`** (`SM-ГГГГММДД-NN`).

**No quote branch.** «Проценка» (a separate terminal 5th sheet, routed by `ТИП ЗАДАЧИ`) was
removed on 2026-08-27 together with its portal section: every request now goes to Закупка,
`transferPendingRequests_` has one destination and `requestRowsToPurchase_` no longer takes one.
The live «Лист проценки» file stays in Drive as an archive.

## Invariants (do not break)
1. **System ID is immutable.** Generated once in Запросы (`nextSystemId_`), passed down as-is.
   Never regenerate at lower stages.
2. **Transfer = upsert by System ID**, not append (`requestRowsToPurchaseLocked_`). Row with that ID
   exists in Закупка → update (РЕВИЗИЯ +letter, don't overwrite buyer's status/supplier);
   doesn't exist — add (status "Новый", Rev.A). Protects against duplicates on re-transfer.
3. **Origin remains.** Row in Запросы is not deleted (history + back-sync goal). Запросы `Статус`
   is NOT transferred to Закупка (it has its own dictionary `СТАТУС ЗАПРОСА`).
4. **Pure logic without Google.** Row transforms (`buildMappedRow_`, `rowFromFields_`) — in
   `Schema.gs`, no `SpreadsheetApp`, covered by tests in `_local/tooling/test`. IO — `Sheets.gs`.
5. **Batch.** Read/write in ranges (`getDisplayValues` / `setValues`), no
   `getValue`/`setValue` in loops (GAS quota).

## Layers
`Config` (ID, names, `REQUEST_TO_PURCHASE_MAP`, statuses, `STAFF_BY_EMAIL`, `SHEET_EDITORS`) →
`Schema`/`Currency`/`SystemId` (pure transforms) → `Sheets` (IO boundary: `readGrid_`,
`primarySheet_`) → `Pipeline` (`requestRowsToPurchase_` — upsert+lock) →
`Triggers`/`Forms`/`Code` (entry points: onEdit router, buttons, menu).

## Transfer Запрос→Закупка — TWO steps (NOT one onEdit)
Button **"Перенести в закупку"** (`transferToPurchase`, menu, FROM USER) sets status
"В закупку" for valid rows and logs WHO. The actual **data copy** to owner-only Закупка columns
is done by **owner-tick** `transferPendingRequests_` (inside `fillRequestStatus`, every minute) — lag
~1 min. Why: in consumer-Gmail, a non-owner in onEdit has an empty email, and Закупка write to
owner-only columns → owner is needed. onEdit on Запросы does NOT transfer — only reverts manual
edits (`requestEditGuard_`).

## Triggers — MAIN trap
Three **separate** spreadsheets + **standalone** script ⇒ simple `onEdit(e)` will NOT work (it only
works for container). Need **installable** triggers on each spreadsheet + one time-based trigger:
```js
ScriptApp.newTrigger('onEditRouter').forSpreadsheet(CONFIG.SS_REQUESTS).onEdit().create();
ScriptApp.newTrigger('fillRequestStatus').timeBased().everyMinutes(1).create();
```
Installed by `installTriggers_()` (via public `setup()`); cleaned by `removeTriggers_()`
(otherwise duplicates — remove all yours first). Public names WITHOUT `_` (trigger calls by name:
`onEditRouter`, `onOpenRouter`, `fillRequestStatus`).

## Transfer serialization
Bulk "В закупку" → Google splits autofill into parallel onEdit calls; without serialization
they write to `getLastRow()+1` one by one and overwrite each other. `requestRowsToPurchase_` holds
`LockService.getScriptLock()` + `flush()` before release — each next call sees a fresh
sheet (upsert), not writing on top.

## Integrity (header guard + Journal protection)
- **Header guard** (`headerGuard_` in `onEditRouter`): header is open to editors, but
  renaming a header breaks `headerIndex_` → reverts to standard. Standard is captured once
  by `snapshotHeaders()` (ScriptProperties, key `hdr_<ssId>`); structure drift (insertion/
  deletion of columns — onEdit does not catch) is checked by `verifyHeaders()` (clasp run). No standard → guard
  stays silent (fail-open).
- **Journal owner-only** (`lockJournal`): past traceability records are immutable for
  collaborators. `logEvents_` from owner-trigger writes directly; from menu/form (non-owner)
  direct write fails on protection → rows go to hidden buffer `Журнал.буфер`, owner-tick delivers
  (`flushJournalBuffer_`, in `fillRequestStatus`).

## Manual edit reversions — what is caught
- Single edit (1×1) → precise revert to `oldValue`.
- Range (insertion/fill) has no `oldValue` → `requestEditGuard_`/`gateNotInWork_`/
  `statusManualBlocked_` clean edited cells (cannot restore overwritten original — the price).
- **Stage regression in range is NOT caught** (`statusRegressed_` only 1×1 — no per-cell old).
- Row deletion in Запросы is blocked by hidden owner-only column `ЗАМОК`.

## Build strategy
Pure `Schema`/`Currency`/`SystemId` (tests in `run.mjs`) → `Pipeline` (upsert+lock) →
`onEditRouter`+`installTriggers_`. v1 = forward Запросы→Закупка; back-sync Закупка→Запросы —
phase 2 (origin is preserved for this).

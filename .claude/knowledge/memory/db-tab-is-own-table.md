---
name: db-tab-is-own-table
description: "Owner 28.10 — every «База данных» tab is its own table: own columns, own «+ Столбец» / «Удалить», nothing shared with another tab"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 128bab83-781a-4ce3-a66d-a0a9a0fa29cb
  modified: 2026-10-29T12:35:12.923Z
---

Owner 28.10.2026 («каждая вкладка в БД — это отдельная таблица БД… вбей себе это правило»), sharpened 29.10
(«наконечники и контакты хранятся в одной базе БД.ТЕР — я же писал, что так не должно быть»): a tab of «База данных» is
its own STORAGE table, not just its own column set. Never keep two tabs' records in one table split by a kind flag
(БД.ТЕР «Терминалы» / «Наконечники» by `ter_kind` was a half measure — lugs are being moved to their own table, 29.10).
Own columns, own «+ Столбец» / «Удалить», nothing shared with another tab. Shared field NAMES are fine only where one code
path reads both (contacts and lugs both carry L+ / L- / strip), the data stays per table.

**Why:** after the lugs split (26.10, migration 0155) «Наконечники» had a fixed column set and no own «+ Столбец» /
«Удалить»; own columns of БД.ТЕР belonged to «Терминалы» only — the owner expected a separate table.
**How to apply:** when splitting a list into a new tab, give it its own column machinery at once (own/custom columns,
removal, shared column view key per tab), as the cable lists have (`columns_by_kind`, `custom_columns_by_kind`).
Related: [[reference-lists-one-look]], [[sparse-columns-are-junk]], [[prov-kind-split-plan]].

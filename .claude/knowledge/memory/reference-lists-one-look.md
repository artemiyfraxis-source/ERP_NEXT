---
name: reference-lists-one-look
description: "Owner 25.10 — every «База данных» tab uses ONE list layout (toolbar, chips, blocks per group with heading+count and own table, «Сортировка: размер, затем наименование», same footer) — never a per-tab look"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 51316a75-b68c-47c7-b496-e2ce4ac5a6ae
  modified: 2026-10-25T19:33:44.850Z
---

All tabs of «База данных» except «Техкарты» (Операции, Оборудование keep their own look — owner 25.10 «техкарты и
оборудование оставь»): Разъёмы, Коаксиальные разъёмы, Терминалы, the 4 cable lists, Оболочки, Расходники, Соответствия
share one layout, modelled on «Оболочки»: one toolbar (search, «Сортировка: …» hint,
+ Столбец / + Запись / Колонки / Excel), one quick-filter chip row, then a block per group (heading + count chip + its own
table), rows inside a block ordered «размер, затем наименование», the same footer. A list without groups is one block.

**Why:** 25.10 the owner liked the coverings blocks (ТУТ / ТТК…) and asked to bring every list to it: «это везде надо…
приводи БД так, чтобы визуал их был один, а то так не дело — где-то так, где-то так».
**How to apply:** any new reference list or tab is built on the shared layout, not a new table style; a new grouping goes
in as blocks, not as group header rows. Related: [[sparse-columns-are-junk]], [[erp-screens-rules]].

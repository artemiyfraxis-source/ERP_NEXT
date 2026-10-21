---
name: prov-kind-split-plan
description: "БД.ПРОВ split DONE 21.10 (#620, migrations 0131/0132) — each cable kind its own columns via cable_kind; монтажные = one row per colour (key Марка+Цвет)"
metadata:
  node_type: memory
  type: project
  originSessionId: 3a6f7ee7-153e-4145-ab46-a3d32531b583
  modified: 2026-10-21T15:28:44.529Z
---

Owner 20.10.2026: «у каждого своя структура… развести по разным»; «каждый цвет своя строка». Released 21.10.2026 in
PR #620 (merge e5d7e55), migrations `0131_prov_cable_kind`, `0132_prov_wire_color_rows`; rehearsed on a prod copy
(713 old rows/IDs kept, 15517 filled values lost 0, +1081 colour rows, ETK dry-run 27 cards / 0 revisions before
and after). Prod after: coax 193, wire 1235, multi 366; «Прочие кабели» gone (2 rows deleted 20.10, restorable from
the `references.record_deleted` audit).

How it works now (option C, decided by agent — check): tab `prov` stays; `tech_operation_records.cable_kind`
(coax|wire|multi, CHECK); `CABLE_COLUMNS[kind]`; custom columns `prov:<kind>:<name>`; xlsx round-trip and
`import_rows` per kind with a «Вид кабеля» field; wire rows keyed «Марка» + «Цвет» — mark values are shared by all
colours of the mark, only colour and the seller offer (Поставщик, артикул, ссылка, цены, Закупок, Источник) are per row.
ETK picks the colour row from the item name, else the first row of the mark. БД.КОАКС «Провод» links cables by
mark name — never rename marks. Open: coax list shows key columns by default (owner asked «куда делись столбцы»
20.10 — they are in «Колонки»; restore by default only if he asks again).

Даша (YdO) got the zip «Выгрузка ERP 21.10.2026» (MD description + per-tab xlsx of prod) in the owner's Downloads.
Related: [[module-reference-db]], [[hookup-wire-suppliers]], [[prod-reference-write]], [[live-data-protocol]].

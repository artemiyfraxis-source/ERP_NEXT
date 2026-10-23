---
name: sparse-columns-are-junk
description: "Owner 23.10 — a reference-DB column filled in only a few % of a list's rows is junk: remove it from the list (or move it to a separate DB); columns are grouped in a fixed logical order"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 51316a75-b68c-47c7-b496-e2ce4ac5a6ae
  modified: 2026-10-23T17:33:43.669Z
---

A БД column filled in ≤ ~5 % of the list's rows is junk for that list: remove it from the list's columns (values may stay
in the records' JSON) or move it to a separate database — never keep it as a visible/hidden column. Exception: the supply
columns (Поставщик, Артикул поставщика, Ссылка, Цена, Дата цены) stay in every list ([[one-article-one-row]]).
Constant columns (every row the same, e.g. монтажные «Экран» = «нет», «Число жил» = 1) and legacy duplicates of a
structured field are removed too, after copying whatever the structured field lacks.

Column order («своя инфа в своём месте»): идентификация → конструкция → электрика → механика и условия → поставка →
служебное; the owner's custom columns go into their group, not appended at the end.

**Why:** 23.10 the owner reviewed the «Колонки» pickers of БД.ПРОВ (coax 42 columns, 8 shown; multi 32; монтажные 31) and
БД.КОАКС: «если что-то заполняется на 3 % — это мусор для БД: либо выносим в другую БД, либо удаляем столбец»; «сделай
сортировку нормальную, чтобы своя инфа в своём месте».
**How to apply:** after any reference import/fill, check fill % per column per list (prod, `limit=5000`) and clean up;
new columns go into their group. Related: [[module-reference-db]], [[prov-kind-split-plan]].

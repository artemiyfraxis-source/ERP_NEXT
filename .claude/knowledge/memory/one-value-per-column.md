---
name: one-value-per-column
description: "Owner 17.11 — tables: one value per cell, own column per field, row per position with its action; no expand rows"
metadata:
  type: feedback
---

Owner 17.11.2026 on «Приёмка с производства» / shipment lists: «почему тут все в куче, почему мы не можем сделать
отдельные столбцы, и так везде»; «было лучше когда была таблица и кнопка у каждой позиции сразу, а не раскрывая что-то».
- Every field in its own column, one value per cell: no stacked lines (способ + ТК + трек, получатель + телефон + адрес,
  № + «Отгрузка k из n» + buttons, date + grey «создана»), no grey second lines with a second value.
- One row per position (КБ line), its action button in that row, visible at once. No «one row per shipment, expand
  to see positions» (b33541b2 was rolled back for this).
- Group headers are one short line; never a second column system (a header grid whose columns don't line up with
  the table's).
- Empty tables still lay columns over the full width.
- No duplicates: a value shared by many rows is shown once, at its level — project header → shipment row (its values
  in the table's columns) → КБ rows with only per-КБ values. No «Создана»/«Срок»/«Этап позиции»/«Данные» repeats
  (owner 17.11: «тут дубли и лишняя инфа везде»).
- Edit windows: just editable fields + «Сохранить»; no per-field «как у проекта / не менять» mode selects
  (owner 17.11: «просто меняешь и всё»).
- Shipping words: `receiver` = «Организация», `contact` = «Получатель» (the person); shown together in that order
  with Телефон, Адрес (owner 17.11: «организация и получатель — разные вещи»).

**Why:** the storekeeper reads and filters like in Google Sheets; stacked cells and expanders hide data and break sort/filter.
**How to apply:** any ERP table, not only shipping — check new and touched screens against this before shipping.
Related: [[shipping-warehouse-sees-all]], [[erp-screens-rules]], [[edit-in-windows]].

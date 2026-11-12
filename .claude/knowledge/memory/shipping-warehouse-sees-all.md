---
name: shipping-warehouse-sees-all
description: "Owner 12.11 — warehouse sees and edits shipping data inline; group by shipment; shipments auto-close, no «Закрыть» button"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 7282dcdb-81b3-42e8-90ff-8679bf5f3d51
  modified: 2026-11-12T08:36:07.311Z
---

Owner 12.11.2026 on «Приёмка с производства» / «Данные отправок»:
- The storekeeper must see ALL shipping data at once (получатель, телефон, адрес, ТК, трек, УПД) and be able to enter it —
  never hide it in «Колонки» or a read-only window. This reverses the 06.11 audit «склад — только просмотр».
- Shipped/packed lists are grouped by shipment inside a project: «Отгрузка k из n», number, date, quantity in normal text,
  not a tiny grey line in every row.
- No manual «Закрыть»: a shipped shipment closes by itself once № УПД, файл УПД, трек (if needed) and ship date exist.
  Address/receiver/phone don't block closing («по адресам пофиг»).

**Why:** «зачем ты где-то что-то прячешь», «зачем кнопка». Ульяна fills УПД after the fact; manual close left shipments hanging.
**How to apply:** new shipping UI defaults to inline, editable-by-warehouse data; any new completion rule closes automatically.
Related: [[module-shipping]], [[erp-screens-rules]].

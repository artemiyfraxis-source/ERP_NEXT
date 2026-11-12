---
name: supplymap-parity
description: "Deliberate legacy differences, box-number ranges and out-of-scope blocks"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 588273da-1ef1-4aac-9b53-d023958d79e8
  modified: 2026-11-12T12:00:00.000Z
---

SUPPLYMAP is legacy; current behavior comes from ERP code/data and owner decisions.

- Deliberate differences: FIFO by ПТ number; УПД upload limit 20 MB; invoice text-layer parsing; supplier return is distinct from return to stock; defects keep nature + description.
- Procurement: «В закупку» assigns «В работе» to the actor, grouped by supplier with one request per group; invoice and «Оплачено» once per PO, «Цена факт» per line.
- Out of scope: legacy commercial «Проекты» and «Расчёт и КП» belong to another block/programmer.
- Box numbers: ERP 000001–099999, legacy SUPPLYMAP 100001+ (Луна in both).
- Owner 19.10: no SUPPLYMAP techcard imports or new legacy-specific techcard branches.
Hub: [[erp-next-hub]].

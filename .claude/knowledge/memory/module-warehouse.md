---
name: module-warehouse
description: "Warehouse series table, FIFO and current scan-based intake"
metadata:
  type: project
---

- «Склад 0.5»: table grouped by connector series.
- FIFO by ПТ number → [[supplymap-parity]]. Never write to live legacy warehouse sheets.
- Current intake: «Склад» → «Внести позиции» from a УПД scan; «Приёмка» redesign is on hold → [[receiving-now-vs-later]].
- Consumed by [[module-fulfillment]], fed by [[module-procurement]]. Decisions: `docs/migration/RUNLOG.md`. Hub: [[erp-next-hub]].

---
name: module-warehouse
description: Warehouse — «Склад 0.5» table grouped by series, live ЦАР/ЖУК never touched from Google, load-then-top-up import
metadata:
  type: project
---

- Look: «Склад 0.5» table grouped by connector series, like Google (PR #82, #86).
- Never write to the live SUPPLYMAP warehouse (ЦАР / ЖУК / СПИСАНИЕ); imports read only.
- Import: load now, re-run tops up the difference from Google (new lots, balances/places for lots without ERP movements; conflicts to a report, `--dry-run`). Owner 2026-09-29 20:19.
- FIFO by ПТ number (deliberate difference) → [[supplymap-parity]].
- Consumed by [[module-fulfillment]], fed by [[module-procurement]] (receiving). Decisions: [[runlog-decisions]]. Hub: [[erp-next-hub]].

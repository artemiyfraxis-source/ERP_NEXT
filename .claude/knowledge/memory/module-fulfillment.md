---
name: module-fulfillment
description: "Kit requests, lot selection and whole-kit issue with stock write-off"
metadata:
  type: project
---

- Issue always creates a request and waits for delivery; a person picks the lot; the kit leaves whole.
- «Заявка по расчёту» (IssueCalc) links to [[module-projects]] and [[module-procurement]].
- Writes off stock in [[module-warehouse]]. Decisions: `docs/migration/RUNLOG.md`. Hub: [[erp-next-hub]].

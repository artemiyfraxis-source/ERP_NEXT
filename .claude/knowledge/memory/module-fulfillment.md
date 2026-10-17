---
name: module-fulfillment
description: Kit requests and issue with write-off — reverted to SUPPLYMAP behaviour (FF-1…FF-5), IssueCalc
metadata:
  type: project
---

- Issue always creates a request and waits for delivery; a person picks the lot; the kit leaves whole.
- Parity work FF-1…FF-5 and a revert of agent decisions back to SUPPLYMAP behaviour (RUNLOG 2026-09-29).
- «Заявка по расчёту» (IssueCalc) links to [[module-projects]] and [[module-procurement]].
- Writes off stock in [[module-warehouse]]. Decisions: [[runlog-decisions]]. Hub: [[erp-next-hub]].

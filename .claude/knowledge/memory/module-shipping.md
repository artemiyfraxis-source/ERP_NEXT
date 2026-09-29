---
name: module-shipping
description: Shipping — manager creates shipment requests, warehouse executes; parity with SUPPLYMAP restored
metadata:
  type: project
---

- Shipment requests come from the manager, the warehouse executes; partial-shipment rules → [[supplymap-domain-decisions]] (SUPPLYMAP note shipment-flow).
- «Отгрузка» was brought back to SUPPLYMAP behaviour (RUNLOG 2026-09-29) → [[supplymap-parity]].
- Takes finished goods from [[module-production]]. Go-live block by 05.10 → [[launch-plan]]. Decisions: [[runlog-decisions]]. Hub: [[erp-next-hub]].

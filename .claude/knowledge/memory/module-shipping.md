---
name: module-shipping
description: Shipping — manager creates shipment requests, warehouse executes; parity with SUPPLYMAP restored
metadata:
  type: project
---

- Shipment requests come from the manager, the warehouse executes; partial-shipment rules → [[supplymap-domain-decisions]] (SUPPLYMAP note shipment-flow).
- «Отгрузка» was brought back to SUPPLYMAP behaviour (RUNLOG 2026-09-29) → [[supplymap-parity]].
- Finished goods: `shipped` = picked (leaves ГП stock at pick/pack), `sent` = left the plant (shipments Отгружена/Закрыта, migration 0071), `before_erp` = shipped in Google before ERP (part of accepted/shipped/sent). KB stage «Отгружено» follows `sent` (agent decision 30.09).
- Google shipments: `scripts/projects_import.py drop-shipped` reads shop sheet «ЗАГРУЗ» — fully ОТГРУЖЕН projects are removed (kitset cascade), partial ones get `before_erp` from the comment («Отгрузили 154 шт») when the project has one KB; projects with ERP movement are never removed. Runs from `update-local.ps1` (re-reads the sheet with the key path in `data/import/google-key.path`), `apply`, `deploy/load-real-data.sh shipped`.
- Project status after shipment: owned by thread «Карточка проекта как в Google»; shipping will call their status function, no own status model here.
- Takes finished goods from [[module-production]]. Go-live block by 05.10 → [[launch-plan]]. Decisions: [[runlog-decisions]]. Hub: [[erp-next-hub]].

---
name: module-shipping
description: Shipping — manager creates shipment requests, warehouse executes; parity with SUPPLYMAP restored
metadata:
  type: project
---

- Shipment requests come from the manager, the warehouse executes; partial-shipment rules.
- «Отгрузка» was brought back to SUPPLYMAP behaviour (RUNLOG 2026-09-29) → [[supplymap-parity]].
- Finished goods: `shipped` = picked (leaves ГП stock at pick/pack), `sent` = left the plant (shipments Отгружена/Закрыта, migration 0071), `before_erp` = shipped in Google before ERP (part of accepted/shipped/sent). KB stage «Отгружено» follows `sent` (agent decision 30.09).
- Google shipments: `sync_shipped` in `scripts/projects_import.py` reads shop sheet «ЗАГРУЗ» — fully ОТГРУЖЕН projects are removed (kitset cascade), partial ones get `before_erp` from the comment («Отгрузили 154 шт») when the project has one KB; projects with ERP movement are never removed. Since 30.09 not automatic (it would delete an owner-created project marked shipped): only `projects_import.py load` for the project being loaded (`only=`); the standalone `drop-shipped` command and the server `shipped` step are gone.
- Address inheritance: shipment stores effective values + `inherited` (fields not typed in it); `set_project_addressee` rewrites them in open shipments of the project, closed/cancelled stay frozen (agent decision; SUPPLYMAP recomputes even closed). Head edit `PUT /shipments/{id}/head`, empty = back to the card.
- Project status after shipment: owned by thread «Карточка проекта как в Google»; shipping will call their status function, no own status model here.
- Takes finished goods from [[module-production]]. Decisions: [[runlog-decisions]]. Hub: [[erp-next-hub]].

---
name: module-procurement
description: Requests, purchase sheet and receiving — second go-live wave 05–09.10; «Заявки» is a list only
metadata:
  type: project
---

- Second wave 05–09.10 → [[launch-plan]].
- «Заявки» — list only (PR #64); a request is created from a project calc (PR #79) → [[module-projects]].
- Purchase sheet aligned with SUPPLYMAP (PR-11, PR-12, mass actions). Receiving: manufacturer, country, documents.
- Invoice recognition only for PDFs with a text layer; UPD file ≤ 20 MB → [[supplymap-parity]].
- Receiving closes the purchase line: timer `sync_receipt_stages` (port of `syncPurchaseStatusFromWaitlist_`) moves В пути → Карантин → Готово, forward only (2026-09-30). UPD/quality scans stored in DB (`receiving_files`, 0073), not Drive.
- Second developer (Vitya) does part of procurement on his own branch; none found in ERP_NEXT on 2026-09-30. Keep procurement changes additive, don't restyle the purchase-sheet screen.
- Receiving puts stock into [[module-warehouse]]. SUPPLYMAP pipeline skills: `supplymap-pipeline`, `supplymap-schema`. Decisions: [[runlog-decisions]]. Hub: [[erp-next-hub]].

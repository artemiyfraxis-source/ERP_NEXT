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
- Receiving puts stock into [[module-warehouse]]. SUPPLYMAP pipeline skills: `supplymap-pipeline`, `supplymap-schema`. Decisions: [[runlog-decisions]]. Hub: [[erp-next-hub]].

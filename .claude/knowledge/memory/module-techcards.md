---
name: module-techcards
description: Tech cards (ЭТК) and templates — generator port, coax dimensions from the cable, sync scripts
metadata:
  type: project
---

- Has its own sync scripts: `npm run sync:techcards`, `npm run sync:techcard-library`.
- Coax: D1–D3 come from the cable (СПР.КАБ), the connector carries only lengths L1–L3, L+, L− (PR #65, generator PR #67) → [[module-reference-db]].
- Templates «Шаблоны техкарт» moved under «База данных» → [[module-reference-db]].
- SUPPLYMAP rules for twist, shop rules, crimp heights → [[supplymap-domain-decisions]].
- Bulk generation for all projects and SUPPLYMAP issued ТК import → [[techcards-bulk]].
- Feeds [[module-production]] (operations per project) and [[module-projects]]. Decisions: [[runlog-decisions]]. Hub: [[erp-next-hub]].

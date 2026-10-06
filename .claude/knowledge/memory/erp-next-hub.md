---
name: erp-next-hub
description: Map of ERP_NEXT project knowledge — start here; links every module, owner rule, launch and infra note
metadata:
  type: reference
---

ERP_NEXT = FastAPI + React successor of SUPPLYMAP (Google Apps Script) for a cable-assembly plant.
Owner: Артемий. Project knowledge lives in this folder (mounted into the Obsidian vault as
`projects/ERP_NEXT`, see brain note memory-topology), so every note here is one node of the graph.

- How the owner wants agents to work → [[owner-working-rules]]; screens → [[erp-screens-rules]] (skill `erp-screens`)
- Production server → [[erp-server]]
- Staff, roles, sites (ЦАР / ЖУК), PIN login → [[staff-and-sites]]
- Parity with SUPPLYMAP and deliberate differences → [[supplymap-parity]]
- Modules: [[module-projects]] · [[module-techcards]] · [[module-reference-db]] · [[module-warehouse]] · [[module-fulfillment]] · [[module-production]] · [[module-shipping]] · [[module-procurement]]
- Google (read-only, being left) → [[google-wiring]]
- Deferred by the owner → [[deferred-by-owner]]
- Agent decisions to review → [[runlog-decisions]] (source: `docs/migration/RUNLOG.md`)
- Tooling: [[cloud-threads-setup]] · [[erp-merge-gotchas]] · [[gate-red-without-failures]] · [[windows-python-crlf]] · [[erp-local-demo]]

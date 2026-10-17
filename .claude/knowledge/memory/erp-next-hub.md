---
name: erp-next-hub
description: Map of ERP_NEXT project knowledge — start here; links every note in this folder and the brain-side entry note
metadata:
  type: reference
---

ERP_NEXT = FastAPI + React successor of SUPPLYMAP (Google Apps Script, legacy since 05.10.2026) for a cable-assembly
plant. Owner: Артемий. Project knowledge lives in this folder, mounted into the owner's Obsidian vault as
`projects/erp_next`; the brain-side entry note is [[erp-next-project]] (it links back here; layers explained in brain
note [[memory-topology]]). Every note here is one node of the graph; `MEMORY.md` is the one-line index.

- Owner: how he wants agents to work → [[owner-working-rules]] · mid-turn request queue → [[owner-request-queue]] ·
  goals of 05.10 → [[owner-goals-1005]] · usage-limit resume → [[resume-at-limit-reset]]
- Data safety: [[live-data-protocol]] · writing reference values to prod → [[prod-reference-write]] ·
  maker's document beats shop values → [[maker-docs-over-shop-values]]
- Screens: [[erp-screens-rules]] (skill `erp-screens`) · cards/forms open as windows → [[edit-in-windows]]
- Production server, deploy, backups → [[erp-server]]; repo public since 10.10 → [[repo-public-ci]]
- Staff, roles, sites (ЦАР / ЖУК), PIN login → [[staff-and-sites]]
- SUPPLYMAP legacy and deliberate differences → [[supplymap-parity]]; Google (read-only) → [[google-wiring]]
- Modules: [[module-projects]] · [[module-techcards]] · [[module-reference-db]] · [[module-warehouse]] ·
  [[module-fulfillment]] · [[module-production]] · [[module-shipping]] · [[module-procurement]]
- Techcards: per-project generation → [[techcards-bulk]] · ETK per semi-finished item → [[etk-by-semifinished]] ·
  Василий's code may be ported → [[vasily-code-permission]] · 713К.4 agreed deviations → [[713k4-accepted-deviations]]
- Reference data: terminals → [[crimp-contacts-protocol]] · [[applicators-by-series]] · coax →
  [[coax-data-sources]] · connector names → [[connector-gender-names]] · connector pictures →
  [[connector-3d-only-on-card]] · maker documents from Russia → [[maker-docs-from-russia]]
- Deferred by the owner → [[deferred-by-owner]]; agent decisions to review → [[runlog-decisions]]
  (source: `docs/migration/RUNLOG.md`)
- Tooling: [[parallel-sessions-worktrees]] · [[worktree-symlink-removal]] · [[erp-merge-gotchas]] ·
  [[gate-red-without-failures]] · [[windows-python-crlf]] · [[cloud-threads-setup]] · [[erp-local-demo]]

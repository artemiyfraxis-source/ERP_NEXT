---
name: techcards-bulk
description: "On-request project techcards: generator and refresh modes, no legacy issued-route import"
metadata:
  type: project
---

- Owner 25.10: ТК are needed for every non-shipped КБ, including «Упаковано», ХОЛД and СТОП; skip only «Отгружено».
- Projects are created by the owner in ERP or loaded one at a time on request; no automatic project loading or batch ТК run. Existing stock, reserves and КД must survive.
- On request: `scripts/load-project.ps1 <№>` / `deploy/load-real-data.sh projects <№>` → `scripts/projects_import.py load`, scoped to that project. Existing ERP projects are left alone.
- `python -m app.workflows.techcards_all generate|refresh [--dry-run] [--project №]`; only `generate` and `refresh` are accepted. `generate` builds missing ETKs; `refresh` rebuilds eligible issued generator ETKs from saved configuration.
- The generator uses the same form logic: `frontend/src/tcGenBatch.ts` + `tcGenLogic.ts`, `npm run build:tcgen` → `frontend/dist-tools/tcGenBatchCli.js`. It builds and issues an ETK (draft remains if issue validation fails); the server image supplies Node.js.
- Calc КБn cut lengths need the erp-reader key (`backend/google-key.path` for local tooling).
Related: [[module-techcards]], [[module-projects]], [[google-wiring]]. Hub: [[erp-next-hub]].

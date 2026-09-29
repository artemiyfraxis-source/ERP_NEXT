---
name: techcards-bulk
description: Bulk techcards for all projects — SUPPLYMAP issued routes import + generator run headless in Node
metadata:
  type: project
---

- Owner 29.09.2026: generate ТК for every loaded project to review and edit; skip shipped and ХОЛД. `python -m app.workflows.techcards_all supplymap|generate [--dry-run]`, wired into `scripts/update-local.ps1`, `load-real-data.ps1`, `deploy/load-real-data.sh techcards`.
- SUPPLYMAP issued ТК live in «ERP — Производство» sheet «Техкарты» (route rows, file link in КОММЕНТАРИЙ); `supplymap` turns each into an ISSUED ETK (no operation cards, stages also in `build_config.ops` for `route_from_document`). ERP-issued ETKs are never overwritten.
- `generate` does not port the generator: `frontend/src/tcGenBatch.ts` runs the form logic (`tcGenLogic.ts`) in Node (`npm run build:tcgen` → `frontend/dist-tools/tcGenBatchCli.js`); a vitest pins config parity with the «Создать» click; like the button, the ETK is built and issued at once (draft only when a cut length is missing). Server image installs Debian `nodejs` for it.
- Cut lengths from the calc КБn sheet need the erp-reader key; update-local remembers its path in `backend\google-key.path`.
Related: [[module-techcards]], [[module-projects]], [[google-wiring]]. Hub: [[erp-next-hub]].

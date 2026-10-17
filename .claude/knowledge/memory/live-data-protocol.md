---
name: live-data-protocol
description: "No Alembic migration reaches production without backup → rehearsal on a copy → integrity/row-count check (owner, 05.10.2026)"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 6b98e0d2-15aa-4b5d-9f38-40d68dd980c1
  modified: 2026-10-05T15:32:19.749Z
---

Owner forwarded a reviewer's rule (05.10.2026, the main rule the ERP lacks): **live data protocol** — бэкап → прогон на копии → integrity check / сверка количеств строк → только потом прод. `main` auto-deploys at 02:00/06:00 MSK, so a merged migration reaches the live DB with no human — hence the pipeline below.

**Why:** real stock, orders and reserves live in production; a bad migration is unrecoverable without a tested restore. Migration 0088 (two empty tables) went to prod on 05.10 without a rehearsal — harmless, but the rule was broken.
**How to apply:**
- The pipeline exists since 05.10.2026 (`6c29ac6c`): migrations are applied only by `deploy/update.sh`, never on container start — pre-update dump → `deploy/rehearse.sh` restores it into a throwaway `erp-rehearsal` compose project, snapshots row counts/invariants (`app.db_integrity`), upgrades, compares, probes the API → only then production. A red rehearsal stops the update. A `DESTRUCTIVE = True` migration also needs the owner's revision id in «Обновить сервер».
- Every PR body states «Миграция: да/нет». If yes, link the rehearsal log: copy restored, upgrade run, row counts before and after, invariants green.
- A migration that drops or rewrites data needs an explicit owner OK.
- Release path and backups: [[erp-server]]; reference-data writes outside migrations: [[prod-reference-write]].

**Agreed with Витя (05.10.2026), owner-side rules (apply in every session):**
- `npm run check` proves logic only — always say so in reports. Screens are checked by screenshots, data by the rehearsal/reconciliation.
- Keys/tokens/passwords never go into chat output or the repo (not in commits, PR bodies, logs, memory).
- Scripts never delete or rebalance warehouse movements/stock — corrections go through the app's own documented operations.
- Shared RULES.md (via PR to the owner): a migration PR is rehearsed on a production copy first and its description carries the row-count reconciliation; for new code prefer **constraints over rollbacks** (CHECK/UNIQUE/FK/NOT NULL/version checks that make bad states impossible).

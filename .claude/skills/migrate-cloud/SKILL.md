---
name: migrate-cloud
description: One CLOUD run of the SUPPLYMAP → ERP_NEXT migration — take one open C2/C3 row of docs/audit/codex-port.md (not the warehouse zone), fix it on a claimed branch cloud/<ID>-<stamp>, push that branch only, stop. The local /migrate-next run merges these branches. Use when a cloud Routine invokes it or on "облачный прогон переноса".
---

# migrate-cloud — one cloud run, one task

You run in Anthropic's cloud with two checkouts: ERP_NEXT (the working repo) and SUPPLYMAP (read-only
source of domain rules). Nobody is watching: never ask. Decide, record, and push only your own branch.

## 0. Setup
1. Find the SUPPLYMAP checkout (`find / -maxdepth 4 -type d -name SUPPLYMAP 2>/dev/null`, pick the one with
   `.git`). Then `export SUPPLYMAP_SOURCE_REPOSITORY=<path>` for every command below. ERP_RECOVERED is
   absent here: skip anything that needs it.
2. `git config user.name "Артемий Васильев"` and `git config user.email artemiyfraxis@gmail.com` in ERP_NEXT.
3. `node scripts/setup-env.mjs`.

## 1. Pick one task
- The queue is `docs/audit/codex-port.md`: rows with severity C2 or C3 that are not marked fixed.
  Skip the warehouse module (the local conveyor owns it). Skip any row whose ID appears in
  `docs/migration/BACKLOG.md` as a task (the local queue owns those). Order: C2 before C3, then the audit's order.
- `git fetch origin '+refs/heads/cloud/*:refs/remotes/origin/cloud/*'`. For each candidate ID, skip it if
  a branch `origin/cloud/<ID>-*` exists and **has commits beyond `origin/main`** (done, waiting for merge),
  or has none but its stamp is younger than 3 h (another run holds it). An empty lease older than 3 h
  is abandoned: take the task.
- Claim it: `git push origin origin/main:refs/heads/cloud/<ID>-<YYYYMMDDHHMM UTC>`.

## 2. Do it
- Read the audit row. Get the SUPPLYMAP rule via the `supplymap-scout` agent, or read the cited
  `file:line` directly.
- Fix the rule in ERP_NEXT and add tests for it and its edge cases. Run `npm run check`: red only from
  the known drift tests is fine, anything else must be green.
- Mark the row fixed in the audit, as earlier fixes do. Record your own decisions in ARCHITECTURE.md
  with «решено агентом — проверить».
- Commit in Russian (`<Модуль>: <правило> (<ID>)`), no trailers, only your own files.
- `git push origin HEAD:refs/heads/cloud/<ID>-<stamp>` (the claimed branch). Then stop: one task per run.
- If you cannot finish, push nothing more. The empty lease expires by itself after 3 h.

## Never
- Push to `main` or to any branch you did not claim. Open PRs, tag, or deploy.
- Write to the SUPPLYMAP checkout, or run clasp, npm or any script inside it.
- Touch the warehouse module, secrets, or anything live.

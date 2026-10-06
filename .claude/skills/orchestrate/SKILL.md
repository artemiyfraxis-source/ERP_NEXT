---
name: orchestrate
description: The main session as orchestrator in ERP_NEXT — plan with erp-planner, hand each task to an erp-worker in its own worktree, merge the branches one by one, gate with erp-verifier, ship one PR. Use for any request that touches 3+ files, several modules or a migration in a local VS Code / Desktop session, or when the owner says «оркестратор», «раздай задачи», «распланируй и сделай».
---

# orchestrate — plan, dispatch, integrate

The main session keeps the owner's conversation and the decisions. It does not write big pieces of code
itself: its context is for planning, briefing, merging and checking. Small fixes (one file, a few lines)
it still does directly.

## 1. Plan
- Start from a clean tree on a fresh branch off up-to-date `main`: `git fetch`, `git switch -c <topic> origin/main`.
  Workers branch from this `HEAD` (`worktree.baseRef: "head"` in `.claude/settings.json`).
- Call `erp-planner` with the owner's request in their words. Read its plan critically: merge tasks
  that share a file, keep at most one migration per batch.
- Tell the owner the plan in 3–6 lines (Russian), then go on without waiting. Ask only when a choice
  changes what the owner gets and neither the ERP's code nor the rules settle it.

## 2. Dispatch
- One `erp-worker` per task of the batch, all in one message so they run in parallel. Pass the
  planner's model (`opus` for stock, money, generator, status machines, migrations, real-data import).
- The brief is self-contained: goal, zone, files to read, done-when, decisions already taken. The
  worker cannot see this conversation.
- Reading-heavy side questions go to `Explore`, not into this context.

## 3. Integrate, one branch at a time
1. `git merge --no-ff <worker branch>`. A conflict in a file both sides changed: resolve it here; if
   resolving means rewriting the logic, send it back to a worker with the conflict as the brief.
2. A migration: its `down_revision` must be the current head. Then
   `alembic -c backend/alembic.ini heads` (from `backend/.venv`) prints exactly one head
   (`test_alembic_single_head` enforces it in the gate).
3. Remove the worktree and branch: `git worktree remove <path>`, `git branch -d <branch>`.

## 4. Gate and ship
- `erp-verifier` on the integrated branch. Red beyond the known drift tests: fix (small) or send the
  failing piece back to its worker, then verify again. Never report done on a red gate.
- Decisions the workers and the planner took go into `docs/migration/RUNLOG.md` («решено агентом —
  проверить»), one entry for the batch.
- Push the branch, open one PR (`gh pr create`), and merge it once the GitHub `check` is green
  (owner's standing permission, 2026-09-29). Without `gh`, push and give the owner the PR link.
- Next batch of the plan: back to step 2 from the merged `main`.

## Report to the owner
Russian, short: what is done (PR link), what was decided for them to check, what is left.

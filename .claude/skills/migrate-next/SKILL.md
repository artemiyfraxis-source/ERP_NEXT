---
name: migrate-next
description: One unattended run of the SUPPLYMAP → ERP_NEXT migration conveyor — take the next 1–3 non-overlapping tasks from docs/migration/BACKLOG.md, do each in its own worktree agent, verify, merge to main, record, stop. Use when invoked as /migrate-next (a scheduled Routine calls it) or when the owner says "продолжай перенос", "следующий шаг переноса", "migrate next".
---

# migrate-next — one conveyor run

Unattended: the owner is not watching. Never ask questions mid-run. Decide, record the decision, move on.
One batch per run, then stop: a fresh session per run keeps quality high.

## 0. Lock and preflight
1. If `.claude/migrate.lock` exists and is younger than 6 h, append a RUNLOG entry saying the previous
   run is still active, then stop. Otherwise write the lock (content: run id `YYYYMMDD-HHMM`). Remove it
   at the end, whether the run succeeded or failed.
2. `git status --porcelain` must be empty on `main`. If it is not, someone's work is in the tree: log
   it and stop without touching it. `git fetch` + `git merge --ff-only origin/main` when possible.
3. `node scripts/setup-env.mjs`, then `node .claude/hooks/supplymap-drift.mjs` (the drift report).

## 1. Keep the queue current (cheap, every run)
- Stale `[~]`: a task marked in progress by an earlier run or a manual session. If `git log main`
  holds its commits, mark it `[x] <commit>`. If a worktree branch for it still exists under
  `.claude/worktrees/`, leave it alone: someone is working. Otherwise, after 12 h, reset it to `[ ]`.
- Drift: for SUPPLYMAP code commits after `supplymapSeen`, use `supplymap-scout` to classify each one.
  If it changes a rule of an already-ported module, add a `DRIFT-<sha>` task. Then move `supplymapSeen`.
- If the queue has open `IMPORT-AUDIT` / `DISCOVER` tasks and nothing above them is open, those
  become the batch.

## 1b. Merge finished cloud branches (before picking the batch)
The cloud Routine (skill `migrate-cloud`) fixes audit C2/C3 rows on branches `cloud/<ID>-<stamp>`.
- `git fetch origin '+refs/heads/cloud/*:refs/remotes/origin/cloud/*'`
- For each `origin/cloud/*` that has commits not yet in `main`, do this in a temporary worktree: rebase
  onto `main`, run `npm run check` via `erp-verifier` (red only from known drift = ok), then
  fast-forward `main`. Record `[x] <ID> · <commit> · ☁` under «Сделано» in BACKLOG. On conflict or a
  real red: skip it and add `[!] <ID> · ☁ <reason>` to BACKLOG. Never push, and never delete remote
  branches; the owner cleans them up when pushing.
- Merged cloud tasks count toward this run's work but not toward the batch limit of 3.

## 2. Pick the batch
Discovery tasks (`*-DISCOVER`, `IMPORT-AUDIT`) that sit at the top run first, in the main session
(scout + writing tasks into BACKLOG). The tasks they create fill this same batch.
Then top-down, `[ ]` only, skipping `[?]` and `[!]`. Take at most 3 tasks, with different zones, and at
most one that adds an Alembic migration. Skip any task whose zone overlaps a `[~]` task: someone is in
there. A zone of «все модули» overlaps everything. C1 or drift of a C1 area comes first. Mark them
`[~] <run id>` and commit BACKLOG.md alone.

## 3. Execute — one Agent per task, `isolation: "worktree"`
Model: opus for C1, migrations, money, stock, status machines; sonnet for C2/C3 and routine porting.
The brief to each agent must say:
- first `git rebase main` (a worktree may start from the unpushed-behind origin/main), then
  `node scripts/setup-env.mjs`;
- read the task's audit row + the SUPPLYMAP rules via `supplymap-scout`;
- implement, add tests for the rule and edge cases, run `npm run check`
  (red only from the known drift tests = acceptable, say which);
- commit on its branch in Russian, owner identity, no trailers, only its own files;
- return: branch, commits, check result, decisions taken on its own, anything it could not do.

## 4. Merge, one task at a time
Rebase the branch onto `main`. If two migrations now share a number or `down_revision`, fix the later
one. Fast-forward `main`, then run `npm run check` via `erp-verifier`. If the gate goes red beyond the
known drift: reset `main` to before the merge, mark the task `[!] <reason>` and continue with the next.
Remove the worktree and its branch after a successful merge.

## 5. Record, then stop
- BACKLOG: `[x] <commit>`. Audit row: mark it fixed with the commit. Each own decision goes into
  ARCHITECTURE.md with «решено агентом — проверить».
- A task that truly needs the owner (money policy, legal, credentials, anything live) → `[?] <question>`.
- RUNLOG.md: a new entry on top (template inside the file).
- Commit the docs (BACKLOG, RUNLOG, audit, ARCHITECTURE) in one commit. Remove the lock.

## Never
- Push, tag, deploy, or touch any remote.
- Write to `../SUPPLYMAP` or `../ERP_RECOVERED`, or run clasp/scripts there (read-only via git and the scout).
- Touch live Google data, production databases, or secrets.
- Take a second batch in the same run, even if time remains.
- Retry a `[!]` task: it waits for the owner.

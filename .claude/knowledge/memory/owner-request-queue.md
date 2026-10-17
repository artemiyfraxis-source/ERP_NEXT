---
name: owner-request-queue
description: Owner piles several requests mid-turn — keep a numbered visible queue and dispatch each through the orchestrator at once
metadata:
  node_type: memory
  type: feedback
  originSessionId: 6b98e0d2-15aa-4b5d-9f38-40d68dd980c1
  modified: 2026-10-05T12:15:53.110Z
---

The owner often sends 3–5 requests (screenshots + one line each) while a turn is running (2026-10-05: «я так часто буду накидывать, а ты теряешься»; «ты не пользуешься оркестратором»).

**Why:** the owner lost track of what was taken, what runs and what is done; the main session was doing one item by hand instead of orchestrating.
**How to apply:**
- On each new mid-turn request, reply at once with a numbered queue (№ · screen · what · state: план / в работе / проверка / PR), and keep showing the updated queue in every status message.
- Each new item goes straight to `erp-planner` (background) or, if the plan is obvious, an `erp-worker` — in parallel, never wait for the current item to finish. The main session only plans, merges, verifies, reports ([[owner-working-rules]], skill `orchestrate`).
- One branch/PR per screen-topic; items touching the same module go to one planner so workers don't collide.
- Worker worktrees symlink `frontend/node_modules` and `backend/.venv` to the main checkout: `rmdir` the links before `git worktree remove`, never `--force` over them.

**Merge only on green (2026-10-05 incident):** `gh pr checks N | head -1 && gh pr merge N` merged #211 with a RED check — `head` exits 0 regardless. Always gate the merge on the check state: `S=$(gh pr checks N --json state --jq '.[0].state'); [ "$S" = SUCCESS ] && gh pr merge N --merge --delete-branch`. Frontend tests must not depend on the runner's time zone (CI is UTC) — vite.config.ts sets `process.env.TZ = "Europe/Moscow"` (PR #212).

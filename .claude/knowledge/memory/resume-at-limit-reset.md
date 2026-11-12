---
name: resume-at-limit-reset
description: "Persist interrupted Codex worker state and schedule a real resume after the reported reset"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 8677a0f6-6abc-4b3c-ac40-0726d25e18d0
  modified: 2026-11-12T12:00:00.000Z
---

- On a Codex worker limit, record its reset time, process/resume ID, worktree/branch/base SHA, dirty files and next action in root gitignored `TASKS.md`.
- Main schedules a one-shot wake-up just after the reported reset using an available scheduler; record its real identifier. If unavailable, report that automatic resume is not scheduled.
- At reset, inspect worker process state, current branch, dirty files and `git reflog`; resume the existing Codex worker or restart it with the handoff in its assigned worktree. Preserve unfinished edits; main owns commits.
- Never remove another session's worktree. Keep worker ports distinct and follow [[parallel-sessions-worktrees]].
- Claude subagent resume mechanics are fallback only when Codex is unavailable; use the runtime's actual resume tool and verify it restarted.
Why: waiting for the owner after the reset wastes production time. Related: [[owner-working-rules]].

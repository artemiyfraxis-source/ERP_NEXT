---
name: agent-unique-scratch-files
description: Parallel agents must use unique scratch file names (PR bodies etc.) — a shared pr-body.md cross-posted PR descriptions
metadata:
  type: feedback
---

19.11.2026 two parallel workers both wrote `scratchpad\pr-body.md`; `gh pr edit --body-file` then published another PR's
description on #960 and #967 (restored by hand). 

**Why:** the session scratchpad is shared by every subagent of the session.
**How to apply:** in every worker brief require per-PR file names (`scratchpad\<branch>\pr<N>-body.md`) and a check of the
PR body after `gh pr edit`; never a generic `pr-body.md` / `pr.md` ([[parallel-sessions-worktrees]]).

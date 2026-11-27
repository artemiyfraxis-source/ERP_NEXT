---
name: night-coordinator
description: "Owner 20.11 — when several sessions run overnight, one session coordinates: keeps every session loaded and audits that each finishes all its items"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 3f2c5096-0909-4ab1-99bc-847937a5ed30
  modified: 2026-11-19T21:29:04.669Z
---

Owner 20.11.2026: «ты ответственный, чтобы все сессии работали; если закончили — грузануть чем-то быстрым» and «чтобы закончили
все свои задачи, а не забыли что-то, как сегодня». 

**Why:** on 19.11 sessions lost items between handoffs; the owner wants to wake up to everything closed and a clear summary.
**How to apply:** the coordinating session (the one the owner tells this) keeps a per-session list in TASKS.md, audits it
against live state (ListAgents, open PRs, CI, update-server runs, dirty worktrees) on a schedule (CronCreate audits + one-shot
wake-ups after the usage-limit reset), sends idle sessions a concrete next step or a quick useful item, serialises releases
(one update-server at a time) and ends with a morning summary: done / needs the owner physically / blockers.
Related: [[owner-tasks-into-queue]], [[resume-at-limit-reset]], [[owner-working-rules]].

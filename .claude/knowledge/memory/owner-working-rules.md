---
name: owner-working-rules
description: "Standing owner authority, Codex routing, batch releases and access requests"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 6b98e0d2-15aa-4b5d-9f38-40d68dd980c1
  modified: 2026-11-20T10:23:50.956Z
---

- Global style and attribution: see global `CLAUDE.md` / `.claude/team/RULES.md`.
- Find answers in git, the DB mirror, code and project knowledge. Decide disputed points yourself; mark «решено агентом — проверить» in `docs/migration/RUNLOG.md`.
- Routing: Claude directs, Codex does the volume → [[claude-directs-codex]].
- Standing authority: merge your own PR once CI `check` is green, run `gh workflow run update-server.yml`, then check the changed screen on prod. Migrations follow the current Alembic head; keep one head.
- Merge each green PR as parallel work finishes; run one server update after the last PR in the batch. An urgent production fix may ship earlier. First check `gh run list --workflow update-server.yml`; do not release during another active run.
- Owner 25.10; 12.11 owner reconfirmed: no announce rule for the owner's releases. Shared release contract: `.claude/team/RULES.md`.
- Merge chains: never combine head-branch deletion with a guarded merge; verify merge success before deletion.
- Full authority to deploy and run on the owner's PC without re-asking. Platform-only actions (device cards, GitHub secrets) remain his; collect access requests into one RUNLOG list → [[erp-server]].
- Priorities: preserve real production data, quality, low token cost. Decide process/rules/deploy questions, then report results briefly; do not send diffs for approval.
- Housekeeping is mine to decide (owner 20.11, irritated by «оставить или удалить?»): stale TASKS lines, merged/superseded branches and worktrees, server rehearsal leftovers — check it's safe (remote copy, content on main, no live session), do it, report. Ask only about domain/product choices.
- Overnight drafts «for the owner» (UI-audit PRs etc.) are not a hold (owner 20.11: «почему я должен что-то отвечать, почему тормозимся»): with CI green, merge and release them under the standing authority. Ask only about domain/product choices.
- Parallelism (owner 23.11.2026: «ПК не узкое место, я гонял по 12»): up to ~12 parallel workers/subagents is fine; what overloads the PC is several FULL test suites at once — workers run affected tests only (pytest -n 4), the full gate runs one at a time (CI is the default gate).
- Use the fewest agents needed; small tasks need no fan-out. Run long waits in the background. New routines only on request; model/effort follow global rules.

Hub: [[erp-next-hub]].

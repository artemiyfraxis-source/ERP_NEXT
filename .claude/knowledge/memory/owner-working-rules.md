---
name: owner-working-rules
description: "Standing owner authority, Codex routing, batch releases and access requests"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 6b98e0d2-15aa-4b5d-9f38-40d68dd980c1
  modified: 2026-11-12T12:00:00.000Z
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
- Use the fewest agents needed; small tasks need no fan-out. Run long waits in the background. New routines only on request; model/effort follow global rules.

Hub: [[erp-next-hub]].

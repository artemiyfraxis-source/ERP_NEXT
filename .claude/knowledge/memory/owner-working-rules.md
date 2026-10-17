---
name: owner-working-rules
description: "Standing owner rules for every ERP_NEXT agent — Russian replies, no questions, merge/deploy permissions, batch access asks"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 6b98e0d2-15aa-4b5d-9f38-40d68dd980c1
  modified: 2026-10-06T09:26:58.503Z
---

- Every message to the owner is in Russian, short (owner was annoyed by English, 2026-09-29 16:24). Notes to other threads also in Russian.
- Nothing is needed from the owner (2026-09-29): ask nothing; find it in git, the DB mirror, code, claude-brain. Decide disputed points yourself and mark them «решено агентом — проверить» in `docs/migration/RUNLOG.md` → collected in [[runlog-decisions]].
- Standing permission (2026-09-29 11:09): every project session may merge its own PR into `main` once CI `check` is green; migration goes after the current alembic head; one head after merge.
- Full permission for the future (2026-09-29 15:20): merge, deploy, run on the owner's PC without re-asking. Platform buttons (device card, GitHub secrets) are still his — batch them into one action.
- Never ask for access one item at a time («я устал»): collect what only he can do in RUNLOG and hand over one list at server setup → [[erp-server]].
- Usage limits (2026-10-06, the 5-hour window ran out): token cost matters now — fewest agents that do the job, no parallel fan-out for small tasks, long waits in the background, fresh session per slice.
- New routines only on request. Effort/model levels follow brain `_global/CLAUDE.md`.
- Commits/PRs carry no Claude attribution (ERP_NEXT `CLAUDE.md`).
- 2026-10-06: «реши сам, это тяжко мне» — process/rules/deploy decisions are the agent's; priorities in order: real production data is never lost, quality, low token cost. Don't send him diffs to approve — decide, then report 2–5 lines.
- Batched releases (2026-10-06, «жди окончания параллельных и выпускай вместе»): while parallel work in the session is still running, merge each green PR into `main` as it lands, but run `update-server.yml` ONCE after the last of them is merged — one server update per batch, not one per PR. Exception: an urgent production fix that can't wait for the batch.
- Back to VS Code (2026-10-02): the owner wants a planning orchestrator that hands out tasks. Local main session = orchestrator (`CLAUDE.md` «Orchestrator mode», skill `orchestrate`, agents `erp-planner` / `erp-worker` / `erp-verifier`). Owner's Claude account is used only from his PC through a proxy — cloud sessions don't replace the local one.

Why: the owner runs production and has no time for agent questions. How to apply: act, log the decision, report the result in 2–5 Russian lines. Hub: [[erp-next-hub]].

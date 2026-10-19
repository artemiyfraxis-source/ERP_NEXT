---
name: etk-auto-revisions
description: "Since 19.10 issued ETK cards get a new revision automatically after reference/template/builder changes — no «Пересобрать»; how it works on prod, the flag, dry-run commands"
metadata:
  node_type: memory
  type: project
  originSessionId: 588273da-1ef1-4aac-9b53-d023958d79e8
  modified: 2026-10-19T11:18:14.526Z
---

Owner 19.10: «при 100 ТК каждый раз пересобирать — не путь»; «если изменение коснулось — сразу ставить новую ревизию».
Built in #564 (migration 0127: `techcard_projects.inputs/overrides/review_note/derived_hash`,
`techcard_revisions.source/diff`, `techcard_refresh_requests`), #567 (`workflows/etk_refresh.py`, backfill, journal UI),
#571 («нормы устарели» compares effective norms, not revision numbers).

- A reference/template write enqueues a refresh request (a `before_flush` listener); the outbox worker re-derives
  issued cards from `inputs` + overrides (`etk_derive`) and issues revision N+1 (`source=auto`,
  `diff={changes, reason}`) only where the document changed. Drafts refresh in place. Launched tasks stay on their
  pinned revision. Issued cards no longer change silently on screen (`live_document(stored=True)`).
- Enabled on prod 19.10 ~14:20: `ETK_AUTO_REVISION=1` in `/opt/erp/config/.env` (worker restarted). The first run
  issued 26 revisions (old norms with setup, linear cut time, reference/text changes), 0 conflicts; a rerun = 0.
- Manual: from `/opt/erp/app/deploy`, `sudo -n bash erp.sh cli python -m app.workflows.etk_refresh --all [--dry-run]`;
  backfill of inputs `… app.workflows.etk_inputs_backfill [--apply]`. `update.sh` runs the refresh after each deploy.
- 20 cards had no inputs = empty drafts never built; they get inputs when built in the generator.
- Generator (TS) rule changes are NOT re-derived (the form isn't re-run): those still need «Пересобрать».

**Why:** manual rebuilds don't scale and silently stale cards misled fitters.
**How to apply:** don't tell the owner to «Пересобрать» after reference/builder fixes — check the journal instead; a
builder change must keep `etk_derive` deterministic (golden tests). Related: [[module-techcards]], [[live-data-protocol]].

---
name: gate-red-without-failures
description: "Why `npm run check` could be red with every test passing, and which drift tests can never go red"
metadata:
  node_type: memory
  type: project
  originSessionId: b64a8917-d86d-4437-9a3c-8986b777200e
  modified: 2026-09-28T12:40:13.943Z
---

- Fixed 2026-09-28: `npm run test:backend` used to exit 1 with all tests green. The cause was not the non-ASCII
  user name. pytest's numbered temp dirs publish `pytest-current`, which Windows creates as a *file* symlink
  to a directory; `stat()` on it raises `PermissionError [WinError 5]` in session-finish cleanup.
  `backend/tests/conftest.py` now sets a per-process `--basetemp` (`%TEMP%/erp-pytest-<pid>`), so the link is
  never created. It also calls `gc.collect()` before deleting that dir, otherwise open SQLite files keep it
  locked. If exit 1 comes back with green tests, check that conftest first.
- `npm run check` is one `&&` chain — it stops at the first red stage, so backend/frontend never run after an
  architecture drift failure; run the stages separately to get real counts.
- Architecture drift tests only go red for modules whose `SOURCE.json` `sourceRef` is a moving branch (`main`):
  production and the techcards operation library/grapher. Side-branch, sha-pinned, stale `origin/main` and
  `merge-base --is-ancestor` checks never fail — the SessionStart hook is the only drift signal for the other 9.

**Why:** a green architecture suite is not evidence of no drift.
**How to apply:** judge drift from the hook output, not from `npm test`. Full audit: `docs/audit/codex-port.md`
(commit b04dfd2).

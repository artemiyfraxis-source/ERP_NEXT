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
  architecture failure; run the stages separately to get real counts.
- SUPPLYMAP drift is no longer a thing: since 05.10.2026 the architecture tests only check module boundaries and that
  each `SOURCE.json` keeps its frozen `sourceCommit` (nothing reads `../SUPPLYMAP`); the drift hook is not wired.
  History of the old drift checks: `docs/audit/codex-port.md`.

**Why:** exit 1 with green tests wastes a debugging round. **How to apply:** check conftest first, then run the
stages one by one. Hub: [[erp-next-hub]].

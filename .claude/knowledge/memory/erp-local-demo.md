---
name: erp-local-demo
description: Running ERP locally to show or screenshot it — preview vs seeded demo, ports, Remote Control Auto-mode limits; the old ERP_NEXT-demo folder is gone
metadata:
  type: project
---

- Production is the shop server since 05.10.2026 ([[erp-server]]). The old `Projects\ERP_NEXT-demo` folder (real.db on
  8022, branch claude/project-thread-gd6e8g, SHA in `.git/refs/archive`) is gone since 06.10 — don't look for it.
- Local runs: `.claude/launch.json` preview (`erp-api` 8021 over `backend/preview.db`, `erp-web` 5176, login
  preview / Preview-2026!) or the seeded demo `scripts/demo/run-demo.ps1` / `run-demo.sh` over `backend/demo.db`
  (demo staff Demo-2026!). Both default to 8021/5176: a second instance (demo next to the preview, parallel workers)
  gets its own `-ApiPort/-WebPort` (→ `ERP_API_PORT` / `ERP_WEB_PORT`).
- `seed_demo.py` fills every section via the HTTP API (needs `x-csrf-token` from cookie `erp_csrf` and
  `Idempotency-Key`; outbox via `python -m app.outbox_worker --once`).
- Remote Control sessions start in Auto mode even with `defaultMode: bypassPermissions`; DB swaps and key work are
  run by the owner in PowerShell.
- `scripts/auto-update-local.ps1` / `update-local.ps1` (local auto-update via Task Scheduler) still exist; their
  pitfalls (never read a child's output through a pipe, never clear std-handle inheritance) are in the script headers.

Related: [[erp-screens-rules]], [[staff-and-sites]]. Hub: [[erp-next-hub]].

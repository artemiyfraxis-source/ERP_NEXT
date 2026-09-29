---
name: erp-local-demo
description: Showing ERP to the owner locally over Remote Control — demo folder, ports, update script, Auto-mode limits
metadata:
  type: project
---

- Demo: `Projects\ERP_NEXT-demo` (branch claude/project-thread-gd6e8g = main + scripts/demo), API 8022, site http://localhost:5177; the owner's preview on 8021/5176 is not touched. Login preview / Preview-2026!, demo staff Demo-2026!.
- Update: merge origin/main into that branch, push, `start_rc_session` on the default device, run `scripts\demo\update-demo.ps1` (demo.db kept). Start Vite with `ERP_API_PORT=8022 ERP_WEB_PORT=5177`. No `git stash` (shared with the owner's working copy).
- `scripts/demo/run-demo.ps1` + `seed_demo.py` fill every section via the HTTP API (needs `x-csrf-token` from cookie `erp_csrf` and `Idempotency-Key`; outbox via `python -m app.outbox_worker --once`).
- RC sessions start in Auto mode even with `defaultMode: bypassPermissions`; DB swap and key work are run by the owner in PowerShell. Real-data switch: `scripts/demo/switch-to-real.ps1`.
Related: [[erp-server]], [[staff-and-sites]]. Hub: [[erp-next-hub]].

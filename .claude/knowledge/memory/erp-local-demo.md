---
name: erp-local-demo
description: Showing ERP to the owner locally over Remote Control — demo folder, ports, update script, Auto-mode limits
metadata:
  type: project
---

- 2026-10-06: obsolete — the `ERP_NEXT-demo` folder is gone, branch claude/project-thread-gd6e8g deleted (SHA in `.git/refs/archive`); production runs on [[erp-server]]. Current local demo: `scripts/demo/run-demo.ps1` over `backend/demo.db`.
- Was: `Projects\ERP_NEXT-demo` (branch claude/project-thread-gd6e8g = main + scripts/demo), API 8022, site http://localhost:5177; the owner's preview on 8021/5176 is not touched. Login preview / Preview-2026!, demo staff Demo-2026!.
- Update: merge origin/main into that branch, push, `start_rc_session` on the default device, run `scripts\demo\update-demo.ps1` (demo.db kept). Start Vite with `ERP_API_PORT=8022 ERP_WEB_PORT=5177`. No `git stash` (shared with the owner's working copy).
- `scripts/demo/run-demo.ps1` + `seed_demo.py` fill every section via the HTTP API (needs `x-csrf-token` from cookie `erp_csrf` and `Idempotency-Key`; outbox via `python -m app.outbox_worker --once`).
- RC sessions start in Auto mode even with `defaultMode: bypassPermissions`; DB swap and key work are run by the owner in PowerShell. Real-data switch: `scripts/demo/switch-to-real.ps1`.
- Real ERP on the owner's PC (`ERP_NEXT-demo`, real.db, 8022) auto-updates: the scheduled task is only a timer; each check runs `scripts\auto-update-local.ps1 -Tick` fresh from disk, so fixes to the update logic ship with main — never ask the owner to reinstall. A failed update retries twice (10 min), then waits for the next commit; the reason («СБОЙ: …») and output tail land in `backend\auto-update.log` and `frontend\dist\update-status.json` → red banner in the ERP. Ask for a screenshot of the banner, not for the log. Never read a child's output through a pipe in these scripts: `Start-Process -RedirectStandard*` (API, outbox_worker) inherits every inheritable handle, the children held the old loop's stdout pipe and the loop hung silently after the first update (fixed 2026-09-30: output to `backend\update-last.log`, wait on exit only). Never clear handle inheritance of std handles either: the venv `python.exe` is a launcher that duplicates stdin for the real interpreter and dies «stdin duplication failed» (2026-09-30 14:48 outage). Each tick checks out `scripts/update-local.ps1` + `scripts/auto-update-local.ps1` from `origin/main` first, so a broken updater is healed by the next main.
Related: [[erp-server]], [[staff-and-sites]]. Hub: [[erp-next-hub]].

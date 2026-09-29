---
name: erp-server
description: Production server — own Ubuntu PC in the shop, Docker deploy, LAN + Tailscale, backups, how to push a release
metadata:
  type: project
---

- Host: the owner's own PC in the shop (not VPS/Timeweb — never raise it again). Ubuntu Server 24.04, user `erp`, hostname `erp-server`, `/opt/erp`. Fallback: WSL2.
- Deploy (PR #70, #77): Dockerfile + `deploy/` compose — postgres16, api with the site, worker, caddy, nightly backup 03:00 (14 daily + 8 weekly). Docs `deploy/README.md`, plan `docs/launch/CUTOVER.md`. Mode `lan`: http://<IP> inside the shop, outside via Tailscale.
- Cloud sessions can't reach the host (proxy 403, no ssh). Release only via Remote Control on the owner's PC: `deploy\push.ps1 -Server <IP>` (git archive of origin/main, key `~\.ssh\erp_server`). If Auto mode blocks it, give the owner one PowerShell line.
- PostgreSQL: all FKs DEFERRABLE INITIALLY DEFERRED (`migrations/env.py`) — no ORM relationships, insert order not guaranteed; SQLite tests don't check FKs.
- Real data on server: `deploy/load-real-data.sh base | projects | stock [--yes]`.
- Owner's manual part: erp-server key on GitHub, Ubuntu from USB with Import SSH identity, BIOS Power On, sudoers + Tailscale line, send the IP.

Related: [[launch-plan]], [[google-wiring]], [[erp-local-demo]], [[staff-and-sites]]. Hub: [[erp-next-hub]].

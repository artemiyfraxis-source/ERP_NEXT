---
name: erp-server
description: Production server — own Ubuntu PC in the shop, Docker deploy, LAN + Tailscale, backups, how to push a release
metadata:
  type: project
---

- Host: the owner's own PC in the shop (not VPS/Timeweb — never raise it again). Ubuntu Server 24.04, user `erp`, hostname `erp-server`, `/opt/erp`. Fallback: WSL2.
- Deploy (PR #70, #77): Dockerfile + `deploy/` compose — postgres16, api with the site, worker, caddy, nightly backup 03:00 (14 daily + 8 weekly). Docs `deploy/README.md`, plan `docs/launch/CUTOVER.md`. Mode `lan`: http://<IP> inside the shop; from anywhere — https://erp-server.tail811f20.ts.net (Tailscale Funnel on, proxies 127.0.0.1:80; checked 05.10.2026 — public, answers 200). The bare shop IP 46.242.14.51 is closed.
- Cloud sessions can't reach the host (proxy 403, no ssh). Release only via Remote Control on the owner's PC: `deploy\push.ps1 -Server <IP>` (git archive of origin/main, key `~\.ssh\erp_server`). If Auto mode blocks it, give the owner one PowerShell line.
- Reaching it from the owner's PC (fixed 2026-10-04): the PC is not on the shop LAN (192.168.31.x) and StealthSurf VPN owns the default route, so 192.168.1.85 gives a fake handshake + `Connection reset`. Use Tailscale: installed on the PC, server `erp-server` = 100.96.49.68. Windows Firewall here has DefaultOutboundAction=Block, so an outbound allow rule «ERP: исходящие в Tailscale (100.64.0.0/10)» was added. `ssh -i ~/.ssh/erp_server erp@100.96.49.68`; compose only via `sudo -n /opt/erp/app/deploy/erp.sh ps|logs|cli …`; deployed commit = last line of `/opt/erp/status/update-last.log`. Deploy = `gh workflow run update-server.yml` (server polls every 2 min).
- PostgreSQL: all FKs DEFERRABLE INITIALLY DEFERRED (`migrations/env.py`) — no ORM relationships, insert order not guaranteed; SQLite tests don't check FKs.
- Real data on server: `deploy/load-real-data.sh base | projects | stock [--yes]`.
- Owner's manual part: erp-server key on GitHub, Ubuntu from USB with Import SSH identity, BIOS Power On, sudoers + Tailscale line, send the IP.

Related: [[google-wiring]], [[erp-local-demo]], [[staff-and-sites]]. Hub: [[erp-next-hub]].

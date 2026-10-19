---
name: erp-server
description: "Production server — own Ubuntu PC in the shop, Docker deploy, LAN + Tailscale, backups, how to push a release"
metadata:
  node_type: memory
  type: project
  originSessionId: 19b4b321-0510-4f61-9970-d9722981d5ea
  modified: 2026-10-19T21:06:47.233Z
---

- Host: the owner's own PC in the shop (not VPS/Timeweb — never raise it again). Ubuntu Server 24.04, user `erp`, hostname `erp-server`, `/opt/erp`. Fallback: WSL2.
- Deploy (PR #70, #77): Dockerfile + `deploy/` compose — postgres16, api with the site, worker, caddy, backup loop (`deploy/backup-loop.sh`: nightly 03:00 — 14 daily, 8 weekly, 36 monthly; daytime every 3 h at 9–21 MSK, 15 kept, PR #272). Off-server copy: the owner's PC pulls the newest dump daily over Tailscale and checks SHA-256 (`deploy/pull-backup.ps1`, scheduled by `register-pull-backup.ps1`, into `~\Documents\ERP-backups`). Docs `deploy/README.md`, plan `docs/launch/CUTOVER.md`. Mode `lan`: http://<IP> inside the shop; from anywhere — https://erp-server.tail811f20.ts.net (Tailscale Funnel on, proxies 127.0.0.1:80; checked 05.10.2026 — public, answers 200). The bare shop IP 46.242.14.51 is closed.
- Release from anywhere (cloud threads included): `gh workflow run update-server.yml` → branch `update-request` → the server's timer (`deploy/update-request.sh`, every 2 min) runs `update.sh`: pre-update dump → rehearsal of new migrations on a copy (`rehearse.sh`) → update → health check; the job fails if the update did. Plus auto-update 02:00 / 06:00 MSK. `deploy\push.ps1 -Server <host> [-Ref <sha>]` from the owner's PC (git archive over ssh) stays for first install and rollback to a commit. Cloud sessions can't ssh to the host.
- Reaching it from the owner's PC (fixed 2026-10-04): the PC is not on the shop LAN (192.168.31.x) and StealthSurf VPN owns the default route, so 192.168.1.85 gives a fake handshake + `Connection reset`. Use Tailscale: installed on the PC, server `erp-server` = 100.96.49.68. Windows Firewall here has DefaultOutboundAction=Block, so an outbound allow rule «ERP: исходящие в Tailscale (100.64.0.0/10)» was added. `ssh -i ~/.ssh/erp_server erp@100.96.49.68`; compose only via `sudo -n /opt/erp/app/deploy/erp.sh ps|logs|cli …`; deployed commit = last line of `/opt/erp/status/update-last.log`. Deploy = `gh workflow run update-server.yml` (server polls every 2 min).
- SSH «kex_exchange_identification: Connection closed by remote host» while the site answers (19.10.2026, 21:03→night): not the server — Tailscale on the PC was logged out («Tailscale is starting»), so 100.96.49.68:22 went into the StealthSurf VPN. Check `tailscale status` first; the owner logs Tailscale back in.
- Web login for the agent (owner 19.10): stored outside git in `~/.claude/secrets/erp-server.env` (`ERP_SERVER_URL`, `ERP_SERVER_LOGIN`, `ERP_SERVER_PASSWORD`). Use it to look at prod screens yourself; never echo the password into chat, logs, commits or memory.
- PostgreSQL: all FKs DEFERRABLE INITIALLY DEFERRED (`migrations/env.py`) — no ORM relationships, insert order not guaranteed; SQLite tests don't check FKs.
- Real data on server: `deploy/load-real-data.sh base | projects | stock [--yes]`.
- Owner's manual part: erp-server key on GitHub, Ubuntu from USB with Import SSH identity, BIOS Power On, sudoers + Tailscale line, send the IP.

Related: [[google-wiring]], [[erp-local-demo]], [[staff-and-sites]]. Hub: [[erp-next-hub]].
- Network (09.10.2026): shop router replaced by a Keenetic after a building outage (server offline 06.10 22:30 → 09.10). LAN stays 192.168.1.0/24; server registered as erp-server, 192.168.1.85, port 2. prepare-host.sh pins a STATIC netplan (/etc/netplan/60-erp-static.yaml, dhcp4 false) — a router with another subnet/gateway takes the server offline; fix on console: move that file to /root/ + `netplan apply`, then reserve the IP on the router instead.
- SSH from Git Bash (11.10.2026): `/usr/bin/ssh` (OpenSSH 10.5, MSYS) mangles the Cyrillic home path, can't open `~/.ssh/known_hosts` and fails with «Host key verification failed» — the server key did NOT change (ED25519 `SHA256:lUUSKgGD1eJ/eGuNBf9TV7vXtRB5KS/L8xNUeSsuRAA`). Use Windows OpenSSH: `& "C:\Windows\System32\OpenSSH\ssh.exe" -i "$HOME\.ssh\erp_server" erp@100.96.49.68 …` from PowerShell. Never bypass host-key checking.

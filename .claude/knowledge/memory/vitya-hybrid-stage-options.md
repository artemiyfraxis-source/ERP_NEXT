---
name: vitya-hybrid-stage-options
description: "Витя's purchasing/warehouse hybrid (Roller_Buyer tabs, GRN labels, TSD) is in main since 21.10 (#627) but dark on prod — every block sits behind ERP_STAGE_OPTIONS"
metadata:
  node_type: memory
  type: project
  originSessionId: 54f356df-788e-49be-8c10-a9e933f86534
  modified: 2026-10-21T21:24:15.201Z
---

PR #627 (21.10.2026) merged `vitya/hybrid` @ dae04c3e into `main` and released it (98e347b, migrations 0133–0140, new
`purchasing_*` + `warehouse_label_settings` tables only). Every hybrid block — «Сегодня», «Заявки», «Запросы КП», «Счета и
поставки», «Счета Kaiten», GRN labels on TSC MB341, `/tsd`, device gate — is behind a stage option
(`backend/app/shared/stage_options.py`). Without the env var `ERP_STAGE_OPTIONS` (prod, CI) all options are off, nothing
can switch them on, and the gated routes answer 404 / «Опция выключена».

**Owner, 22.10.2026: «нахрена так, всё надо включать — тестить будем».** All 24 options are ON in prod since 22.10 00:23
MSK for testing. Server setup: `ERP_STAGE_OPTIONS=/app/data/import/stage/stage-options.json` added to
`/opt/erp/config/.env` (copy `.env.bak-20261022-*`). The file is `/opt/erp/import/stage/stage-options.json` on the host,
owned by uid 10001, and survives updates. The toggles are in the app («Опции стенда», admin), or edit the file.
Backup before switching: `/backups/daily/erp_2026-10-22_002241.dump`.

**Why:** the owner tests the hybrid on live data; he doesn't want features hidden from him.
**How to apply:**
- To switch a block off, flip it in the app or the file. No redeploy is needed: `_read()` re-reads the file on every check.
- The device gate («Пускать устройства из сети») stays off: inside Docker it would only listen in the container. The TSD uses the public URL `/tsd` (→ `?section=tsd`).
- Printing to the TSC works only where the ERP server runs on Windows. On the Linux prod it only writes a file, so a print agent on the warehouse PC is needed first.
- `/tsd` login is by PIN. Before it is reachable through the public Tailscale Funnel it needs a PIN attempt limit and LAN-only access.
- Not behind options, already live: the `@vitejs/plugin-legacy` build (Chrome 70; build ~1 min; inline loaders moved to files for CSP), the one-pass stock page/search, and the theme colour tokens in `styles.css`.
- Витя's later work on `vitya/hybrid` after dae04c3e goes in as separate PRs on top of `main`.
- Related: [[module-procurement]], [[module-warehouse]], [[live-data-protocol]].

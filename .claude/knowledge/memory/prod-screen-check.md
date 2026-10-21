---
name: prod-screen-check
description: "Owner 20.10 — the agent HAS the owner's web login to prod; check released screens/data on prod itself, never ask for a login or spin up a local preview for that"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 588273da-1ef1-4aac-9b53-d023958d79e8
  modified: 2026-10-20T09:51:18.689Z
---

The agent has the owner's web login to https://erp-server.tail811f20.ts.net — `~/.claude/secrets/erp-server.env`
(`ERP_SERVER_URL`, `ERP_SERVER_LOGIN`, `ERP_SERVER_PASSWORD`; outside git, never echo it). Owner 20.10: «у тебя есть
вход … есть пароль мой поищи»; «зачем каждый раз новую вкладку локальную, если есть доступ и инет».

**Why:** after releases I reported «экран на проде не смотрел — нужен ваш вход» and opened local previews, although
the login was saved on 19.10 ([[erp-server]]).
**How to apply:** after `update-server.yml`, look at the changed screen on prod yourself: source the env file in Bash
(`set -a; . ~/.claude/secrets/erp-server.env; set +a`), then either
`ERP_URL=$ERP_SERVER_URL ERP_LOGIN=$ERP_SERVER_LOGIN ERP_PASSWORD=$ERP_SERVER_PASSWORD node scripts/demo/shoot.mjs …`
or a headless Playwright script that logs in via `POST /api/auth/login`. Read-only on prod screens: no clicks that
write. A local preview/demo is only for screens not yet released (before/after shots of an unmerged change).
Never open extra local browser tabs just to look at prod.

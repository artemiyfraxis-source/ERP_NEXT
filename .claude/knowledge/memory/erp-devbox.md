---
name: erp-devbox
description: "Access to the shared ERP devbox (dev.fraxislab.ru) — SSH alias, clone, own stand, dev-help commands"
metadata:
  node_type: memory
  type: reference
  originSessionId: 38185913-4448-4e6c-b46f-e9eb28b1d37c
  modified: 2026-11-19T10:26:04.047Z
---

Shared ERP devbox, access granted 19.11.2026 (separate repo, merge owner — vasily; see [[vasily-code-permission]]).
- SSH: alias `erp-devbox` in `~/.ssh/config` (dev.fraxislab.ru:2222, user `artemy`, key `~/.ssh/id_ed25519`).
- Repo clone on the box: `~/erp` (from `/srv/git/erp.git`).
- Own stand: https://dev.fraxislab.ru:8104 (browser login `artemy`; credentials are in the owner's chat, never store them here).
- Self-service: `dev-help` (dev-check, dev-deploy, dev-handoff, dev-db-*); agent rules `/srv/queue/bin/ИНСТРУКЦИЯ-АГЕНТАМ.md`.
- Box has 4 GB RAM for everyone: no parallel full pytest runs there.

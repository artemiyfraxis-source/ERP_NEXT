---
name: live-data-protocol
description: "Why production rehearsal exists and how update.sh/rehearse.sh validate a restored copy"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 6b98e0d2-15aa-4b5d-9f38-40d68dd980c1
  modified: 2026-11-12T12:00:00.000Z
---

Why (05.10): real stock, orders and reserves already lived in production; automatic updates could apply a merged migration without a person watching. An empty-table migration exposed the missing rehearsal gate.

The binding rule is `.claude/team/RULES.md` «Live-data protocol»; this note records mechanics only.
- `deploy/update.sh` takes a pre-update dump; `deploy/rehearse.sh` restores it into throwaway compose project `erp-rehearsal`, snapshots row counts/invariants via `app.db_integrity`, upgrades, compares and probes the API. A red rehearsal stops the production update.
- Migrations run in the update script, not on container startup. `DESTRUCTIVE = True` additionally needs the approved revision id in «Обновить сервер».
- Keep restored-copy, upgrade and reconciliation evidence with migration PRs per RULES.md.
Related: [[erp-server]], [[prod-reference-write]].

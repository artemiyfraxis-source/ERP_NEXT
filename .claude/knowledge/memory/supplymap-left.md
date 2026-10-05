---
name: supplymap-left
description: "Owner 05.10 — the ERP has left SUPPLYMAP; don't consult it as the reference for fixes or new rules"
metadata:
  node_type: memory
  type: feedback
  originSessionId: e89ae524-0e2a-41d1-850c-46cab5cfa86d
  modified: 2026-10-05T08:39:21.791Z
---

Do not read ../SUPPLYMAP to decide how a fix or a new feature should work. Decide by the ERP's own data, code and
the owner's words; ask the owner when the ERP can't settle it. Don't brief workers with «check SUPPLYMAP».

**Why:** owner, 2026-10-05, when a generator bug was being traced to a SUPPLYMAP rule: «ты почему к суплаю
обращаешься, если мы уже ушли от него». Production runs on the ERP since the 05.10 launch.

**How to apply:** supersedes «SUPPLYMAP = source of domain rules» in CLAUDE.md and the «всё как в SUPPLYMAP» rule in
[[supplymap-parity]] for new work. The drift hook and SOURCE.json pins still exist; the owner hasn't said to remove them.

---
name: vasily-code-permission
description: Василий (SaveOurSouls) allowed his TECHMAP-GRAPHER code to be used in ERP_NEXT — port/copy freely with attribution
metadata:
  node_type: memory
  type: project
  originSessionId: 6b98e0d2-15aa-4b5d-9f38-40d68dd980c1
  modified: 2026-10-05T22:21:17.612Z
---

Owner, 2026-10-06: «Василий дал доступ на внедрение, это его наработка для ERP». SaveOurSouls/TECHMAP-GRAPHER (the real app is on branch `codex/integration-m5-22`, .NET 10 + SQLite + React 19; the export is `.techmap-project.zip` → `project/snapshot.json`) may be ported or copied into ERP_NEXT despite having no LICENSE file.

**Why:** the repo has no license, so earlier agents took ideas only. The author's permission was given to the owner.
**How to apply:**
- Port its logic faithfully: export parsing, cut length/corrections/L±, strip layers, route rows. Note the original file path in code comments.
- Add an attribution entry in `THIRD_PARTY_NOTICES.md` («used with the author's permission», source commit).
- The QCAD hatching catalog under its `design/` is GPL-3.0 — that is a third party, not Василий; keep it out.
- If SaveOurSouls/PRODUCTION_PLANNER is also Василий's, the same applies — check with the owner before copying from it.
Related: [[module-techcards]].

---
name: vitya-requests-verify
description: "Owner 23.10 — Витя's change requests are verified against code and prod usage before doing them; never apply blindly"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 128bab83-781a-4ce3-a66d-a0a9a0fa29cb
  modified: 2026-10-23T17:16:14.914Z
---

Owner 23.10.2026, forwarding Витя's 13 purchasing/UI edits: «ты только проверяй его тейки, а то сломаем что-то».

**Why:** Витя's requests (remove tabs, move topics, copy Roller_Buyer screens) can drop working functions, routes, rights or data the shop uses.
**How to apply:** for each of Витя's items check first — who uses it on prod, what links/routes/rights/tests/data depend on it, what disappears. Do the safe ones; bring the risky ones to the owner with the consequence in one line before building. Removal only when every function is kept or moved. Related: [[vitya-hybrid-stage-options]], [[owner-working-rules]].

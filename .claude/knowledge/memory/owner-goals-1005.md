---
name: owner-goals-1005
description: "Owner's two current goals (05.10.2026) and receiving decisions — minimal working chain procurement→receiving→issue; then debug production"
metadata:
  node_type: memory
  type: project
  originSessionId: 6b98e0d2-15aa-4b5d-9f38-40d68dd980c1
  modified: 2026-10-05T14:11:10.957Z
---

Owner, 05.10.2026 evening:
- **Goal 1 (main):** a minimal, working, convenient chain «закупка → приёмка → выдача». Fewer actions, nothing blocks good stock. Optional data stays optional.
- **Goal 2:** debug production end to end: techcards → planning → how tasks arrive to fitters («Мои задания») → how work in them proceeds.

Receiving decisions (answers to the receiving redesign questions):
1. ВК act stays one per position (line), not per delivery.
2. Недовоз: the delivered qty goes to stock at once, the remainder becomes the `-Д` backorder with the buyer's confirmation.
3. Fact quantity is entered only on a discrepancy. Empty means «как в УПД».
4. One УПД may cover several orders of one supplier («да, в теории»).
5. Mass, manufacture date and quality document are NOT mandatory. A missing quality document must not block acceptance.

**Why:** the receiving screen was legacy-shaped (per-line queue, ~50 actions per 10-line delivery, shortfall blocked good stock). See [[module-warehouse]] and [[module-procurement]].
**How to apply:** judge every change in procurement/receiving/warehouse/fulfillment by «fewer actions from order to issue». Prefer warnings to blocks. Don't add mandatory fields. Ask only for real domain forks. See [[owner-request-queue]].

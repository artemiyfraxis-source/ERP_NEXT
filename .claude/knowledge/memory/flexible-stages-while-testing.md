---
name: flexible-stages-while-testing
description: "Owner 29.10 — production stages/quantities stay flexible while the shop tests the ERP: warn, don't refuse; tighten later"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 128bab83-781a-4ce3-a66d-a0a9a0fa29cb
  modified: 2026-10-29T14:08:11.104Z
---

Owner 29.10.2026 («пока что гибкая система, потом будем закручивать, чтобы всё было по этапам; пока мы тестируем и
пробуем, это может мешать работе»): manual stage marks and per-KB partial quantities (Начато / Готово / На ОТК /
ОТК прошло) must not block work. Refuse only junk: negative numbers, unknown stage words and any count above the KB
run (owner: «больше тиража нет»). A later counter above an earlier one auto-raises the earlier ones (Готово 120 lifts
Начато 80 → 120) with a visible notice, in the same journal entry; lowering never cascades. Stage order (ОТК after
Готово, acceptance only after ОТК) is never enforced. Manual marks win over computed ones.

**Why:** the shop is still learning the ERP; ОТК doesn't record inspections in ERP yet (0 rows on prod 29.10), counts are
entered by hand and will be wrong at first — hard gates would stop acceptance and shipping.
**How to apply:** for stage/progress features in production, intake and shipping, store what people enter, compute
stages from it, flag contradictions as a warning chip; keep the «constraints, not rollbacks» rule (RULES.md) for data
integrity (FK, NOT NULL, ≥ 0, version checks) but not for workflow order until the owner says to tighten.
Related: [[module-production]], [[owner-working-rules]].

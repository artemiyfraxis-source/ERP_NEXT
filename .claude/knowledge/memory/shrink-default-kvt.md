---
name: shrink-default-kvt
description: ETK heat-shrink material without a readable maker → take КВТ's tube of the same type/size/colour
metadata:
  type: feedback
---

Owner 17.11.2026, on ETK shrink temperatures: «квт если не читается по базе». When an ETK/generator heat-shrink
material can't be resolved to one «Термоусадка» row (generic «ТУТ 6/3мм, черная», «ТТК 12/4мм, черная»), use the КВТ row
of the same type, size (before/after) and colour, and say in the ETK that КВТ is the default maker.

**Why:** issued ETK showed «не задано — проверить» for ~80 operations after #869 because compositions name tubes without a
maker; the shop buys КВТ by default.
**How to apply:** exact match first (article, КВТ article number or series inside the name), then the КВТ default; if
several КВТ series of that size differ in temperature, show them all for review — never average. Related:
[[module-techcards]], [[etk-auto-revisions]].

Owner 17.11.2026 («реши сам») → decided: generic ТУТ (no maker) = КВТ ТУТнг-LS 2:1 (passport 90…120 °C); generic transparent
ТУТ = КВТ ТУТ(HF) прозрачная (art. 83266, passport ТУТ(HF) 80…110 °C — fill that row's temperature); generic ТТК = КВТ ТТК 3:1
(115…140 °C). ТТ-150, ТНТ and other special series are never a default.

---
name: stock-first-then-issue
description: "Owner 23.11 — every received item, tools and equipment included, lands on warehouse stock first, then is issued/moved; keep OLD GRN; person names allowed in «Назначение»"
metadata:
  type: feedback
---

Owner 23.11.2026 on the warehouse audit: «сначала всё помещается на склад и потом выдаётся, перемещается». A lot typed as
tool/equipment (Makita screwdriver, ultrasonic bath, soldering tips) stays a warehouse lot; never auto-route intake to the
«Оборудование» tab — it moves there only by an explicit issue/move. Same day: existing `OLD.*` GRN stay; a person's name in
«Назначение» («Даша») is a valid responsible person, not junk to clean.

**Why:** the storekeeper accounts everything through stock; skipping it loses the receipt trail.
**How to apply:** data cleanups give such lots a correct type only; intake validation must not branch to equipment.
Related: [[module-warehouse]], [[receiving-now-vs-later]].

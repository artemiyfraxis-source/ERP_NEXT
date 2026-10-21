---
name: applicators-by-series
description: "Owner rule 16.10 — every contact of one terminal series carries all the series' applicators (A*); S*/M* stay per wire range"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 343caf92-9053-4fec-a7f9-0ccc286890c9
  modified: 2026-10-16T08:47:37.479Z
---

Owner 16.10.2026: «внутри одной серии у всех контактов должны быть все аппликаторы — они смежные, тут спокойно можно
дописать A2» (JST XH: SXH-002T-P0.6 S5/M5 → S5/M5/A2).

Applied as: series = manufacturer + «Серия» of the БД.ТЕР row; the union of A* tokens goes to every contact; an empty
cell takes the series value when all other contacts agree. S* (semi-auto) and M* (hand tool) are NOT merged — in the DB
they follow wire range (Micro-Fit S8/M8 on 20–24 AWG vs S18/M9 on 26–30, КВТ M31/M32/M33 by size, TE one tool per
contact). Script: docs/data «Терминалы — аппликаторы по серии 16.10 запись в прод.py» (rerunnable, dry → apply).

**Why:** the owner sees adjacent contacts of a series as crimpable by the same applicator; gaps looked like a data defect.
**How to apply:** after any terminal load or new contact in a series, rerun the script (dry first); don't merge S/M
tool codes across wire ranges without the owner. [[module-reference-db]] [[prod-reference-write]]

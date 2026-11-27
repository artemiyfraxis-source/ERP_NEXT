---
name: equipment-naming
description: "БД.ОБ names — look the article up online, name by function + spec range; no shop slang («большой»)"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 5ce49b55-2573-4a7d-92f3-b40ed9d819be
  modified: 2026-11-17T19:56:45.597Z
---

Owner 17.11.2026: equipment names in БД.ОБ describe what the machine is. Look the article up online (maker card on
made-in-china / Alibaba) to learn what it is. Shop slang («большой», «стрипер») is a local name, never the DB name.
Seller specs are unreliable: BZW-D2.0S cards say 0,5–8 / 0,3–5 mm², yet the shop strips 0,2 and 0,35 mm² on it (owner
17.11) — never put a seller's range in the name; same-type machines may share a name, the article tells them apart.

Kind word per ГОСТ 23004-78: «Автомат» = runs the full cycle alone; «Полуавтомат» = an operator loads each piece
(e.g. ОБ.101/102 label printers, stripping benches); operations named per ГОСТ 23587/23585 («снятие изоляции»,
«разделка»), pattern «<Автомат|Полуавтомат|Станок> для <операций>».

**Why:** «большой это локальное название» — names were generated at different times and must be systematic.
**How to apply:** rename via `edit_record` on prod with old-value guard, backup first, journal in
`docs/data/equipment-renames-*.csv` ([[prod-reference-write]]). ОБ.036 AM901 — no source found yet; still open.

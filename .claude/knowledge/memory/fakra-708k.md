---
name: fakra-708k
description: "708К. (Яндекс, 41 КБ) — Rosenberger FAKRA/HFM on RTK 031: 6 drawing groups, generator flow shipped 16.11 (cut+strip, L+, tape labels, colour marking), tooling open"
metadata:
  node_type: memory
  type: project
  originSessionId: 5ce49b55-2573-4a7d-92f3-b40ed9d819be
  modified: 2026-11-16T15:52:58.046Z
---

- Drawings are in ERP: project card «КД» per КБ (`GET /api/techcards/kitsets/708К./documents/{id}`). 6 groups: 122664 КБ1–2
  (straight sealed + angled FAKRA), 122666 КБ3–10 (HFM quad), 121537 КБ11–14 (antenna, LMR-100), 122662 КБ15–27 (beam),
  122663 КБ28–40 (Amphenol bulkhead + straight), 121501 КБ41 (harness, TZTEK 818018737).
- Parts: straight = 59K13O-1M4A4 + 59Z163-003-Z + seal 59K14B-1M4/50 + cap 59Z163-000/51; angled = 59K25H-1M4A4 + 59Z006-T00-Z;
  HFM AMK11D-1M4Z5-Z; Amphenol 2FA1-NZSP-C30E2. Maker docs: `docs/data/FAKRA 708К — *.md`.
- 16.11.2026 owner decisions (PR #854, released): cut+strip for straight/angled, cut = L + L+A + L+B (prod БД.КОАКС L+ 30 / 25 mm —
  agent's estimate, refine on first article); labels printed on Vell VL-S641 tape on ZCUT-RT660, optional clear TUT; print and
  TUT cut are separate ops; connector colour marking paint/laser/tape; per-connector order stays a generator recipe.
  (PR #854 shipped HFM/Amphenol with a separate strip — superseded below.) Saved ETKs keep the old recipe — new settings are switched on in the generator.
- 16.11 owner (technologist): HFM too — full strip at cut, support sleeve crimped AFTER strip («инструкция для ручного
  монтажа; сначала зачистить, потом фиксировать»), overriding MA_AMV003's partial-strip order. Same for Amphenol 2FA1 —
  «везде полная разделка» at cut. Not yet in the generator.
- Owner 16.11: L+ is the technologist's decision and may differ from the calc (calc habitually adds +50/+60 per cable) —
  never «align» L+ to the calc; cut = L from the drawing + L+ per end.
- Owner 16.11: КБ41 (harness 121501/AA, TZTEK 818018737) stays WITHOUT an ETK — exclude it from regeneration, cut lists, grouping.
- Owner 16.11: label tape (Vell VL-S641) always lies at the printer — shop consumable, never a BOM/kitting row.
- Owner 16.11: the calc sheet is never corrected; wrong calc data is corrected in ERP (project BOM) after intake.
  Maker manuals saved in `~/Downloads/708К инструкции/` (housings 59Z163/59Z006, seal, cap, Amphenol 2FA1 — 403 from here).
- 16.11 КБ1 (OG) / КБ2 (PK) prepared as generator drafts on prod; «Пересобрать» waits for the owner's OK. Next groups: КБ3–41.
- Open: tooling «пока не определён» (25.10); calc RTK 031 4.8 m < cut 4.855; RTK 031 has no D3 → generator picks the big
  machine by default; calc errors from 25.10 (КБ4 duplicate block, marker colours) — reported.
Related: [[module-techcards]], [[coax-data-sources]], [[maker-docs-from-russia]].
- Owner 17.11.2026: protective transparent ТУТ over coax tape labels — END labels (at connector A / B) 35 mm, CENTRAL label
  (FRX serial) 55 mm. Before this the generator used one length for all (60 mm; КБ4 had 30 mm) — wrong everywhere.

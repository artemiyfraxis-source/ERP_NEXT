---
name: gost-72182-series
description: "ГОСТ Р 72182-2025 «Соединения без пайки» (GOST analogue of IPC-620/IEC 60352) downloaded locally; parts, where the PDFs and verified maps live, open next steps for templates/СТО/pictures"
metadata:
  node_type: memory
  type: project
  originSessionId: 9962f786-8b96-40dd-9ab8-a1bd25a560cf
  modified: 2026-10-23T20:53:40.714Z
---

23.10.2026: Vasily (colleague) — «620 стандарт, только ГОСТ, на максималках по отдельным темам»; tech cards should get
documentation, process descriptions and pictures from it. Series = parts .2 обжим, .3 IDC с доступом, .4 IDC без доступа,
.6 прокалывание изоляции, .7 пружинные зажимы, .9 УЗ-сварка (no .1/.5/.8). All in force from 01.06.2026.

Where: PDFs in `C:\Users\Артемий Васильев\Documents\ERP-norms\ГОСТ Р 72182-2025\` (outside git — the repo is public, never
commit GOST text or images); verified maps `…\ERP-norms\maps\gost72182-map.{md,json}` (150 clauses, 76 figures/tables, PDF
page = printed page + 4), `corrections.md`, plus the 22.10 `norms-map.*` and template write scripts (apply_templates.py,
tpl-rehearse.sh, check.py, gen_defects.py).

Open next steps (not done yet):
- СТО: IDC rows cite part 2 wrongly → parts 4/3; caps/splice sleeves pull-out → part 2 табл. А.11 (not табл. 7);
  табл. 5 is an «example», табл. 7 only classes A/B; 4.11 УЗ-сварка rows → part 9 clauses + 4 new rows; sharper bases
  for «нет раструба / вылета / изоляция в зоне обжима / сопротивление» (А.5, А.4.4, А.6, рис. 44).
- Templates: US_WELD → part 9 (no tinned cores п. 4.2, weld length табл. 1, ≤20 cores, nodes ≥50 mm apart, compaction
  95–120 %); PRS_IDC → part 4; crimp-height measurement procedure (part 2 А.7.2, рис. А.13–А.14, micrometer with ratchet).
- Pictures in ETK: our own redrawn SVGs citing «ГОСТ Р 72182.2-2025, рис. А.14» (crimp-height measuring, accept/defect
  crimp states табл. А.3/А.4, US-weld samples рис. 26–29) — a new feature (templates have no image storage yet).
Write path for templates: [[rv-td-base]] (one transaction by code, rehearsal on a prod copy first).

23.10 update: #674 released (d34d0e0) — defect pictures picked by СТО row code (`frontend/src/etkStoScenes.ts`, all 320
rows, 29 new scenes), parser fixed for СТО-format texts. The shop owns IPC/WHMA-A-620E (CN edition, PDF in
`ERP-norms\IPC\`); `maps\figure-plan.json` maps every СТО row to GOST/IPC figure or own scene.

23.10 evening: owner reversed the earlier «agent doesn't crop» policy («вырежи пж») — the agent crops the figures itself
for internal shop use. Done: 215 crops for the 112 gost/ipc rows in `ERP-norms\figures\` + `manifest.json` (code, role
norm/defect/ref, kind gost/ipc, caption, page, bbox); scripts `maps\fig_crop.py` + `maps\fig_trim.py` (IPC = exact
embedded-image bbox; ГОСТ scans = table cell around the bbox centre + hand FIX list). Never commit crops to the public
repo — they go to the server DB. IPC figures: internal ETK screen and shop printouts only, never the customer ТД kit.
Whole-book IPC translation is still out; a Russian digest in our own words is fine.

23.10 night, merged to main (not released yet): #677 std figures in ETK (table 0148, API /api/techcards/std-figures,
`scripts/figures/upload_standard_figures.py`, token scope `figures:write`), own SVGs for 53 rows; #679 БД.ОП cleanup
(15 rows deleted on prod already); #680 10 new templates + US_WELD/IDC/crimp-height by ГОСТ, СТО ред. 3 (351 rows).
Owner: «гост важнее» → 20 rows switched IPC→ГОСТ 2358x/23594/61191-4 (norm 64 ГОСТ / 48 IPC). Extra standards:
`ERP-norms\ГОСТ-доп\` (gost-extra.md, 2 searches), coax interfaces `maps\coax-interfaces.md` (IEC 61169-x + MIL-STD-348B
2014; Change 4 needs VPN), IPC digest `ERP-norms\IPC\Конспект IPC-WHMA-A-620E.md`. ГОСТ Р 51914 exists only as closed РВ.
After release, in order: upload figures (`--apply`), rehearse `docs/data/operation-templates-2026-10-23/tpl-rehearse.sh`
on a prod copy, write templates (every issued ETK gets one auto revision). Open owner question: SMA female depth
1,88…2,41 (shop) vs 1,88…2,23 (MIL-STD-348B рис. 310-2). Brain TASKS has the checklist.

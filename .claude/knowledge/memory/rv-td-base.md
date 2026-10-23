---
name: rv-td-base
description: "GOST RV tech-doc base (rev 1, 20.10.2026) + STO FRAK-001-2026 typical defects — Claude Doc link, verified standards, owner-side decisions"
metadata:
  node_type: memory
  type: project
  originSessionId: 836e108e-4323-44af-b43d-938100743c43
  modified: 2026-10-22T14:26:04.368Z
---

Owner asked (20.10.2026) for a maximal-rules base of tech documents (ТД) for ГОСТ РВ customers, acting as technologist + normcontrol; ERP code changes come later.
Doc: https://claude.ai/code/artifact/ed9b7e73-a452-49c3-8774-f9a88bea27e7 — tab 1 «База ТД под ГОСТ РВ» (12 sections), tab 2 «СТО: типовые дефекты» (236 defects from preview.db operation templates, classes per ГОСТ 15467).

Verified facts that are easy to get wrong:
- ГОСТ 3.1102-2011 is void in RF since 01.01.2026 → ГОСТ Р 3.102-2024 (literas); ГОСТ 2.503-2013 → ГОСТ Р 2.503-2023 (since 01.03.2024); ГОСТ 3.1001 → ГОСТ Р 3.001-2023.
- There is no «КТК» in ЕСТД: ГОСТ 3.1502 defines ОК технического контроля (code 60) and ВОП.
- 3.1201 codes: МК 10, КЭ 20, ТИ 25, ВТД 40, ВО 42, ВМ 43, КТП 50, КТО 57, ОК 60; method 85 электромонтаж, 88 сборка, 80/81 пайка, 02/03 контроль.
- ГОСТ 3.1118 МК has no row Р (only ОК by 3.1407); ГОСТ 3.1703 does not cover электромонтаж.
- Crimp: ГОСТ Р 72182.2-2025 (from 01.06.2026).

Working decisions (rev 1, change via the doc's change sheet): temporary org code «ФРАК»; Разработал — технолог, Проверил — директор производства, Утвердил — генеральный директор, Н.контр. by order; no ОТК service yet → no RV set released until an independent controller exists.

Implemented 20–21.10.2026 in PRs #614, #622 (KD designation, profession/grade from БД.ОП, workshop/site, background auto-issue, backfill `python -m app.modules.techcards.td_backfill [--apply]`), #624 (internal-code check = БД.ОП op families only); prod: 26 issued cards all active 21.10. Original PR #614 (branch rv-td-print-1020, migration 0129_td_issues): backend `techcards/td_issues*.py`, `td_sto.py` (op code → СТО section, combined codes get all sections), data `backend/data/sto-frak-001-2026.json`; frontend `TdIssuesPanel.tsx` (block in ETK), `tdPrintSheets.ts` / `stoPrint.ts` / `routeSheetPrint.ts`, contract `tdTypes.ts`. Showcase: https://claude.ai/artifact/HGZfFCdHf7TPnNTfGJPjiM. Gotchas found on real ETK: template names carry «CODE | name» (print strips it, normcontrol must too); wires from БД.ПРОВ have empty `name`, the grade is in `art`.

22.10.2026 (PR #643, released): СТО ред. 2 (320 rows, `basis` per row, `norms` per section) and TD print with standard + clause
(МК/ОК «Технические требования — по ГОСТ …, п. …», ВТД lists only standards actually cited). 112 operation templates audited by
group and written to prod in one transaction (`docs/data/operation-templates-2026-10-22/`, rollback + verified clause map there).
Owner facts from that audit: ГОСТ 23585-96 never introduced in RF — cite 23585-79; no open standard for coax connector assembly →
«по ДИ изготовителя (ГОСТ 3.1129, п. 6.3)»; ФР 544-1ФГ is no-clean; ОБ.071.X / ОБ.070.X = soldering irons / hot-air stations,
X = unit number at the workplace (not a gap); circuit tester = ОБ.040 SR-350A; VNA = ОБ.105 (temporarily from stock ГРН
OLD.26.0000498); PRS_CAP deleted (coax cap = PRS_CAP_COAX); PRS_COPPER = copper-tape splice; PRS_TERMINAL_multi = all cores of a
multicore cable crimped in one cycle; GX, D-Sub, СНП are soldered only; folding cores / wire-to-wire splices on RV only by КД.
Template write = apply_templates.py by code (all A/B sides) → one ETK refresh → one auto revision per card.

**Why:** colleague said current printed ETK «на РВ не прокатит»; typical defects must move to an STO and be referenced.
**How to apply:** ETK print work (queue 1 in section 12: STO reference instead of inline defects, ГОСТ designation/litera/signatures, RV revisions = draft ИИ not ETK_AUTO_REVISION) builds on this doc. See [[etk-auto-revisions]], [[module-techcards]].

---
name: module-production
description: Production — master screen in «Ресурсное планирование», plan with Gantt, fitters by site, boxes like SUPPLYMAP
metadata:
  type: project
---

- «Ресурсное планирование» is the master screen (owner 2026-09-29): after the tech card, projects split into operations; the master assigns them to fitters; load shown as Gantt (PR #75, per site #78, cards #88, compact buttons #89). Production plan with Gantt — PR #62.
- A master sees only his site → [[staff-and-sites]].
- Boxes as in SUPPLYMAP (PX-6); numbering → [[supplymap-parity]].
- Bug fixed in #50: second single-operation assign gave 500 (naive vs aware datetime in `_result`).
- Operation shares (2026-09-30, migration 0076): each fitter on an operation has a share — crew (no quantity, one window) or pieces (own window by norm). Assigning places from the first free time with the tail on next working days and pushes the fitter's later work (`production/dispatch.py` place/reflow). Fact moves the plan: «▶ Начать» = took it, all own pieces submitted = finished; earlier pulls adjacent work (gap ≤ 1 h), later pushes, incl. the next stage. «Назначить на день» appends after the day's work.
- Postponed items → [[deferred-by-owner]]. Operations come from [[module-techcards]]. Decisions: [[runlog-decisions]]. Hub: [[erp-next-hub]].

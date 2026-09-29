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
- Postponed items → [[deferred-by-owner]]. Operations come from [[module-techcards]]. Decisions: [[runlog-decisions]]. Hub: [[erp-next-hub]].

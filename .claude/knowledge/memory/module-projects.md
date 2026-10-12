---
name: module-projects
description: Production projects — created from a calc link (Google sheet), shown as cards like SUPPLYMAP, request from calc
metadata:
  type: project
---

- Only production projects live here; they are created through a calc («Расчёт») that is a link to a Google sheet, as in SUPPLYMAP (PK-3, PK-5).
- Look: cards like the Google screen (PR #83, `frontend/src/ProjectCard.tsx`).
- A request is created from the project: button «Оформить заявку из расчёта» (PR #79) → [[module-procurement]].
- Project KD is stored in the ERP DB, not on Drive → [[google-wiring]].
- Kit sets and names back-fill (PK-1), checked against SUPPLYMAP together with tech cards (PR #80) → [[module-techcards]].
- Decisions to review: [[runlog-decisions]]. Hub: [[erp-next-hub]].

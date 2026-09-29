---
name: module-reference-db
description: «База данных» section (was Справочник) — coax, terminals, operations/equipment as cards, AWG picker
metadata:
  type: project
---

- «База данных» replaces «Справочник» and «Шаблоны техкарт» (owner request).
- Coax: cable diameters D1–D3 in «Коаксиальные кабели» (СПР.КАБ), connectors show lengths only (PR #65).
- «Операции» (PR #76) and «Оборудование» (PR #85) shown as cards; AWG with wire pick by brand and cross-section (PR #85); «Провода и кабели», «Материалы» are working tabs.
- One-off mirror dump tabs removed from the UI; rows stay in the DB because the ETK generator and the warehouse read them.
- Used by [[module-techcards]] and [[module-warehouse]]. Decisions: [[runlog-decisions]]. Hub: [[erp-next-hub]].

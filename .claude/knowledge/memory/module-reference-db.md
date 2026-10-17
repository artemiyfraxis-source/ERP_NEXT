---
name: module-reference-db
description: «База данных» section (was Справочник) — coax, terminals, operations/equipment as cards, AWG picker
metadata:
  type: project
---

- «База данных» replaces «Справочник» and «Шаблоны техкарт» (owner request).
- Coax: cable diameters D1–D3 belong to the cable, connectors show lengths only (PR #65).
- «Операции» (PR #76) and «Оборудование» (PR #85) shown as cards; AWG with wire pick by brand and cross-section (PR #85).
- Tab groups (owner 04.10): Техкарты · Разъёмы и контакты · Кабели и провода · Материалы · Соответствия; every chip has a count.
- «Кабели и провода» (04.10): БД.ПРОВ + D-table split into коаксиальные / монтажные / прочие by a rule in
  `references/cables.py`; manual moves in `reference_entries` kind `cable_kind`; D1–D3 edited in the coax tab
  (`edited_in_erp`, survives mirror reload). Storage unchanged (`prov`, `kab_coax`), no migration. Old keys
  `prov`/`kab_coax` open the new tabs.
- One-off mirror dump tabs removed from the UI; rows stay in the DB because the ETK generator and the warehouse read them.
- Used by [[module-techcards]] and [[module-warehouse]]. Decisions: [[runlog-decisions]]. Hub: [[erp-next-hub]].

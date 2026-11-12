---
name: module-techcards
description: Tech cards (ЭТК) and templates — generator port, coax dimensions from the cable, sync scripts
metadata:
  type: project
---

- Coax: D1–D3 come from the cable (СПР.КАБ), the connector carries only lengths L1–L3, L+, L− (PR #65, generator PR #67) → [[module-reference-db]].
- Templates «Шаблоны техкарт» moved under «База данных» → [[module-reference-db]].
- Bulk generation for all projects and SUPPLYMAP issued ТК import → [[techcards-bulk]].
- Owner 25.10.2026 kit facts for the generator: «XP-3»/«XP-6» (and «Оплетка XS-n») = braided sleeving; a kit row's type from the calc can be wrong (725К. «U/UTP 2x2x0,48» typed «Соед» is a cable) — trust the reference title; a ТТК next to soldered USB connectors = shrink over the connector body. Project КД lives in the project's Drive folder (folder_url on the kit card, subfolder «КД» / «КД новое»), not yet in ERP.
- Owner 25.10.2026: marking is printed on heat-shrink tubes (INS_MARK_TUBE_AUTO on ZCUT-RT660, then INS_MARK) — never hand marking with ink/marker; marking is not lacquered (no PLASTIK-71 op) even when a customer drawing asks.
- Owner 25.10.2026: ПМ lugs (ПМ6-6, ПМ10-6, ПМ16-6 — «наконечник медный луженый под пайку») are soldered (SDR_НК) even where a reference title says «Контакт обжимной».
- Owner 25.10.2026: the «Ручная сборка» tab is NOT in use — every ТК goes through the generator tabs (Межплатная / Сложная сборка / КОАКС). Don't prepare «Ручная сборка» drafts — prefill the generator form as the owner's generator draft instead (tool: session scratchpad tcx/, see [[techcards-bulk]]).
- Owner 26.10.2026 USB cable strip: CCCU-4G etc. is a jacketed multicore, not a wire. Jacket strip — USB-A plug 15 mm, the other end (micro-USB or USB-A female) 10 mm; core strip under soldering 3–4 mm. One USB 2.0 solder card (SDR_USB_A_2.0) for USB-A plug/receptacle and micro-USB, pinout per КД.
- Owner 26.10.2026: «нет работы с БД» — the generator must decide wire vs cable (and cores, colours, screen, Ø) from БД.ПРОВ `cable_kind` (wire = монтажный, multi = многожильный, lan, coax), not from name patterns; headless batch must load `prov` like the form.
- Feeds [[module-production]] (operations per project) and [[module-projects]]. Decisions: `docs/migration/RUNLOG.md`. Hub: [[erp-next-hub]].

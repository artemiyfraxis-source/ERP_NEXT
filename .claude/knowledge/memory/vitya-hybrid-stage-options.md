---
name: vitya-hybrid-stage-options
description: "Витя's purchasing/warehouse hybrid is permanent in main since 22.10 (#641) — stage options cut out; label printing goes through a DB queue and a print agent on the warehouse PC"
metadata:
  node_type: memory
  type: project
  originSessionId: 54f356df-788e-49be-8c10-a9e933f86534
  modified: 2026-10-22T11:04:18.422Z
---

PR #627 (21.10.2026) merged `vitya/hybrid` @ dae04c3e into `main`. On 22.10 the owner said «вырезать вообще — мы же
приняли апдейт», and PR #641 removed the stage-options mechanism completely. All 24 blocks are permanent: «Сегодня»,
«Заявки», «Запросы КП», «Счета и поставки», УПД, the document chain, nomenclature, word search, quantities only by
movements, scan, «Отменить последнее», labels, TSD, dark theme, Ctrl+K. The «Было» branches, the sidebar panel,
`ERP_STAGE_OPTIONS` and `stage_options*` are gone. The prod `.env` line was removed 22.10 (copy `.env.bak-*`). The
hand-made `/opt/erp/import/stage/` folder is unused leftover.

**Why:** the owner tested the hybrid on live data and accepted it; a second «old» path per screen was dead weight.
**How to apply:**
- There is no feature-flag mechanism any more. Don't reintroduce per-block toggles for Витя's work; new hybrid work lands as normal PRs on `main`. His `vitya/hybrid` branch / PR #628 is superseded except the «Перенос закупок из Roller_Buyer» commit (b5657470).
- GRN labels: the server never prints. Jobs are queued in `warehouse_label_jobs` (migration 0141). An agent on the warehouse Windows PC (`tools/print-agent/install.ps1 -Token …`, API token scope `labels:print`, TSC MB341 on USB) claims and prints them. «Склад → Оборудование» shows the agent status.
- Label presets are 58×30, 58×40 (default) and 100×50. The prod settings row stayed 58×30 until the storekeeper changes it.
- TSD: `<server>/tsd`, PIN login. Device logs live in `ERP_DEVICE_LOG_DIR` (compose: `/app/data/import/devices`). The Windows device gate and TLS were deleted.
- Related: [[module-procurement]], [[module-warehouse]], [[live-data-protocol]].

---
name: launch-plan
description: ERP_NEXT go-live dates (server 01.10.2026, five blocks by 05.10, second wave 05–09.10) and the cutover plan
metadata:
  type: project
---

Owner, 2026-09-29 16:03:
- Thu 2026-10-01 — server up → [[erp-server]].
- By Mon 2026-10-05 on real data: projects [[module-projects]], warehouse [[module-warehouse]], kit requests and issue with write-off [[module-fulfillment]], shipping [[module-shipping]].
- Week 05–09.10: requests, purchase sheet, receiving → [[module-procurement]].
- Everything else is second priority.

Plan: `docs/launch/CUTOVER.md`; deploy PR #70/#77. Real-data import verified on PostgreSQL (PR #60, #66): `scripts\load-real-data.ps1` / `deploy/load-real-data.sh`.
Warehouse: no freeze day — load now, re-run later tops up the difference from Google (owner 20:19 «грузим сейчас потом догружаем»).
The SUPPLYMAP → ERP port was declared complete 2026-09-29 ~14:00; owner edits since then are PR #46–#89. Hub: [[erp-next-hub]].

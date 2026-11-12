---
name: module-procurement
description: "Requests, supplier purchase orders, receipt stages and stored documents"
metadata:
  type: project
---

- «Заявки» is a list; requests originate from a project calc → [[module-projects]].
- Purchasing groups lines by supplier, invoice/payment belong to the PO → [[supplymap-parity]].
- Invoice text-layer parsing and УПД scans are separate paths; УПД model recognition → [[upd-recognizer-openai]]. УПД limit: 20 MB.
- Receiving stores manufacturer, country and documents; `sync_receipt_stages` advances «В пути» → «Карантин» → «Готово» only forward. Files live in `receiving_files`, not Drive.
- Receiving redesign is on hold; current intake → [[receiving-now-vs-later]]. Stock destination: [[module-warehouse]]. Decisions: `docs/migration/RUNLOG.md`. Hub: [[erp-next-hub]].

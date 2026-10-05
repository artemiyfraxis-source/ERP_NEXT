---
name: supplymap-parity
description: Rule «всё как в Google SUPPLYMAP» plus the list of deliberate differences and out-of-scope blocks
metadata:
  type: feedback
---

> Superseded for new work by [[supplymap-left]] (owner 05.10).

- Owner 2026-09-29: ERP repeats SUPPLYMAP behaviour and look. Agent decisions that diverge are reverted to SUPPLYMAP. Exceptions: obvious SUPPLYMAP bugs, and storage (everything in SQL, nothing on Drive → [[google-wiring]]).
- Deliberate differences: FIFO by ПТ number; UPD file limit 20 MB; invoice recognition only for PDFs with a text layer; fixed bug «возврат поставщику = возврат на склад»; defects keep nature + description. Procurement buyer flow (owner 05.10): «В закупку» lands the row «В работе» for whoever pressed it (no «Неразобранные» step), «В работе» grouped by supplier with a request per group, invoice and «Оплачено» once per order (PO), only «Цена факт» per row — SUPPLYMAP does these per row.
- Out of scope: commercial «Проекты» and «Расчёт и КП» of the old ERP — another block, another programmer (removed, PR PK-4).
- SUPPLYMAP caught up 2026-09-29 (PR #84, pins → d0a030b). Drift report: [[cloud-threads-setup]].
- Box numbers: ERP 000001–099999, SUPPLYMAP 100001+ (Луна in both).

Owner decisions stored on the SUPPLYMAP side → [[supplymap-domain-decisions]]. Hub: [[erp-next-hub]].

---
name: supplymap-parity
description: Rule «всё как в Google SUPPLYMAP» plus the list of deliberate differences and out-of-scope blocks
metadata:
  node_type: memory
  type: feedback
  originSessionId: 588273da-1ef1-4aac-9b53-d023958d79e8
  modified: 2026-10-19T09:27:33.730Z
---

> Superseded for new work (owner 05.10): SUPPLYMAP is legacy — see root CLAUDE.md. Kept for the deliberate differences and box numbers below.

- Owner 2026-09-29: ERP repeats SUPPLYMAP behaviour and look. Agent decisions that diverge are reverted to SUPPLYMAP. Exceptions: obvious SUPPLYMAP bugs, and storage (everything in SQL, nothing on Drive → [[google-wiring]]).
- Deliberate differences: FIFO by ПТ number; UPD file limit 20 MB; invoice recognition only for PDFs with a text layer; fixed bug «возврат поставщику = возврат на склад»; defects keep nature + description. Procurement buyer flow (owner 05.10): «В закупку» lands the row «В работе» for whoever pressed it (no «Неразобранные» step), «В работе» grouped by supplier with a request per group, invoice and «Оплачено» once per order (PO), only «Цена факт» per row — SUPPLYMAP does these per row.
- Out of scope: commercial «Проекты» and «Расчёт и КП» of the old ERP — another block, another programmer (removed, PR PK-4).
- Box numbers: ERP 000001–099999, SUPPLYMAP 100001+ (Луна in both).
- Owner 19.10: there are NO techcards imported from SUPPLYMAP — «мы от него уже ушли». Never plan SUPPLYMAP-specific
  branches/exceptions for techcards; the legacy import («issued outside ERP») and SUPPLYMAP library-reload paths in
  `techcards/application.py` are dead code to delete, not to preserve.

Hub: [[erp-next-hub]].

---
name: supplymap-parity
description: Rule «всё как в Google SUPPLYMAP» plus the list of deliberate differences and out-of-scope blocks
metadata:
  type: feedback
---

- Owner 2026-09-29: ERP repeats SUPPLYMAP behaviour and look. Agent decisions that diverge are reverted to SUPPLYMAP. Exceptions: obvious SUPPLYMAP bugs, and storage (everything in SQL, nothing on Drive → [[google-wiring]]).
- Deliberate differences: FIFO by ПТ number; UPD file limit 20 MB; invoice recognition only for PDFs with a text layer; fixed bug «возврат поставщику = возврат на склад»; defects keep nature + description.
- Out of scope: commercial «Проекты» and «Расчёт и КП» of the old ERP — another block, another programmer (removed, PR PK-4).
- SUPPLYMAP caught up 2026-09-29 (PR #84, pins → d0a030b). Drift report: [[cloud-threads-setup]].
- Box numbers: ERP 000001–099999, SUPPLYMAP 100001+ (Луна in both).

Owner decisions stored on the SUPPLYMAP side → [[supplymap-domain-decisions]]. Hub: [[erp-next-hub]].

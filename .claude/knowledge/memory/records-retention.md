---
name: records-retention
description: "Owner 26.10.2026 — УПД/счета kept 3 years, техкарты/документация 5 years (in the database); backups only a week old"
metadata:
  type: project
---

Owner 26.10.2026: «счета упд хранятся 3 года техкарты и документация 5 лет бекап недельной давности».

**Why:** PDFs of УПД/счета live inside the database (Roller_Buyer files ~1.3 GB were about to be attached), so long backup
retention multiplies that size; the records' lifetime is the database's job, not the backups'.
**How to apply:** backups — 7 daily on the server (`KEEP_DAILY=7`, weekly/monthly off) and 7 on the owner's PC
(`pull-backup.ps1 -KeepDaily 7`), shipped 26.10. Never delete УПД/счета younger than 3 years or техкарты/КД/ТД younger
than 5; a purge job past those ages is not built (nothing reaches 3 years before 2029) — if built, it drops file
content only after the age, dry-run first. Supersedes the 14.09 «36 monthly backups = 3 years of traceability» rule.
Related: [[erp-server]], [[live-data-protocol]].

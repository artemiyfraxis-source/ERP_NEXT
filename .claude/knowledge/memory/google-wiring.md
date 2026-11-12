---
name: google-wiring
description: "Google calc access and project Drive documentation; ERP data stays in SQL"
metadata:
  type: project
---

- ERP operational records live in PostgreSQL. Project КД remains in the project's Google Drive folder (owner 25.10), not the ERP DB → [[module-techcards]]. Never write to live legacy warehouse sheets.
- Calc-sheet reading uses the service account `erp-reader@techmap-498119.iam.gserviceaccount.com` in GCP `techmap-498119`; `fraxis-erp@evident-league` is not ours.
- КД root «!Проекты»: `1cPlM5X6ZSdiPpSajOw1YvBYfOdqGdzfA` in the owner's Drive.
- Credentials stay outside git. Check service-account access with Actions `google-access` or `python -m app.modules.requests.google_check`; project-folder OAuth access is a separate concern.
Related: [[module-projects]], [[erp-server]]. Hub: [[erp-next-hub]].

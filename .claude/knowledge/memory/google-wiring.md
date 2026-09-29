---
name: google-wiring
description: Google is read-only and being left — service account, KD folder id, key location, how to check access
metadata:
  type: project
---

- Leaving Google (owner 2026-09-29 20:20 «уйти от диска всю бы надо на sql»): everything is stored in ERP PostgreSQL, nothing is written to Google/Drive. Project KD lives in the DB (one-off import of old Drive files). `ERP_KD_DRIVE_OAUTH_JSON` and owner OAuth are no longer needed.
- Service account `erp-reader@techmap-498119.iam.gserviceaccount.com` (Editor in GCP techmap-498119; project owner tit1963yt@gmail.com). Sheets + Drive API on. `fraxis-erp@evident-league` is not ours.
- KD folder «!Проекты» `1cPlM5X6ZSdiPpSajOw1YvBYfOdqGdzfA` (My Drive of artemiyfraxis@gmail.com).
- Key: project uploads `/mnt/project-files/uploads/hearth/be8cdc4b-…` — never in git. Cloud auto-mode forbids running imports with it; real data is loaded from the owner's PC or the server → [[erp-server]].
- Check: Actions → google-access, or `python -m app.modules.requests.google_check`.

Used by: [[module-projects]] (calc link), [[module-warehouse]] (top-up import). Hub: [[erp-next-hub]].

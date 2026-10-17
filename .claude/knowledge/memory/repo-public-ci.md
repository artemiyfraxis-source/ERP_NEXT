---
name: repo-public-ci
description: "ERP_NEXT repo is PUBLIC since 10.10.2026 (owner's decision, for free GitHub Actions); owner wants no extra caution about it"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 4e05f939-5a52-41de-a5cc-05dc983852b3
  modified: 2026-10-10T15:44:35.342Z
---

On 10.10.2026 GitHub Actions stopped starting jobs (billing / spending limit). The owner made
`artemiyfraxis-source/ERP_NEXT` public himself to get free Actions minutes.

**Why:** owner's call — «никто не будет заходить», nothing critical in the repo; he does not want warnings about it.
**How to apply:** treat the repo as before — commit docs, reports and data as usual, don't lecture about publicity.
Only real credentials (passwords, API keys, the server SSH key, Google service-account JSON) stay out of git, as they
always did. RULES.md «Repo stays private» line updated to match. Related: [[erp-merge-gotchas]].

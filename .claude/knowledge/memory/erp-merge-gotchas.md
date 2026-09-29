---
name: erp-merge-gotchas
description: Merging ERP_NEXT branches in the cloud — CRLF whole-file conflicts, shallow SUPPLYMAP breaks architecture tests, Python 3.12
metadata:
  type: reference
---

- `.gitattributes` has `* -text`; some files (ARCHITECTURE.md, earlier api.ts, App.tsx) are CRLF and agents save LF → whole-file conflict. Take main's version, strip CR, apply `git diff --ignore-cr-at-eol base..theirs -- file` via `patch -p1`, restore CR if main had it.
- Architecture tests need full SUPPLYMAP history; `--depth 1` gives false «Not a valid commit name» → `git fetch --unshallow` ([[cloud-threads-setup]]).
- `backend/.venv` must be Python 3.12 (3.11 fails collecting tests on f-strings): `uv venv -p /usr/bin/python3.12 backend/.venv`.
- Several sessions merge in parallel: pull main, renumber your migration after the head, `alembic heads` = 1, full `npm run check`, merge with `expectedHeadSha` → [[owner-working-rules]].
Hub: [[erp-next-hub]].

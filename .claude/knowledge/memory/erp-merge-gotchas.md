---
name: erp-merge-gotchas
description: Merging ERP_NEXT branches in the cloud — CRLF whole-file conflicts, shallow SUPPLYMAP breaks architecture tests, Python 3.12
metadata:
  type: reference
---

- `.gitattributes` has `* -text`; some files (ARCHITECTURE.md, earlier api.ts, App.tsx) are CRLF and agents save LF → whole-file conflict. Take main's version, strip CR, apply `git diff --ignore-cr-at-eol base..theirs -- file` via `patch -p1`, restore CR if main had it.
- `backend/.venv` must be Python 3.12 (3.11 fails collecting tests on f-strings): `uv venv -p /usr/bin/python3.12 backend/.venv`.
- Several sessions merge in parallel: pull main, renumber your migration after the head, `alembic heads` = 1, full `npm run check`, merge with `expectedHeadSha` → [[owner-working-rules]].
- After a conflicted merge, list every unmerged file (`git diff --name-only --diff-filter=U`) and `git grep -nE '^(<<<<<<<|=======|>>>>>>>)( |$)'` before `git add -A`: `npm run check` does not read `deploy/*.sh` or `.ps1`, so markers there ship green (#118 broke `deploy/load-real-data.sh`, #120 fixed it). `bash -n deploy/*.sh` catches it.
- `WarehousePage.test.tsx` «warns instead of printing when nothing is waiting to be put away» can miss its `role="alert"` under full-gate load (2026-09-30, passed alone and on a rerun of `npm test`); rerun before chasing it.
Hub: [[erp-next-hub]].
- Agent worktrees link `backend/.venv` and `frontend/node_modules` to the main checkout: removing one by hand → [[worktree-symlink-removal]].

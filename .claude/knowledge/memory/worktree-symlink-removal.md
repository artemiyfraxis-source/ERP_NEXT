---
name: worktree-symlink-removal
description: Worker worktrees carry junctions to the main node_modules and backend/.venv — unlink them before removing a worktree, or the main copies get wiped
metadata:
  type: feedback
---

`.claude/settings.json` → `worktree.symlinkDirectories` links `frontend/node_modules` and `backend/.venv` of every
worker worktree to the MAIN checkout. Removing a worktree by hand with `git worktree remove --force` or
`Remove-Item -Recurse` walks through those junctions and empties the main copies (10.10.2026: main `node_modules`
wiped, restored with `npm ci`; `.venv` survived by luck).

**Why:** recursive deletes on Windows follow junctions; only `rmdir <link>` removes just the link.

**How to apply:** before removing a worker worktree by hand, run
`cmd /c rmdir "<wt>\frontend\node_modules"` and `cmd /c rmdir "<wt>\backend\.venv"`, then
`git worktree remove --force <wt>` and `git branch -D <branch>`. Never add your own junctions to a throwaway
worktree — run `npm ci` there instead. Cross-project version: brain note worktree-junction-wipe.
Hub: [[erp-next-hub]]. Related: [[parallel-sessions-worktrees]], [[erp-merge-gotchas]].

Second trap (10.10.2026, PR #285 → fixed by #286): `git add -A` inside a worker worktree staged those two junctions as
symlink blobs (`.gitignore` had `node_modules/` and `backend/.venv/` with a trailing slash, which matches directories
only) and they reached `main`. Stage explicit paths in worker worktrees (`git add frontend/src backend/app …`), never
`-A`; `.gitignore` now also lists both names without the slash.

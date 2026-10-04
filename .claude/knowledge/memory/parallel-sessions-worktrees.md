---
name: parallel-sessions-worktrees
description: "Several Claude sessions share the ERP_NEXT main checkout and .claude/worktrees — never remove a worktree or commit on a branch you didn't create"
metadata:
  node_type: memory
  type: feedback
  originSessionId: be43bccf-ce4c-4562-96ff-e0fbf3ca21a2
  modified: 2026-10-04T15:27:30.874Z
---

On 2026-10-04 two orchestrator sessions ran at once on the owner's PC (erp-next-5b and erp-next-e7). e7 saw a
worker worktree with no commits yet, took it for orphaned and ran `git worktree remove --force` + `branch -d` on it —
the worker lost its in-progress work. e7 then redid the same screen and committed onto 5b's branch in the shared main
checkout, which produced a 9-file conflict and two competing `0080` designs.

**Why:** worktrees and the main checkout's current branch are shared state between sessions; a worktree with no
commits is normal for a worker mid-task. On Windows `worktree remove --force` often fails half-way (permission denied),
leaving a gutted directory.

**How to apply:**
- Before removing a worktree or deleting a branch, run `ListAgents` and remove only worktrees your own session's
  workers created (names from your own Agent results). Locked worktrees (`claude agent … (pid)`) are live — never force.
- Don't commit on a branch another session checked out; branch off instead. If a peer's commits show up on your
  branch, abort, `SendMessage` the peer, and rebuild on a new branch — don't `reset --hard` shared history.
- Unsure whose a worktree/branch is → ask the owning session via `SendMessage` (ListAgents) before touching it.
- An owner's screenshot of another session's report with no text is context, not an assignment to take that work over.
- Tell workers to commit a WIP early so a lost worktree doesn't lose everything.
- Related: [[erp-merge-gotchas]], [[owner-working-rules]].

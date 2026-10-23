---
name: parallel-sessions-worktrees
description: "Several Claude sessions share the ERP_NEXT main checkout and .claude/worktrees — never remove a worktree or commit on a branch you didn't create"
metadata:
  node_type: memory
  type: feedback
  originSessionId: be43bccf-ce4c-4562-96ff-e0fbf3ca21a2
  modified: 2026-10-23T10:20:56.651Z
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
- Before rebasing/releasing the main checkout's branch, check `git reflog` for fresh merges: a live peer session may be
  orchestrating on it (17.10: batch-1017b was rebased under erp-next-23). Release from a separate branch + worktree.
- `erp-worker` / `isolation: "worktree"` can refuse to start when the session cwd's drive letter case (`c:` vs `C:`)
  differs from git's path. Workaround: `git worktree add ../erp-<task>` + junctions, run a general-purpose agent there;
  unlink the junctions before removing that worktree ([[worktree-symlink-removal]]).
- Workers that start the app need their own ports (14.10: three workers collided on 8021/8031/8047) — give each
  `ERP_API_PORT` / `ERP_WEB_PORT` in the brief (defaults 8021/5176 belong to the owner's preview, [[erp-local-demo]]).
- `isolation: "worktree"` branches from the HEAD of the session's CURRENT cwd (`worktree.baseRef: "head"`). After a
  `cd` into a worker worktree the cwd can stay there, and new workers then silently start from that worker's branch
  (22.10: the label-presets worker got the ТД branch instead of `stage-out-1022` and redid the label template). In
  every brief, name the base explicitly: `git checkout -B <topic> <base-branch>` as the worker's first step.
- A claim sent to the sessions `ListAgents` shows is not enough. On 23.10 another owner session (not in the list) built
  and released the same feature, scan-to-ERP #654 with migration 0145, while this session built it a second time; the
  duplicate was dropped. Right before dispatching workers, and again before integrating, run `git fetch` +
  `git log origin/main -15` + `gh pr list`. Look for the topic's words and the migration number.
- Related: [[erp-merge-gotchas]], [[owner-working-rules]].

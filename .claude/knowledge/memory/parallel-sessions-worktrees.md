---
name: parallel-sessions-worktrees
description: "Codex worker worktrees: ownership, explicit base, ports, reflog and safe cleanup"
metadata:
  node_type: memory
  type: feedback
  originSessionId: be43bccf-ce4c-4562-96ff-e0fbf3ca21a2
  modified: 2026-11-12T12:00:00.000Z
---

- Main creates a task-owned worktree per Codex worker from an explicit verified base SHA; assign absolute cwd, branch, exclusive files and ports. Setup never changes Git history.
- Before dispatch/integration, check open PRs and fresh `origin/main` for duplicate work. Before commit/merge/push, recheck branch, worktree ownership and recent `git reflog`; another session may be active outside the agent list. Confirm PR `headRefName` before editing it.
- Never remove another session's worktree/branch, force removal, or commit onto its branch. Unknown ownership is a blocker; preserve its work and coordinate with the owning session.
- Main owns commits and integration; Codex workers never commit/push/merge. Preserve uncommitted changes and record handoff state in root gitignored `TASKS.md`.
- Workers starting apps get distinct `ERP_API_PORT` / `ERP_WEB_PORT`; preview defaults belong to the owner → [[erp-local-demo]].
- Worktree cleanup and junction safety have one home → [[worktree-symlink-removal]].
- Claude subagent worktrees are fallback only when Codex is unavailable; main still creates/verifies the exact base/cwd and exclusive ownership. Normalize Windows drive-letter case if isolation rejects a path.
- Before overwriting a shared generator draft, compare current version/updated_at with your last write and save a copy. A newer draft requires the owner's decision.
Related: [[erp-merge-gotchas]], [[owner-working-rules]].

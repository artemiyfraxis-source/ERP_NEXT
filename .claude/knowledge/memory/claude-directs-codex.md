---
name: claude-directs-codex
description: "Owner 11.11.2026 — Claude ($20 plan) orchestrates, Codex CLI ($200 plan) does searches, code, tests and first-pass review via codex exec"
metadata:
  node_type: memory
  type: user
  originSessionId: 3299b8a4-260f-413b-822c-c78a05c76e54
  modified: 2026-11-11T19:31:26.493Z
---

Owner's subscriptions since 11.11.2026: Claude $20 (tight quota), OpenAI Codex $200 (large quota). «Ты будешь управлять им»:
Claude main is the orchestrator, Codex CLI the workhorse.

- Codex CLI 0.167.1 installed globally via npm (`codex` on PATH), logged in with ChatGPT; shares `~/.codex` with the
  Codex desktop app (default model there gpt-5.6-sol/high; workers pass `-m gpt-6.1-sol` explicitly).
- `codex exec -s workspace-write -C <dir>` works from Claude's Bash tool on this PC (Windows elevated sandbox), ~30 s for a trivial task.
- Codex can't commit in a git worktree (main `.git` is outside its writable root) — main reviews and commits.

**Why:** Claude tokens are the scarce resource; spending them on file reading and code writing burns the 5-hour window.
**How to apply:** follow `orchestrate` skill «Budget routing» and `cross-review`; Claude subagents only as a fallback
when Codex is down. See [[owner-working-rules]] (token cost) and [[parallel-sessions-worktrees]].

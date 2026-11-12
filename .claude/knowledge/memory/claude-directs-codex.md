---
name: claude-directs-codex
description: "Owner 11.11.2026 — Claude ($20 plan) orchestrates, Codex CLI ($200 plan) does searches, code, tests and first-pass review via codex exec"
metadata:
  node_type: memory
  type: user
  originSessionId: 3299b8a4-260f-413b-822c-c78a05c76e54
  modified: 2026-11-12T08:17:03.868Z
---

Owner's subscriptions since 11.11.2026: Claude $20 (tight quota), OpenAI Codex $200 (large quota). «Ты будешь управлять им»:
Claude main is the orchestrator, Codex CLI the workhorse.

- Codex CLI 0.167.1 installed globally via npm (`codex` on PATH), logged in with ChatGPT; shares `~/.codex` with the
  Codex desktop app (its default stays gpt-5.6-sol/high). Owner 11.11: «свой подход к каждой задаче» — every
  `codex exec` picks model+effort from the per-task table in `orchestrate` «Budget routing» (luna / terra / sol / astra,
  low…max, never ultra); one model for everything is wrong.
- Launch `codex exec` from Claude's **PowerShell** tool only: from Git Bash every sandboxed shell fails with Windows
  error 740 («требует повышения») in every model and sandbox mode, while the model still claims success via
  apply_patch — a «worked» smoke test from Bash was false. Verified 11.11.2026.
- `-s read-only` limits only shell commands: account connectors (gmail, drive, github), plugins, sub-agents and
  config MCP servers stay callable unless disabled per call (`--disable apps/plugins/multi_agent/...`,
  `-c mcp_servers.<name>.enabled=false`). `--ignore-user-config` does NOT help: the connectors come from the account and the shell breaks.
- `workspace-write` denies writes outside the workdir but allows `%TEMP%`. Pass the brief by path
  («Read the task brief at … and follow it»), `$null |` as stdin. Codex never commits — main reviews and commits.

- 12.11.2026 owner screenshot «чет пипец»: 4 parallel Codex workers each ran `npm run test:backend`
  (`scripts/run-backend-tests.mjs` uses pytest `-n auto` → 42 python processes per run) → 95 % CPU, ~27 GB of 31 GB.
  Limit: ≤ 3 editing workers, affected tests only with `-n 4`, one full gate at a time (verifier only). Owner worried
  about speed: model time dominates, 4 parallel full suites were not faster (shared cores + RAM paging).

**Why:** Claude tokens are the scarce resource; spending them on file reading and code writing burns the 5-hour window.
**How to apply:** follow `orchestrate` skill «Budget routing» and `cross-review`; Claude subagents only as a fallback
when Codex is down. See [[owner-working-rules]] (token cost) and [[parallel-sessions-worktrees]].

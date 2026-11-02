---
name: erp-orchestrate
description: Plan, delegate and integrate ERP_NEXT work with Codex subagents when the owner requests orchestration or a change spans modules, domain invariants or migrations.
---

# ERP orchestration in Codex

Read the shared workflow at `../../../.claude/skills/orchestrate/SKILL.md` and the relevant role cards in
`../../../.claude/agents/`: `erp-planner.md`, `erp-worker.md` and `erp-verifier.md`.
Resolve these paths relative to this skill's directory. The shared workflow owns the process; do not copy it here.

Use the subagent tools actually exposed in this session. Claude YAML fields such as `model: opus`, `tools` and
`isolation: worktree` are Claude configuration, not active Codex settings. Pass the shared role's instructions in
the task brief when a native ERP role is unavailable. Create and assign each editing worker's worktree explicitly.

Default to the parent's model and effort. The owner may authorize task-specific overrides; select only model and
effort values accepted by the current runtime, and include the selected values in the dispatch record.
An override with a self-contained brief uses `fork_turns: none`; a full-history fork inherits the parent's settings.
Keep the root session's model and user settings unchanged unless the owner explicitly requests a supported change.

# ERP_NEXT — shared instructions for Codex

Read `~/.claude/CLAUDE.md` for the owner's global rules, then this repository's `CLAUDE.md` for ERP rules.
The `.claude/` directory also belongs to Codex: it contains the shared roles, workflows and project memory.
Use `.agents/skills/erp-orchestrate/SKILL.md` when the owner requests orchestration or a multi-file domain change.
Use `.agents/skills/cross-review/SKILL.md` to have Claude Code review your diff (protocol: `CLAUDE.md` «Claude ⇄ Codex»).
Read the relevant project notes through `.claude/knowledge/memory/MEMORY.md`.
Access the cross-project brain only through Obsidian MCP, as required by the global rules.

Preserve another session's uncommitted changes and use a clean worktree from a verified base for your task.

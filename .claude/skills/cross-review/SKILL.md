---
name: cross-review
description: Запуск перекрестного код-ревью между Claude Code и Codex CLI по текущему git diff. Use after finishing a feature or a hard refactor, or on «перекрёстное ревью», «пусть Codex проверит», «пусть Claude проверит», «cross-review».
---

# Cross-review: Claude Code ⇄ Codex CLI

The agent that wrote the change asks the other CLI for an independent, read-only review of the diff.
Running in Claude Code → the reviewer is `codex`; running in Codex CLI → the reviewer is `claude`.
The reviewer never edits files; the author decides what to fix (see `receiving-code-review`).
Budget (owner, 11.11.2026: Claude $20, Codex $200): when Codex wrote the code under Claude's orchestration,
Claude main reads the diff itself as part of integration and the independent review is a *fresh* read-only
Codex run of this brief; call `claude -p` from Codex only for small diffs.

## 1. Collect the change
```sh
git status --short            # "??" lines are new files: git diff does NOT show them
git diff --stat HEAD          # uncommitted work; for a branch use: git diff --stat main...HEAD
```
Pick the range: uncommitted (`git diff HEAD`) or the branch (`git diff main...HEAD`). List untracked files
separately for the reviewer. Nothing changed and no untracked files → stop and say so.
Note the task in one or two sentences (what the change must do) — the reviewer needs intent, not just the diff.

## 2. Call the other agent (non-interactive, read-only)
Write the brief to `<scratch>/review.brief.md` (English):

```text
You are an independent reviewer of ERP_NEXT (rules: CLAUDE.md, .claude/team/RULES.md, ARCHITECTURE.md).
Task of the change: <intent>.
Run `git diff <range>` yourself and read the touched files as needed. Also read these new untracked files
in full: <list or "none">. Do NOT edit anything.
Find: logic bugs, data/stock/money invariant breaks, security problems (authz, injection, secrets),
architecture violations (module boundaries in module-map.json, schema outside Alembic, edited migrations
already on main), missing tests, performance bottlenecks.
Report each finding as: severity (blocker/major/minor) | file:line | problem | concrete failure scenario | fix.
No findings → say "no findings" and list what you checked. Answer in Russian.
```

- **From Claude Code (reviewer = Codex): PowerShell tool, `run_in_background`.** Not Git Bash: under its
  environment Codex's Windows sandbox can't start a shell (error 740, verified 11.11.2026).
  ```powershell
  $null | codex exec --disable apps --disable plugins --disable multi_agent --disable computer_use --disable browser_use --disable in_app_browser --disable image_generation -c mcp_servers.github.enabled=false -c mcp_servers.obsidian.enabled=false -c mcp_servers.node_repl.enabled=false -c mcp_servers.chrome-devtools.enabled=false -m gpt-6.1-sol -c 'model_reasoning_effort="high"' -s read-only -C "<repo>" -o "<scratch>\review.out.md" "Read the task brief at <scratch>\review.brief.md and follow it." *> "<scratch>\review.log"
  ```
  `-s read-only` limits only shell commands; the `--disable`/`mcp_servers.*` flags remove the account
  connectors, plugins, sub-agents and MCP servers that could act outside it. The `mcp_servers.*` list must
  cover every `[mcp_servers.<name>]` table in `~/.codex/config.toml` and nothing else (see `orchestrate` «Budget routing»). Model: `gpt-6-astra` instead of
  `gpt-6.1-sol` when the change touches migrations, stock, money or auth. Read only `review.out.md`.
- **From Codex CLI (reviewer = Claude):** write `<scratch>\no-hooks.json` = `{"disableAllHooks": true}` (project
  SessionStart hooks would otherwise install deps and copy DBs), then
  `claude -p "Read the task brief at <scratch>\review.brief.md and follow it." --permission-mode plan --strict-mcp-config --settings "<scratch>\no-hooks.json" --allowedTools "Bash(git diff:*) Bash(git status:*) Bash(git log:*) Read Grep Glob"`
  The prompt goes first: `--allowedTools` is variadic and would swallow it. `--strict-mcp-config` without
  `--mcp-config` loads no MCP servers. PowerShell has no `<` redirection; the brief by path keeps Cyrillic intact.

Reviewer CLI missing → `npm install -g @openai/codex` / `npm install -g @anthropic-ai/claude-code`, or fall back to the local
`code-reviewer` role in `.claude/skills/requesting-code-review/` and say that the review was not cross-agent.

## 3. Report
Verify each finding against the code before accepting it (reviewers hallucinate lines). Then print:

```
Перекрёстное ревью (<reviewer>, диапазон <range>, N файлов)
Блокеры: … | Существенные: … | Мелкие: …
Подтверждено / отклонено (почему) / требует решения владельца
```
Fix confirmed blockers before claiming done; the review never replaces `npm run check` or the verifier.

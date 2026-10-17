# Third-party skills — sources & licenses

Copied 2026-09-29 from SUPPLYMAP `.claude/skills` (commit d0a030b) — only the skills that fit ERP_NEXT;
the table below is SUPPLYMAP's and also names skills not copied here. `erp-screens` is ERP_NEXT's own.

Removed 2026-10-06 (token economy, duplicates of `orchestrate` / `diagnosing-bugs` or legacy): `migrate-next`,
`migrate-cloud`, `supplymap-pipeline`, `supplymap-schema`, `frontend-design`, `webapp-testing`, `executing-plans`,
`writing-plans`, `subagent-driven-development`, `dispatching-parallel-agents`, `systematic-debugging` — see git history.

Added 2026-09-30: `design-critique` from github.com/anthropics/knowledge-work-plugins (`design/skills`,
commit da38ec1), Apache-2.0 (`LICENSE.txt`). Changed: the CONNECTORS.md pointer line replaced by a pointer to
`erp-screens`. `frontend-design` description narrowed so it does not trigger on ERP screens.

Skills imported into `.claude/skills/` from public repos (2026-07-23/24 night run №2).
Bodies are agent-facing guidance; bundled scripts are NOT auto-run.

| Skill(s) | Source repo | License |
|---|---|---|
| frontend-design, webapp-testing | github.com/anthropics/skills | Anthropic (see each `LICENSE.txt`) |
| verification-before-completion, brainstorming, executing-plans, writing-plans, systematic-debugging, test-driven-development, requesting-code-review, receiving-code-review, subagent-driven-development, using-git-worktrees, finishing-a-development-branch, dispatching-parallel-agents | github.com/obra/superpowers | MIT © 2025 Jesse Vincent |
| obsidian-markdown | github.com/kepano/obsidian-skills | see repo LICENSE (kepano) |
| gstack-design-review, gstack-design-consultation | github.com/garrytan/gstack | see repo (garrytan); `.tmpl`/`sections` kept as-is |
| code-review-checklist | github.com/awesome-skills/code-review-skill | see repo LICENSE |

Native repo skills (code-review, codebase-design, diagnosing-bugs, domain-modeling, grilling,
implement, prototype, research, wayfinder, writing-great-skills, to-spec, to-tickets, triage,
supplymap-*) are project-owned — not third-party.

## ui-guidelines-audit
Adapted from https://github.com/vercel-labs/agent-skills (`skills/web-design-guidelines`, commit 063bee9) and
https://github.com/vercel-labs/web-interface-guidelines (`command.md`, pinned 10.10.2026). MIT License, © Vercel.

## erp-dense-ui
Adapted from https://github.com/educlopez/ui-craft (`skills/ui-craft-dense-dashboard`, commit ceecc8e).
MIT License, © educlopez.

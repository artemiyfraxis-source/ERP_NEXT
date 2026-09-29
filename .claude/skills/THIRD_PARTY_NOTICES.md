# Third-party skills — sources & licenses

Copied 2026-09-29 from SUPPLYMAP `.claude/skills` (commit d0a030b) — only the skills that fit ERP_NEXT;
the table below is SUPPLYMAP's and also names skills not copied here. `migrate-next`, `migrate-cloud` are ERP_NEXT's own.

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

---
name: team-two-devs
description: Since 05.10.2026 Витя (GitHub h4styq) works on ERP_NEXT via his own Claude with his own rules; shared contract is .claude/team/RULES.md
metadata:
  type: project
---

On 2026-10-05 the owner gave Витя (GitHub `h4styq`, write collaborator) access; Витя drives his own Claude and
"works differently", so the owner wanted separate rule sets, not his `CLAUDE.md` imposed on Витя.

Setup: shared contract `.claude/team/RULES.md` (imported by root `CLAUDE.md`); Витя excludes root `CLAUDE.md`
via `claudeMdExcludes` in his gitignored `.claude/settings.local.json` and keeps his rules in `CLAUDE.local.md`
(gitignored) or `.claude/team/vitya/`. The account has no GitHub Pro, so `main` can't be branch-protected and it
auto-deploys at 02:00/06:00 MSK. Same day the owner decided Витя also releases: each merges their own PR with green
CI and runs «Обновить сервер» (RULES.md «Releasing»). `guard-danger.mjs` denies only direct push to `main`,
`push.ps1` and server ssh to non-owners, and merge/server update to unknown identities.

**Why:** two humans' agents on one repo with a live auto-deploying `main`.
**How to apply:** owner sessions never touch `vitya/*` branches or `.claude/team/vitya/`; before taking a task,
check open PRs for the same module (draft PR = claim). Upgrading to GitHub Pro would allow real protection of
`main`. Related: [[parallel-sessions-worktrees]], [[owner-working-rules]], [[erp-server]].

# ERP_NEXT — shared rules for both developers (owner, 05.10.2026)

The only rules every session on this repo follows, whoever drives it. Everything else is personal:
- the owner (Артемий, GitHub `artemiyfraxis-source`, git email `artemiyfraxis@gmail.com`) — root `CLAUDE.md`,
  his skills/agents/memory under `.claude/`;
- Витя (GitHub `h4styq`) — his own `CLAUDE.local.md` (gitignored) and, if he wants them in git, files under
  `.claude/team/vitya/`. His `.claude/settings.local.json` excludes the owner's `CLAUDE.md` (`claudeMdExcludes`).

Who you work for = `git config user.email`: the owner's email → owner session; anything else → Витя's session.

## Production
The Fraxis shop ERP, live since 05.10.2026: FastAPI + SQLAlchemy/Alembic backend, React/TS (Vite) frontend,
PostgreSQL in production, SQLite in tests. `main` auto-deploys to the shop server at 02:00 and 06:00 MSK, and the
GitHub plan can't protect it — so:
- **Only the owner's sessions touch `main`**: push to it, `gh pr merge`, the «Обновить сервер» workflow,
  `deploy/push.ps1`, server ssh. Витя's sessions work on `vitya/<topic>` and stop at a ready PR; the owner
  reviews and merges. `.claude/hooks/guard-danger.mjs` denies the rest for any non-owner identity.

## Not getting in each other's way
- **Claim before code.** First step of a task: `gh pr list --state open --json number,title,headRefName,files`.
  If an open PR already changes the same module (`backend/app/modules/<m>`, its screen, its tests), take
  something else or tell your human. Then push the branch and open a **draft PR** right away, title
  `[<module>] <what>` — the draft is the claim the other side sees. One topic per branch, small PRs.
- **Branches are owned by prefix**: `vitya/*` — Витя's; everything else (`cloud/*`, `release-*`,
  `worktree-agent-*`, …) — the owner's. Never commit on, rebase, force-push or delete the other side's branch.
- **Personal files are personal**: the owner's `CLAUDE.md`, `.claude/skills`, `.claude/agents`,
  `.claude/knowledge/memory`, `docs/migration/BACKLOG.md` / `RUNLOG.md`, `docs/audit/` and the migration conveyor
  (`/migrate-next`, `/migrate-cloud`) belong to the owner; `.claude/team/vitya/` to Витя. Don't edit or run the
  other side's. This file and the hooks change only by agreement (PR reviewed by the owner).

## Repo invariants
- Gate: `npm run check` (repo root) — architecture tests + backend pytest + frontend tests + build. CI runs the
  same minus architecture tests (they need a sibling `../SUPPLYMAP` checkout; without it, skip them locally).
  A PR with red `check` is not merged.
- Python 3.12 venv in `backend/.venv` (`pip install -r requirements.lock.txt`), frontend `npm ci` in `frontend/`.
- Schema only via Alembic migrations; the app never creates tables. Never edit a migration already on `main` —
  add a new one after the head. One head: right before merge, rebase on fresh `main`; if `main` gained a
  migration meanwhile, re-parent/renumber yours (not on `main` yet, so allowed) and rerun the gate
  (`backend/tests/test_alembic_single_head.py`).
- Repo stays private. No secrets in git. Never write to live Google data (SUPPLYMAP warehouse sheets).
- Commits are authored by the human whose session it is, under their own git identity. **No
  `Co-Authored-By: Claude`, no "Generated with Claude Code"** in commits or PRs.
- PR ready = rebased on `main`, gate green, body says what changed, which module, migration yes/no, how to
  verify. PRs, commit messages and UI text in Russian.

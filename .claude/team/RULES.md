# ERP_NEXT — shared rules for both developers (owner, 05.10.2026)

The only rules every session on this repo follows, whoever drives it. Everything else is personal:
- the owner (Артемий, GitHub `artemiyfraxis-source`, git email `artemiyfraxis@gmail.com`) — root `CLAUDE.md`,
  his skills/agents/memory under `.claude/`;
- Витя (GitHub `h4styq`) — his own `CLAUDE.local.md` (gitignored) and, if he wants them in git, files under
  `.claude/team/vitya/`. His `.claude/settings.local.json` excludes the owner's `CLAUDE.md` (`claudeMdExcludes`).

Who you work for = `git config user.email`: the owner's email → owner session; anything else → Витя's session
(Витя's: `tektra.real.tektra@gmail.com`, or `gh api user` = `h4styq`).

## Production
The Fraxis shop ERP, live since 05.10.2026: FastAPI + SQLAlchemy/Alembic backend, React/TS (Vite) frontend,
PostgreSQL in production, SQLite in tests. `main` auto-deploys to the shop server at 02:00 and 06:00 MSK, and the
GitHub plan can't protect it — so `main` changes only through a merged PR with green CI, never a direct push.

## Testing
- Locally first: `npm run check`, then the app itself — `.claude/launch.json` (`erp-api` on 8021 over
  `backend/preview.db`, `erp-web` on 5176, login `preview` / `Preview-2026!`) or `scripts/demo/run-demo.ps1`
  (seeded `backend/demo.db`). Screens: before/after shots with `scripts/demo/shoot.mjs`.
- On the server after a release: https://erp-server.tail811f20.ts.net (public via Tailscale Funnel, own login).
  Production holds real stock and orders — test with actions you can undo, never bulk edits or deletions.

## Releasing (both developers)
1. PR ready (see the end of this file), CI `check` green, rebased on fresh `main`.
2. Tell the other one in the chat: «выпускаю #N». Don't release while the other one's release is under way.
3. `gh pr merge <N> --merge --delete-branch`.
4. `gh workflow run update-server.yml` — the server backs up the DB, takes `main`, checks health within ~2–5 min.
   Without it the release goes out by itself at 02:00 / 06:00 MSK.
5. Check the screen you changed on https://erp-server.tail811f20.ts.net; if it broke, say so in the chat at once — the owner rolls back.
Direct push to `main`, `deploy/push.ps1` and ssh to the server stay the owner's (`guard-danger.mjs` enforces it for
other identities).

## Not getting in each other's way
- **Claim before code.** First step of a task: `gh pr list --state open --json number,title,headRefName,files`.
  If an open PR already changes the same module (`backend/app/modules/<m>`, its screen, its tests), take
  something else or tell your human. Then push the branch and open a **draft PR** right away, title
  `[<module>] <what>` — the draft is the claim the other side sees. One topic per branch, small PRs.
- **Your PRs are yours to merge**: each one merges and releases only their own PRs; the other side's PR — leave
  a review comment instead.
- **Branches are owned by prefix**: `vitya/*` — Витя's; everything else (`cloud/*`, `release-*`,
  `worktree-agent-*`, …) — the owner's. Never commit on, rebase, force-push or delete the other side's branch.
- **Personal files are personal**: the owner's `CLAUDE.md`, `.claude/skills`, `.claude/agents`,
  `.claude/knowledge/memory`, `docs/migration/BACKLOG.md` / `RUNLOG.md`, `docs/audit/` and the migration conveyor
  (`/migrate-next`, `/migrate-cloud`) belong to the owner; `.claude/team/vitya/` to Витя. Don't edit or run the
  other side's. This file and the hooks change only by agreement (PR reviewed by the owner).

## Repo invariants
- Gate: `npm run check` (repo root) — architecture tests + backend pytest + frontend tests + build. CI runs the
  same gate, architecture tests included. A PR with red `check` is not merged. The gate proves logic only — say so in
  reports: screens are checked by screenshots, data by the live-data rehearsal.
- Python 3.12 venv in `backend/.venv` (`pip install -r requirements.lock.txt`), frontend `npm ci` in `frontend/`.
- Schema only via Alembic migrations; the app never creates tables. Never edit a migration already on `main` —
  add a new one after the head. One head: right before merge, rebase on fresh `main`; if `main` gained a
  migration meanwhile, re-parent/renumber yours (not on `main` yet, so allowed) and rerun the gate
  (`backend/tests/test_alembic_single_head.py`).
- **Live-data protocol:** backup → rehearsal on a copy of the prod DB → integrity/row-count
  check → only then prod.
- A PR with an Alembic migration is rehearsed on a copy of prod first; its description carries the row-count
  reconciliation from that run (before/after per table plus the invariants) and links the log.
- Migrations are applied by the update script (`deploy/update.sh`), never on container start. A migration declaring
  `DESTRUCTIVE = True` runs only after the owner approves its revision id via the «Обновить сервер» button.
- New code: constraints, not rollbacks — protect data with CHECK, UNIQUE, FK, NOT NULL and version checks that make bad
  states impossible, instead of relying on rollbacks or compensating scripts afterwards.
- Repo stays private. No secrets in git. Never write to live Google data (SUPPLYMAP warehouse sheets).
- Commits are authored by the human whose session it is, under their own git identity. **No
  `Co-Authored-By: Claude`, no "Generated with Claude Code"** in commits or PRs.
- PR ready = rebased on `main`, gate green, body says what changed, which module, migration yes/no, how to
  verify. PRs, commit messages and UI text in Russian.

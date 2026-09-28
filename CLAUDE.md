# Fraxis ERP Next — working rules for Claude Code

The successor to SUPPLYMAP (Google Apps Script). Owner leaves Google within ~6 months of 2026-09-14;
this repo is where the product moves. Modular monolith: FastAPI + SQLAlchemy/Alembic backend,
React/TypeScript (Vite) frontend, PostgreSQL in production, SQLite for tests and local preview.

## Read first
- `ARCHITECTURE.md` — module boundaries, end-to-end flow, migration order.
- `module-map.json` — which module is native, which is still legacy-owned (Projects, Calculation).
- `README.md` — every command, env var and the legacy bridge.

## Sibling repos (expected next to this one in `Projects\`)
- `../SUPPLYMAP` — live GAS system and the **source of domain rules**. Read-only from here: port the
  rules, not the GAS files or the sheet layout. `SUPPLYMAP_SOURCE_REPOSITORY` overrides the path.
- SUPPLYMAP keeps changing (the owner maintains it in VS Code; this repo is worked on in Claude Desktop).
  The SessionStart hook `.claude/hooks/supplymap-drift.mjs` prints how many SUPPLYMAP code commits each
  ported module is behind, plus the latest ones — start a session by checking whether any of them change
  a rule that is already ported.
- `../ERP_RECOVERED` — recovered legacy ERP; serves Projects/Calculation through `legacy-bridge.mjs`
  and the contract in `contracts/`. `ERP_RECOVERED_ROOT` overrides the path.

## Commands
- `npm run check` (repo root) — THE gate: architecture tests + backend pytest + frontend tests + build.
  Run it after every slice. Backend alone: `npm run test:backend`; frontend: `npm run test:frontend`.
- Known red: architecture tests pin each module's `SOURCE.json` to a SUPPLYMAP commit, so they fail
  whenever SUPPLYMAP `main` moves on. Only techcards, the operation library and the legacy contracts
  have a `npm run sync:*` script; run it for those. For every other module, re-baseline by hand, and
  only after reviewing the SUPPLYMAP commits since the pin: port any rule change first, then move
  `sourceCommit`/`sourceRef` in its `SOURCE.json`. Never edit file hashes in `SOURCE.json` by hand.
- Local preview: `.claude/launch.json` (`erp-api` on 8021 over `backend/preview.db`, `erp-web` on 5176).
  Preview login: `preview` / `Preview-2026!` (local SQLite only).
- Python 3.12 venv in `backend/.venv` (`pip install -r requirements.lock.txt`); schema changes only via
  Alembic migrations — the app never creates tables itself.

## Choosing mode, model and depth — decide yourself, don't ask
- **Plan first** (enter plan mode yourself) when starting a new module, porting a drift commit that
  touches a status machine or money/stock quantities, or any change across 3+ files. Small fixes: act.
- **Delegate by model:** wide SUPPLYMAP reading → `supplymap-scout` (haiku). Grading → `erp-verifier`
  (sonnet), before saying a slice is done and before every commit. Mechanical bulk edits (renames,
  fixtures, lint) → a general-purpose subagent with `model: haiku`, then check its diff yourself.
  Keep architecture, domain modelling and migrations in the main session.
- **Depth:** think hard on domain invariants, migrations and anything that moves stock or money. Go
  quick on wiring, copy and tests that mirror existing ones.
- **One slice per session.** Once the slice is green and committed, say so and suggest a fresh session
  instead of starting the next module in the same context.

## Project memory
`.claude/knowledge/memory/` is THE project memory (in git; `MEMORY.md` is the index — read only the
relevant note). `autoMemoryDirectory` in the gitignored `.claude/settings.local.json` points there; set
it on a new machine. The brain vault mounts it next to SUPPLYMAP's memory, so `[[links]]` to SUPPLYMAP
notes resolve. Save here only what the repo doesn't already say.

## Autonomous migration conveyor
The remaining port runs unattended: a Desktop Routine calls `/migrate-next` (skill
`.claude/skills/migrate-next`) on a schedule. Each run takes 1–2 tasks from `docs/migration/BACKLOG.md`,
does them in worktree agents, merges into `main`, and logs to `docs/migration/RUNLOG.md`. The queue
order is the priority; the owner edits it by hand. Outside a run, don't take BACKLOG tasks silently.
If you do one on request, mark it there the same way.
A cloud Routine (Opus, skill `.claude/skills/migrate-cloud`) fixes non-warehouse audit C2/C3 rows in
parallel, each on a branch `cloud/<ID>-<stamp>` that it pushes. The local run merges those branches (step 1b).
The cloud never pushes `main`.

## Git — owner rule (2026-09-24)
- Every commit is authored by the owner only. **No `Co-Authored-By: Claude`, no "Generated with Claude
  Code"** in commits or PRs — Claude is not a collaborator on this repo. `.claude/settings.json` turns
  attribution off; do not add the trailers by hand either.
- Releases (push to `main`, tags, deploys) happen only when the owner asks.

## Hard don'ts
- No GAS tooling here: no clasp, no `deploy.ps1`. SUPPLYMAP's rules live in its own repo.
- Never write to the live warehouse (SUPPLYMAP Склад: ЦАР / ЖУК / СПИСАНИЕ). Imports read from git
  snapshots; any importer that touches live Google data must be dry-run first.

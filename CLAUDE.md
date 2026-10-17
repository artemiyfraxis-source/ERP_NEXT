# Fraxis ERP Next — working rules for Claude Code

The production ERP of the shop, running on its own server since the 05.10.2026 launch. Modular monolith: FastAPI +
SQLAlchemy/Alembic backend, React/TypeScript (Vite) frontend, PostgreSQL in production, SQLite for tests and preview.

## Read first
- `ARCHITECTURE.md` — module boundaries, end-to-end flow. `module-map.json` — module ownership (the legacy Projects
  registry and «Расчёт и КП» are out of scope — another block, another programmer). `README.md` — commands, env vars.

## Screens — mandatory skill
- Any change to a screen (`frontend/src/*.tsx`, `styles.css`) or an owner screenshot with a UI complaint → load the
  `erp-screens` skill first and follow it: Russian labels only (no type codes like `cutWireStrip`), a visible date/period
  on anything that plans, dense Google-like tables and forms, fixed ETK block order, before/after shots via
  `scripts/demo/run-demo.sh` + `scripts/demo/shoot.mjs`, then `design-critique` on the after-shots.

## SUPPLYMAP is legacy (owner, 05.10.2026)
- Don't consult `../SUPPLYMAP` to decide how a fix or feature works: the ERP's own data, code and the owner's words
  decide. Don't brief workers with «check SUPPLYMAP». Demo/test reference data: `backend/data/reference-mirror/`.
- `SOURCE.json` files are frozen provenance records: no pins, no `sync:*`.

## Commands
- `npm run check` (repo root) — THE gate (architecture tests + backend pytest + frontend tests + build), after every
  slice. Parts: `npm run test:backend`, `npm run test:frontend`. Merge only when CI `check` is SUCCESS (never pipe
  `gh pr checks` into `head` before a merge).
- Local preview and venv — see RULES.md «Testing»; schema only via Alembic.

## How to work — decide yourself, don't ask
- **Plan first** (`orchestrate` skill → `erp-planner`) for a new module, a status machine, money/stock quantities, or
  a change across 3+ files. Small fixes: act directly.
- **Orchestrator (local VS Code / Desktop):** the main session plans, dispatches and integrates — one `erp-worker` per
  task in its own worktree, merge branch by branch, one Alembic head, `erp-verifier` before saying done and before
  every commit, one PR. Architecture/domain decisions and migration revision chains stay in the main session.
- **Delegate by model:** wide reading → `Explore`; mechanical bulk edits → general-purpose `model: haiku`, then check
  its diff. Don't fan out agents for a task a few tool calls finish.
- **Depth:** think hard on domain invariants, migrations and anything that moves stock or money; go quick on wiring,
  copy and tests that mirror existing ones.
- **One slice per session.** Once green and committed, say so and suggest a fresh session.

## Project memory
`.claude/knowledge/memory/` (in git; `MEMORY.md` is the index — read only the relevant note). `autoMemoryDirectory`
in the gitignored `.claude/settings.local.json` points there; set it on a new machine. Save only what the repo doesn't say.

## Cloud sessions and project threads
- `.claude/hooks/cloud-siblings.mjs` (SessionStart when `CLAUDE_CODE_REMOTE=true`) clones the sibling repos and prints
  the owner's global rules; in claude.ai project threads (cwd = parent dir of the repos, this repo's settings and hooks
  don't apply) run `node ERP_NEXT/.claude/hooks/cloud-siblings.mjs` by hand. Obsidian MCP and brain sync are local-only.
- The SUPPLYMAP → ERP migration conveyor (`/migrate-next`, `/migrate-cloud`) is finished (BACKLOG empty, 06.10.2026);
  `docs/migration/RUNLOG.md` stays the log of orchestrated batches.

## Two developers — owner + Витя (05.10.2026)
@.claude/team/RULES.md

That file is the only shared contract; the rest of this `CLAUDE.md` is the owner's way of working and doesn't bind
Витя. Owner sessions keep Витя's `vitya/*` branches and `.claude/team/vitya/` out of orchestration and cleanups.
Releases (push to `main`, tags, deploys) happen only when the owner asks.

## Hard don'ts
- No GAS tooling here (no clasp, no `deploy.ps1`). Never write to the live warehouse (SUPPLYMAP Склад: ЦАР / ЖУК /
  СПИСАНИЕ); importers that touch live Google data are dry-run first.
- Never edit an Alembic migration already on `main` — add a new one after the head (`guard-migrations.mjs` enforces).
- `guard-danger.mjs` (PreToolUse, Bash|PowerShell) holds in bypass mode: denies making the repo public, asks before
  force-push, `reset --hard`, `clean -f`, `checkout -- .`/`restore .`, `import_stock` without `--dry-run`, `alembic downgrade`.

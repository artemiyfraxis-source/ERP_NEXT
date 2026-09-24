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
- `../ERP_RECOVERED` — recovered legacy ERP; serves Projects/Calculation through `legacy-bridge.mjs`
  and the contract in `contracts/`. `ERP_RECOVERED_ROOT` overrides the path.

## Commands
- `npm run check` (repo root) — THE gate: architecture tests + backend pytest + frontend tests + build.
  Run it after every slice. Backend alone: `npm run test:backend`; frontend: `npm run test:frontend`.
- Known red: architecture tests pin each module's `SOURCE.json` to a SUPPLYMAP commit, so they fail
  whenever SUPPLYMAP `main` moves on. Fix by re-running the matching `npm run sync:*`, never by editing
  hashes in `SOURCE.json` by hand.
- Local preview: `.claude/launch.json` (`erp-api` on 8021 over `backend/preview.db`, `erp-web` on 5176).
  Preview login: `preview` / `Preview-2026!` (local SQLite only).
- Python 3.12 venv in `backend/.venv` (`pip install -r requirements.lock.txt`); schema changes only via
  Alembic migrations — the app never creates tables itself.

## Git — owner rule (2026-09-24)
- Every commit is authored by the owner only. **No `Co-Authored-By: Claude`, no "Generated with Claude
  Code"** in commits or PRs — Claude is not a collaborator on this repo. `.claude/settings.json` turns
  attribution off; do not add the trailers by hand either.
- Releases (push to `main`, tags, deploys) happen only when the owner asks.

## Hard don'ts
- No GAS tooling here: no clasp, no `deploy.ps1`. SUPPLYMAP's rules live in its own repo.
- Never write to the live warehouse (SUPPLYMAP Склад: ЦАР / ЖУК / СПИСАНИЕ). Imports read from git
  snapshots; any importer that touches live Google data must be dry-run first.

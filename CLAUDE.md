# Fraxis ERP Next — working rules for Claude Code

The production ERP of the shop, running on its own server since the 05.10.2026 launch. SUPPLYMAP (Google Apps
Script) is legacy: the work has fully moved here. Modular monolith: FastAPI + SQLAlchemy/Alembic backend,
React/TypeScript (Vite) frontend, PostgreSQL in production, SQLite for tests and local preview.

## Read first
- `ARCHITECTURE.md` — module boundaries, end-to-end flow, migration order.
- `module-map.json` — module ownership (all native; the legacy Projects registry and «Расчёт и КП» are out of
  scope since 2026-09-29 — another block, another programmer).
- `README.md` — every command and env var.

## Screens — mandatory skill
- Any change to a screen (`frontend/src/*.tsx`, `styles.css`) or an owner screenshot with a UI complaint → load the
  `erp-screens` skill first and follow it: Russian labels only (no type codes like `cutWireStrip`), a visible date/period
  on anything that plans, dense Google-like tables and forms, fixed ETK block order, before/after shots via
  `scripts/demo/run-demo.sh` + `scripts/demo/shoot.mjs`, then `design-critique` on the after-shots.

## SUPPLYMAP is legacy (owner, 05.10.2026)
- Do not consult `../SUPPLYMAP` to decide how a fix or a feature works: the ERP's own data, code and the owner's
  words decide. Don't brief workers with «check SUPPLYMAP». The sibling checkout is still needed only by the
  architecture tests (`SOURCE.json` pins); the drift hook is off.

## Cloud sessions (claude.ai/code)
- `.claude/hooks/cloud-siblings.mjs` (SessionStart, only when `CLAUDE_CODE_REMOTE=true`) clones `../SUPPLYMAP` and
  `../claude-brain` (the owner's notes, read-only here) and prints the owner's global rules. If a clone fails,
  attach the repo with `add_repo` and rerun the hook. Obsidian MCP and the brain's PowerShell sync are local-only.
- Plugins come from `enabledPlugins` + the `claude-code-skills` marketplace in `.claude/settings.json`; generic
  skills copied from SUPPLYMAP live in `.claude/skills` (see `THIRD_PARTY_NOTICES.md`).
- **Project threads (claude.ai project «ERP NEXT") are different:** the cwd is the parent dir holding all three
  repos (`ERP_NEXT`, `SUPPLYMAP`, `claude-brain` side by side, already cloned by the platform), so this repo's
  `.claude/settings.json` does NOT apply there — no SessionStart hooks, no `enabledPlugins`. `.claude/skills`,
  `.claude/agents` and this file still load. Run `node ERP_NEXT/.claude/hooks/cloud-siblings.mjs` by hand: it prints the
  owner's global rules.
  Plugins for threads are enabled only in the project's settings (Plugins tab), by the owner.

## Commands
- `npm run check` (repo root) — THE gate: architecture tests + backend pytest + frontend tests + build.
  Run it after every slice. Backend alone: `npm run test:backend`; frontend: `npm run test:frontend`.
- GitHub CI (`.github/workflows/check.yml`) runs the same gate on every PR and push to `main`, minus the
  architecture tests (they need the sibling SUPPLYMAP checkout). Don't merge a PR whose `check` is red.
- Architecture tests pin each module's `SOURCE.json` to a SUPPLYMAP commit; SUPPLYMAP is frozen, so they
  stay green. If they go red because SUPPLYMAP moved, re-baseline the pin (`npm run sync:*` for techcards and
  the operation library) — don't port anything. Never edit file hashes in `SOURCE.json` by hand.
- Local preview: `.claude/launch.json` (`erp-api` on 8021 over `backend/preview.db`, `erp-web` on 5176).
  Preview login: `preview` / `Preview-2026!` (local SQLite only).
- Python 3.12 venv in `backend/.venv` (`pip install -r requirements.lock.txt`); schema changes only via
  Alembic migrations — the app never creates tables itself.

## Choosing mode, model and depth — decide yourself, don't ask
- **Plan first** (locally: `erp-planner` via the `orchestrate` skill; elsewhere plan mode) when starting
  a new module, touching a status machine or money/stock quantities, or any change across 3+ files. Small fixes: act.
- **Delegate by model:** wide code reading → `Explore`. Grading → `erp-verifier`
  (sonnet), before saying a slice is done and before every commit. Mechanical bulk edits (renames,
  fixtures, lint) → a general-purpose subagent with `model: haiku`, then check its diff yourself.
  Architecture and domain decisions stay in the main session; a migration is written by one worker per
  batch and its revision chain is checked by the main session.
- **Depth:** think hard on domain invariants, migrations and anything that moves stock or money. Go
  quick on wiring, copy and tests that mirror existing ones.
- **One slice per session.** Once the slice is green and committed, say so and suggest a fresh session
  instead of starting the next module in the same context.

## Orchestrator mode — local sessions (owner, 2026-10-02)
In VS Code / Desktop on the owner's PC the main session is the orchestrator: it plans, splits, dispatches
and integrates; it does not write big pieces of code itself. Anything over one file or a few lines goes
through the `orchestrate` skill: `erp-planner` → one `erp-worker` per task (own worktree, in parallel) →
merge branch by branch, one Alembic head → `erp-verifier` → one PR. Small fixes it does directly.
Cloud project threads and the migration Routines keep their own flow (`migrate-next`, `migrate-cloud`).

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
- Never edit an Alembic migration that is already on `main` — add a new one after the head.
  `.claude/hooks/guard-migrations.mjs` (PreToolUse) enforces it where `.claude/settings.json` applies.
- `.claude/hooks/guard-danger.mjs` (PreToolUse, Bash|PowerShell) is the gate that holds in bypass mode:
  denies making the repo public; asks before force-push, `reset --hard`, `clean -f`, `checkout -- .` /
  `restore .`, `import_stock` without `--dry-run` and `alembic downgrade`. Ported from SUPPLYMAP's
  guard-hard-donts; it blocks via stdout JSON and always exits 0.

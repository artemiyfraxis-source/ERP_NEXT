---
name: prod-reference-write
description: "How to write reference-DB values straight into prod (owner 14.10: «пиши на сервер», no file review) — ssh + import_rows in a one-off api container, old=\"\" guard"
metadata:
  node_type: memory
  type: project
  originSessionId: 18c9080e-85fd-4574-a846-0e4e1270987e
  modified: 2026-10-14T18:41:50.772Z
---

Owner 14.10.2026: reference-DB fills go straight to the server, not as a file for him to review first.

Working path (used for БД.КОАКС L1–L3, 49 rows):
- ssh with Windows OpenSSH (see [[erp-server]]), everything under `sudo -n`; app at `/opt/erp/app`, env file
  `/opt/erp/config/.env` (root-only), DB URL env var `ERP_DATABASE_URL`.
- Pipe a python script into `docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - <arg>`
  from `/opt/erp/app/deploy`. Script: `build_references_uow_factory(build_engine(url))` + `importers.import_rows(table, uow,
  [ImportRowIn(fields=…, old=…)], Actor(...), moscow_now(), dry_run=…)`.
- Key by the table's natural key (`import_schema`), never by record ID — IDs of rows created by later loads differ
  between prod and any local copy. `old={field: ""}` makes a filled cell come back «refused» instead of overwritten.
- Order: dry run (created must be 0) → `sudo -n bash erp.sh backup-now` → apply → dry run again (all refused = written).
- Keep the cell list (key, field, old, new, source) in `docs/data/` as the rollback record (PR #411).

- Shell gotchas: from the Bash tool call `/c/Windows/System32/OpenSSH/ssh.exe -i "$(cygpath -w ~/.ssh/erp_server)"`
  (quote the path — the home has a space) and pipe the script with `< file.py`; PowerShell pipes add a BOM and strip
  inner quotes. `docker compose exec -T` inside `bash -s` eats the rest of stdin — give it `</dev/null`.
- Renaming a БД.КОАКС «Артикул» breaks links: warehouse lots and kits carry the connector by that name and the
  generator matches by it. Supplier codes go to the custom column «Артикул поставщика», never into the name.

**Why:** no prod API token exists for `refs:write`; the xlsx round-trip is keyed by ID and breaks on prod.
**How to apply:** same recipe for терминалы and провода fills; still never overwrite filled cells without the owner. [[live-data-protocol]] [[module-reference-db]]

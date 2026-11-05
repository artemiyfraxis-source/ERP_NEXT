# Release gate: equipment setup and exact production resources

## ERP_EQUIPMENT_SETUP_ENABLED

- Name: `ERP_EQUIPMENT_SETUP_ENABLED` (strict `0` or `1`; default `0`).
- Type: Release. Owner: ERP integration maintainer (root), introduced 2026-11-03.
- Scope: one composition-root decision for production resource factory, setup factory/router and rollback CLI.
- Rollout: 0% until UI, additive migration 0170, PostgreSQL rehearsal/races, independent review and full gate pass; then one explicitly approved activation, not per-user cohorts sharing resources.
- Readiness: all 12 tables and the generation singleton must exist. Missing schema never falls back to unprotected writes.
- Kill switch trigger: resource integrity error or failed deployment validation. Abort by stopping API/CLI writers and investigating/restoring a verified copy; do not switch back to legacy writing after resource accounting has been used. Disabled startup rejects nonzero generation or any persisted setup/resource data.
- Monitoring dashboard: existing deployment health and server error logs; inspect resource conflict/error reports and generation state during release smoke. No new dashboard service.
- Retirement: owner reviews removal after seven stable days at 100%; initial review date 2026-11-17. Remove disabled write path only after explicit release-owner approval.
- No production environment value is changed by this development slice.

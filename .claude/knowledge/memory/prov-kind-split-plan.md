---
name: prov-kind-split-plan
description: "Implemented lug/cable bases and grouped TERMINAL_TABS/CABLE_TABS readers"
metadata:
  node_type: memory
  type: project
  originSessionId: 3a6f7ee7-153e-4145-ab46-a3d32531b583
  modified: 2026-11-12T12:00:00.000Z
---

- Implemented: `0165_split_lug_cable_tabs` gives lugs and each cable kind its own base/tab: `lug`, `cab_coax`, `cab_multi`, `cab_lan`; mounting wires remain `prov` → [[db-tab-is-own-table]].
- `ter_kind` / stored `cable_kind` were dropped; the API derives kind from tab. External readers use `TERMINAL_TABS` (`ter` + `lug`) and `CABLE_TABS` (`prov` + cable tabs), ordered by `(sort_key, id)` to preserve ETK stamps.
- Mounting wires have one row per colour. Cable mark names are link keys for БД.КОАКС; never rename them during enrichment.
Related: [[module-reference-db]], [[hookup-wire-suppliers]], [[prod-reference-write]], [[live-data-protocol]].

---
name: calc-copy-wrong-number
description: "Copied calcs retain old project numbers; explicit number, rename workflow and NOT VALID gotcha"
metadata:
  node_type: memory
  type: project
  originSessionId: 9faa89d8-8c17-4501-a0c6-e7b3d817cab2
  modified: 2026-11-12T12:00:00.000Z
---

- Repeat-order calc copies may retain the old № inside. Before loading a «missing» project, compare the Drive calc filename, internal № and ERP card's `calc_source`; pass an explicit correct `number` to `POST /api/techcards/kitsets/load`.
- An empty № matching a card from another calc is rejected (422). Do not refresh the old card to create a new repeat order.
- Renumber via `python -m app.workflows.rename_project OLD NEW [--apply]`: dry-run first, rehearse on a dump copy, backup, stop api/worker, apply, restart.
- PostgreSQL gotcha: rewriting a legacy row rechecks even `NOT VALID` constraints. `rename_project` drops/re-adds only those checks inside its transaction; do not validate unrelated legacy rows accidentally.
Related: [[techcards-bulk]], [[module-projects]], [[prod-screen-check]].

# Third-party code in ERP_NEXT

Skills under `.claude/skills/` have their own notices in `.claude/skills/THIRD_PARTY_NOTICES.md`.

## TECHMAP-GRAPHER

TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission, 2026-10-06.
Source: github.com/SaveOurSouls/TECHMAP-GRAPHER, commit `e63befb85edb79f0c1635ac8314539ac24d2486a`
(branch `codex/integration-m5-22`).

Used in `backend/app/modules/techcards/harness/` (the harness model, its length rules and the reader/writer of the
`.techmap-project.zip` design document) and `backend/app/modules/techcards/grapher_import.py`. The rules are
rewritten in Python on the ERP's architecture, checked against the author's docs and examples; each function names
the original file it follows, and the places where it deliberately differs.

The component library of the harness drawing (`harness_component_templates`, migration 0091) is seeded with the
author's own JST SH connector artwork — `design/jst-sh-assets/variants/*.png` of the same repository, copied
unchanged into `backend/data/harness-library/jst-sh/` (`build_seed.py` turns them into templates). The QCAD hatching
catalog of that repository (GPL-3.0) is not used anywhere in ERP_NEXT.

«Маршрут v3» (the route-card graph of a harness) is ported from commit `0a456d2f7bd23288111e871058ebf55bf14426cd`
of the same branch, with the same permission: `src/Techmap.Client/src/manufacturing-v3/route-v3-model.ts`,
`route-v3-graph.ts`, `uml/uml-model.ts`, `manufacturing/route-source.ts`, `manufacturing/route-drawing-copy.ts` and
their tests are copied into `frontend/src/harness/route/` (first commit unchanged, ERP edits in the next one, each
file names its original). `backend/app/modules/techcards/harness/route_v3.py` rewrites `parseRouteV3`,
`generateRouteV3`, `createInitialRouteV3` and the part list of `buildRouteSourceItems` in Python. The author keeps the
route in the browser; ERP stores it per position on the server (`techcard_harness_routes`, migration 0111), and a
snapshot keeps hidden object IDs and isolated geometry instead of a full copy of the drawing.

## IBM Plex fonts (GRN labels)

IBM Plex Sans Regular, IBM Plex Sans SemiBold and IBM Plex Mono Bold © 2017 IBM Corp., SIL Open Font License 1.1 — the
font files and the full licence text (`OFL.txt`) are in `backend/app/modules/warehouse/labels/fonts/`, copied unchanged
from Roller_Buyer `apps/api/assets/fonts/`. They draw the text of the GRN label (stage option `warehouse.labels`): the
label is printed as a bitmap, the printer's own fonts have no Cyrillic.

The label's QR code is built with `segno` (BSD 3-Clause, pinned in `backend/requirements.txt`).

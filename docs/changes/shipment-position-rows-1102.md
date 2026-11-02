# Shipment data: project groups and position rows

## Scope

The management shipment list now follows the existing production-intake structure: one project/customer heading and one KB position per row. A real shipment contributes one row per exact shipment line, keyed by shipment ID and line ID. Repeated shipments of one KB remain separate records with their own document actions and actual shipment status/date.

Free quantities use the existing `stagePieces(intake, [])` calculation. Passing no shipments deliberately excludes picked quantities, which already have exact shipment-line rows. Partial positions can therefore have a production slice, an accepted free slice, and actual shipment lines without counting the picked amount twice. The position's own intake stage takes precedence over the project's earliest stage. Unknown positions with no launched quantity remain visible; their unknown quantity displays a dash.

The seven counters count position rows, explicitly labelled in the toolbar. The project-wide, version-aware packing decision appears once in the visible project heading on the existing ready/accepted stages. Its permissions, conflict handling and modal draft refresh use the existing components and APIs.

Opening a position preserves its exact row-stage context separately from the project/shipment entity ID. A free accepted slice therefore retains its packing decision and UPD fields even when the same project also has packed and production slices. Project headers prefer a visible ready/accepted free slice; mixed new shipments preserve the clicked line's context instead of taking their first line's stage.

## Documents and personal views

- PL opens the exact shipment's existing packaging-list print forms.
- DN opens the exact shipment's existing dispatch-note print forms. Missing manager data disables it with the server's missing-data explanation.
- UPD retains its separate file link, including in card mode.
- Before an actual shipment exists, the list says it has not been created. Missing document numbers are not replaced by fabricated actions.
- The default date is the actual shipment date. The optional planned date remains available in the column chooser.
- Only this table uses the new `shipment-data-positions-v2` layout key. Old oversized project-column widths cannot migrate into the position layout. The existing server/browser `shipment-data.view` table/card preference is unchanged.

`DataTable.groupCards` is an explicit opt-in, defaulting to false. It exposes the existing groups and group labels above card grids; existing card consumers retain their flat layout. Shipment-data group actions stay outside the individual card buttons.

No backend, DTO, shared global styles, historical shipment writes or new business-status formulas were introduced.

## Verification

- Existing permissions, unknown-card state, 409 recovery, manager/warehouse restrictions and draft-preservation tests remain covered.
- Representative cases cover 12 KB positions in each of the seven filters, 12 cards with one project decision, two exact shipments of the same KB, partial free quantities, distinct PL/DN/UPD actions and modal targets, and old layout-key isolation.
- Four modal-context regressions were observed failing before the review fix and passing afterward: table/card free accepted slices (packed20 + production40 + accepted10), project-header context, and exact lines of a mixed new shipment.
- After the modal-context review fix, eight affected/adjacent suites passed with 207 tests and the final TypeScript check passed. The full TypeScript/Vite production build passed before that context-only fix; the orchestrator owns the integrated final build/gate.
- Actual local App with sidebar and a representative 12-KB mock packet inspected at 1536×864: 12 rows, one project heading, 13px cell/status type, approximately 47px rows. KB column 56px; name receives remaining width. Workspace/table widths equal their scroll widths (1296/1226px), with no horizontal overflow.
- Screenshot: local temporary artifact `shipment-positions-1536.png`; the orchestrator owns integrated three-size visual verification and the final application gate.

Visual critique against production intake: readable project heading, compact KB identity, individual quantities/GRNs and separate shipment state; the former giant first cell and empty first-column space are removed. Two-line names keep their complete tooltip. The local UI audit checks semantic controls, focus, document propagation, tabular numbers and coarse-pointer targets.

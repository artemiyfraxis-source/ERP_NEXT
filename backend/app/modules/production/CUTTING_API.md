# Cutting groups: T2 integration contract

Backend worker slice on `cut-group-1117`, base `c024f8ff`. No factory-number or print-manifest changes.

## Persistence and equivalence

Migration `0186_cutting_groups`, parent `0185_shared_accessories`, `DESTRUCTIVE = False`.

Execution adds migration `0187_cutting_execution` after `0186_cutting_groups` (`DESTRUCTIVE = False`).
`production_cutting_work` extends one ordinary work session and has one open session per group.
The ordinary active-operator unique index still covers both individual and grouped work.
`production_cutting_actions` stores request fingerprints and committed responses for retries;
`production_cutting_outputs` links each original member/output to that session and submission.
Existing single-output/session uniqueness is unchanged. Downgrade refuses execution history.

- `production_cutting_groups`: UUID, unique number/idempotency key, canonical project/site, recipe hash,
  versioned frozen recipe, optimistic version, state (`open`, `frozen`, `cancelled`), join policy, timestamps.
- `production_cutting_members`: original task/operation IDs, stable row order, positive assembly/blank counts,
  row-specific length/labels, frozen issued document and acknowledged card version. Membership state is
  `active`, `split`, or `cancelled`. A partial unique index permits one active membership per operation.
- A restrictive composite FK `(task_id, operation_id)` targets the production operation candidate key.
  History cannot be deleted through child-task deletion. Downgrade refuses any group history; an empty
  group schema can be downgraded even when ordinary production tasks exist. No cross-module FKs added.

Recipe version 1 compares the first explicit PF without inputs, containing one unambiguous cable cut
and only supported cutting/preparation operations. Its ordered key includes canonical project, site,
cable identity, cable/PF multiplicity, operation/template identity and revision/content, frozen cards,
equipment/tooling, oriented A/B strip dimensions, tolerances, confirmed full/partial mode, label layout,
material/count/printing parameters, and protective tube material/count/length.

Only cable cut/finished length and variable label text are row-specific; KB and drawing identity,
production quantity, later PFs and planning norms do not determine equivalence. Tool identifiers and
program numbers are never normalized as cable lengths. Missing or ambiguous settings are ineligible.
In particular, the imported 708К fixture contains unresolved settings and must not be assumed eligible.

## Launch

Existing `GET /api/production/launch-ready` remains the source of selectable cards.

`POST /api/production/launch-ready/preview`:

```json
{"items":[{"techcard_id":"uuid","techcard_version":3,"launched":0,"quantity":10,"issue_separately":false}]}
```

Returns `fingerprint`, `groups: [{key, recipe_key, recipe_version, site, members}]`, and
`grouping_refused: [{techcard_id, reason}]`. Proposed members carry `techcard_id`, `index`, `quantity`,
`cut_length_mm`, `cables_per_assembly`, `labels`. Preview writes nothing.

`POST /api/production/launch-ready/launch` takes the same payload plus the preview `fingerprint`.
The fingerprint remains optional for existing clients. Returns:

```json
{"created":[{"id":"uuid","number":"ПЗ-0001","project":"708К.","item":"КБ1","quantity":10,"techcard_id":"uuid"}],
 "refused":[{"techcard_id":"uuid","reason":"..."}],
 "groups":[],"grouping_refused":[]}
```

Each compatible selection launches atomically, including child tasks, serials and membership. Independent
groups may succeed separately. A repeated stale selection refuses without duplicating tasks/members.
Ineligible rows launch normally and report the grouping refusal separately from launch refusal.

Existing `POST /api/production/tasks` accepts `issue_separately` (default false), keeps its required
`Idempotency-Key`, and adds `cutting: {eligible, reason, group_id, recipe_key}` to its response.
Separate eligible launches create a nonjoinable singleton group. Compatible later launches join only
an unassigned, unstarted group. Board and autoplan launch use the same recipe/attachment service.
Retries of launches made before this migration retain their original task identity and do not create
historical group memberships.

## Group reads and controls

### Group already-launched work (T1b)

`POST /api/production/cutting-groups/from-existing` requires production edit permission, including preview:

```json
{"project":"708К.","task_ids":["optional-task-uuid"],"dry_run":true}
```

Omit `task_ids` to inspect all tasks of the normalized project. `dry_run` defaults to `true`; send
`false` to apply. Empty/unknown selections or tasks outside the project return 422. Foreign-site work
returns 403 before applying the discovered selection. A task selection may contain at most 1000 IDs.

Response (both modes):

```text
project, dry_run,
groups: [{recipe_key, recipe_version, project, site,
          recipe_summary: {cable, cables_per_assembly, operations: [code],
                           settings: [{code, parameters, fields, tooling}]},
          members: [{task_id, task_number, item, operation_id, quantity,
                     cut_length_mm, cables_per_assembly, labels,
                     operator_ids, planned_start, planned_finish}],
          operator_ids, assignment_consistent, planning_consistent,
          common_planned_start, common_planned_finish, warnings,
          group_id (apply only), number (apply only)}],
created: [group_uuid],
refused: [{task_id, task_number, item, reason}],
skipped: [{task_id, task_number, item, reason}]
```

Only running tasks whose first explicit preparation PF has no work session (including cancelled
sessions), output (including corrections), box, or started/finished share qualify. Uses the exact T1
recipe function, so length and label text may vary, while incomplete recipes are refused. The task's
referenced issued card must still have its launch revision; changed/missing cards are refused with a
reason. No historical recipe is guessed from a newer card.

The site comes from the existing preparation assignees and task handoff. For unassigned tasks without
a handoff it is resolved from the launching employee's unambiguous site; unresolved/conflicting sites
are refused. This is independent of which master invokes the action.

Preview writes nothing. Apply locks the project and tasks and rechecks eligibility, then creates one
group per recipe in a separate transaction, including singleton groups. Existing active memberships
are skipped, so retries create no duplicate memberships. These explicit groups do not accept late
launch joins. Concurrent changes can return 409; earlier groups may already have committed, and a
rerun safely skips them. No child assignment, plan, quantities, materials, serials, or later PF is changed.

The common assignee list and common plan window are inherited independently when all members agree.
On disagreement the respective group field is empty/null with a warning; child assignments and
windows remain intact. Group GET responses also expose `operator_ids`, `assignment_consistent`,
`planning_consistent`, `common_planned_start`, `common_planned_finish`. Existing `planned_start` and
`planned_finish` remain the aggregate extent of child windows, as before.

CLI, from `backend`, using the application's `ERP_DATABASE_URL`:

```powershell
.\.venv\Scripts\python -m app.workflows.cutting_group_existing --project '708К.'
.\.venv\Scripts\python -m app.workflows.cutting_group_existing --project '708К.' --apply
```

Optional repeated `--task-id UUID` narrows the selection. `--dry-run` is an explicit alias for the
default. The CLI prints the same JSON response, and apply records `production.cutting_from_existing`
audit events. The service account is for this local maintenance command; HTTP uses the authenticated
master. No live grouping was performed as part of implementation.

`GET /api/production/cutting-groups` returns `{groups: [...]}`; `GET /.../{id}` returns `{group: ...}`.
Production overview permissions and every member's site are checked.

Group shape:

```text
id, number, version, recipe_version, recipe_key, project, site,
state (open|frozen|cancelled), membership_frozen,
planned_start, planned_finish, norm_seconds,
rows: [{id, task_id, operation_id, task_version, task_number, project, item, name, position,
        assembly_quantity, blank_quantity, operation_quantity, cables_per_assembly,
        cut_length_mm, labels, techcard_revision, good, defects,
        membership_state (active|split|cancelled),
        state (waiting|assigned|started|done|split|cancelled),
        operator_ids, planned_start, planned_finish, norm_seconds,
        boxes: [{id, number, quantity}], etk_url}]
```

Decimal quantities are strings; assembly counts/norm seconds are integers. `operation_quantity` is the
existing PF output quantity. For a kit of four cable blanks it may differ from `blank_quantity`.
Submit quantities in PF units, as in existing production. Group windows/norm totals are read aggregates;
do not add them to child workload/resource totals again.

All control actions require production edit permission and an optimistic `version`:

- `POST /.../{id}/assign`: `{version, operator_ids: [uuid], requested_start?: ISO datetime,
  machine_bindings?: {operation_uuid: [{etk_position, machine_id}]}}`. One transaction; ordered child
  windows, existing skill/calendar/resource checks. At least one operator required, all on one site.
- `POST /.../{id}/split`: `{version, member_ids: [uuid]}`. Whole unstarted rows become standalone;
  child assignments, quantities and material/serial records are preserved.
- `POST /.../{id}/cancel`: `{version}`. Dissolves an unstarted group; it does not cancel KB tasks.
  Existing task cancellation/restoration changes only that child's membership and preserves history.
- `POST /.../{id}/revalidate`: `{version}`. After an issued card changes, explicitly acknowledges a
  still-identical preparation recipe and row values. Changed cut/label/recipe values refuse; launch new
  work separately. It does not silently replace the issued task document or later PFs.

- `POST /.../{id}/refresh-etk`: `{version}`, edit permission. For an unstarted (`open`, no work session) group: atomically
  re-pins every active member task to its current issued ETK revision (same effect as `tasks/{id}/norms/refresh`),
  recomputes each member's recipe for its own PF and refreshes the member rows (cut length, labels). Since 20.11 a
  member whose new recipe differs is not refused: the most common new recipe keeps this group (id, number, positions;
  tie — the lowest row position), every other recipe moves its rows to an open unstarted group of the same project,
  site, kind and recipe, or to a new explicit group; moved rows are `split` here (`regroup`). Still 422 with nothing
  written: a member whose new ETK gives no recipe or whose PF composition changed (`cutting_refresh_cards` error text —
  use the CLI restructure below). Already current group: no-op, version unchanged. Started/frozen group: 422. Group
  views carry `etk_stale` (some member pinned older than the issued ETK).
  CLI: `python -m app.workflows.cutting_group_existing --project X --refresh-etk [--apply]` (dry-run by default).
- Re-pinning a grouped task (`tasks/{id}/norms/refresh`) is no longer refused while its group is open and has no work
  session: the row keeps its frozen document (the group shows `etk_stale`, start stays blocked by the card-version
  check) until `refresh-etk` moves it. A started/frozen group still refuses the re-pin.

## Kinds: cable cut, labels, tubes (owner 20.11)

Grouping takes **every** input-less PF made only of supported preparation operations (`cutting_recipes`), not only
the first; each PF is its own `ProductionOperation`, so one active membership per operation still holds and a КБ may
be a member of up to three groups. `cutting_recipe` (first PF only) is kept for the launch fingerprint and the
`POST /tasks` `cutting` response.

Kind of a PF (`pf_kind`): any cable cut (`coaxCut`, `cutWire`, `cutWireStrip`) → `cut`; otherwise only printing
operations (tape labels `markMaterial = tape`, printed marking tubes `PRINT_MARK…`) → `labels`; only tube cuts →
`tubes`; printing and tube cuts together without a cut → not grouped. The old mixed «Заготовки» PF (cut + labels +
tubes in one PF, ETKs before the generator split) is kind `cut` with all its operations, exactly as before.

Recipe JSON: kind `cut` keeps version 1 and its identity unchanged (stored keys of existing groups stay valid; no
`kind` field); `labels`/`tubes` are version 2 with `"kind"` in the key and **without** the cable (printing a label or
cutting a tube does not depend on the cable; multiplicity, layout, material, tube article/lengths, cards and equipment
still do). A key of one kind never equals a key of another, so a group never mixes kinds. Labels/tubes rows still carry
`cut_length_mm` (from the ETK's only cable cut) for display. Group reads, the launch preview and the from-existing
proposal add `kind` (`cut|labels|tubes`) and `kind_name`. Launch-ready runs items sharing any proposed group in one
transaction.

## Restructure unstarted tasks in place (owner 20.11)

`restructure_project` (CLI only — HTTP wiring of composition changes stays in `api.py`, outside this slice):

```powershell
.\.venv\Scripts\python -m app.workflows.cutting_group_existing --project '708К.' --restructure            # dry run
.\.venv\Scripts\python -m app.workflows.cutting_group_existing --project '708К.' --restructure --apply
```

One transaction for the project (project lock, then its groups by id, then tasks). The dry run executes everything and
rolls back. For every running task whose issued ETK revision differs from the pinned one: refused (task untouched)
when it has any work trace (`worked`: session, output, box, defect, delivery, started share), a PF in a started/frozen
group, a disappearing PF with cutting-group history or with an equipment allocation. Otherwise the task keeps its id,
number, quantity, serial numbers and mark events; operations are rebuilt from the new route
(`restructured_operations`): same ETK operations → same operation (id, assignees, window), PFs split out of one old PF
→ the first keeps the old id and window, the others get its assignees without a window; same scheme block → same
operation; the rest are new. Audit `production.restructured`. Then all unstarted preparation PFs of the project's
tasks are regrouped by recipe (`regroup`): the old group of the cut keeps its id and number; labels and tubes get their
own explicit groups; emptied groups are cancelled. A rerun changes nothing.

Output: `restructured`, `unchanged`, `refused`, `groups`, `moved`, `cancelled`, `counts.before/after` (tasks,
operations, serials, groups by state and kind, members by state, marking batches, label jobs, mark events),
`invariants` (tasks, serials, batches, jobs, mark events unchanged), `labels` (printed/remaining per КБ),
`labels_unchanged`, `printed_one_per_kb`.

## Printed and remaining labels (owner 20.11)

`workflows/marking_progress.py`: no counter is stored; per task, marking batches whose manifest names it and their
jobs. A label counts as printed when its job is `spooled` (unless the tracked printer reported error/cancel) or still
`pending`/`taken`/`unknown`; `failed`/`cancelled` are not printed. Rows match by (task, serial, text), not by the ETK
operation index. An item (serial) is printed when all its numbered labels are.

- `GET /api/production/marking/progress?group_id=…` or `?task_id=…&operation_id=…` (marking read gate):
  `{rows: [{task_id, task_number, item, quantity, serial_first, serial_last, printed, remaining, printed_serials,
  remaining_serials}], printed, remaining}`.
- Preview/batch input `remaining: true` («Печатать оставшиеся»): the same rows minus printed ones; nothing left → 422.

All controls return `{group: ...}`. Stale versions/concurrent resource changes: 409; forbidden site: 403;
invalid state/settings: 422. The first row start or manual output freezes membership and recipe.

## Fitter execution and planning

`GET /api/production/my?operator_id=...` adds `cutting_groups`. Active rows assigned to the viewed
operator appear there and their child cards are omitted from `tasks`. Only authorized rows are returned;
`partial_view` indicates that other group members were omitted. Totals/windows cover only visible rows.
Later PF cards remain ordinary per-KB entries in `tasks`.

Instructions, input inspection and standalone work retain the existing row endpoints:

- `GET /api/production/my/tasks/{task_id}/operations/{operation_id}/etk`
- `GET /api/production/tasks/{task_id}/operations/{operation_id}/inputs`
- `POST /api/production/tasks/{task_id}/operations/{operation_id}/work/start`
- `POST /api/production/work/{session_id}/pause`, `/resume`, `/cancel`, `/submit`

Before the first group start, changed issued cards require revalidation. Grouped preparation ETK reads
and recorded operation-card versions use the stored issued document. After start, newer ETKs cannot
change that frozen work. Printing selected rows uses each row's existing ETK/serial path; no manifest
API or new numbering exists in this slice. Standalone work cannot start on an actively executing group
member, and individual pause/resume/cancel/submit cannot mutate a group session. Manual output,
reassignment, cancellation and handoff cannot change active group members. Completed KB tasks may
finish their own later PFs and close while other group members continue.

### One group start, submission and downtime (T1c)

All endpoints below require the existing **work/edit** permission (`myops`), optional
`?on_behalf_of=operator_uuid` with the ordinary on-behalf checks, `Idempotency-Key` (1–255 characters),
and the group `version`. Both actor and performer pass site checks; the performer must be assigned
to every active member. A partial view cannot execute the whole group.

- `POST /api/production/cutting-groups/{id}/start`: `{version}`. Creates exactly one work session,
  anchored to an unfinished member for resource accounting. Refuses another current session of the
  performer, another open group session or active standalone work on any member. Freezes membership
  and the issued card versions. With physical resources enabled, every member must have the same
  explicit physical machine bindings; the group claims that machine set and the performer once.
- `POST /.../{id}/submit`: `{version, rows: [{member_id, good_quantity, defect_quantity,
  defect_character?, defect_description?, materials?: [{fulfillment_line_id, quantity}]}]}`.
  Decimal quantities use PF units. Duplicate/foreign members, overproduction and invalid defect
  details are refused. Rows may be omitted or have zero quantities; at least one must be positive.
  One transaction creates each positive row's own output, box, defect and optional material
  consumption. A failure in any row rolls back the complete request. A partial submission keeps the
  same session active; it closes when all active members' good + defective quantities reach their
  respective plans. Each KB's downstream readiness uses its own output boxes as before.
- `POST /.../{id}/downtime/start`: `{version, reason, comment?}`. Uses existing downtime reasons;
  `other` requires a comment. Creates one downtime record linked to the group session and pauses
  its timer. Refuses an existing open downtime on any member.
- `POST /.../{id}/downtime/stop`: `{version}`. Closes that interval and resumes the same session.
  Submission while paused is refused. Ordinary downtime endpoints cannot bypass group resume or
  open a parallel interval on one of its members.

Returns `{group, outputs: [{member_id, output_id, box_id, box_number}]}` (`outputs` empty outside
submission). Group reads add `session` (ordinary session fields plus `cutting_group_id`, or null)
and `downtime: {id, reason, reason_name, comment, started_at}` (or null). `/my/execution` also marks
the shared session with `cutting_group_id`; other work remains blocked until it completes.

An identical key, actor-authorized performer, group, action and payload replays the committed response
before rejecting a stale version. A different payload/action/performer for that key returns 409.
Other version/resource conflicts return 409, unauthorized membership/site returns 403, invalid state
or quantities return 422. Group and child locks use the existing resource/project/task lock order.

Group start selects no material. Optional material selections belong to each submission row and
consume only that KB's own issued request's available balance, including ordinary session reservations.
No input material is guessed or shared between KBs. The card submits quantities/defect details and
does not select materials, preserving the existing permission to work without material inputs.

Working time is counted once: each submission allocates only the shared session's elapsed working
seconds since its previous submission across positive rows, weighted by unit norm × submitted count
(minimum weight one). Per-KB earned norms remain ordinary. Linked group outputs have `session_id=null`
and zero standalone duration; the explicit links retain provenance, and the work-time journal holds
the allocated actual duration. Downtime seconds are excluded. Group boxes are never mixed.

Output boxes and material inputs always belong to the original child task/PF. Starting without material
inputs remains permitted. Supplied inputs require the child's own issued request and available quantity;
sharing a reel does not share entitlements.

Autoplan preview adds `cutting_groups: [{operation_ids: [uuid]}]`. A planned group uses one qualified
operator and ordered child windows, with no setup saving added. Apply must select every proposed member
when selecting a group cut. It still reports/counts child operations, and actual completion reflows each
child and its own downstream dependencies. Group membership/version participates in preview fingerprints.

## Decisions for coordinator review

- Split/cancel dissolves grouping and retains child assignments; cancelling a group does not cancel KBs.
- Eligible singleton tasks keep group persistence, and explicit separate issue disables later joining.
- Unknown recipes remain ordinary launchable tasks with an explanatory grouping refusal.
- First version supports one unambiguous cable/wire cut per preparation PF; multi-wire ambiguous recipes
  and unconfirmed full/partial strip settings are refused conservatively.
- T1c wires the group card's start, per-KB submission, downtime and shared timer. Labels remain one
  combined list. True group members are excluded from standalone MyQueue cards. Integration of the
  later interim-list PRs from main is coordinator work under the local no-merge brief.
- The full integrated gate, PostgreSQL concurrency verification and migration rehearsal on a production
  copy remain coordinator/release work; the local worker brief explicitly excludes the full gate.

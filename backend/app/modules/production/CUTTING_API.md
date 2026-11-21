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
        tubes: [{material, length_mm, quantity}],
        state (waiting|assigned|started|done|split|cancelled),
        operator_ids, planned_start, planned_finish, norm_seconds,
        boxes: [{id, number, quantity}], etk_url}]
```

`tubes` (owner 20.11, «Резка трубок»: КБ | Материал | Длина, мм | Шт) is filled only in `tubes` groups, from the
group's frozen recipe: tube article, piece length and pieces for the row's whole assembly quantity, merged by material
and length — protective tubes per cut group of the issued card (`coaxProtectiveTubes`; older cards: one piece per
label place, per cable unless the mark is per assembly), plain tubes `tutLength` × `units` per assembly. `[]` in other
kinds or when the snapshot lacks a positive length/count. `length_mm`/`quantity` are decimal strings.

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
  work separately. It does not silently replace the issued task document or later PFs. A row whose task was
  re-pinned (`tasks/{id}/norms/refresh`) to another ETK revision than the row's frozen one is refused (422, «Обновить
  из ЭТК»): acknowledging only the card version would let the group start and then refuse every later task change.

- `POST /.../{id}/refresh-etk`: `{version}`, edit permission. For an unstarted (`open`, no work session) group: atomically
  re-pins every active member task to its current issued ETK revision (same effect as `tasks/{id}/norms/refresh`),
  recomputes each member's recipe for its own PF and refreshes the member rows (cut length, labels). Since 20.11 a
  member whose new recipe differs is not refused: the most common new recipe keeps this group (id, number, positions;
  tie — the lowest row position), every other recipe moves its rows to an open unstarted group of the same project,
  site, kind and recipe, or to a new explicit group; moved rows are `split` here (`regroup`). Since 20.11 (T2b) a member
  whose reissued ETK changed the PF composition (`cutting_refresh_cards` gives norms `None`) is rebuilt in place by the
  same `rebuild_task` as the CLI restructure below, and all its unstarted preparation PFs are regrouped by kind (the
  cut keeps this group's id and number). Same refusal rules and messages as the CLI (work/output/box on the task, a PF
  in a started group, a disappearing PF with group history or equipment); any refusal is 422 with nothing written.
  Still 422 with nothing written: a member whose new ETK gives no recipe at all. Already current group: no-op, version
  unchanged. Started/frozen group: 422. Group views carry `etk_stale` (some member pinned older than the issued ETK).
  Lock order: project → every group of the project (by id) → member tasks (by id).
  CLI: `python -m app.workflows.cutting_group_existing --project X --refresh-etk [--apply]` (dry-run by default).
- Re-pinning a grouped task (`tasks/{id}/norms/refresh`) is no longer refused while its group is open and has no work
  session: the row keeps its frozen document (the group shows `etk_stale`) until `refresh-etk` moves it. A row behind
  its task's revision counts as stale for `refresh-etk` (also when the card version was already revalidated), and both
  starts refuse it until then with «Обновить из ЭТК» (group start: 422, a member's standalone start: 409), besides
  the card-version check.
  A started/frozen group still refuses the re-pin.

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

`restructure_project` (whole project, CLI); per group over HTTP through `refresh-etk` above — both use `rebuild_task`:

```powershell
.\.venv\Scripts\python -m app.workflows.cutting_group_existing --project '708К.' --restructure            # dry run
.\.venv\Scripts\python -m app.workflows.cutting_group_existing --project '708К.' --restructure --apply
.\.venv\Scripts\python -m app.workflows.cutting_group_existing --project '708К.' --restructure --release-moved-equipment
```

One transaction for the project (project lock, then its groups by id, then tasks). The dry run executes everything and
rolls back. For every running task whose issued ETK revision differs from the pinned one: refused (task untouched)
when it has any work trace (`worked`: session, output, box, defect, delivery, started share), a PF in a started/frozen
group, a disappearing PF with cutting-group history or with an equipment allocation, a kept PF (e.g. the old
«Заготовки» id keeping the cut) with equipment chosen for an ETK operation that moves to another PF or disappears,
or whose code now occurs a different number of times in that PF (ЗАЧ-A/ЗАЧ-B: which one is meant is unknown — the
master binds it again). Inside the PF an operation is followed by its code and its occurrence among the PF's
operations with that code, not by number: an operation inserted elsewhere shifts numbers only, and the binding is
renumbered. A bound number that is not an operation of the old PF stays allowed while the rebuilt PF has that number.
Only current allocations (with a current member) are checked and renumbered; retired ones are history and stay as they
are. CLI option `--release-moved-equipment` (only with `--restructure`, default off): a binding of the kept PF whose
operation moved to another rebuilt PF (labels, tubes) is removed from the kept PF's current allocation instead of
refusing the task — one audit `production.equipment_released` per binding (task id and number, PF, op code, ETK
number, machine, allocation, the new PF and the op's new ETK number); the new PFs stay unbound (planning places them).
Only operations landing in a NEW PF (not a kept one, e.g. assembly) split out of the bound PF itself are released,
matched by occurrence among that code's operations of those new PFs (a same-code operation of another old PF or a
newly inserted one never counts); a different count, a disappearing operation or ambiguity still refuse; retired
allocations are untouched. Renumbering that would put two bindings of one allocation on one ETK number refuses (rebind
by hand). A dry run executes the rebuild and rolls back, but the resource reflow (`sync_reflow`, machine/person
plan claims) runs only on commit, i.e. with `--apply`: a claim conflict can still appear there. Output `released` lists the bindings per КБ (in a dry run: what would be released).
HTTP «Обновить из ЭТК» never releases. Otherwise the task keeps its id,
number, quantity, serial numbers and mark events; operations are rebuilt from the new route
(`restructured_operations`): same ETK operations → same operation (id, assignees, window), PFs split out of one old PF
→ the first keeps the old id and window, the others get its assignees without a window; same scheme block → same
operation; the rest are new. Audit `production.restructured` (with `origins`: new PF → the old PF it was split out
of — for every new PF and every kept PF whose ETK operations changed and whose lineage is not its own id: the old PF
that alone holds all its known operation codes; else the position rule of `restructured_operations` and the code
match (code and occurrence across the whole task) must agree, otherwise the parent is unknown, stored as `null`). A PF
of unknown lineage in a task with a separated PF («Выдать отдельно» or split off by the master) stays standalone (its
own separate group, or ungrouped); equipment on an operation whose new PF has an unknown or another parent is never
released. Known limitation, left as is: the domain (`restructured_operations`) still hands old ids, assignees and
windows by positions, so after an inserted operation a PF may take the id and assignees of an unrelated old PF (e.g.
the tubes PF those of the assembly). Then all unstarted preparation PFs of the project's
tasks are regrouped by recipe (`regroup`): the old group of the cut keeps its id and number; labels and tubes get their
own explicit groups; emptied groups are cancelled. A rerun changes nothing.

Regrouping rules (review of PR #977):
- A group takes a new recipe key only when every active row of it outside the PFs being regrouped already has that
  key (computed from the row's frozen ETK with the current code). Rows of tasks whose rebuild was refused, tasks outside
  `--task-id`, or not running tasks keep their recipe: the regrouped PFs then go to another (or a new) group.
- The master's separation is honoured. A PF without an active membership whose row was `split` from a group (split on
  the group card or the group cancelled) stays standalone while its recipe key equals that row's key; with a changed
  key it is regrouped. Splits the system made (a rebuild, a regroup move) carry `split_by` in the row snapshot and do
  not count, so a PF a rebuild left ungrouped is regrouped on a rerun. New PFs a rebuild split out of such a PF (labels,
  tubes) stay standalone too, also on a rerun: the lineage is read back from the stored `origins` of the task's audit. A PF of a «Выдать отдельно» launch group (`joinable = false`, idempotency key `cutting:…`)
  stays in that group alone: regrouping never moves it elsewhere and never puts other PFs into it; new PFs split out of
  it get one separate group each, as a separate launch makes; a separate group that cannot take its PF back is replaced
  by a new separate group (`cutting:separate:…`), so a rerun changes nothing. Explicit groups (`cutting-existing:…`, from
  existing work or regrouping) merge as before.

Output: `restructured`, `unchanged`, `refused`, `groups`, `moved`, `cancelled`, `released`, `counts.before/after` (tasks,
operations, serials, groups by state and kind, members by state, marking batches, label jobs, mark events),
`invariants` (tasks, serials, batches, jobs, mark events unchanged), `labels` (printed/remaining items and
`remaining_labels` per КБ), `labels_unchanged` (printed, remaining and remaining_labels equal before and after — a
changed fixed label shows in `remaining_labels`), `printed_one_per_kb`. «Before» labels come from the ETK revision each task is pinned to before the
rebuild (a missing stored revision gives none, so the check reports a change); «after» — from the reissued ETK for a
rebuilt task and from its pinned revision for a refused one. A changed label text therefore shows as a change.

## Printed and remaining labels (owner 20.11)

`workflows/marking_progress.py`: no counter is stored; per task, marking batches whose manifest names it and their
jobs. A label counts as printed when its job is `spooled` (unless the tracked printer reported error/cancel) or still
`pending`/`taken`/`unknown`; `failed`/`cancelled` are not printed. Rows match by (task, serial, text), not by the ETK
operation index. An item (serial) is printed when all its numbered labels are. A batch printed «чередовать по
изделию» gives fixed labels the item serial too: a fixed row of a plain preview also takes fixed labels of interleaved
items, a fixed row of an interleaved preview also takes fixed labels of plain batches — neither is printed again.
A numbered label whose expanded text equals a fixed text (numbered «{№}» of item 1 and fixed «1») is never taken as a
fixed one (accepted: a fixed «1» printed inside an interleaved item 1 is then printed again — the safer error).
A reprint job («Перепечатать номера…») resolves to the label it reprints; one label counts once.

- `GET /api/production/marking/progress?group_id=…` or `?task_id=…&operation_id=…` (marking read gate):
  `{rows: [{task_id, task_number, item, quantity, serial_first, serial_last, printed, remaining, printed_serials,
  remaining_serials, remaining_labels, label_size, sizes_differ, wire_mm}], printed, remaining, remaining_labels,
  label_size, sizes_differ, wire_mm}`. A plain read: the issued ETK is read
  without `FOR UPDATE`. `printed`/`remaining` count items by serial; `remaining_labels` counts label pieces still to
  print, numbered and fixed (0 when the task released its serials). «Печатать оставшиеся» on the labels group card is
  enabled while `remaining_labels > 0`, so fixed labels left after every serial is printed can still be printed.
- Preview/batch input `remaining: true` («Печатать оставшиеся»): the same rows minus printed ones; nothing left → 422.
  The print queue is read once per preview for all tasks of the source.
- Batches are found by a text match of the task id over every manifest (base64 PNG included): no table links batches
  to tasks; an indexed lookup needs one (a migration, not done).

## Marking pult: trial, ETK label size, wire Ø (plan 21.11)

- ETK label size = the first «W×H» of a mark `material` (`marking_progress.label_size`): `×`, Latin `x`/`X`, Cyrillic
  `х`/`Х` or `*` between the numbers, decimal comma or dot, «мм» optional; only 5…150 мм (a Godex label) and never a
  tube material. `{width_mm, height_mm, material}` or `null` («размер в ЭТК не указан»). Two sizes are the same within
  ±0,5 мм in either orientation (`same_size`).
- `/progress` rows: `label_size` — the one size of the task's ETK marks (`null` when none parses or they disagree,
  then `sizes_differ: true`); `wire_mm` — sorted distinct `fields.wires[].outerDiameter` (mm, numbers > 0) over the
  ETK operations. Top level: `label_size` when every row's known size agrees, else `null` with `sizes_differ: true`
  (rows without a size do not disagree); `wire_mm` — distinct over the rows.
- Preview/batch input `limit: int|null` (strict, 1…500): only the first N label pieces after the `remaining` filter
  (a row's `quantity` is cut). Materials are checked over everything chosen first, so a trial is refused when the full
  print would be. Serials are issued (on enqueue only) to the tasks whose numbered labels are in the limited rows.
  `limit: 1` = «Пробная»: a real one-label batch of the first remaining label — its serial is spent and `/progress`
  counts it. Released serials still refuse (422 «Номера задания освобождены — печать недоступна»). `limit` with
  `reprint_of` → 422.
- Preview output adds `etk_size` (`label_size` of the batch material, one per batch) and `profile_matches: bool`
  (`true` when `etk_size` is `null`); both are inside the manifest hash.
- `POST /batches`: `etk_size` known, profile off it (`profile_matches: false`) and not `reprint_of` → a blank
  `reason` is 422 «Размер этикетки не как в ЭТК — укажите причину» (after the manifest hash check, nothing persisted);
  with a reason the batch stores it and the journal gets `production.marking_size_deviation`
  `{batch_id, etk_size, profile {id, name, width_mm, height_mm}, reason}`. Enforced for every caller (machine card,
  cutting group card, project ETK). `/batches/{id}/reprint` and `reprint_of` keep their own reason only.
  Stored `reason` with `limit: 1`: «Пробная печать» or «Пробная печать · <причина>» (whitespace collapsed; ≤ 1000).
- «Свой текст» input `wire_mm: float|null` (0,1…60): kept in `source_snapshot.manual.wire_mm` (in the hash) and in
  `production.marking_manual_queued`; it checks nothing and issues no serials.

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
  Quantities are whole pieces in PF units (sent as decimal strings): a fraction → 422 «Годные и брак — целое
  число штук». Duplicate/foreign members, overproduction and invalid defect details are refused. Rows may be omitted or have zero quantities; at least one must be positive.
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
submission). Group reads add `session` (ordinary session fields plus `cutting_group_id` and `operator_name` — who
runs it, for the card's «Группу ведёт <имя>»; "" when the employee is not found — or null)
and `downtime: {id, reason, reason_name, comment, started_at}` (or null). `/my/execution` also marks
the shared session with `cutting_group_id`; other work remains blocked until it completes.

An identical key, actor-authorized performer, group, action and payload replays the committed response
before rejecting a stale version. A different payload/action/performer for that key returns 409.
Other version/resource conflicts return 409, unauthorized membership/site returns 403, invalid state
or quantities return 422. Group and child locks use the existing resource/project/task lock order.

Lock order (T2b, every cutting path): project advisory lock → groups (`with_for_update`, sorted by id) → tasks (sorted
by id). `locked_group` takes this group (refresh: every group of the project), restructure every project group;
launch late-join locks candidate groups by id; `task_changed` re-locks the task's groups by id (already held on cutting
paths; elsewhere the project lock taken first serializes it). Verified by a lock-sequence spy on SQLite only —
PostgreSQL concurrency is unproven.

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

## Очередь резки (owner 20.11, manual mode)

«Оптовая нарезка по станку, через все проекты»: one list per machine of every open cable cut, the operator goes row by
row. `cutting_queue.py`, routes in `cutting_api.py`. Computed on every read — no table, no migration.

- Rows: active members of `open`/`frozen` groups of kind `cut` (their frozen issued document) and input-less PFs of
  running tasks without an active membership whose ETK operations include a cable cut (`coaxCut`, `cutWire`,
  `cutWireStrip`; read from the task's pinned issued revision). Labels/tubes PFs and groups are not rows. A task handed to
  another site, a cancelled/closed task, a PF whose good + defect output reached its quantity, and work of another site
  (group: `check_access`; standalone: handed-to/assignee sites vs the viewer's site) are left out. The old mixed
  «Заготовки» PF is one row: its output covers the whole PF, as everywhere.
- Standalone PFs are read from the ETK revision the task is pinned to (one card read per project; a card taken back to
  draft still gives that revision). No readable ETK (no card, no issued revision, techcards not configured) never drops a
  running input-less PF: it is listed under «Станок не указан» with `warnings: ["нет в ЭТК: выданная карта"]`, values
  `null` and `blocked` «Нет ЭТК задания — не понять, резка ли это: готовые вводит мастер» (it may be a labels or tubes PF).
  A standalone PF with an open individual work session (active or paused) is listed with `blocked` «По этому ПФ идёт
  работа на карточке — сдайте там» — the same text «Готово» refuses with; the screen keeps that PF's card.
- One read per request (`collect`): running tasks, their outputs and the active member ids in a few queries; members
  of finished or closed work are dropped before the group access check and before their snapshot (the frozen document)
  is read; open group sessions and operator names are read once.
- Machine = what the cut operation's card tooling «Оборудование» names (`inventory_number`, else
  `equipment_model_id`; owner 20.11, final — 708К. today names ОБ.094 BZW-886+Q2). Several — the row is on EACH of
  those machines' lists with the same `key`, so one «Готово» removes it everywhere; on a machine's list its
  `machine_id`/`machine_name` is that machine, in «Все станки» the first the card names (the default). `machines` lists
  them all in card order. None — never put on a real machine: the row gets `machine_id: null`, `machines: []`, «нет в
  ЭТК: станок», and its own bucket «Станок не указан» (`machine_id=none`). No machine is stored per row; «Готово»
  records a machine only when the screen sends `equipment_id` (below): the operator may work a row on any machine.
  No machine is preferred any more (the old «ОБ.095 if listed» rule is gone — owner 20.11, final).
- Pool (owner 20.11, «любой свободный»): a tooling row the БД.ОБ catalog (`EquipmentCatalog.machine`, article
  tie-break included) resolves to a model with SEVERAL physical units, none of them the row's exact number (`ОБ.100.X`,
  bare `ОБ.100`, a model id only), is keyed by the MODEL id (`machine_id` = model id, not the inventory number) and
  labelled «ОБ.100 BZW-D2.0S · любой свободный (3)» — 3 = physical units of the model. A model with one physical unit is
  not a pool: its row is keyed by that unit's inventory number. Rows the catalog cannot resolve keep their raw key. A
  pool is one of the row's `machines`, like any other. `/machines` lists the pool as one machine, sorted by catalog
  code then label. The catalog is read only when the app has the references factory.
- A missing or odd ETK value never drops a row: the value is `null` and `warnings` says «нет в ЭТК: длина / провод /
  зачистка А / режим зачистки А / …». A grouped row whose frozen ETK lacks the cut stays with «нет в ЭТК: резка кабеля».
- Order: setup key (wire, then side A and B strip values/mode/diameters, then laser marking mode), then length short →
  long (unknown last), then project, КБ, task number. `setup_change` — the row's setup key differs from the previous
  row (always true on the first); `setup_text` — the banner («Заправьте RTK 031 · зачистка А: 3/7,6/12,7 мм, полная ·
  зачистка Б: … · лазер по краям»; «без зачистки» when no side is stripped).

`GET /api/production/cutting-queue?machine_id=ОБ.095` (myops view; `machine_id=none` — the «Станок не указан» bucket,
answered with `machine_id: null`; `machine_id=all` — «Все станки»: every row of every machine once, same order, answered
with `machine_id: "all"`). `?equipment_id=<БД.ОБ unit uuid>` instead (wins over `machine_id`): the rows naming the unit
itself (its inventory number) or its model's pool, with that machine as the row's `machine_id`; the answer carries
`equipment_id`; a record that is not a physical machine (`.X`, `(R)`, a hand tool with an ОБ number, non-machine,
unknown) → 422; no references factory → 503; neither parameter → 422:

```text
{machine_id, equipment_id, version, rows: [{key, task_id, operation_id, task_version, task_number, project, item,
  group_id, group_number, group_state (open|frozen|null), machine_id, machine_name,
  machines: [{machine_id, machine_name}],
  wire, wire_name, wire_title, length_mm, planned, done, left,
  strip_a, strip_a_mode (full|partial|null), strip_a_program (confirmed|unconfirmed|absent; wires: null),
  strip_b, strip_b_mode, strip_b_program, diameters, laser_mark, marking_mode (full|ends|null),
  operator_ids, operator_names, warnings, blocked, setup_change, setup_text}]}
```

`wire` is the article (always full). `wire_name` is for display: a name up to 40 characters as is, a longer one (supplier
description) is replaced by the article, else cut at a word with «…». `wire_title` is the full text (tooltip). The setup
sort/grouping uses the full name. The setup text names the article in full.

Decimals are strings (`length_mm` mm; `planned`/`done`/`left` in PF units, `done` = good + defect). `strip_*` is `null`
when that side is not stripped; coax values are the confirmed program boundaries from the conductor tip, else L1/L2/L3.
`version` is a digest of (row, left, task version, group state, machine, machines, length, setup text, warnings,
blocked) for change detection. `project` is the issued ETK's spelling (`row_project`), the same in the «Готово» notice.

`GET /api/production/cutting-queue/machines` (myops view): `{machines: [{machine_id, machine_name, rows, left, projects}]}`
— «Все станки» (`machine_id: "all"`, every row once) first, then machines with open cut work by catalog code, label and
id (a row naming several machines counts on each; a pool is one entry), the bucket without a machine (`machine_id: null`, «Станок не указан») last. No open
cut work at all — `[]`.

`POST /api/production/cutting-queue/done` (myops edit, optional `?on_behalf_of=`, required `Idempotency-Key`):
`{task_id, operation_id, quantity, equipment_id?}` (integer > 0; `equipment_id` — the physical unit that did the work).
One transaction, lock order project → group → task:

0. Only queue rows: an active member of an `open`/`frozen` cut-kind group, or a standalone input-less PF whose pinned
   issued ETK holds a cable cut. Anything else (labels/tubes PF grouped or split off, a PF with inputs) → 422 «В очереди
   резки сдаётся только резка кабеля — этот ПФ сдают на его карточке»; no readable ETK → 422 «Нет ЭТК задания — …».
   Checked before anyone is assigned or anything is posted.
1. Owner 20.11 (final): any user with access to that site's tasks may press «Готово». The presser who is not a монтажник
   (master, technologist; not on the PF) posts as «Ввод готовых»: the output is under the presser, nobody is assigned.
   A монтажник not yet on the PF (himself, or the one worked for via `on_behalf_of`) joins its assignees through the
   ordinary `assign_task`; its refusals apply — e.g. another site of a grouped row, «… не монтажник» when working for a
   non-монтажник. Site rules and the cut-only rule (step 0) hold for everyone. An account without an employee record
   or with an empty site (no site to limit it) may press only with production edit (master/admin, as «Ввод
   готовых»; the site rules of the operation/group then apply as for a site-less master); otherwise 403 «Вас нет в
   справочнике сотрудников — …» / «У вас в справочнике сотрудников не указана площадка — …». Existing assignees and
   their facts stay.
   Known effect: on a grouped row only that member's PF
   is assigned (the group's own `assign` replaces every row's assignees and refuses a started group, so it is not
   used), so the group card's common assignment becomes inconsistent (`assignment_consistent: false`, no common
   assignees shown) and `assign_task` re-plans that PF's window from now (or from its planned start when it already had
   assignees). Group start still needs one performer on every member — the master reassigns on the group card.
2. The pieces are the PF's manual output (`enter_manual_output`, as «Ввод готовых»): box and output on that КБ's own PF,
   no work session and no time-log entry, plan fact moved; over the quantity left → 422 «Этап N «…»: уже сдано X из Y,
   осталось Z.»; a task not running or handed over → 422.
3. Grouped rows follow the group card rules: without an open group session the output is accepted and the first one
   freezes the group (`frozen`, membership fixed); a group started on its card (open group session, whoever runs it)
   is listed with `blocked` («Группа РЗ-… начата на карточке группы (Имя) — сдают там») and «Готово» is 422 «Группа
   РЗ-… начата на карточке — сдайте там», checked in `mark_done` before anyone is assigned or anything is posted: the
   group session submits there.

Returns `{notice: "Готово: 708К. · КБ3 · 120 шт", assigned, box: {id, number, code}, output_id,
row: {key, task_id, operation_id, task_version, planned, done, left}}`. The receipt is an audit event
`production.cutting_queue_done` whose id is `uuid5(key)`: the same key and payload (task, PF, quantity, performer) replays
the stored response without a second output; the same key with another payload → 409. 403 another site, 404 unknown
task/PF. With `equipment_id` the unit must serve one of the row's machines (its inventory number, or its model for a
pool) and, when the PF has a site (handed-to site, its assignees' and the performer's — not the presser's own: an admin
may enter «Готово» for a worker of another site), stand on that site — else 422 with the reason; a unit with an unknown
site is allowed. The unit id and inventory number go into the receipt details (`equipment_id`, `inventory_number`) and
into the replay fingerprint. The receipt is also the «Журнал» line «Готово в очереди резки» (`frontend/src/auditLabels.ts`): actor = the
presser; details `{project, item_index, task_number, quantity, performer, pressed_by, operator_id, pressed_by_id,
task_id, operation_id, replay_key, fingerprint, result}` (`performer` — whose pieces: the монтажник, or the presser when
posted as «Ввод готовых»; the journal hides ids, `replay_key`, `fingerprint`, `result`). Receipts written before
20.11 evening carry `key` instead of `replay_key` and no names. Without `equipment_id` no machine is recorded;
auto-write to ОБ.019 is a later PR.

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

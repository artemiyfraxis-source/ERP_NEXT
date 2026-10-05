---
name: drawing-to-assembly
description: Read a product drawing (PDF/photo) and draft its assembly scheme — a «Ручная сборка» build_config of blocks and operations — through the ERP API, then learn from the master's correction (draft vs final → lesson → rule). Use when the owner sends a drawing to turn into an assembly, or says «чертёж», «собери схему по чертежу», «черновик сборки», «схема сборки по чертежу», «урок по черновику», «что поправил мастер».
---

# drawing-to-assembly — чертёж → черновик «Ручной сборки» → урок

You read a drawing, write down what it says, map it to operations, check the draft with the real ETK builder
through the ERP API and save it next to the project position. The master corrects it in «Ручная сборка» and builds
the techcard naming the draft; then you compare draft and final, write the lesson and keep the rule below.
Everything goes through the API; you never touch the database, the project techcard or the build endpoint.

Reference (read the parts you need, don't paste them back):
- `docs/assembly-corpus/README.md` — the corpus format (spec / draft / final / lesson) and naming.
- `docs/assembly-corpus/build-config.schema.json` — what a manual config may contain.
- `docs/assembly-corpus/vocabulary.md` — library operation codes and which generator type picks each.
- `backend/app/modules/techcards/etk_builder.py` — `_build_manual` (the truth when the schema and docs disagree),
  `GENERATOR_TYPES`, `_result_name`, `_components`, `_time`.
- Example configs: `backend/tests/techcards/test_etk_manual.py`, `test_assembly_drafts_api.py`,
  `test_etk_golden.py` (`_cut_crimp`, `_splice_strip`, `_coax`).

## 0. Auth and calls

- Token: env `ERP_API_TOKEN` (an API token with the area «Черновики сборки» = scope `assembly`; README «Как прислать
  чертёж» says how the owner issues one). Base URL: env `ERP_BASE_URL`, default
  `https://erp-server.tail811f20.ts.net` (production — real data; only the calls below).
- **Never print, echo, log or commit the token.** Refer to it only as `$ERP_API_TOKEN` / `os.environ[...]`. No `set -x`,
  no `env` dumps, no token in a file inside the repo. If it is missing, tell the owner to set it; don't ask for its
  value in chat.
- Big JSON bodies go into a file in the scratchpad, not on the command line.

```bash
BASE="${ERP_BASE_URL:-https://erp-server.tail811f20.ts.net}"
curl -sS -H "Authorization: Bearer $ERP_API_TOKEN" "$BASE/api/techcards/assembly/corpus?q=коакс"
curl -sS -X POST -H "Authorization: Bearer $ERP_API_TOKEN" -H "Content-Type: application/json" \
     --data-binary @"$SCRATCH/draft-body.json" "$BASE/api/techcards/assembly/drafts?dry_run=true"
```

```python
import json, os, urllib.request
BASE = os.environ.get("ERP_BASE_URL", "https://erp-server.tail811f20.ts.net").rstrip("/")
def call(method, path, body=None):
    request = urllib.request.Request(BASE + path, method=method,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {os.environ['ERP_API_TOKEN']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:          # 422 detail is Russian and says what to fix
        return error.code, json.loads(error.read() or b"null")
```

Endpoints (all under `/api/techcards/assembly`, token scope `assembly`):

| Call | What |
|---|---|
| `POST /drawings` `{project, item_index, file_name, content_base64}` | drawing → КД of the position (PDF/JPG/PNG ≤ 20 MB); returns `id` |
| `POST /drafts?dry_run=true` `{project, item_index, drawing_document_id, source, build_config, note}` | check with the real builder, store nothing; returns `warnings`, `operations`, `route` |
| `POST /drafts` (same body, `dry_run=false`) | store the draft (201, `draft.id`) |
| `GET /drafts?project=&item_index=` | drafts of a position, newest first (no configs) |
| `GET /drafts/{id}` | one draft: `status` (`draft`/`final`), `draft_config`, `final_config`, `final_revision`, `lesson`, `note` |
| `PUT /drafts/{id}/lesson` `{lesson}` | «что поправил и почему» + rule (replaces the previous one) |
| `GET /corpus?project=&q=` | finalised examples (draft, final, lesson), newest first, ≤ 50; `q` = substring of lesson/note/project/КБ/assemblyName |
| `POST /from-generator` `{config}` | a generator config → manual config (pure, stores nothing) |

**Never call** `POST /api/techcards/projects/{id}/build` (or anything else outside this table): the build is the
master's, from a cookie session. The token is refused there anyway (403); don't try.

## 1. Inputs from the owner

- the drawing (PDF or image), the project number (e.g. `708К.4`), the position КБ (`item_index`, e.g. `КБ1`),
  the batch (`partQty`, тираж).
- Ask **only** if the project or КБ is missing — one short question. No batch given → `partQty` from the drawing or
  1, and say so in the note. Everything else: decide and record in the note.
- The position must already exist in «Проекты» (kit card with that КБ); a 422 «Карточка проекта … не найдена» /
  «нет сборки КБ…» means the owner has to create it first — tell him, don't invent another position.

## 2. Read the drawing → `spec.md` facts first

Before any operation, write the facts in the corpus `spec.md` format (README «spec.md») into the scratchpad
(`<scratch>/<drawing-id>/spec.md`, drawing-id as in README «Именование», e.g. `708k-4-kb1`). Facts only, no assembly
decisions:

- product: designation, name, batch;
- wires: marking/grade, section (мм² / AWG), colour, length with tolerance, quantity, ends A/B;
- contacts/terminals: article, crimp or solder, side; connectors/housings: article, pinout (which circuit in which
  position);
- coax: cable grade, connector per side, centre contact (solder / crimp, R/A «внутри корпуса»), shield (обжим / пайка),
  cover (обжим / винт), strip dimensions L1–L3 (D1–D3) if given;
- heat-shrink (ТУТ/ТТК): article, length, position, count; marking: what, where, how; braiding / tape: article,
  length; splices (УЗ-сварка / пайка); soldering points; final assembly (стяжки, гофра, заливка…);
- requirements: tolerances, standard (IPC/WHMA-A-620…), notes.

Then a list **«Не читается / не указано»** — every value you could not read or that the drawing doesn't give. Never
guess a length, article or section silently: put «не указано» in spec, and if the draft needs a value, take it from
a corpus example or the rules below and name it in the note as an assumption.

## 3. Retrieve before mapping

1. `GET /corpus` with `q` = 1–3 distinctive words (connector article, cable grade, «коакс», «жгут», «пайка») and once
   with the project; read `lesson` and `final_config` of the closest examples (final, not draft — final is what the
   master accepted).
2. Read **Rules learned** at the end of this file.
3. A lesson's rule or a rule below beats your own guess. If two contradict, the newer one wins; mention it in the note.

## 4. Map to operations

`build_config` = `{"mode": "manual", "projectNo", "assemblyIndex", "assemblyName", "partQty", <generator data>,
"ops": [...], "route": {...}}`. `assemblyName` — the product name from the kit card / drawing; `projectNo` and
`assemblyIndex` — the position.

### 4a. Generator-typed operations (preferred where they fit)

A generator op keeps the generator's own fields and is built by the generator's typed code: lengths, terminals and
component norms come out right without hand-typed kit lines. Its `qty` is ignored (it counts its own wires /
terminals / segments). Give it `templateId` + `opNum` of the card (lookup in 4c); `tPrep` (s per batch), `tOp`
(s per piece), `tMachine`; optional `label`, `params`, `note`, `result`, `components`, `seq`.

Shared data at the top level of the config (an op may carry its own `wires` instead — it then uses those):
- `wires`: `[{"name", "art", "qty", "length" (mm), "cores"?}]`
- `strip`: `{"a": bool, "lenA": "5", "b": bool, "lenB": "5"}` (used by `cutWire`; `cutWireStrip`/`cutWireCrimp` carry
  their own `strip`)
- `twist`: `{"enabled": true, "mode": "pairs", "pairs": [[i, j], …] (indices into wires), "pitch": ""}`
- `tin`: `{"enabled": bool, "stripLen": "2,5"}`
- `coax`: `{"cableArt", "sideA": {"article", "l1", "l2", "l3", "d1", "d2", "d3", "lPlus", "lMinus"}, "sideB": {…}}`
- `connections` (optional pin-to-pin list): `[{"wire", "from": {"end", "conn", "pin"}, "to": {…}}]`
- endpoint object `ep` for terminal/connector ops: `{"connArt", "connName", "termArt", "termName", "double": false,
  "perTerm"?, "label": ""}`

| type | what | fields it reads (besides tPrep/tOp/templateId/opNum) | typical card |
|---|---|---|---|
| `cutWire` | cut wires | `wires`; top-level `strip` | `CUT_WIRE_auto`, `CUT_WIRE_hand`* |
| `cutWireStrip` | cut + strip | `wires`, `strip`, `cutTolerance`? | `CUT_WIRE+1strip`, `CUT_WIRE+2strip`, `CUT_COAX+…` |
| `cutWireCrimp` | cut + crimp on the machine | `wires`, `strip`, `side`, `ep` (termArt), `tin`? , `terminalNorm`? | `CUT_WIRE+1crimp`, `CUT_WIRE+2crimp`, `CUT_WIRE+1crimp+tin` |
| `prsTerm` | crimp terminals | `side` A/B, `ep` (termArt/connArt), `wires`, `wireSources`?, `firstTerm`? | `PRS_TERMINAL_auto`, `PRS_НШВИ`, `PRS_ТМЛ`… |
| `insTerm` | insert terminals into housing / RJ | `side`, `ep` (connArt), `wires`, `pins`? (per wire, «1-3,5»), `wireSources`? | `INS_terminal_<pitch>`, `INS_RJ_…` |
| `solderConn` | solder a connector | `side`, `ep` (connArt), `wires`, `pins`? | `SDR_XT60`, `SDR_DB`, `SDR_GX12`, `SDR_USB_…` |
| `splice` | splice (US weld / solder) | `units` | `US_WELD`, `SDR_W_W` |
| `insHousing` | mount a housing | `units` | `INS_housing_DB/DH/СНП`, `INS_housing_GX`, `INS_housing_USB` |
| `cutTut` | cut heat-shrink | `tutArt`, `tutName`, `tutCount`, `tutSegs` `[{len, count}]` | `CUT_TUT_short` |
| `insTut` | put heat-shrink on | `tutCount`, `harness` (on the harness) / `spliceWeld` (on splices) | `INS_TUT` |
| `heatTut` | shrink | `tutCount`, `harness` / `spliceWeld` | `HEAT_TTK_short/long`, `HEAT_TYT_short/long` |
| `stripCable` | strip cable ends | `tutCount` (ends) | `STRIP_WIRE`, `STRIP_CABLE…`, `STRIP_…_ethernet` |
| `endCap` | seal free ends | `tutCount` | `INS_END_CAP` |
| `foldCore` | fold the core | `tutCount` | `PREP_FOLD` |
| `twist` | twist pairs | top-level `twist`, `wires` | `TWIST` |
| `tin` | tin ends | `wires` | `SDR_tin` |
| `insWrap` | tape / braid | `wrapKind` `tape`/`braid`, `wrapArt`, `wrapName`, `wrapLen` (mm), `tutCount` | `INS_WRAP`, `INS_BRAID` |
| `insMark` | marking | `marks` `[{art, name, qty}]` | `INS_MARK_HARNESS`, `INS_MARK` |
| `seal` | sealing | `sealArt`, `sealName`, `sealPlaces` | `SEAL` |
| `coaxCut` | cut coax cable | top-level `coax` | `CUT_WIRE_auto` |
| `coaxCutTut` | cut coax heat-shrink | `units` (sides) | `CUT_TUT_short` |
| `coaxStrip` | strip coax side | `side`; dimensions from `coax.sideA/sideB` | `STRIP_COAX_auto` |
| `coaxPin` | solder centre contact | `side` | `SDR_COAX_pin`, `SDR_COAX_pin_R/A` (R/A — contact inside the housing) |
| `coaxInsTut` | put coax heat-shrink | `units` | `INS_TUT` |
| `coaxInsSleeve` | shield sleeve on | `units` | `INS_SLEEVE_coax` |
| `coaxHousing` | mount coax housing | `side` | `INS_housing_COAX` |
| `coaxShield` | crimp shield sleeve | `units` | `PRS_SLEEVE_coax` |
| `coaxSolderShield` | solder shield | `side` | `SDR_COAX_housing` |
| `coaxHeat` | shrink coax heat-shrink | `units` | `HEAT_TYT_short` |
| `coaxCapCrimp` / `coaxCapScrew` | cover by crimp / screw | `units` | `PRS_CAP_COAX` / `INS_CAP` |

\* `CUT_WIRE_hand` has no generator type in vocabulary.md — as a hand cut use `libraryOp`.

Generator coax order (the master expects it unless the drawing says otherwise): cut → cut TUT → strip A (B) →
centre contact A (B) → TUT on → sleeve (shield «обжим») → housing A/B (not when the contact is «внутри корпуса») →
shield solder A/B or crimp sleeve → shrink → cover (crimp/screw) for R/A sides.

Reference norms seen in production generator configs (seconds; use only when no corpus example is closer, and list
them as assumptions in the note): cutWire 180/3,5 · cutWireStrip 600/6,5 · cutWireCrimp 3600/4 · prsTerm (auto)
1800/6 · insTerm 120/10 · solderConn 120/35 · splice 120/25 · cutTut 300/1 · insTut 120/3 · heatTut 600/15 ·
twist 120/11 · insWrap 120/20–30 · insMark 120/20 (tPrep/tOp).

### 4b. Library and own operations (otherwise)

- `{"type": "libraryOp", "templateId", "opNum", "label"?, "qty" (per item), "tPrep", "tOp", "params"?, "note"?,
  "side"?, "components": [{"art", "name", "qty" (per item), "unit"}]}` — any card of vocabulary.md, also those with
  «—» as generator type (hand variants, `INS_стяжки`, `INS_гофра_…`, `MOLD_…`, `SDR_PCB`…). Kit lines are not
  computed for it: put the wires/parts it consumes into `components` per item.
- `{"type": "customOp", "label", "qty", "tPrep", "tOp", "steps": [{"kind": "Операция"|"Контроль", "text",
  "tolerance"?}], "tooling": [{"section": "Инструмент"|"Оборудование"|"Расходники", "name"}]}` — no card fits. Give it
  real steps and at least one «Контроль» step with a tolerance; say in the note «нужна карточка: …». Never invent a
  card code.

### 4c. templateId lookup

`templateId` is the card id, never made up. Look it up by code in `backend/data/techcard-library.json` and
`backend/data/techcard-library-erp.json` (`templates[].id`, title «CODE | Название»); sided cards have ids ending
`-a` / `-b` — take the one of the op's side.

```python
import json, pathlib
cards = [t for f in ("techcard-library.json", "techcard-library-erp.json")
         for t in json.loads(pathlib.Path("backend/data", f).read_text(encoding="utf-8"))["templates"]]
by_code = {}
for t in cards:
    by_code.setdefault(t["title"].split("|")[0].strip(), []).append(t["id"])
```

A card missing in the server's library is a warning in dry-run, not an error — fix the id rather than leave it.

### 4d. Blocks (полуфабрикаты)

`route = {"blocks": [{"key", "name", "ops": [positions, 1-based], "final": bool, "components"?}], "edges":
[{"from", "to", "per_unit"}]}`:
- every op in exactly one block; exactly one `final` block (the product — leave its `name` empty, it becomes
  `assemblyName`); every non-final block feeds some block through an edge;
- a block = one semi-finished product made in one go: e.g. cut wires, wires with crimped terminals side A, cut TUT,
  braided sub-harness, coax cable stripped and soldered; ops of a block run in list order;
- `per_unit` = pieces of the source's product per **one** piece of the target (2 identical wires per cable → 2);
- parallel branches (cut TUT while wires are crimped) → separate blocks both feeding the next one;
- no `route` → a plain chain in list order (fine for a 2–3-op product).

Naming: keys short latin (`cut`, `prsA`, `tut`, `coaxA`, `final`); block names in Russian, product-shaped, with the
distinguishing fact: «Провод ПВАМ 0,5 черный 300 мм, обжат SXH-001T-P0.6 (ст. А)», «ТТК 4,8/1,6 — 3×20 мм»,
«Кабель RG-174, разделан и пропаян (ст. А)». Op `label` only when the card name is not enough («Пайка экрана к
лепестку»). Russian everywhere a person reads it; no type codes in names.

## 5. Validate and save

1. Upload the drawing once: `POST /drawings` (base64 of the file, `file_name` with its real extension). Keep the
   returned `id`; on retries reuse it, don't upload again.
2. `POST /drafts?dry_run=true` with `source: "claude-code"`, `drawing_document_id`, `note`. A 422 `detail` is Russian
   and names the op («Ручная сборка: операция 3 …», «Схема полуфабрикатов: операция 5 не входит ни в один блок») —
   fix and repeat. Read every `warning` («нет карточки», «не задана норма времени», «не заполнен ход операции») and
   fix what you can; the rest goes into the note.
3. Check `operations` (count = your ops) and `route` (blocks and inputs as you meant).
4. Save: same body, `dry_run=false` → 201 and `draft.id`. `note` (≤ 20 000 chars, Russian, short): what the drawing
   says in 3–6 lines (the spec summary), assumptions taken, unreadable items, cards that are missing, remaining
   warnings.
5. Tell the owner: draft id, position, number of ops/blocks, the assumptions, and that the master opens it in
   «Ручная сборка» and builds the techcard with this draft. Keep `spec.md` in the scratchpad until the lesson.

## 6. After the master corrects and builds → lesson → rule

1. Find the draft: `GET /drafts?project=…&item_index=…` → `GET /drafts/{id}`. `status` must be `final`
   (`final_config` set); while it is `draft`, the master hasn't built yet — stop.
2. Diff `draft_config` vs `final_config`: ops by `seq` (else position) — added, removed, moved, type/card changed,
   `tPrep`/`tOp`/`qty`/fields changed; `route` blocks and edges; top-level data (`wires`, `partQty`, `coax`…).
   Write a short list, each item pointing at an op number or block key.
3. Lesson (README «lesson.md»), Russian:
   ```
   Что поправили:
   1. оп. 4 (insTerm, ст. B): tOp 10 → 14 …
   Почему: …
   Правило: если на чертеже …, то …
   ```
   «Почему» — from the owner's words; if he didn't say and it isn't obvious, ask him once; otherwise write
   «причина не названа» and mark your guess as «предположение». A rule always has a condition («если X, то Y»);
   unconditional only if the master said exactly that. No changes → «черновик принят без правок».
4. `PUT /drafts/{id}/lesson`.
5. Append the rule to **Rules learned** below: one line, dated, with the drawing-id and draft id prefix —
   `- 2026-10-05 · 708k-4-kb1 (a1b2c3d4): если …, то …`. Keep it short; merge with an existing rule instead of adding
   a near-duplicate; replace a rule the new lesson contradicts (say so in the line).
6. Optional export of the example into the repo: `python scripts/assembly-corpus/export.py --dry-run`, then without
   `--dry-run` (writes `docs/assembly-corpus/<drawing-id>/`). Copy your `spec.md` over the exported one when yours is
   fuller.
7. Commit the skill change (and the export) on a working branch, never directly on `main`: Russian subject
   («Схемы сборки: правило из урока 708К.4 КБ1»), the owner's identity, no Claude trailers; `main` changes only
   through a PR (`.claude/team/RULES.md`).

## Don'ts

- No `POST /api/techcards/projects/{id}/build`, no project-card edits, no other ERP writes than drawing upload, drafts
  and lessons.
- No token in output, files, commits or URLs.
- No guessed articles/lengths without saying so; no invented card codes or templateIds.
- No SUPPLYMAP: the ERP's corpus, code and the owner's words decide.

## Rules learned

<!-- One line per rule: `- YYYY-MM-DD · <drawing-id> (<draft id prefix>): если …, то …`. Newest last. -->

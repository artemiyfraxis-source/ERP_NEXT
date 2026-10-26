# Note for Артемий's Claude — branch `vitya/parser` (Витя, 26.10.2026)

Витя asked for this branch to be taken over by Артемий's session: merge it AFTER your own pending commits and adapt
it to them yourself. You own the merge; this branch is a proposal, not a claim on your code. Nothing here needs a
migration, nothing touches the warehouse's scan flow.

## What it is
The УПД registry («Реестр УПД», module `purchasing`) gets its own PDF parser — a Python port of the Google Apps Script
parser that has filled this registry until now (via `rb_import`). Unlike the warehouse's scan reader it reads the
money: prices, net / VAT / gross per line and totals, VAT rate, KPP, buyer INN, country, ГТД, the document kind (УПД,
СФ, ТН+СФ, ТОРГ-12, акт, М-15, КСФ, «прочее»), basis and payment documents — and runs the old parser's arithmetic and
INN checks (`qty × price = net`, VAT by rate, line sum = total, file-name amount, INN checksum, our INN as seller).

| File | Role |
|---|---|
| `backend/app/modules/purchasing/upd_parse.py` | pure: prompt, JSON schema, values, line repair, document checks, registry values |
| `backend/app/modules/purchasing/upd_pdf.py` | writes: upload «В очереди», claim («В очереди» → «Разбирается», one process wins), store parse / failure; buyer's manual header/lines, «Проверено», supplier, project untouched |
| `backend/app/workflows/upd_pdf_parse.py` | the port: page pictures + text layer → ONE model call (all pages) → normalize → store; background task; CLI (`--benchmark`, `--queued`, `--all`, `--ids`, `--apply`) |
| `backend/app/modules/purchasing/api_upd.py` | `POST /api/purchasing/upd/import-pdf`, `POST /api/purchasing/upd/{id}/reparse` (background parse) |
| `backend/app/modules/purchasing/upd_checks.py` | «Разбирается» reads as `waiting` |
| `frontend/src/PurchasingUpd.tsx`, `PurchasingUpdCard.tsx`, `purchasingUpdApi.ts` | «Загрузить PDF» dialog; «Разобрать заново» / «Обновить» in the card |
| `backend/tests/purchasing/test_upd_parse.py`, `test_upd_pdf.py`, `frontend/src/PurchasingUpd.test.tsx` | tests (the JS parser's test cases ported) |

Uploaded PDFs keep origin `google` (= «paper document read by a model» everywhere: doc chain, twins, screens), key
`pdf-<sha1>`, folder «Загрузка в ERP». The same file already in the registry (by md5, e.g. imported from Roller_Buyer)
is reported, not added.

## The model key — yours
The parser uses the SAME settings as the warehouse scan reader: `warehouse.receipt_llm` config (env
`ERP_UPD_LLM_BASE_URL / _API_KEY / _MODEL / _FALLBACK_MODEL / _EFFORT` over «Склад» → «Распознавание УПД»). So on prod it
works with your relay key as soon as it is set there — nothing to add. Without a key uploads wait «В очереди» and
«Разобрать заново» answers 409. Витя did not have the key: the code is tested with a fake model only, NO real-model
run was made. Please run, on a prod copy with your key:

```
ERP_DATABASE_URL=… python -m app.workflows.upd_pdf_parse --benchmark          # writes nothing
```

It reads every PDF whose document also came as an exact EDO XML (~14 on Roller_Buyer's data) and prints, field by
field, the errors of the stored Google parse vs the new reading against the XML. Baseline of the stored parse
(gemini-3.5-flash-lite), measured by Витя: number/date/KPP/line count 0 errors; INN 1/14; total 1/14; payment numbers
3 real; 1 line qty↔price swapped; ~13/51 lines with wrong characters in names. Switch the registry to the new parser
only if it is not worse on any field. Re-reading all imported PDFs is `--all --apply` (paid: ~500 documents; run
without `--apply` first on `--limit 5`).

Transport choices made (decided by Витя's agent, check them):
- `response_format: json_schema` (non-strict); a relay that refuses it gets the plain request (`receipt_llm.complete`
  drops a refused optional parameter).
- Pages as JPEG via `receipt_ocr.page_images` + `page_turn` + `receipt_llm.page_jpeg` (≤ 10 pages); the PDF text layer,
  when it has ≥ 200 Cyrillic letters, is appended to the prompt as the source of exact characters.
- One call per document with `CALL_TIMEOUT = 300 s` (background task, no proxy minute). If your relay/model is slow on
  multi-page documents, lower pages or raise the timeout there.
- Gemini's own OpenAI-compatible endpoint (`https://generativelanguage.googleapis.com/v1beta/openai/`) should work
  as `ERP_UPD_LLM_BASE_URL` too, if you ever want the models without a relay (documented by Google, not tried here).

## What to adapt when merging
- `receipt_llm` / `receipt_ocr` / `scan_recognition.llm_config` are your warehouse code; the workflow imports
  `complete`, `parse_answer`, `message_text`, `page_jpeg`, `page_images`, `page_turn`, `OcrUnavailable`, `LlmConfig`,
  `LlmError`, `llm_config`. If you rename or move them (e.g. into `app/shared`), change the imports in
  `app/workflows/upd_pdf_parse.py` and the monkeypatches in `tests/purchasing/test_upd_pdf.py`.
- If you add registry origins or statuses: `upd_parse` statuses «В очереди» / «Разбирается» / «Ошибка» and
  `upd_checks.registry_status` must stay consistent.
- Natural next step (yours to decide): the warehouse scan could call this parser for the document and keep its own
  item fields (article/maker/type/short name) — then «Скан склада» registry documents get prices too. Not done here:
  `upd_scans` rewrites scan documents from the delivery, so a registry parse of an origin-`scan` document would be
  overwritten.
- Gate on Витя's machine: see the PR body (architecture tests + the new tests + the full `npm run check`).

Roller_Buyer keeps its Google parser while it is used; nothing there changes.

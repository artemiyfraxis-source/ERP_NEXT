# Note for Артемий's Claude — branch `vitya/parser`, PR #724 (Витя, 26.10.2026)

Витя asked for this branch to be taken over by Артемий's session: merge it AFTER your own pending commits and adapt it
to them yourself. You own the merge; this branch is a proposal, not a claim on your code. No migration.

## What it is now: ONE document reader for the ERP
Owner (Витя), verbatim: «Наш парсер идёт не рядом с парсером Артемия, а они становятся одним целым. Наш парсер более
подготовлен и обучен работе с разными типами документов и разными полями. Поэтому он идёт как модификация парсера
Артемия.»

So the reader in `backend/app/shared/doc_reader/` **replaces your model reading of a УПД scan** (your page prompt with
`supplier / inn / upd_number / … / lines[n, supplier_code, quantity, …]`). It is Roller_Buyer's УПД parser (prompt,
kinds УПД / СФ / ТН+СФ / ТОРГ-12 / акт / М-15 / КСФ / прочее, money, basis, payments, arithmetic and INN checks) built on
YOUR transport and page handling, which moved there unchanged. One reading of a file serves:

1. **Warehouse «Отсканировать УПД» / «Поставка»** — `warehouse.scan_reading.read_scan` → the same answer shape as
   before (`recognised` header, lines with article / name / upd_name / item_type / manufacturer / quantity / unit /
   packs / supplier_code / source). Your header rules are kept: the invoice only from «Основание передачи…» (now the
   reader's `basis`, the «Счёт № … от …» in it via your `parse_upd_text`), the ПРД only from «К платёжно-расчётному
   документу» (the reader's `paymentDoc`, via your `prd_refs`). Your item fields are asked as `makerArticle`, `maker`,
   `itemType` (only from the stock dictionary passed in), `shortName`; your `item_line` (packs, article rules) and
   `snap_articles` work on the result as before.
2. **«Реестр УПД», origin `scan`** — the same reading's money goes to the registry: kind, price, net / VAT / gross per
   line, totals, KPP, buyer INN, the full basis and every payment with its date, the reader's remarks and verdict
   (`purchasing.upd_scans` + `app.workflows.receipt_upd_registry`). The storekeeper's saved header still wins (№ /
   date / seller; the basis and payments are the reading's full wording only while the delivery keeps the invoice /
   ПРД the scan gave). A scan read without the model (tesseract, readings stored before this branch) is written exactly
   as before: no money, «Требует проверки».
3. **Registry «Загрузить PDF» / «Разобрать заново»** — `app.workflows.upd_pdf_parse.read_pdf` uses the same reader.

Invoices (`procurement/invoice_file.py`, `document_items.py`) and quotes (`purchasing/quote_import.py`) are NOT touched:
by the owner's decision they stay on their text parsers (Roller_Buyer data: 649 of 651 invoice PDFs and 17/17 quote
PDFs have a text layer; the text parsers read invoice INN 671/677, total 668/677, positions summing to the total
587/649; quote_import read 9/9 real quotes) — «Проблемные счета и КП буду вносить руками».

## Reading strategy
- Electronic PDF (text layer with ≥ 200 Cyrillic letters, `reader.text_layer`) → ONE text-only call (no pictures),
  the text with one line per baseline (`closing.text_lines`), up to 60 000 characters.
- Scan / photo → your page pipeline: `page_images` (pdftoppm 300 dpi, ≤ 10 pages) → `page_turn` (OSD, then most
  Russian words) → `page_jpeg` (grayscale, 2400 px, q85) → pages in parallel (≤ 4) with the full field list and a
  «this is ONE page» rule (`closing.prompt(page=True)`), your numbering-gap reread (now on `seq`, still accepts `n`),
  then `closing.merge_pages` (header fields: first non-empty in page order; totals and crossCheckTotal: last non-null;
  lines concatenated, a row with the same seq + code + name once; ТОРГ-12 pages next to СФ / УПД pages → TORG12_INVOICE
  with lines from the invoice pages) → `normalize` + document checks (line sum = total, INN checksum, our INN as
  seller, file amount for registry PDFs only).
- A page neither model read: warehouse → your tesseract `page_text` for that page (model lines first, then the
  tesseract lines; warning PAGES_FAILED as before); registry → the remark «модель не прочитала стр. N — их строк в
  разборе нет». A text layer the model did not read → warehouse falls back to the text-layer rules (as before, plus
  MODEL_FAILED warning).
- Without a key: everything exactly as before (text-layer rules, tesseract; registry uploads wait «В очереди»).
- Options per call: `temperature 0`, `response_format {"type": "json_object"}` (your measured setting — the earlier
  draft of this branch used `json_schema`, dropped), `reasoning_effort` = the saved effort. Warehouse budget 200 s per
  file (your DOCUMENT_BUDGET, the screen's 4-minute wait); registry background 900 s, text call timeout 300 s.

## What moved where (re-exports keep your names)
| Was | Now | Re-exported from |
|---|---|---|
| `receipt_llm`: LlmConfig, llm_config, env_fields, key_hint, LlmError, _post, complete, message_text, parse_answer, check, DEFAULT_*, ENV, EFFORTS, CALL_TIMEOUT, CHECK_TIMEOUT | `app/shared/doc_reader/transport.py` (+ `ask`: main model ×2, fallback ×1 within a deadline; `json_options`) | `receipt_llm` |
| `receipt_llm`: page_jpeg, LONG_SIDE, JPEG_QUALITY | `doc_reader/pages.py` | `receipt_llm` |
| `receipt_llm`: read_page, read_pages, numbering_gap, PageAnswer, PARALLEL | `doc_reader/reader.py` (generic, prompt passed in) | `receipt_llm.read_page/read_pages` are thin wrappers with the CLOSING page prompt + item types; `PROMPT` = that prompt |
| `receipt_ocr`: page_images, page_turn, OcrUnavailable, _program, _run, _tesseract, _words, _pdf_pages, constants | `doc_reader/pages.py` (+ `page_pictures`) | `receipt_ocr` (tesseract text — `_read_page`, `ocr_text`, `page_text` — stays in `receipt_ocr`) |
| `receipt_llm.page_header` / `page_lines` (your answer shape) | removed; `receipt_llm.reading_header(doc)` / `reading_lines(doc, types)` map the reader's `ParsedDoc` | — |
| `purchasing/upd_parse.py` pure parts | `doc_reader/closing.py` | `purchasing.upd_parse` (keeps `header_values`, `line_values`, QUEUED / PARSING / ERROR) |
| `purchasing.suppliers.inn_valid`, `upd_sellers.OWN_INNS` | `app/shared/inn.py` | both modules import it |

Kept as they were: the settings table and screen (`receipt_llm_api`, `warehouse_upd_llm_settings`), `scan_recognition`
(jobs, `scan_answer`, `edo_answer`), the ЭДО names call (`receipt_llm.name_fields` / `names_prompt` / `row_fields`),
`upd_lines`, `receipt_scan`. `scan_reading.ScanReading` gained `pages` (the model's JSON per page, None = not read by
the model) and `complete`; `reading_json` stores `"pages"`; a stored reading without it (all readings stored before)
loads with `pages=()` and behaves as before. `merge_readings(readings, types)` re-merges the pages as one document when
every sheet was read by the model, else merges as before. `scan_reading.model_view(pages, types, engine)` makes the
warehouse view of the pages.

Tests changed where behaviour intentionally changed: `tests/warehouse/test_receipt_llm.py` (fixtures in the reader's
answer shape; mapping tests on `reading_header` / `reading_lines`; logger is `doc_reader.transport`; the `stand`
fixture patches `doc_reader.pages._pdf_pages`). Added: `tests/test_doc_reader.py`, new cases in
`test_receipt_llm.py`, `tests/workflows/test_receipt_upd_registry.py`, `tests/purchasing/test_upd_pdf.py`.

## Your key is used as-is
Same settings as before (env `ERP_UPD_LLM_*` over «Склад» → «Распознавание УПД»). Nothing to add. Витя has no key:
**no real-model run was made**; everything is tested with fake models. Before relying on it, on a prod copy:

```
ERP_DATABASE_URL=… python -m app.workflows.upd_pdf_parse --benchmark      # writes nothing; PDFs with an EDO XML twin
```

It prints field-by-field errors of the stored Google parse vs the new reading against the exact XML (baseline of the
stored parse, gemini-3.5-flash-lite, 14 pairs / 51 lines: INN 1, total 1, payment numbers 3, one qty↔price swap, ~13
lines with wrong characters in names). Then scan two or three real paper УПД on the warehouse screen (one multi-page)
and compare with what your reader gave (lines, quantities, packs, invoice from «Основание», ПРД).

## What to adapt when merging your newer commits
- If you changed `receipt_llm` / `receipt_ocr` since 26.10 (prompt, transport, page handling): transport/page changes
  go into `doc_reader/transport.py` / `pages.py`; prompt changes go into `doc_reader/closing.py` (`_COMMON`, `_ONE_PAGE`,
  `_ITEMS`, `_UPD`); new answer fields need a key in `closing.normalize` / `ParsedLine` and the mapping in
  `receipt_llm.reading_lines` / `reading_header`.
- New code calling `receipt_llm.page_header` / `page_lines` → `closing.closing_doc(pages)` + `reading_header` /
  `reading_lines`.
- Tests patching `receipt_ocr._pdf_pages` → patch `app.shared.doc_reader.pages._pdf_pages`; patches of
  `receipt_ocr.page_turn` / `page_text` / `ocr_text` / `_tesseract`, `receipt_llm.page_jpeg` and
  `receipt_llm.urllib.request.urlopen` still work.
- `test_architecture_imports`: `app.shared.doc_reader` imports no module; purchasing reaches the warehouse settings only
  via `app.workflows.upd_pdf_parse.llm_config`.

## Open decisions (yours / the owner's)
- Text-only reading of electronic PDFs: cheap and exact characters, but the model sees no layout. If `--benchmark`
  shows column mix-ups on text, send the page pictures with the text (one call) for electronic PDFs.
- Per-page reading of ТОРГ-12 + СФ scans relies on the model labelling each page (TORG12 vs INVOICE/UPD); a mislabelled
  continuation page could duplicate lines — the line-sum check would flag it.
- Service lines (delivery) stay in the warehouse lines as before (the service regex also matches «упаковка 6000 шт»
  in item names, so it is not used to drop rows).
- A warehouse scan's file name is not used for the file-amount check (scanner names carry no amount).
- `json_object` vs `json_schema`: kept your measured `json_object`.

Roller_Buyer keeps its Google parser while it is used; nothing there changes.

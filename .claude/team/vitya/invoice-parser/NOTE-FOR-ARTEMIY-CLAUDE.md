# Invoice parser (PR «[procurement] Полный разбор счёта из PDF как в Roller_Buyer») — note for Артемий's Claude

Branch `vitya/invoice-parser`, written 27.10.2026 on top of main `7ebf8298` (after #731). Owner's words (Витя):
«Там нужен полноценный разборщик всех данных, как у нас в roller_buyer». No model, no OCR: scans stay manual.

## What is where
- `backend/app/shared/invoice_header.py`, `invoice_lines.py` — your port of RB `invoice-parse.ts` / `invoice-lines.ts`
  (f6bc855e), **unchanged**: fed with RB's own pdf.js words they give RB's results on all 673 Kaiten invoices.
- `backend/app/shared/pdf_layout.py` — **rewritten inside, same API** (`pdf_layout(content, max_pages) -> PdfLayout(text,
  pages)`, never raises, caller runs `pdf_text` first). It now glues glyphs into items by pdf.js 4.10 `getTextContent`
  rules (glyph-level gaps 0.102 / 0.6 of the font size, flush on Tf change, on a Q that changes the font, on Do, on
  BDC/BMC/EMC; prevTransform kept across flushes) and fixes two pypdf gaps: simple-font widths are looked up by the
  character CODE (pypdf looked them up by the decoded char → every Cyrillic glyph of a single-byte font had the default
  width), and PDFs without a usable `startxref` are read by scanning. Words are no longer rounded to 0.01.
  Measured on RB's 673 text invoices: old layout → positions on 551, Σ positions = total on 473, № equal on 643;
  new layout → 670 / 612 / 673, identical to RB on every header field and every position list.
  `document_items.read_supplier_document` («Вставить счёт») uses it, so its table reading improves too; its tests stay green.
- `backend/app/shared/invoice_reading.py` (new) — the whole reading of one file: budgeted `pdf_text` → `pdf_layout` →
  header + lines + RB's remarks (`поставщик не найден в тексте (скан?)`, `сумма не найдена`, `позиции не прочитаны`);
  falls back to `pdf_text`'s text for the header when pypdf decodes a Cyrillic font wrongly.
- `procurement/invoice_file.py` — `recognise_invoice_file(content) -> InvoiceRecognition(info, hint, reading)`;
  `recognise_invoice` keeps returning `(info, hint)` with your 26.10 rule (RB's title wins when it finds № and date).
- `procurement/invoice_checks.py` (new) — seller vs the order's supplier (INN from purchasing's directory through the
  `SupplierInns` port), invoice total vs Σ line sums (1 ₽ tolerance), positions vs order lines (`match_document_items`).
- `procurement/application.upload_invoice_file(command, uow, suppliers=None)` returns `reading`, `checks`,
  `order_number`, `supplier` on `UploadedInvoice` (defaults keep old callers working). The test
  `test_attachment_safety.py::test_foreign_line_is_refused_403_before_the_file_is_read` now patches
  `recognise_invoice_file` (the name the application calls).
- `procurement/api.py`: `POST /invoice-files` answers with `reading` and `checks` (old keys unchanged) and, after the
  answer, pushes the file to purchasing «Счета»; `POST /orders/{n}/recognize` with `kind=invoice` pushes it too.
- `workflows/procurement_invoice_registry.py` (new) — `PurchasingSupplierInns` (the port) and `push_after_upload`
  (never raises; mirrors `receipt_upd_registry`).
- `purchasing/invoice_uploads.py` (new) — `record_uploaded_invoice`: an `InvoiceRow` of source `procurement`,
  external_id = procurement's stored file uuid, status `to_pay`, header/lines/text from the reading, the PDF copied into
  `purchasing_document_files`, supplier by INN (else by the order's supplier name when the directory has no other INN).
- `purchasing/invoices.py`: JSON carries `source`; `invoice_title` says «Счёт № …» for non-Kaiten rows.
- Frontend: `InvoiceFileUpload` (ProcurementPage) shows «По счёту: продавец…, сумма…», the checks (warnings as
  `warning-inline`) and a collapsible positions table; the «Счета» card says «Загружен в закупке» for such rows.

## Migration
`0155_invoice_procurement_source` (after `0154_user_ui_settings`): CHECK `ck_purchasing_invoices_source` widened to
`('kaiten', 'procurement')`, `batch_alter_table` (SQLite rebuild / PG drop+create). DESTRUCTIVE = False. Downgrade
deletes only `procurement` invoices (their file rows, link decisions; quotes lose the link). Needs the owner's prod-copy
rehearsal before merge (RULES). If main gains a migration first, re-parent it (only `down_revision` + the docstring
numbers in `invoice_uploads.py` / `InvoiceRow`).

## When merging newer commits of yours
- Changing `pdf_layout.py`: keep the item rules above, or re-run the parity (Витя has the script; RB data are on his PC).
- New callers of the invoice reader: use `invoice_reading.read_invoice(content, plain=...)` rather than wiring
  header/lines/layout by hand — it has the budget, the font fallback and the remarks.
- `InvoiceRow.source` is no longer always `kaiten`: anything that filters «Kaiten cards» should say `source == "kaiten"`.
- Known open points: the registry row's status stays `to_pay` (payment lives on procurement lines, not synced yet);
  every upload makes its own row, so a wrong file uploaded and replaced stays in «Счета»; no backfill of invoices
  uploaded before this PR.

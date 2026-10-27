"""The ERP's one document reader (Витя, 26.10.2026): «Наш парсер идёт не рядом с парсером Артемия, а они становятся
одним целым». Roller_Buyer's УПД parser (kinds, fields, money, checks) as a modification of Артемий's warehouse reader
(his model transport, page rendering and upright turn, page-by-page reading with the numbering-gap reread).

* `transport` — the OpenAI-compatible call, its config (env over the warehouse's saved «Распознавание УПД»), the key
  kept out of logs and answers;
* `pages` — a file's pages as upright grayscale JPEGs (pdftoppm, tesseract's orientation);
* `closing` — the CLOSING task: the prompt, the merge of pages, normalization and the document checks;
* `reader` — the strategy: an electronic PDF in one text call, a scan page by page in parallel.

Used by the warehouse («Отсканировать УПД»: `warehouse.scan_reading`), the УПД registry's scan documents
(`purchasing.upd_scans`) and the registry's PDF upload («Загрузить PDF», «Разобрать заново»:
`app.workflows.upd_pdf_parse`). Shared code: it imports no module."""

---
name: upd-recognizer-openai
description: "Implemented model-based UPD recognition, cached readings, configuration and OCR fallback"
metadata:
  node_type: memory
  type: project
  originSessionId: 51316a75-b68c-47c7-b496-e2ce4ac5a6ae
  modified: 2026-11-12T12:00:00.000Z
---

- Implemented: scans go through `app.modules.warehouse.receipt_llm` and the shared `app.shared.doc_reader` OpenAI-compatible transport. The model reads header and lines; ERP validates the result and the storekeeper confirms.
- `scan_reading.py`: configured main model, then fallback model; pages no model read fall back to tesseract. Without a key: PDF text layer, then tesseract. A deployed local-model fallback is not established by the code.
- Configuration: «Склад» → «Распознавание УПД»; `ERP_UPD_LLM_*` overrides saved settings field by field. API keys stay outside git/output and are masked in API responses.
- Migration `0151_upd_llm_recognition` creates settings and `warehouse_receipt_files.recognition` for cached readings (the original commit called it 0150, renumbered in this branch).
- Open work: compare recent scans with accepted lots and turn systematic misses into checks; per-supplier correction memory remains planned → [[upd-scan-learning]]. Production configuration and recognition quality need live verification.
Related: [[warehouse-scanner-usb]], [[scanner-any-pc]], [[erp-server]].

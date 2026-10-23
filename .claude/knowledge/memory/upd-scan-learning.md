---
name: upd-scan-learning
description: Owner 23.10 — paper УПД recognition must keep learning from what Алексей scans and corrects; review his scans vs accepted lots regularly
metadata:
  node_type: memory
  type: project
  originSessionId: 128bab83-781a-4ce3-a66d-a0a9a0fa29cb
  modified: 2026-10-23T13:11:08.021Z
---

Owner 23.10.2026: «пока работаем так … пусть учится … тебе нужно будет смотреть на то что сканирует алексей». Алексей (storekeeper) scans paper УПД in «Поставка» (Pantum, tesseract, no paid OCR). When Витя's purchasing flow is live quantities matter less, but recognition should still improve.

**Why:** OCR line parsing (article from the name, pack × rolls) is heuristic; real УПД from new suppliers will break it.
**How to apply:**
- When working on the warehouse / receipts, compare recent scans with what was accepted: scan answer = `warehouse_scan_jobs.result` (+ the file), accepted lots = `warehouse_lots.receipt_id` → `warehouse_receipts`. Read via the prod API or `erp.sh cli` (see [[warehouse-pc-ssh]], [[erp-server]]); never write.
- Turn every systematic miss into a parser rule + a test on anonymised OCR text (repo is public: no prices, ИНН, addresses).
- Planned: remember corrections per supplier (supplier + name / supplier code → our article, pack) and apply them on the next scan.
Related: [[module-warehouse]], [[warehouse-scanner-usb]].

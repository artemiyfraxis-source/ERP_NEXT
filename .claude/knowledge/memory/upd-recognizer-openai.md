---
name: upd-recognizer-openai
description: "Owner 26.10 — paper УПД recognition goes through the OpenAI GPT API for now (vision on the scan → table), behind a swappable recognizer (cloud / local model on the shop server); not started yet"
metadata:
  node_type: memory
  type: project
  originSessionId: 51316a75-b68c-47c7-b496-e2ce4ac5a6ae
  modified: 2026-10-25T22:31:24.180Z
---

Decision 26.10.2026: УПД scans (Алексей, Pantum → warehouse_scan_jobs) will be parsed by the OpenAI GPT API — image of the
scan → structured table (supplier, ИНН, УПД №/date, lines: name, article, qty, unit, price, sum, НДС), then ERP checks
sums and the storekeeper confirms. Owner: «пока поюзаем чат гпт». Build it as a swappable recognizer (env switch:
openai | local), with a local fallback — owner confirmed 26.10 «и резервом локалку»: the local model is deployed too
and takes over automatically when OpenAI fails/times out (llama.cpp/Ollama, Qwen 7–14B on the server's CPU, ~1–3 min/doc) so the
warehouse never stops if the cloud is down.

Open before building: OpenAI blocks Russian IPs (the server needs a stable foreign proxy) and Russian cards (foreign card
or reseller); УПД carry signers' ФИО (152-ФЗ cross-border transfer) and supplier prices — the owner accepted the cloud
for now; Yandex Vision OCR + YandexGPT / GigaChat were offered as in-Russia alternatives. API key goes to the server env
(outside git), never into the repo.

**Why:** the shop server (i7-13700K, 24 threads, 63 GB, no GPU) is idle; owner first asked about a local model, then
chose GPT for speed. **How to apply:** start in a fresh session; first benchmark on Алексей's recent scans vs accepted
lots ([[upd-scan-learning]]) before switching the warehouse to it. Related: [[warehouse-scanner-usb]], [[erp-server]].

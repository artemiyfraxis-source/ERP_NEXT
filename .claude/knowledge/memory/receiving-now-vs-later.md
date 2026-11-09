---
name: receiving-now-vs-later
description: "Owner 26.10 — today goods enter via «Склад → Внести позиции» (Поставка, УПД scan); later, once Витя buys in ERP, Алексей scans the УПД in «Приёмка» and it matches the paid lines"
metadata:
  node_type: memory
  type: project
  originSessionId: 128bab83-781a-4ce3-a66d-a0a9a0fa29cb
  modified: 2026-10-26T13:10:39.596Z
---

Owner 26.10.2026: «Приёмка» is on hold — Витя does not buy through ERP yet. NOW: Алексей takes goods in by the paper УПД through «Склад → + Внести позиции» (the «Поставка» window: scan → model reads the УПД → accept per row). LATER: when Витя buys in ERP, paid lines (status «Оплачено») land in Витя's waiting list → «Приёмка»; there Алексей just scans the УПД and ERP matches it to the purchased lines automatically, with the full history (заявка → КП → счёт → оплата → УПД → ВК → ГРН).

Since 26.10 (Витя via owner: «сваливать всё в реестр, что принимаем — он сам разберётся с агентом») every accepted «Поставка» file also lands in purchasing's «Реестр УПД»: ЭДО as an `edo` document, a paper scan as origin `scan` («Скан склада», needs review). The registry is Витя's to process — don't build matching/cleanup there for him.

**Why:** I mixed up the two paths when answering where paper УПД «fall».
**How to apply:** improvements to the «Поставка» scan are for the interim path; plan the reuse of the same scan + model recognition (receipt_llm / scan jobs) in «Приёмка» matching expected lines when Витя's buying goes live. Don't push «Поставка → реестр УПД» work as the main road. Related: [[module-warehouse]], [[upd-scan-learning]], [[vitya-hybrid-stage-options]].

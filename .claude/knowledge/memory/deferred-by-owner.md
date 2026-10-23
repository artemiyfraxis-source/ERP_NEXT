---
name: deferred-by-owner
description: "Features the owner explicitly postponed — traceability, moving boxes/defects out of resource planning"
metadata:
  node_type: memory
  type: project
  originSessionId: 128bab83-781a-4ce3-a66d-a0a9a0fa29cb
  modified: 2026-10-23T17:10:52.659Z
---

- Traceability (lineage search): hidden from the UI (`SHOW_TRACEABILITY` in `ProductionPage.tsx`, PR #57); API and data stay. Return later.
- Move «Коробки на подтверждение», «Брак цеха», «Брак ОТК» out of «Ресурсное планирование» into their own section — later; they stay for now.
- РВ document chain (owner 23.10.2026, «на будущее, держать в голове»): for РВ everything must be gathered per project — invoice, УПД, ВК (входной контроль) and the lots (ГРН) they produced. Today the pieces live apart: «Поставка» scans in `warehouse_receipt_files` (PDF in the DB) + header in `warehouse_receipts` (lots via `receipt_id`); ЭДО УПД and Kaiten invoices in purchasing (`purchasing_upd_documents`, `purchasing_invoices`); ВК acts on `receiving_lines`. Design schema changes so one query can walk project → invoice → УПД → ВК → ГРН (FKs, not text matches) — see [[rv-td-base]]. Owner 23.10: «будем делать периодически» — the requirements and the step list live in the PRIVATE brain (vault root `erp-next-rv-goz.md`, `erp-next-rv-goz-plan.md`, read via Obsidian MCP; owner: defence-order topics stay out of this public repo); take the next unchecked step when there is room, tick it there.
- How tasks come out of tech cards — decided 13.10 with Василий: one task per semi-finished item → [[etk-by-semifinished]].

Related: [[module-production]]. Hub: [[erp-next-hub]].

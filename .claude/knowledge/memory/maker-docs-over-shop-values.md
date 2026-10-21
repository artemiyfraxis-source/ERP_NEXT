---
name: maker-docs-over-shop-values
description: "Owner 17.10: a maker's document beats the shop's own reference values (L1–L3, crimp heights…) — overwrite, keep the old value in the journal"
metadata:
  node_type: memory
  type: feedback
  originSessionId: dc9f7942-b0b7-4ca6-9c72-41631fc5294c
  modified: 2026-10-19T15:33:47.846Z
---

Owner 17.10.2026, on «11 coax rows with shop strip lengths kept because the maker's instruction differs»:
«нет, документация превыше».

**Why:** the reference DB must follow the manufacturer's documented values; a shop value without a document is not
a source, however long it has been used.
**How to apply:** when the maker's document for that exact part (instruction, drawing, application spec) disagrees
with a filled cell — shop's or an agent's analog — overwrite it via `_edit_in` with the old-value check, append
«было по цеху …» to the source, keep the rollback CSV ([[prod-reference-write]]). Analog/other-series documents still
don't override a filled cell. [[coax-data-sources]] [[crimp-contacts-protocol]]

Owner 19.10.2026, on the supplier-catalogue sweep (Kroks / gsm-repiteri / АМЭЛ / vegatel): «если есть опора на что-то,
то ты имеешь право переписывать». A value backed by a verified source (the evidence snippet found verbatim in the saved
page of that exact product, the value inside it) may overwrite a filled cell too; the old value goes to the rollback
journal. Priority when sources disagree for one mark: maker's document → page of the row's own supplier («Ссылка» /
«Поставщик») → other suppliers' pages. Agents only collect; the main session verifies every value before writing
(«они дают инфу, а ты проверяешь, чтобы не было выдумываний»).

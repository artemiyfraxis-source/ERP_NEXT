---
name: maker-docs-over-shop-values
description: "Owner 17.10: a maker's document beats the shop's own reference values (L1–L3, crimp heights…) — overwrite, keep the old value in the journal"
metadata:
  type: feedback
---

Owner 17.10.2026, on «11 coax rows with shop strip lengths kept because the maker's instruction differs»:
«нет, документация превыше».

**Why:** the reference DB must follow the manufacturer's documented values; a shop value without a document is not
a source, however long it has been used.
**How to apply:** when the maker's document for that exact part (instruction, drawing, application spec) disagrees
with a filled cell — shop's or an agent's analog — overwrite it via `_edit_in` with the old-value check, append
«было по цеху …» to the source, keep the rollback CSV ([[prod-reference-write]]). Analog/other-series documents still
don't override a filled cell. [[coax-data-sources]] [[crimp-contacts-protocol]]

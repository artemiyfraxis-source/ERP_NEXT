---
name: ufl-not-mhf
description: Owner 20.10 — U.FL (Hirose) and MHF1/MHF/IPEX (I-PEX) are different series; never merge their rows even with the same supplier page/code
metadata:
  node_type: memory
  type: feedback
  originSessionId: 588273da-1ef1-4aac-9b53-d023958d79e8
  modified: 2026-10-20T07:46:55.577Z
---

U.FL (Hirose) and MHF1 / MHF4 / IPEX (I-PEX) are different connector series — partly mate-compatible, but separate
products with their own articles and data. Owner 20.10: «U.FL — это вообще чуть другое».

**Why:** on 20.10 a dedupe pass merged «MHF1-plug RG-178» into «U.FL» because both rows carried the same gsm-repiteri
page/code 3148, and deleted the MHF1 row.
**How to apply:** any БД.КОАКС dedupe/merge must require the same series (and gender/angle); a shared supplier code or
page is not enough to call rows duplicates. Related: [[one-article-one-row]], [[coax-data-sources]].

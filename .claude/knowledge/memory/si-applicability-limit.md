---
name: si-applicability-limit
description: VSWR 1.30 «требование цеха» was never the owner's — applicability of СИ adapters comes only from the item's own norm
metadata:
  type: feedback
---

The VSWR 1.30 «application limit / требование цеха» on СИ labels, passports and the VNA screen was NOT set by the owner
(18.11.2026: «я не задавал его, я сказал тебе узнать, какой применим»). Earlier notes that attribute 1.30 to the owner are wrong.
Applicability («Пригоден / Применять до X ГГц») = highest frequency where the measured VSWR/loss stays within the item's own
norm: maker spec → MIL-PRF-55339 sheet → otherwise «норма не установлена» (no applicability line).

**Why:** the owner asked for norms, not invented thresholds; a made-up number presented as his requirement misled him.
**How to apply:** never introduce a threshold without a primary source; never attribute a number to the owner unless he said it.
Passport stays short — no sources/URLs printed ([[coax-vswr-acceptance]] is the separate product-assembly rule ≤1.32).

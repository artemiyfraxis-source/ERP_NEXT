---
name: coax-vswr-acceptance
description: "Owner 22.10 — a coax assembly's VSWR is judged against the limit its components allow (connector VSWR specs summed), never against a generic «good assembly ≤1,3»"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 51316a75-b68c-47c7-b496-e2ce4ac5a6ae
  modified: 2026-10-22T10:13:35.464Z
---

Acceptance of a coax cable assembly by VSWR: the limit comes from the components. Worst case
Γmax = Γ1 + Γ2·10^(−2·A/20) (Γ = (КСВ−1)/(КСВ+1) of each connector's spec, A = cable loss in dB one way),
КСВmax = (1+Γmax)/(1−Γmax). Two connectors at 1,35 on a short cable → ≤1,85. Below that = годная, whatever the
ripple looks like (peaks/dips move with length).

**Why:** 22.10 the owner measured a 11,5 cm SMA-C174P + SMA-C316F RUICHI assembly on RG316 (Arinst VNA-DL, SMA-MF06
kit): peak 1,74 at 2,28 GHz. I called it «брак» against a generic norm; owner: «почему ты считаешь браком… мы исходим
из комплектующих». Diagnosis that day: reflections sit in the crimp-ferrule zone (ripple 1270 MHz ≈ 8,2 cm between
them), braid contact solid (wiggle test flat), solder bumps negligible (~1,02).
**How to apply:** ask for / look up each connector's VSWR spec and frequency range first; a customer or drawing norm
overrides only when stated. RUICHI VSWR specs were not findable online (22.10). Related: [[coax-contact-depth]],
[[coax-data-sources]], [[one-article-one-row]].

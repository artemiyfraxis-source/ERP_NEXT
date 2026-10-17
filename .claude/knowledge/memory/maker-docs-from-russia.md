---
name: maker-docs-from-russia
description: "te.com, molex.com, mouser, digikey, farnell, octopart block Russian IPs (403); what works instead for crimp specs and drawings"
metadata:
  node_type: memory
  type: reference
  originSessionId: 082e841f-05ea-4a88-a2c9-b8b5fae27f4e
  modified: 2026-10-17T09:11:06.046Z
---

From this machine (exit via Moscow) te.com, molex.com, mouser, farnell, rs-online, digikey and octopart answer 403 even in a real browser — a geo block, not bot protection (found 17.10.2026).

What works:
- Web Archive **from the browser** (chrome-devtools MCP); curl gets 429. TE route: archived `te.com/en/product-<PN>.datasheet.pdf` → spec/drawing number → CDX search `DocNm=<number>&` → `id_` PDF link.
- JST application tooling guide jst.com/resources/application-tooling-guide/crimp-style/ (browser only).
- Mirrors: datasheets.ibselectronics.com (browser), static.chipdip.ru (curl with Referer chipdip.ru), cdn.eicom.ru.
- Pin-1 checks: KiCad footprints (gitlab kicad-footprints) + EasyEDA/LCSC footprints, calibrated on families with a maker drawing.

Doesn't work: r.jina.ai (451), Google Translate proxy, device.report, alldatasheet.

Journal: docs/data «БД.ТЕР — где брать спецификации без регистрации 17.10.md». Related: [[crimp-contacts-protocol]], [[prod-reference-write]], [[coax-data-sources]].

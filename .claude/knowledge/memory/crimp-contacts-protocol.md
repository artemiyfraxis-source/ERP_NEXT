---
name: crimp-contacts-protocol
description: "Owner's old crimp-contact DB protocol v5.2 (28 columns) — what to keep for terminal imports via API, what ERP does differently, field gaps vs TerminalRecord"
metadata:
  node_type: memory
  type: project
  originSessionId: 3604d195-140f-4ea7-b7bb-8743a45761a9
  modified: 2026-10-12T10:05:20.006Z
---

On 12.10.2026 the owner pasted an old agent protocol, «СИСТЕМНЫЙ ПРОТОКОЛ ИИ-АГЕНТА v5.2», for a crimp-contact database. His words: «старая разработка… может быть не правильно местами, но можно взять на разбор». Treat it as input to weigh, not as a spec.

**Keep these rules for any terminal/contact import (API `POST /api/references/import/ter`):**
- One row = one physical terminal article × base housing series. Don't create one row per pin count unless the housing series code changes.
- REEL and BAG packaging of the same terminal go in one record as two article fields, not two records.
- Null-spec: a crimp value that is not in the official crimp spec / ATS PDF stays empty. Never average, interpolate or guess.
- Sources: official maker sites only (molex.com, jst-mfg.com, te.com, hirose.com). No Mouser, Digi-Key or forums.
- Pull force is stored in N: kgf × 9.8, lbf × 4.448. Crimp heights and insulation OD are min–max ranges per AWG of that article. AWG values carry no «#».
- Material is written «Металл / Покрытие» (e.g. Brass / Gold 0,76µm).
- Applicator, pneumatics, ОТР, «с зачисткой» and mm² are filled by the technologist. An importer must not fill them; mm² is not converted from AWG.
- Wire-to-wire: a female housing needs its male mate added too.

**Where ERP deliberately differs:**
- Molex articles: the protocol strips hyphens. ERP keeps the article exactly as the datasheet and the warehouse write it, and matches on a normalized key that ignores hyphens, spaces and case. Otherwise warehouse and techcard links break.
- Decimal comma: that is a display and sheet convention. Storage and the API accept both separators (`technology.py` does `replace(",", ".")`).

**Gaps vs `TerminalRecord` (`references/technology.py:199`)** — today it has article, step, strip, l_plus/l_minus, applicator, crimp_height, pull_force_min/max, awg_from/to, mm2_from/to. Missing:
- contact gender (Male/Female);
- BAG article;
- insulation crimp height (already queued);
- insulation OD from/to;
- material/plating;
- max current;
- datasheet URL;
- wire insertion depth (check whether l_plus/l_minus already mean this).

Related: [[module-reference-db]], [[module-techcards]].

---
name: cable-standard-by-type
description: "Owner 25.10 — a cable's «Стандарт» (for the assembly passport) is the general CONSTRUCTION standard of its type (MIL-DTL-17/xxx, JIS C 3501, ГОСТ 11326…), not the maker's document; fire/eco standards don't go there"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 51316a75-b68c-47c7-b496-e2ce4ac5a6ae
  modified: 2026-10-25T16:29:32.609Z
---

«Стандарт» of a cable in БД.ПРОВ (printed in the coax assembly passport) = the general standard that defines the
cable's construction for its type: RG-xxx → the MIL-DTL-17 slash sheet, D-FB → JIS C 3501, РК 50-… → the ГОСТ 11326 part,
etc. It is assigned «условно» by type; the owner checks conformity himself and works only with verified suppliers, so a
cable of that type is taken to conform. Fire / smoke / eco standards (ГОСТ 31565, IEC 60332/60754/61034, EN 50575, UL) are
NOT a «Стандарт». A type with no general construction standard (proprietary LMR, micro-coax RF 1.13…) stays empty.

Applied on prod 25.10 (scripts docs/data «БД.ПРОВ — стандарт коакс 25.10 …»): ~160 of 193 coax cables. Precedence: type sheet
(MIL-DTL-17/xxx, ГОСТ 11326.x) → category standard (IEC 61196-5 CATV trunk, -6 CATV drop, -8 semi-flexible tin-soaked braid,
-9 flexible incl. foamed/LD-PTFE dielectric — owner 25.10 «ставь») → empty. Never a general ТУ (ГОСТ Р 58416 rejected by owner:
«на общий лучше не ссылаться»). Still empty: corrugated feeders (no sectional spec), РК50-9/12/23 (no ГОСТ/ТУ found).

**Why:** 25.10 a maker-document search filled 1 of 193 coax cables; owner: «нужен только стандарт на конструкцию — найди
какие-то общие, а не от производителя… проверять соответствие будем сами, мы работаем с проверенными».
**How to apply:** each type → standard mapping needs a source for the mapping itself (standard index / slash-sheet list),
recorded in «Источник данных»; never invent a slash number. Related: [[maker-docs-over-shop-values]] (does not apply to
this field), [[coax-vswr-acceptance]].

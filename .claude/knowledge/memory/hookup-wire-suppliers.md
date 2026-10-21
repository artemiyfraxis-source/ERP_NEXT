---
name: hookup-wire-suppliers
description: "Owner 19.10 — supply base for монтажные провода is Снабкабель (snabcable.ru) + Паритет (paritet.su); what each sells, price/article quirks, colours as a list"
metadata:
  node_type: memory
  type: project
  originSessionId: ae752c28-5d3b-4207-a937-924c7d540ec8
  modified: 2026-10-21T15:28:54.617Z
---

Owner 19.10.2026: «основа это снабкабель паритет» — the supply columns of «Монтажные провода» take the cheaper of
Снабкабель (snabcable.ru) and Паритет (paritet.su), per [[one-article-one-row]].

- Снабкабель sells МГТФ, МГШВ, МК 26-13, МС 15-11, МС 16-13, НВ (НВ-4 = «4 600» variant), ПВАМ, ПГВА, ПВ-3/ПуГВ,
  ПуГВнг(А)-LS. One page per series with an «интерактивный прайс» (row per size × colour, price = оптовая при 1000 м с
  НДС, no articles) and «макс. наружный диаметр» + maker + ТУ in the description. Pages fetch fine with curl.
- Паритет sells only НВМ from that list: one article per colour, price per km with VAT.
- Not sold by either: UL-series, силиконовый, МНВ-4, НВМ ≥ 1,0 — those keep other suppliers.
- Strands: Чувашкабель catalogue PDF (chuvashcable.ru, tables are images) covers МГТФ, МГШВ, МК 26-13; no maker gives
  strand counts for НВ-4, ПВАМ, ПГВА, ПуГВ, МС.
- Owner 19.10: colours «каждый цвет новой строчкой и текстом». The 19.10 session (bff4771a, #597) built it as a list
  inside one cell — WRONG: owner 20.10 «имелось ввиду каждый цвет своя строка». Each colour = its own TABLE ROW (key =
  mark + colour); done 21.10 in #620, see [[prov-kind-split-plan]]. Coax parameters are not columns of this list.
- 19.10 fill script + journal: «Монтажные провода — Снабкабель и Паритет 19.10» in `docs/data/` ([[prod-reference-write]]).

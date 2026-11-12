---
name: coax-data-sources
description: "Coax sourcing rules, analog matching and source URL patterns; research log in docs/data"
metadata:
  node_type: memory
  type: project
  originSessionId: 18c9080e-85fd-4574-a846-0e4e1270987e
  modified: 2026-11-12T12:00:00.000Z
---

- Primary coax-cable suppliers: АМЭЛ, Kroks, gsm-repiteri; another supplier is an explicit exception. Retain referenced cables during cleanup.
- Resellers belong in «Поставщик», never «Производитель». Without a maker article, name connectors by type/angle/mount/cable; site numbers belong in supplier articles.
- Prefer the maker's document; journal overwritten shop values → [[maker-docs-over-shop-values]], [[prod-reference-write]]. Never write applicators → [[applicator-code-kinds]].
- AMEL analogs require the same series, gender, angle/mount and cable or true construction equivalent. RG-402 = UT141 = SF141 = SR141 = РК50-3-28/29; RG-405 = UT086 = SF085; EZ47 = UT047. RG-141 is not RG-402; RG-8X is not RG-8. No matching analog → leave the cell empty.
- Take own-part specs first; use only the matching AMEL analog group for reference values (worst КСВН, lowest maximum frequency) → [[coax-vswr-acceptance]]. RUICHI frequency sheets do not supply connector КСВН.
- Depth: trace dimension extension lines; mm only; source precedence and settled values → [[coax-contact-depth]].
- Supplier URLs: `amel.ru/products/coaxial_connectors/<series>/`, `kroks.ru/shop/cable-rf`, `gsm-repiteri.ru/{cable,cable-75-om,cable-vegatel}`; additional specs: `vegatel.ru`.
- Interface PDFs: `radiolab.ru/ru/attach_file/<series>radiolab.pdf`; RUICHI: `ruichi.ru/files/Datasheet-RUICHI_raz_<series>-<hash>.pdf`.
- Archive pattern: `https://web.archive.org/web/<year>id_/<original-url>`; MIL-STD-348B: `https://everyspec.com/MIL-STD/MIL-STD-0300-0499/download.php?spec=MIL-STD-348B.051541.pdf`.
- Amphenol Connex: `https://www.peigenesis.com/images/content/pei_tabs/amphenol/rf-series/connex/{mcx,mmcx,fme}.pdf`; Powertec: `portal.powertec.com.au/equipment/rf-communications/rf-connectors/rf-connector-series/<series>`.
Research log: [docs/data/coax-sources-log.md](../../../docs/data/coax-sources-log.md). Related: [[module-reference-db]].

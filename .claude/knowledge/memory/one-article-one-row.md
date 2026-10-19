---
name: one-article-one-row
description: "Owner 19.10 — reference rows are one supplier article each (cheapest seller wins), never glued article lists; coax cable card holds only VNA data + connectors pulled from БД.КОАКС"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 19b4b321-0510-4f61-9970-d9722981d5ea
  modified: 2026-10-19T12:31:26.275Z
---

- One cable (one set of materials: core, dielectric, shield, jacket, FR, indoor/outdoor) = one БД.ПРОВ row, exactly as
  connectors in БД.КОАКС: «Поставщик», «Артикул поставщика», «Цена…», «Дата цены», «Ссылка» of the CHEAPEST offer.
  Offers with the same materials merge into that row; different materials → a separate row. Never glue several
  articles/sellers into one cell («kroks.ru: 3521; gsm-repiteri.ru: 0090…»), never make a variant an alias of the family card.
- Coax cable card (expanded row): only vector-analyser data (span, VNA markers, attenuation) and compatible connectors,
  pulled automatically from БД.КОАКС. Construction and «Параметры» (impedance, capacitance, bend radii, voltage, mass)
  are table columns.

**Why:** owner 19.10: «ты разные артикулы кабелей записал в одно, там же разные материалы»; «выбираем низшую цену,
ставим его как поставщика и заносим то же самое как и в разъёмах — база где-то так, где-то так, надо к одному подобию».
**How to apply:** any reference fill or import (cables, wires, connectors, materials): one article per row, cheapest
seller, same supply columns everywhere. Related: [[module-reference-db]], [[prod-reference-write]], [[coax-data-sources]].

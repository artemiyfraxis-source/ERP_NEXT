---
name: coax-data-sources
description: "Owner's reference sources for coax cable/connector data and supplier articles — gsm-repiteri.ru, Амел, kroks.ru, vegatel.ru; resellers are suppliers, not makers"
metadata:
  node_type: memory
  type: project
  originSessionId: 18c9080e-85fd-4574-a846-0e4e1270987e
  modified: 2026-10-14T14:35:04.898Z
---

Owner 14.10.2026: coax data (cables, connectors, specs, supplier articles) is taken from the suppliers' sites —
gsm-repiteri.ru, «Амел», kroks.ru, vegatel.ru («оттуда кстати и артикулы»).

- A reseller is a «Поставщик», never a «Производитель» (owner: «больше надо не производитель, а поставщик, но
  производителя тоже можно оставить»). On 14.10 the reseller text of 17 coax cables was moved to «Поставщик» (domain),
  the old text kept in «Источник данных».
- Connectors without a maker article must not carry the site's number in the name («а зачем мне номера?») — they are
  named by type / angle / mount / cable (PR #412).

**How to apply:** enrich from these four first; fill only empty cells, write to prod per [[prod-reference-write]].
[[module-reference-db]]

---
name: supplymap-domain-decisions
description: Owner decisions recorded in SUPPLYMAP memory that bind the port — read the linked note before porting that area
metadata:
  type: reference
---

ERP_NEXT ports SUPPLYMAP's rules, and the owner's decisions about those rules live in SUPPLYMAP's project
memory (mounted next to this one in the brain vault). Before porting an area, read its note there. Code
alone does not say why.

- Warehouse balances/reserve, writeoff by ГРН, live ЦАР/ЖУК/СПИСАНИЕ → [[supplymap-warehouse]]
- Issue always creates a request and waits for delivery; a person picks the lot; kit leaves whole → [[issue-lot-binding]]
- Shipment requests come from the manager, the warehouse executes; partial shipment rules → [[shipment-flow]]
- Production traceability target (TC in DB, work log, WIP boxes) → [[production-traceability-vision]]
- Twist rules, shop rules, crimp sources for techcards → [[twist-domain-rules]], [[techcard-shop-rules-0916]], [[crimp-height-sources]]
- Access/roles model being replaced by native Identity → [[supplymap-access-model]], [[staff-directory]]
- Why this repo exists and its git rules → [[erp-next-repo]], [[google-exit-horizon]]

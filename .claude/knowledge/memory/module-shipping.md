---
name: module-shipping
description: "Shipment execution, picked vs sent quantities, inherited addresses and warehouse access"
metadata:
  type: project
---

- Managers create shipment requests; warehouse executes, including partial shipments.
- `shipped` = picked/packed out of ГП stock; `sent` = left the plant (shipments «Отгружена»/«Закрыта»); `before_erp` = historical pre-ERP shipments, included in accepted/shipped/sent. КБ «Отгружено» follows `sent`.
- Shipment stores effective addresses + `inherited` fields; project addressee changes update open shipments only. Closed/cancelled stay frozen. `PUT /shipments/{id}/head`, empty = inherit from project card.
- Warehouse access, grouping by shipment and automatic closing → [[shipping-warehouse-sees-all]].
- Takes finished goods from [[module-production]]. Decisions: `docs/migration/RUNLOG.md`. Hub: [[erp-next-hub]].

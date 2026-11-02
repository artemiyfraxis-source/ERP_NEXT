# Shipment position rows

Claim: replace project summaries and stacked shipment contents with one real KB/position per row, grouped by project. Show names, quantities, position stage and shipment state distinctly; expose separate PL/DN/UPD actions tied to the exact shipment.

Base: `55c0c0e4d81a6d0a5a46a9b160299a5aab2af2c7` (the reviewed accepted-readiness prerequisite).

Scope: shipment-data screen, focused tests and scoped styling only. Existing batch intake already provides the required position data. No API, stock, shipment-history or quantity mutations.

Remove the generic deadline from default shipment columns; show actual shipment date. Fix oversized persisted first-column layout and unreadable stacked text. Verification pending implementation, independent review, integrated gate and representative multi-position visual smoke.

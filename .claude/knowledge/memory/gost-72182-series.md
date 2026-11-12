---
name: gost-72182-series
description: "ГОСТ Р 72182 series map, local files and outstanding production verification"
metadata:
  node_type: memory
  type: project
  originSessionId: 9962f786-8b96-40dd-9ab8-a1bd25a560cf
  modified: 2026-11-12T12:00:00.000Z
---

Series: .2 crimp; .3 accessible IDC; .4 inaccessible IDC; .6 insulation piercing; .7 spring clamps; .9 ultrasonic welding. No .1/.5/.8.
Files (outside public git): `C:\Users\Артемий Васильев\Documents\ERP-norms\ГОСТ Р 72182-2025\`.
Maps: `ERP-norms/maps/gost72182-map.{md,json}`, `corrections.md`, `figure-plan.json`; crops: `ERP-norms/figures/manifest.json`. IPC source/digest: `ERP-norms/IPC/`; extra ГОСТ: `ERP-norms/ГОСТ-доп/`.
Never commit standards or crops to this public repo. IPC figures are internal shop screen/print only, excluded from customer ТД.
Implemented: standard-figure storage/API and `scripts/figures/upload_standard_figures.py`; US_WELD/IDC/crimp-height templates and СТО revision 3. Write/rehearsal path → [[rv-td-base]].
Open verification: confirm figure upload, template rehearsal/application and issued-ETK revisions on prod; old merge notes do not prove release. SMA depth is settled → [[coax-contact-depth]].

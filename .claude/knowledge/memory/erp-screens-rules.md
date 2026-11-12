---
name: erp-screens-rules
description: "Mandatory ERP screen workflow, Russian labels, shared layout and screenshots"
metadata:
  type: feedback
---

- Load `.claude/skills/erp-screens` before every UI change, then use `design-critique` on after-shots. Follow the ERP's shared style, dense tables and fixed ETK block order.
- Russian labels, visible planning dates/periods; no raw type IDs (`cutWireStrip`, «БЕЗ КОДА») as names. Operation-card code may be a grey second line below the Russian operation name.
- Before/after tooling: `scripts/demo/run-demo.sh`, `scripts/demo/shoot.mjs` (27-inch/tablet PNGs and checks for raw IDs, stuck loading, horizontal scroll).
- ETK defect art: `frontend/src/etkDefectArt.ts`, picked by defect name and operation code; new wording needs a matching rule.
Why: shared rules prevent each thread inventing its own screen. Hub: [[erp-next-hub]] · [[owner-working-rules]] · [[module-techcards]] · [[module-production]].

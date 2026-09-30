---
name: erp-screens-rules
description: Why every ERP_NEXT screen change goes through the erp-screens skill + design-critique, and where the demo/screenshot tooling lives
metadata:
  type: feedback
---

- 2026-09-30 the owner: «дизайн шакальный во многих местах… в чем причина, скиллов не хватает?», «каждый раздел править тяжко», «зачем делать, мб подключим что-то».
- Cause found: each thread invented its own look; no shared rules; English type ids (`cutWireStrip`, «БЕЗ КОДА») leaked into
  the master screen; planning actions had no visible period; ETK block order drifted from Google; no committed demo seed or
  screenshot script in main (seed lived on branch `claude/project-thread-gd6e8g`).
- Fix: skill `.claude/skills/erp-screens` (production-specific rules, mandatory via CLAUDE.md) + vendored Anthropic
  `design-critique` (knowledge-work-plugins, Apache-2.0) for generic critique; `frontend-design` narrowed
  (it pushes "distinctive" aesthetics, wrong for a dense ERP). Plugins were not enabled in project settings: that is an
  owner-only click, and the owner (30.09) does not want extra plugins burning limits; a repo skill costs one description line until loaded.
- Tooling: `scripts/demo/run-demo.sh` (Linux demo on 8021, seeded via `seed_demo.py`), `scripts/demo/shoot.mjs`
  (27" + tablet PNGs and a lint for English ids / raw codes / stuck loading / sideways scroll).

Why: the owner judges by Google SUPPLYMAP and wants better, not worse; he should not have to correct each section.
How to apply: load `erp-screens` before any UI change. Hub: [[erp-next-hub]] · [[owner-working-rules]] · [[module-techcards]] · [[module-production]].

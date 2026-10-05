# Third-party code in ERP_NEXT

Skills under `.claude/skills/` have their own notices in `.claude/skills/THIRD_PARTY_NOTICES.md`.

## TECHMAP-GRAPHER

TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission, 2026-10-06.
Source: github.com/SaveOurSouls/TECHMAP-GRAPHER, commit `e63befb85edb79f0c1635ac8314539ac24d2486a`
(branch `codex/integration-m5-22`).

Used in `backend/app/modules/techcards/harness/` (the harness model, its length rules and the reader/writer of the
`.techmap-project.zip` design document) and `backend/app/modules/techcards/grapher_import.py`. The rules are
rewritten in Python on the ERP's architecture, checked against the author's docs and examples; each function names
the original file it follows, and the places where it deliberately differs.

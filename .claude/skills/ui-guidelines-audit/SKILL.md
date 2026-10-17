---
name: ui-guidelines-audit
description: Terse file:line audit of ERP_NEXT screen code against the Web Interface Guidelines (accessibility, focus, forms, keyboard, tabular numbers, Intl formats, interactive states). Use as the pre-ship check after a screen change, or when asked to «проверь вёрстку», «аудит экрана», «review UI code». Reads a local pinned rule copy — no network.
argument-hint: "<file-or-glob>"
---

# UI guidelines audit (ERP_NEXT)

Adapted from vercel-labs/agent-skills `web-design-guidelines` (MIT, commit 063bee9). The upstream skill fetched
its rules from GitHub `main` on every run; here the rules are a pinned local copy, `rules.md`, trimmed of
Images / Safe Areas / Hydration / English copy rules.

## How
1. Read `rules.md` next to this file.
2. Read the files given (default: the `frontend/src/*.tsx` and `styles.css` changed on the branch —
   `git diff --name-only origin/main...HEAD -- frontend/src`).
3. Check every rule; report in the `file:line - issue` format from `rules.md`, grouped by file, no preamble.

## ERP_NEXT overrides (win over `rules.md`)
- UI copy is Russian, sentence case; never flag a Russian label for Title Case or English style.
- Labels never show type codes (`cutWireStrip`) — flag any that do.
- Dense tables are intentional (Google-Sheets-like rows); don't flag density, flag only real defects
  (no `tabular-nums` on numbers, numbers not right-aligned, no visible focus, clickable `<div>`).
- Keyboard hints use Ctrl, not ⌘ (Windows shop).
- The `erp-screens` skill stays the source of truth for layout and block order; this is a code-level check.

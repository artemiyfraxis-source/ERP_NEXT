---
name: cloud-threads-setup
description: How claude.ai project threads run ERP_NEXT — cwd above three repos, no repo hooks/plugins, manual drift report
metadata:
  type: reference
---

- Threads start in `/home/claude` holding `ERP_NEXT`, `SUPPLYMAP`, `claude-brain`. ERP_NEXT `.claude/settings.json` does NOT apply (no SessionStart hooks, no `enabledPlugins`); `.claude/skills`, agents and `CLAUDE.md` do load.
- Platform clones SUPPLYMAP shallow; run `node ERP_NEXT/.claude/hooks/cloud-siblings.mjs && node ERP_NEXT/.claude/hooks/supplymap-drift.mjs` (unshallow + drift report, PR #71).
- The owner's personal plugins (Data, Engineering, Writing Skills, loop-and-gate-foundation, handoff) sync from the account into every thread. Project plugins are set in Settings → Plugins.
- Knowledge graph: this folder is mounted in the owner's Obsidian vault as `projects/ERP_NEXT`; write notes here with `[[links]]` → [[erp-next-hub]].
Related: [[erp-merge-gotchas]], [[owner-working-rules]].

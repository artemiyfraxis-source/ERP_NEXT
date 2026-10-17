---
name: cloud-threads-setup
description: How claude.ai project threads run ERP_NEXT — cwd above the repos, no repo hooks/plugins, run cloud-siblings by hand
metadata:
  type: reference
---

- Threads start in `/home/claude` holding `ERP_NEXT`, `SUPPLYMAP`, `claude-brain`. ERP_NEXT `.claude/settings.json` does NOT apply (no SessionStart hooks, no `enabledPlugins`); `.claude/skills`, agents and `CLAUDE.md` do load.
- Run `node ERP_NEXT/.claude/hooks/cloud-siblings.mjs` by hand there (clones the siblings, prints the owner's global rules and brain INDEX). SUPPLYMAP is legacy since 05.10: nothing in the gate reads it, no drift report is needed.
- The owner's personal plugins (Data, Engineering, Writing Skills, loop-and-gate-foundation, handoff) sync from the account into every thread. Project plugins are set in Settings → Plugins.
- Obsidian MCP works only on the owner PC (`claude mcp add --transport http --scope user obsidian http://127.0.0.1:27123/mcp --header "Authorization: Bearer <key>"`); cloud threads use git instead.
- Knowledge graph: this folder is mounted in the owner's Obsidian vault as `projects/erp_next`; write notes here with `[[links]]` → [[erp-next-hub]].
Related: [[erp-merge-gotchas]], [[owner-working-rules]].

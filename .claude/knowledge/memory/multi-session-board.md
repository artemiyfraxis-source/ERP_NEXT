---
name: multi-session-board
description: Parallel ERP_NEXT sessions coordinate through the private brain board erp-next-board; one coordinator merges, releases and assigns migration numbers
metadata:
  type: project
---

Since 20.11.2026 (owner: «надо через мозг мультисессионность сделать») parallel sessions follow the brain note
`multi-session-coordination` and the live board `erp-next-board` (brain root, private — read/edit via Obsidian MCP).
Only the coordinator named there merges to main, runs `update-server.yml` and assigns Alembic numbers; workers claim a
zone on the board before editing and stop at «PR ready» → message the coordinator.

**Why:** that day 9 sessions merged/released on their own, took migration numbers 0208 and 0209 twice, and the owner
could not see what was in flight.
Editing trap (21.11): `vault_patch` with `within: <n>` + `replace` replaces the WHOLE list block — other sessions' rows in
that list vanish (happened to «Zones»). Change your own row by reading the block right before the patch and writing it
back with only your line changed (or `append` a new line); never write a block from a stale read.
**How to apply:** read the board at session start; never put the board inside this folder (it is mounted into the public
repo). Related: [[night-coordinator]], [[owner-working-rules]], [[parallel-sessions-worktrees]].

---
name: owner-tasks-into-queue
description: Every owner request (incl. «owner additions» in plan files and leftovers of applied work) goes into the active TASKS.md queue at once; audit plan files before saying «всё»
metadata:
  type: feedback
---

19.11.2026 the owner asked «все задачи ты обработал?» and found I had left work undone over a night run: 30 of 94 СИ
left without a norm after the #937 apply (noted as «30 unassigned», never queued), and the 18.11 «owner additions»
(«вне проекта» manual jobs for every machine, full PXS_HMI/BZW2.5 replacement) sat only in
`.claude/plans/machines-section-plan-2026-11-18.md`. Owner: «и почему ты это не делал ночью».

**Why:** a requirement written only into a plan file or a result line («N left over») drops out of the working queue;
night runs then only process the queue.
**How to apply:** when the owner states a requirement, or an applied step leaves a remainder, add a TASKS.md queue line
with owner/next action the same turn. Before a night run or a «всё сделано» report, re-read every plan file's owner
additions and the queue, and start every unblocked item; report only owner-blocked ones as waiting
([[owner-request-queue]], [[owner-working-rules]]).

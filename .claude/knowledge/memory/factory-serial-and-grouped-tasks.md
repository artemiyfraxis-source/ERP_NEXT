---
name: factory-serial-and-grouped-tasks
description: Owner 16.11 — coax centre label = factory serial FRX.<project>.<PN>.<n 1–999999>; similar КБ (only length differs) issued as ONE production task
metadata:
  node_type: memory
  type: project
  originSessionId: 5ce49b55-2573-4a7d-92f3-b40ed9d819be
  modified: 2026-11-16T15:57:18.547Z
---

- Owner 16.11.2026: the centre label of coax assemblies becomes the factory number `FRX.<project number>.<product number>.<serial>`,
  e.g. `FRX.708К.120907/AA.000001` (product number = customer PN from the assembly name; serial = assembly's sequential number
  within the order, 1–999999). Replaces the drawing's «PN + date + engineer №» text. Owner 17.11.2026: the serial is printed WITHOUT padding — `FRX.708К.120907/AA.1` … `.15` («так и нужно»); the template
  is `{№}`, not `{№:6}`.
- Owner 16.11.2026: when issuing work there must not be 41 tabs — ONLY cutting is grouped: КБ whose cut differs only in length
  go into ONE cutting task with a per-КБ table («это только нарезка, дальше уже разное»); later stages stay per-КБ tasks.
  Each КБ keeps its own counts and stage.

**Why:** 708К. has 41 КБ in 6 drawing groups; per-КБ tasks flood the fitter and the print queue.
**How to apply:** design production tasks and label printing around groups of equivalent ETKs; never hard-code a fixed centre
label text in the generator — it is a serial template filled at print. Related: [[fakra-708k]], [[etk-by-semifinished]],
[[module-production]].
- Owner 17.11.2026: the grouped preparation task is ONE task (one start/submit), not a list of per-КБ tasks; label printing
  is part of it — one combined label list for all КБ of the group, printed in one go by one person.
- Owner 20.11.2026 (supersedes the 17.11 «printing is part of it» for task shape): «задание на печать маркировок и на нарезку почему-то
  в одном, трубки тоже в одном» — cutting, label printing and tube cutting must not sit in one task; group across КБ, but split
  by operation/machine (exact shape to confirm with the owner; queue: TASKS.md «Блок заданий + 708К.»).

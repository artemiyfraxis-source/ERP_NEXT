---
name: check-print-progress-myself
description: Owner 19.11 — check label/flag print progress on the machine PC myself (SSH, Windows queue), don't ask
metadata:
  type: feedback
---

When the question is "has the batch physically printed", look yourself: ERP only knows `spooled` (Godex buffers), but
the shop PC's Windows queue shows what is left. SSH route and hosts: [[warehouse-pc-ssh]] (private brain note);
`Get-PrintJob -PrinterName 'Godex EZ2350i'` count + first job status, agent.log tail.

**Why:** 19.11 I asked the owner whether 663К.3 flags were done; owner: «ну ты мог посмотреть».
**How to apply:** before asking the owner about print/machine state, check the PC over SSH (read-only); ask only
about what can't be seen remotely (crumpled/lost flags, physical defects).

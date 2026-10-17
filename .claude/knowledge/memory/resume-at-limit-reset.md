---
name: resume-at-limit-reset
description: "When workers hit the session usage limit, immediately schedule a wake-up at the reset time and resume them then"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 8677a0f6-6abc-4b3c-ac40-0726d25e18d0
  modified: 2026-10-05T04:23:21.798Z
---

When agents fail with «You've hit your session limit · resets HH:MM», schedule a one-shot CronCreate at that reset
time (a minute after) with a prompt that resumes every interrupted worker (SendMessage to each agent id with
«resume, finish, commit, report») and continues the orchestration. Never just wait for the owner.

**Why:** 05.10 the limit hit at ~00:30 (reset 02:10); nothing was scheduled, the work sat idle until the 06:00
release prompt — owner: «очень плохо, что ты не начал в 2:10, мы просто потеряли время».

**How to apply:** the moment the first «session limit» failure arrives, read the reset time from it and create the
cron before replying. The session must stay open (CronCreate is session-only). Related: [[owner-working-rules]].

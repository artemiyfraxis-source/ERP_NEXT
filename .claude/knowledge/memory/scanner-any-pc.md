---
name: scanner-any-pc
description: "Owner 28.10 — scanning must work from any PC: one shared «Сканировать» component routes to the browser's own PC agent, install guide when no agent; never tie to the warehouse PC"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 128bab83-781a-4ce3-a66d-a0a9a0fa29cb
  modified: 2026-10-28T08:23:49.980Z
---

Owner 28.10.2026: «везде это должно быть… универсальность, если вдруг ПК сменять». Every «Сканировать» (Поставка /
Внести позиции, Приёмка, Ульяна's shipment УПД, future ones) is one shared component: the job goes to the agent of the PC
the browser runs on (agent reports PC name + LAN IP + detected WIA scanner; else a remembered scanner choice); no agent →
an install guide (one-line command with a one-time token, «Проверить»), not an error. The agent auto-starts with
Windows, self-updates, finds scanners in the background.

**Why:** today the scanner is wired to the single warehouse PC agent (tools/print-agent); a new or replaced PC must just work.
**How to apply:** never add a scan path bound to one machine; build new scan buttons on the shared component.
Related: [[warehouse-scanner-usb]], [[warehouse-pc-ssh]], [[receiving-now-vs-later]].

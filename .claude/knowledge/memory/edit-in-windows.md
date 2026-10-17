---
name: edit-in-windows
description: "Owner rule — anything opened for viewing/editing a record (cards, forms, «Правка») opens as a modal window (PageWindow), never a side panel or inline block"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 9a217c80-baab-4692-b983-aefbaa8785b1
  modified: 2026-10-14T11:09:13.216Z
---

Everything that opens a record to view or edit it — record cards, edit forms, «Правка», «Данные отправки», calendars,
competences — opens as a modal window (`frontend/src/PageWindow.tsx`, `.page-window`), not as a side panel next to the
table and not as an inline block that pushes the page down.

**Why:** owner, 14.10.2026 (twice: «тут всё должно окнами открываться» on Ресурсное планирование, then «ты можешь
перестать так делать — всё, что править, должно окнами открываться» on the БД coax side card). Side panels squeeze the
table on 16" and hide columns.

**How to apply:** when building or touching a screen with a row → card/edit flow, use PageWindow (Esc closes; click
outside closes only read-only windows). Brief workers with this rule explicitly. Related: [[erp-screens-rules]].

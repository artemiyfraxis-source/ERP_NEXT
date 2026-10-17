---
name: connector-3d-only-on-card
description: Owner 17.10 — the 3D connector picture is only for the series card face in the connector catalog; ETK/generator sketches show two flat views (top + mounting side)
metadata:
  node_type: memory
  type: feedback
  originSessionId: 06abb884-193d-4a30-b642-e60180af7e46
  modified: 2026-10-17T07:47:37.750Z
---

The 3D (perspective box) connector picture belongs ONLY on the face of a connector series card in the catalog
(«Провод — плата», ConnectorGallery). Every other place — ETK operation «Эскиз» (connector mount), generator, print —
shows two flat views: the top view and the mounting side (face from the wire side with pin numbers and wire colours).

Every connector picture (catalog 3D face, top view, ETK sketch) shows on its TOP an orientation feature — the latch
(замок) or the slots (прорези) — so the side can be told at a glance; never a plain top (owner 17.10).

**Why:** owner 17.10 on 713К.4 КБ1 «Монтаж контактов в разъём»: «я вроде просил оставить два вида сверху и со стороны
монтажа … обемная картинка только тут для лица карточки разьема». He had asked before; it came back.
**How to apply:** when touching connector drawings (etkDraw, connectorTopView, connectorPicture) never add the 3D view
outside the catalog card; check existing sketches keep exactly two views. Related: [[erp-screens-rules]], [[module-techcards]].

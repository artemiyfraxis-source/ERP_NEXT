---
name: connector-3d-only-on-card
description: Owner 17.10/18.10 — 3D connector picture only on the catalog card; ETK sketches two flat views; every harness picture (ПФ, scheme) draws the real connector in top view, wire exit toward the harness, never a labelled box
metadata:
  node_type: memory
  type: feedback
  originSessionId: 06abb884-193d-4a30-b642-e60180af7e46
  modified: 2026-10-18T09:23:31.785Z
---

The 3D (perspective box) connector picture belongs ONLY on the face of a connector series card in the catalog
(«Провод — плата», ConnectorGallery). Every other place — ETK operation «Эскиз» (connector mount), generator, print —
shows two flat views: the top view and the mounting side (face from the wire side with pin numbers and wire colours).

Every connector picture (catalog 3D face, top view, ETK sketch) shows on its TOP an orientation feature — the latch
(замок) or the slots (прорези) — so the side can be told at a glance; never a plain top (owner 17.10).

Every drawing that shows a connector inside a harness picture — ПФ pictures (branch, «ТУТ на месте Б», изделие), the
generator harness scheme, print — draws the REAL connector in TOP VIEW (`connectorTopView`, as `pfBranchSvg` does on
ПФ 2), turned so its wire exit faces the harness: side А on the left with wires going right, side Б on the right with
wires going left. Never a labelled box («X1» in a frame), never the wire-side face rotated in its place. The
designator goes next to it (owner 18.10: «когда я уже добьюсь, чтобы на чертежах были разъёмы, вид сверху и
правильно развёрнутые» — I had briefed workers «flat outlines» and «wire-side face», both wrong).

**Why:** owner 17.10 on 713К.4 КБ1 «Монтаж контактов в разъём»: «я вроде просил оставить два вида сверху и со стороны
монтажа … обемная картинка только тут для лица карточки разьема». He had asked before; it came back.
**How to apply:** when touching connector drawings (etkDraw, connectorTopView, connectorPicture) never add the 3D view
outside the catalog card; check existing sketches keep exactly two views. Related: [[erp-screens-rules]], [[module-techcards]].

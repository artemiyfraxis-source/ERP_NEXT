---
name: coax-contact-depth
description: "Owner 19.10 — «заглубление контакта» of a straight coax connector = body front face → contact, read by tracing extension lines; SMA male E 0,38…1,14, female H+I+J 1,88…2,41; mm only"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 588273da-1ef1-4aac-9b53-d023958d79e8
  modified: 2026-10-19T08:58:09.522Z
---

«Заглубление контакта» (stored per straight, non-angled coax connector; shown on the ETK «Монтаж корпуса» sketch and
its КОНТРОЛЬ step) is the distance from the connector BODY's front face to the centre contact, in millimetres only
(never inches; inch-only sheets are converted ×25,4 and marked «пересчитано из дюймов»).

Read it from the maker's «Габаритно-присоединительные размеры» drawing (radiolab.ru has many) by tracing extension
lines from the body face to the contact tip and summing every dimension on that path (min of mins, max of maxes).
SMA, the owner-confirmed example: male (вилка) — E = 0,38…1,14; female (розетка) — H (face → REF) + I (REF → insulator
face) + J (insulator face → contact) = 1,88…2,41. Store the formula/letters in the value's source note.

Plugs (вилки, owner 19.10): «заглубление» = размер от уступа штыря (основание штыря) до REF; торец гайки ≈ REF, ничего не вычитать.
Вилка N: D 5,33…5,84 = уступ штыря → REF, торец гайки ≈ REF (владелец 19.10); тот же размер у TNC (radiolab F / MIL 313-1 E) и BNC (radiolab E / MIL 301-1 E). Written to prod 19.10: N/TNC/BNC-male = 5,33…5,84 (88 rows). SMB/MCX/MMCX/FME/UHF plug sheets give only min or no tolerance — not written.

Written to prod 19.10 (third batch, 12 rows): SMB-female = 0,36…1,19 (MIL-STD-348B fig 311-1: C 3,33…3,58 − D 2,39…2,97, pin tip recessed; radiolab agrees); MCX-male = 2,80…3,20 (insulator face/pin base → REF; radiolab J + Amphenol Connex G); MCX-female = 1,20…1,52 (F 4,00…4,12 − E 2,60…2,80; radiolab G−H + Amphenol F−E). Still one-sided in every source, NOT written: SMB-male (socket plug: only G ≥ 2,97), MMCX male (pin D ≤ 3,15) and female (face→REF B ≥ 2,60), FME (C ≥ 2,50; REF sits mid-body), UHF (only nominal 9,14). SMB gender is the industry one (radiolab «розетка» = jack WITH the pin, fig 311-1; «вилка» = plug with socket contact) — the ERP's SMB-female rows follow it.

**Why:** on 19.10 I took the wrong letters three times — REF-based F/G/I/J (REF is not the body face), the female G by
symmetry with the male E (G is the thread lead), then H alone (stops at REF). Each time I read letters by table
position/symmetry instead of following the lines.
**How to apply:** for any series, write the path «торец → … → контакт» before taking numbers; if the chain is
ambiguous, don't write the value — list it for the owner. Related: [[coax-data-sources]], [[maker-docs-over-shop-values]],
[[prod-reference-write]].

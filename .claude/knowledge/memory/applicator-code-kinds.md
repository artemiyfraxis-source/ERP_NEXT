---
name: applicator-code-kinds
description: "Owner 29.10 — БД.ТЕР «Аппликатор» codes are ALL applicators, three kinds: M = ОТП format (pre-stripped wires only), A = for automatic machines, S = crimps unstripped wires; never matrices / hand tools"
metadata:
  node_type: memory
  type: feedback
  originSessionId: e96d7f67-f2ac-41a7-8dd4-ce6a1fc046a6
  modified: 2026-10-29T13:53:12.359Z
---

Owner 29.10.2026: «У нас три типа аппликаторов: M — ОТП формат, обжимает только зачищенные заранее провода; A — для
автоматов; S — для обжима не зачищенных проводов». The old note in `references/equipment.py` («S — hand tool,
M — matrix, A — applicator») was wrong; on 29.10 only A1–A20 were created as «Аппликаторы» (ОБ.106–ОБ.126) and the
«＋» on a missing code sent M → «Матрицы», S → «Обжимной инструмент» (PR #750) — both corrected.

**Why:** the owner counted his applicators and found M/S missing from «Аппликаторы».
**How to apply:** every M/A/S code of БД.ТЕР «Аппликатор» is one record of type «Аппликаторы» with the code in «Код в базе
терминалов»; the kind comes from the letter. «Матрицы» (ОСН./КВТ die sets) and «Инструмент» are other things with no
БД.ТЕР code. Related: [[applicators-by-series]], [[crimp-contacts-protocol]].

---
name: ob019-erp-hmi-idea
description: Owner idea 18.11 — ERP's own control program for ОБ.019 instead of PXS_HMI; idea only, no build without go
metadata:
  type: project
---

- 18.11.2026 owner: «сделать в ЕРП свою прогу для управления» ОБ.019 (laser cut/strip line), from a tablet or the PC browser —
  then «это пока идея». Do NOT plan or build it until the owner says go.
- Proven the same day: direct Modbus RTU on COM8 of PC .108 (38400 8N1, slave 1, Dn = holding register n); D32 = cut length,
  int32 ×0.01 mm, low word first; FC16 write 326.00 shown by PXS_HMI, owner typed 325 back. HMI reads params from the PLC and
  does not overwrite them on start. COM8 has a single master: PXS_HMI or our agent, never both.
- 18.11 full control test (owner at E-stop, HMI closed): Reset works only with HMI context — D730=13 (page), date
  D1087..1089 rewritten each second, D600 bit1 held 1.5 s (0.5 s with no context did nothing); homing ~8 s → D766=102.
  Start = D599 bit0 held 1.5 s in Cycle mode (D20=1), D22 Total Set = T.Finish+1 → one 100 mm ПВАМ 0.35 piece, owner
  measured 100 mm; ends D766=107 / D782=5 «Total finish» (normal, not a fault). SHA-256 (D1203–1211) not needed.
  E-stop on ОБ.019 also powers off PC .108. Run bus scripts ON the PC detached from SSH (Wi-Fi drops).
- 18.11 owner-approved recipe ПВАМ 0,35 on ОБ.019: strip 5/5 mm (D34/D38=500), pull-off 5/5 (D36/D40=500; 0 = only
  scored, not pulled), blade Dia 0.80 (D42=80; 0.90 scored too shallow), back-off 0.10 (D43=10) → «всё отлично».
  PC moved to 192.168.1.71 (Mercusys dongle); mbc.py needs MBD_HOST. While operators run PXS_HMI never touch COM8 —
  watch only its logs (D:\TextLog\YYMMDD.csv, D:\AlarmLog\YYMMDD.xls).
- Sketch discussed: agent on .108 owns COM8; ERP screen (live panel → params/job from ETK cutting groups → commands with
  confirmation; physical e-stop stays primary) → counts back to the cutting task. Register map doc: PR from branch ob019-map-1118.

- Owner flow (18.11): it goes through the existing «Наладка оборудования» section (EquipmentSetupPage / recipe versions).
  The operator (Слава) first checks the machine state, then corrects; ERP must collect his corrections and learn the setup
  («по началу будет криво»): start values = last verified recipe for machine + cable geometry, else ETK nominal; live PLC
  values next to proposal; each correction journaled; «Сохранить версию» stores actual machine values as a recipe version;
  persistent offsets vs ETK become per-machine corrections. Corrections must go through ERP, not PXS_HMI.

**Why:** the owner wants jobs to flow from ETK to the machine without the vendor HMI.
**How to apply:** treat as backlog; any further bus write needs the owner present and an explicit go. Related: [[bzw-machine-control]].

---
name: vna-measurement-setup
description: "Owner's VNA kit (12.11.2026) — Planar S50180 + АКМ ACM2520-111 + 6650F27 + test cables, registry numbers СИ.NNN, S2VNA COM facts, inventory prefixes per type"
metadata:
  node_type: memory
  type: project
  originSessionId: 08bbf62f-c7d3-4a8a-9736-fe7720579cf1
  modified: 2026-11-12T14:23:16.534Z
---

Inventory prefixes (owner 12.11.2026): every type has its own — measuring instruments/tools/VNA accessories «СИ.NNN»,
equipment «ОБ.NNN», applicators = their M/A/S code (PR #829; prod renumbered 91 rows, ОБ.126 has no code).

Prod registry (Качество → Средства измерения, loaded 12.11 via API scripts): СИ.001 Arinst VNA-DL (verified to 23.10.2027,
own app Arinst VL, no automation → «Снять с ВАЦ» takes the newest saved .s1p); СИ.002 Planar S50180 №26030095 (ports N female,
100 kHz–18 GHz, type approval 82102-21, no verification certificate yet → «Поверка не подтверждена»); СИ.003 kit 6650F27
(full: OPEN/SHORT/LOAD M+F, thrus M-M MA6666A-11, F-F MA6666B-11, M-F MA6666C-11, torque wrench 0.9 N·m; passport coefficients =
S2VNA built-in kit #28); СИ.004 SMA-MF06 (Arinst SMA kit; warehouse had it as «SMA-male RG-6»); СИ.005 N kit (Arinst);
СИ.006–009 ADP1A 35M-35F ×2, 35F-35F ×2 (VSWR ≤1.06 to 20 GHz); СИ.010–013 placeholders (рулетка, штангенциркуль, линейка,
мультиметр); СИ.014–097 84 cheap adapters per piece from stock (B-311/F-311/F-322 are 75 Ω); СИ.098–103 Planar test cables
C50NMIIIM.2 ×2 (N m–тип III m, 2 m), C50SMNM.2 ×2 (SMA m–N m, 0.6 m), C50NMNM.2 ×2 (N m–N m); СИ.104 АКМ ACM2520-111 №26140008
(automatic calibration module; both ports 3.5 mm female — owner 14.11). Owner also has 4 self-made cables (not registered yet).

S2VNA on the owner's PC (C:\VNA\S2VNA, 26.2.2): COM `S2VNA.Application` works without setup; not in the ROT (GetActiveObject
fails) → readiness = process + USB VID_2226; SCPI socket off. Kit definitions readable/writable via
`SCPI.SENSe(1).CORRection.COLLect.CKIT.STAN(i)`; don't leave the operator's selected kit changed.

S50180 primary verification found in ФГИС «Аршин» (fgis.gost.ru/fundmetrology/eapi/vri?mit_number=82102-21&mi_number=<serial>):
С-ГДИ/21-08-2026/551485311, 21.08.2026 → 20.08.2027 (ООО «КИА»); entered on СИ.002 16.11 → «Действует». A new СИ «not needing
verification» usually has a primary one in Аршин — look there before asking. «Снять с ВАЦ» verified end-to-end 16.11 (s1p, 201 pts).
Gotcha: the ERP agent runs non-elevated and can't attach COM to an S2VNA started elevated (e.g. from an admin shell) — no IDN,
server answers «не в сети»; start S2VNA via `explorer.exe <path>` and restart the agent task.

16.11 hardware check (details in docs/vna/s2vna-commissioning-2026-11-14.md): АКМ calibration works (`ECAL.SOLT2`, 21 s),
verified by the 6650F27 LOAD (VSWR ≤1.023 to 18 GHz); fresh sweep needs BUS trigger + `TRIG:SING` + `*OPC?`.
Claude's shell runs elevated → its COM gets a device-less S2VNA; run COM scripts via `explorer.exe <file>.cmd` (user token).
ADP1A serials: СИ.006 N1C765, СИ.007 N1C766 (35M-35F), СИ.008 N8B139, СИ.009 N8B140 (35F-35F, the better one).

ArinstVL on the ОТК PC (checked 23.11): Windows build 2.11.10 (Qt 5.15), per-user install under %APPDATA%; logs, profiles and
calib_table.yaml under %APPDATA%\Kroks\ArinstVL. Owner 23.11 asked for the eax.me guide patch anyway: ArinstVL.exe
patched — the `call fetchManifest` in AppUpdater::init (RVA 0x164F3, file 0x158F3, `e838faffff`) → 5×NOP; original sha256 8347138a…,
patched acaea9ba…; backup `ArinstVL.exe.orig-2.11.10` beside it. No app update check any more; an ArinstVL update/reinstall
overwrites the patch (redo by searching «trying to download manifest» xrefs). WM_CLASS part is Linux-only.

Owner 16.11: all VNA accessories (adapters, test cables, calibration kits) are shared between instruments — none is
bound to one VNA (the 12.11 parenting to СИ.002 was a registry workaround, being removed).

**Why:** stage 2 = ERP-guided calibration/measurement master (plan in session scratch, slices P0–P5); with the АКМ the
calibration step can be one connection instead of SOLT steps.
**How to apply:** build chains from registry items (port → C50SMNM.2 → ADP1A → plane), prefer АКМ calibration; see
[[coax-vswr-acceptance]], [[module-production]].

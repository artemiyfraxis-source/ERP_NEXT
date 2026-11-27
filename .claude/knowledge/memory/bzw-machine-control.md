---
name: bzw-machine-control
description: "Owner 16.11 — control BZW cut/strip machines (PLC, RJ45 for the vendor's MES) from ERP ourselves; no vendor MES/register map on hand"
metadata:
  node_type: memory
  type: project
  originSessionId: 5ce49b55-2573-4a7d-92f3-b40ed9d819be
  modified: 2026-11-26T14:44:44.344Z
---

- BZW (Bozhiwang) cut/strip machines (BZW-886+Q2 ОБ.094 and others) are PLC-based, not PCs; their RJ45 is for control by the
  vendor's MES («кривая китайская»). Owner 16.11.2026: «мы сами хотим управлять»; we do NOT have the MES software and won't ask
  the vendor for the register map — reverse-engineer ourselves.
- Plan: identify panel/PLC brand (nameplates) and IP; check whether the machine is a server (open ports) or a MES client
  (server IP/port in panel settings → listen); read-only Modbus snapshots + diff to map length / strip A/B / quantity / counter;
  then a mini-PC agent per machine group (like the warehouse scanner agent): read counters into the cutting task first, write
  jobs from the ETK only after the map is verified on one machine idle.
- 26.11.2026: ОБ.094 got its mini-PC DESKTOP-OTK01 (192.168.1.73, Win 11, user Admin). Its wired port is cabled into
  the machine and carries **EtherCAT** (Ethertype 0x88A4, ~1000 frames/s from master MAC 16-7F-0F-E4-4F-BA), so the
  «RJ45» reached there is the motion fieldbus, not Modbus/TCP/MES. Protocol bindings on that NIC are disabled so
  Windows never transmits into the bus; passive pktmon capture only. Next: owner confirms which machine socket the cable
  uses; map process data read-only from captures (counters, length) before any thought of control.
- 26.11 later: PLC = Xinje **XLH-24A16** (XL, Ethernet + EtherCAT master for the drives), HMI = TouchWin TGA63G-MT
  (Modbus RTU 115200 8N1 st.1 to the PLC). ERP access: **Modbus TCP 192.168.6.6:502** (Xinje default) from the
  mini-PC NIC «Ethernet» 192.168.6.250 — works alongside the HMI. HMI project upload refused (no source); PLC program
  upload «程序不存在» (protected download) → no vendor map. Map by observation: read-only logger task «Fraxis OB094
  logger (read-only)» (startup, 30 days) writes C:\ProgramData\fraxis-ob094\changes.log; correlate with ERP cutting
  jobs on ОБ.094 (length/strip/qty) or tap the HMI↔PLC line (FTDI RX-only) for exact addresses. Owner won't ask BZW.
- Probe tool (read-only, stdlib Python): `~/Downloads/BZW разбор/bzw_probe.py` (ports / ping / modbus / diff / listen).

**Why:** one operator cuts the whole project; jobs should flow from the ETK cut list to the machine and counts back to ERP.
**How to apply:** never write to a machine register before the mapping is confirmed read-only; ask which machines are on the LAN.
Related: [[factory-serial-and-grouped-tasks]], [[warehouse-scanner-usb]], [[fakra-708k]].

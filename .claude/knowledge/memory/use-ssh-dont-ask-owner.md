---
name: use-ssh-dont-ask-owner
description: Owner 23.11 — never ask him to click/close/start things on shop PCs I can reach by SSH; do it myself and report
metadata:
  node_type: memory
  type: feedback
  originSessionId: 897930ed-5259-4948-8aa2-adab81111703
  modified: 2026-11-23T10:58:36.182Z
---

Owner 23.11.2026, after I asked him to close ArinstVL on the VNA PC for a probe: «и зачем тебе моё участие, если у тебя
полный доступ по SSH».

**Why:** the shop PCs (warehouse .69, VNA DESKTOP-PTA57QF .121, ОБ.019 .71, Godex .45/.70) are reachable through the
erp-server jump; asking the owner to do software actions there wastes his time.
**How to apply:** for any software step on a shop PC (close/start a program, copy a script, run a read-only probe, check
a port or process) use SSH yourself — recipe in the private brain note `erp-next-warehouse-pc-ssh` (Windows OpenSSH with
a temp `-F` config, 8.3 key paths, PowerShell via `-EncodedCommand`, scp for files > ~8 KB). GUI programs for the operator
must be started in session 1 via a one-shot scheduled task (`-LogonType Interactive`), never from the SSH session.
Search for the exact exe by name/uninstall key — a `*-setup*.exe` in Temp is an installer (23.11 I launched the ArinstVL
installer by mistake and had to kill it). Ask the owner only for physical actions (plug, power, press, measure) or
product decisions. Related: [[check-print-progress-myself]], [[warehouse-pc-ssh]].

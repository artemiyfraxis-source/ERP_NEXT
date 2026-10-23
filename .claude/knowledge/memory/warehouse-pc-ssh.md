---
name: warehouse-pc-ssh
description: I can SSH into the warehouse PC through erp-server as a jump host and fix the print/scan agent myself — details in the private brain
metadata:
  node_type: memory
  type: reference
  originSessionId: 128bab83-781a-4ce3-a66d-a0a9a0fa29cb
  modified: 2026-10-23T17:10:37.293Z
---

Since 23.10.2026 the warehouse PC accepts SSH from erp-server only. Hosts, users, keys and the working recipe
(EncodedCommand, 8.3 paths, agent restart) live in the PRIVATE brain note `erp-next-warehouse-pc-ssh.md` (vault root,
read via Obsidian MCP) — this repo is public, so no addresses or users here.

How to apply: when the label/scan agent misbehaves, read that brain note and fix it over SSH instead of sending the
owner commands (his chat mangles pasted commands). Related: [[warehouse-scanner-usb]], [[erp-server]].

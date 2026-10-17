---
name: windows-python-crlf
description: Rewriting a repo file with Python text-mode I/O on this Windows machine turns the whole file into a CRLF diff
metadata:
  node_type: memory
  type: feedback
  originSessionId: 08742427-3489-4a0b-8831-b53738e0ab58
  modified: 2026-09-28T13:14:50.847Z
---

On the owner's Windows machine, `open(p, "w")` in text mode writes `\r\n`. The repo is byte-exact LF
(`.gitattributes`: `* -text`, SOURCE.json pins sha256), so one Python edit script turned `api.ts` and four
procurement modules into full-file diffs (2026-09-28). Bash `sed -i` and the Edit/Write tools keep LF.

**Why:** a whole-file diff hides the real change, breaks hunk-level staging next to a parallel session, and
would change hashes of synced files.
**How to apply:** in Python edit scripts use `open(p, encoding="utf-8", newline="")` for both read and write;
check with `file <path>` (reports "CRLF line terminators") before committing. Also: the Bash tool rejects
heredocs whose body contains an apostrophe (`buyer's`) — use the Write tool for such text.

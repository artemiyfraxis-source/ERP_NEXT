# Arinst control contract (native ArinstVL, 2026-11-23)

Base: `ee0b7914dc6333b6f66d617818f25ff093758c4a`. Migration `0219_arinst_control`,
parent `0216_equipment_screen_group`; the coordinator reparents at integration. C0/T1/T2 own this
contract, warehouse routing/schema and the Windows driver. T3 owns mathematics; T4 owns session
validation, claims and saves; T5 owns screens. No release or hardware acceptance is implied.

## Identity and heartbeat

Canonical Arinst serial: `arinst:<uint32>-<uint32>-<uint32>` (decimal, no leading zeroes).
The observed UID is retained separately; printed serial remains on the instrument registry.
The prefix `arinst:` is reserved and refused for Planar. Generic STM32 VID/PID, COM number,
installed ArinstVL and imported files never establish verified identity.

`warehouse_vna_control` key is `(machine_id, serial)`. New heartbeat field:

```json
{"vna_controls":{"version":1,"devices":[{
  "source":"arinst","serial":"arinst:3932198-827215893-540422231",
  "version":3,"methods":[],"ops":["connect","read_profile","apply_profile","measure_standard","capture","close"],
  "model":"VNA-DL","firmware":"2.0.0","hardware_uid":"3932198-827215893-540422231",
  "identity_verified":true,"license_state":"not_required","trial_remaining_seconds":null,
  "control_ready":true,"reason":"","observed_at":"2026-11-23T12:00:00Z"
}]},"arinst_candidates":[{"usb_path":"USB\\VID_0483&PID_5740\\...","port":"COM3"}]}
```

Envelope version 1 replaces the PC's full control inventory, including an empty list. Protocol
versions 1/2 remain Planar; version 3 is Arinst. Missing identities immediately become absent;
TTL is the existing `LIVE_FOR`. Repeated cached observations do not extend identity freshness.
Candidates are presence only, never routable. Routine heartbeat never opens COM. A legacy singleton
updates/removes only Planar controls, and only one live owned Planar device can receive it; it
does not erase Arinst rows. The singleton is ignored when the new envelope is present. Unknown
legacy protocol versions remain visible but cannot route commands.
Planar entries must also carry `name`, the complete four-part IDN response whose serial matches
`serial`; a list-only heartbeat therefore preserves ordinary file-capture provenance.

`warehouse_vna_identities` binds instrument UUID to source, canonical serial, original UID,
verified agent, firmware, verification time and confirming operator. Instrument UUID is primary;
canonical serial and `(source, hardware_uid)` are unique. Admin confirmation requires fresh native
ID/type/firmware evidence. No synthetic `arinst` backfill. `devices.instrument_identity(session, uuid)` is T4's
lookup seam; `capture_instrument` must use this for direct Arinst sessions, preserving printed
serial and Planar behavior. Association does not silently reassign a device or its PC owner.

Migration keys a cached Planar capability to a serial only with exactly one fresh, present, owned
Planar device on that PC. Every other (ambiguous, stale or deviceless) cache row is kept under the
unroutable serial `legacy,unresolved` (no IDN serial holds a comma), unverified and not ready, until
the next heartbeat replaces it; no row is lost. History/jobs/artifacts are preserved.
Downgrade refuses persistent associations and multi-device/Arinst control data before DDL.

## Routing and typed commands

`control_station(..., source="arinst")`, `control_capability(..., serial=..., source=...)` and
`control_ops` select the exact identity. Callers must pass the serial; old callers without it
can select only one unambiguous Planar control. Transfer keeps the binding lock and active-session
guard, cancels previous captures and prevents late completion. Session claims must select by
observed identity; T4 must pass the selected source/serial to these seams. The agent polls each
fresh identity separately, but runs at most one worker/command at a time.
Configured Arinst UIDs also participate in polling after observation expiry, solely
so an explicit operator `connect`/`close` can arrive. T4 must permit those two read-only operations
using the persisted association and owner, without requiring an unexpired measurement capability;
profile changes/acquisitions still require fresh capability. Polling never opens COM.

Arinst commands use protocol 3 and `source="arinst"`, the existing session/command/instrument IDs,
`instrument_serial`, `expected_version`, `expected_step`, `op`, `args`. Operations:
`connect`, `read_profile`, `apply_profile`, `measure_standard`, `capture`, `close`.
Profile: integer `start_hz`, `stop_hz` (within advertised `SR`, increasing), `points` (2–1000),
`if_bandwidth_hz` (one of the advertised `BW` values converted from kHz to Hz). The confirmed
unit advertises 1 MHz–9 GHz and 20/50/100/250/500/1000 Hz. No power setter, arbitrary command, COM path, reset,
activation key, RF-off claim, guessed abort or persistent storage command crosses this boundary.
Port selection is a local `arinstDevices` config entry with `serial` and `usb_path`.
`settings_verified` is obsolete: there are no device setters. No scan of all matching COM ports.

Exclusive serial access is a command lease; every open drains binary leftovers to bounded silence,
verifies UID, model, firmware and options, and closes in finally. The existing worker process/watchdog bounds native
Open/Close too. Every command rechecks the server lease. Profiles are host-side session state,
persisted atomically and bound to session UUID and serial across worker processes. `read_profile`
returns null before an explicit `apply_profile`; no hardware default is invented. Apply validates
the profile against current options and stores it locally, without sending device setters. Unknown
completion is journaled and never replayed. Closing COM says nothing about RF output.

## Native serial transport

115200 baud, 8N1, no flow control, DTR/RTS false. After opening, drain leftovers to bounded silence:
ArinstVL can leave a scan stream behind. Identification order is D008, D001, D002, D003. ID and
firmware payloads each contain three little-endian u32 values; device type is reply header
`dev=0x17` (VNA-DL), without bootloader bits. Use `dev=0` before identification and `0x17` afterward.

Both directions use `AA | cmd u16 | seq u16 | dev u16 | arg u16 | len u16 | hcs u16 | payload | CRC32 u32`,
all little-endian. `hcs = ~(((sum >> 16) + (sum & 0xffff)) & 0xffff) & 0xffff`, where
`sum=cmd+seq+dev+arg+len`. CRC32 is zlib/ISO-HDLC over the twelve header bytes and payload, excluding AA.
Replies must match the command and request sequence plus one (modulo 65536). The parser resynchronizes
at AA, validates both checksums and bounds buffers and deadlines. No automatic retransmission.

The frame builder permits **only** D008 (ID), D001 (type), D002 (firmware), D003 (options),
DA00 (scan), FCA0 (read calibration range) and FCA1 (read calibration data).
Every other command is refused, including A001, B0xx, FCB0–FCB3, DA0D, FAC0 and DEAD.
There is no SCPI or activation exchange in this driver.

Options are parsed at connect: `SR=1000000,9000000000;BW=0.02,0.05,0.1,0.25,0.5,1;PR=0` on the bench.
Receipt `options` contains `scan_range_hz`, `bandwidths_hz`, `max_slice_points` and the original `raw` text.
DA00 sends `arg=0` for S11+S21 and payload `bw_idx u8, N u8, N × frequency u64`. Slice size is at most
255 and at most advertised `MAX_SLICE_POINTS`; each request has a 15-second timeout within the overall
watchdog budget. The exact grid is `start + floor(i * (stop-start)/(points-1))`; both endpoints are included
and duplicate frequencies are rejected. The receipt contains the exact integers actually sent.

Scan replies start with f32 N and f32 auxiliary value, then N pairs of complex S11/S21 (four f32 per point).
N must equal the requested slice length and all values must be finite. Preserve the auxiliary value.
Both real bench scans additionally contain eight trailing bytes: retain this undocumented trailer as
evidence; do not interpret it as another frequency or channel. Reject other unexpected payload lengths.

## Receipts and calibration persistence

Receipts preserve the existing outer session/version/step envelope. Result contains source,
serial, hardware_uid, model, firmware, options, licence state, operation, UTC and monotonic duration, host profile,
parser status, raw TX/RX evidence and SHA-256. Acquisition is `arinst.partial.v1`: channels S11/S21
as arrays of `[real, imaginary]`, orientation `forward`/`reverse`, actual point count and settings,
`correction_origin="raw"`, `grid_confirmed=true`, `frequencies_hz` equal to the exact request grid.
The backend requires integer frequencies, matching point count, strict increase, advertised SR bounds,
and equality to the session profile's complete grid. No zero-filled S12/S22 or complete Touchstone export.
This permits host-side calibration/correction on the exact grid; acceptance-grade saving still requires
characterized standards and physical bench comparison.
Local `arinst-wire-<command UUID>.json` checkpoints are flushed before writes and after each
receive chunk (at most 4096 bytes). Identity errors retain their checkpoint too. The watchdog
journal references the last checkpoint/hash with `parser_status=incomplete`; the current receive chunk
and bytes still in the OS at termination may be absent, so this is partial diagnostic evidence,
never a complete acquisition. Normal successful receipts include the complete bounded transcript.

Reuse `production_vna_artifacts` for immutable standard acquisitions and coefficient JSON, one
artifact per acquisition/save command, with the existing hashes and session relations. Coefficient
artifact evidence must contain algorithm/version, raw standard artifact IDs/hashes, characterized
kit hash, exact profile/grid, UID/firmware, fixture/reference planes, orientation, operator/time and
verification result. The session snapshot references that immutable artifact. No new calibration
table is needed in T1. T4 enforces exact-grid reuse/invalidation and prohibits double correction.
`SMA-MF06-loads` stays loads-only; no RF-kit CHECK widening without characterized OPEN/SHORT data.

## Licence: не требуется; commissioning

Native ArinstVL commands need no licence, registration or key. `license_state="not_required"`
is ready; there is no trial countdown. Russian UI copy: «Связь: протокол ArinstVL».
The native path uses the same physical UID as the earlier SCPI identification, preserving associations.

The 2026-11-23 read-only bench captures confirmed ID/type/firmware, options, two scans and eight
calibration-data chunks. On this unit the device calibration arrays are ideal placeholders
(−1, +1, 0, 1), so output is raw, uncorrected. ERP standard calibration provides real correction;
never apply it twice. Do not infer the native array standard order from the earlier static analysis.

Remaining release gates are characterized standards and physical comparison with the bench.
No acceptance/save gate is removed by protocol support or passing software tests.
Migration 0219 is adjusted only because its original licence/readiness CHECK constraints reject
`not_required`; it is unpublished on main. Existing production migrations are unchanged.

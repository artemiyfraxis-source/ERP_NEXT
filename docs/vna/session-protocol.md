# VNA session protocol v1 — P2 / P3 / P4

ERP owns the session, immutable plan snapshot, instrument lease, ordered steps, command receipts and evidence.
The PC agent owns execution, local backups and the device mutex. This contract adds **control** to the existing
warehouse agent identity/polling transport; capture-only agents keep their current behavior.

Implementation: `production/vna_sessions.py` defines the bounded schemas and step derivation;
`production/vna_session_infrastructure.py` persists the state; `warehouse/devices.py`, `vna_jobs.py` and
`scan_jobs_api.py` provide station capability/transport; `workflows/vna_measurement_api.py` composes them.

## Capability and identity

Use the existing `scan:upload` API token, enrolled/bound to a PC. `machine_id` and `X-Agent-Machine-Id` are checked
against that token's existing binding; neither a browser header nor a machine name grants authority.

Extend `POST /api/stock/scan/agent/heartbeat`:

```json
{
  "machine_id": "pc-1",
  "pc_name": "VNA-PC",
  "version": "2.8.0",
  "vna": {"name": "Planar, S50180, 26030095, 26.2.2/2", "ready": true},
  "vna_control": {"version": 1, "methods": ["acm"]}
}
```

This is an example of the next agent contract, **not an assertion that agent 2.8.0 has shipped**. The server
requires an installed version greater than 2.7.0, protocol version exactly 1, fresh control heartbeat and a fresh,
ready S2VNA device with the session serial. Omission of `vna_control` revokes the advertisement. Arinst and agents
without this capability remain capture/file mode. Unsupported protocol versions cannot claim session commands.
The server persists `vna_control.version` and `vna_control.methods` directly; the P3 heartbeat uses this same
object shape, including an empty `methods` array when no calibration method is commissioned.

`methods` lists only methods commissioned on that station. Main commissioned **two-port `acm`** on the real
S50180 + ACM2520-111 on 16.11.2026; its verified calls are below. A driver implementing those calls may advertise
`acm` on the commissioned station. This does not establish a one-port ECAL binding: P2 refuses one-port ACM plans
before creating a session; manual SOLT1 remains supported by the protocol. P1 may prefer one-port ACM even when
manual standards are available; P2 does not automatically replan that selection to SOLT1. An empty list authorizes
no new calibration session. Existing sessions may still
receive `abort`/`restore` with an empty methods list if protocol, device identity and readiness remain valid.

The browser explicitly confirms `agent_id`; the server resolves it against the **existing serial-to-PC binding**
in the device inventory. IP/NAT and caller-supplied PC headers are not routing authority. This is an explicit
station selection, not cryptographic proof that the browser itself runs on that PC. P5 must display the resolved PC
and serial for confirmation. No new local browser-pairing mechanism is introduced by P2.

## Operator API and snapshots

All browser endpoints require the existing quality-edit permission and the session's operator identity.

| Endpoint | Body / behavior |
|---|---|
| `POST /api/production/vna-sessions` | `task_id`, `operation_id`, `instrument_id`, `product_position` (1-based), `agent_id`; optional `overrides`, `qualification_reason`. Returns a `draft` session. |
| `GET /api/production/vna-sessions/{id}` | State/version/step, immutable snapshot, ordered steps, receipts and artifact references. Also runs the lazy command watchdog. |
| `POST /api/production/vna-sessions/{id}/actions` | `action`, `expected_version`, `expected_step`. Actions: `plan`, `start`, `confirm`, `abort`, `recover`. |
| `GET /api/production/vna-sessions/{id}/artifacts/{artifact_id}` | The original bytes, available to the owning operator. |

Every override is `{field, value, reason}`. Allowed fields are `points`, `if_bandwidth_hz`, `power_dbm`.
Reasons are required. The frequency band and norm come from P1 and cannot be replaced by browser claims.
Points cannot be reduced below P1's grid; missing cable length/velocity can be resolved by an explicit point count
of at least 401. Missing norm, band or instrument capacity still blocks creation. Capacity and a conservative
2 MiB full-resolution export estimate are checked before any device work. Qualified plans require a reason and
retain their acceptance restrictions. No automatic acceptance is added by acknowledging the reason.

The snapshot contains the P1 plan (including ETK and norm, accessory allocations, calibration planes and
connections), resolved profile, reasoned overrides, qualification reason and RF kit-definition snapshots with their
registry `serial_number`. ACM requires a nonblank module serial in the registry; it is not supplied by the browser. The
session separately retains operator, machine, registry instrument UUID/serial, task, operation and product position.
It is immutable once created; changed settings require another session and calibration.

`kit_fingerprint` is SHA-256 of the canonical JSON RF-definition snapshot (sorted keys, UTF-8, no whitespace).
It identifies the **ERP registry definition**, not a measurement of S2VNA's actual coefficients. P3 must resolve
the plan's labels/standards against a locally commissioned kit manifest, verify its definitions and return the
actual definition digest as `definition_hash`. For ACM, use the verified module information/definition manifest,
not the manual CKIT slot. Never infer physical contents or coefficients from slot 28 or a kit label, and never
rewrite coefficients automatically. P3 must perform these checks with the live module before any acquisition.

## State, lease and ordered steps

```text
draft -> planned -> preparing -> awaiting_standard <-> acquiring_standard
      -> saving_calibration -> awaiting_dut -> acquiring_dut -> review -> recorded
any started active state -> aborting -> aborted
uncertain execution / device error -> recovery_required -> aborting -> aborted
preparing -> failed (completed prepare error explicitly reports backup_saved:false)
```

`failed` releases the lease without scheduling restore when a completed `prepare` error explicitly confirms
that no backup was saved and no device work remains to undo. All other device errors retain the lease for
recovery. Current operator actions cancel untouched drafts/plans directly to `aborted`.

Both registry UUID and serial have partial unique indexes over nonterminal states. `review`, `aborting` and
`recovery_required` retain the lease. Ordinary capture creation and session creation serialize through the
existing serial binding row and reject each other. A second registry record with the same serial cannot bypass
the physical lease. An administrator transferring the existing device binding does not release a session lease;
commands to the former PC stop being dispatched until ownership is restored. Finish/recover a session before
moving the instrument.

`machine_id` is NOT NULL with a nonempty CHECK, without a cross-module FK to the warehouse agent table. The workflow
verifies that the PC exists and revalidates its serial binding, live device and control capability. Production's
other new FKs target its own tables or `identity_users`, consistent with existing production tables. Warehouse's
control-capability FK stays within warehouse. Alembic explicitly imports both new persistence owners.

The sequence is derived from P1:

1. `prepare`, `apply_profile`, `begin_calibration` run automatically.
2. ACM: one human connection confirmation, then `acm_calibrate`, preserving the P1 port permutation.
3. SOLT2: six SOL acquisitions, then two distinct through directions `[1,2]` and `[2,1]`.
4. SOLT1: three SOL acquisitions and explicit matched load at the far DUT end. Both orientations are captured
   under the same session; the second orientation has its own begin/SOL/save sequence.
5. `save_calibration`, human DUT confirmation, `capture`.
6. After the final capture, `restore`; only successful restore opens `review`.

`step` is a zero-based index into the server's ordered steps. Versions are monotonic, not guaranteed contiguous
from the agent's perspective. Human connection waits have **no timeout**. Pending commands have not executed and
also have no execution timeout. A claimed command unanswered for 180 seconds becomes `uncertain`, with the
session entering `recovery_required`; polling does not extend this watchdog. The 180-second bound covers the
commissioned profile: ACM took about 21 s and a fresh sweep 3.9 s at 1601 points, IFBW 1 kHz, 10 MHz–18 GHz.
Slower profiles/narrower IF bandwidth still need a timing check. The ordinary queue's
cleanup/expiry never touches the session tables. The watchdog runs on session reads/actions, agent polls and
results; without traffic the stale session still holds its lease.

## Agent polling and command envelopes

Use approximately 3 seconds while idle, 500 ms while device work is active, and backoff on transport errors.
Run heartbeat/polling separately from blocking COM execution. Extend the existing claim request:

```json
{
  "machine_id": "pc-1",
  "kinds": ["scan", "vna", "vna_control"],
  "observed_vna_serial": "26030095"
}
```

An explicit `kinds` list is exclusive: `["vna_control"]` returns only session commands, or `job:null` when none
is available (including human connection waits). `["scan"]` and `["vna"]` return only their respective ordinary
jobs and never session commands. Mixed lists prioritize control commands, then allow only the requested ordinary
kinds. An explicit empty list requests no jobs; omitting `kinds` preserves legacy scan-only polling and its response
shape. Agents holding a local session marker can therefore poll only `vna_control` during session work/recovery.

Read `observed_vna_serial` from the connected instrument, not from the registry or a cached browser value. The
server refuses a mismatch before handing out a command. **Read/compare it again immediately before every device
operation**, including backup, abort and restore: hardware can be swapped after polling.

The response contains `job: null`, an ordinary job, or this envelope (control work also returns `poll_after_ms: 500`):

```json
{
  "id": "<command UUID>",
  "command_id": "<same command UUID>",
  "kind": "vna_control",
  "protocol_version": 1,
  "session_id": "<session UUID>",
  "expected_version": 4,
  "expected_step": 0,
  "instrument_id": "<registry UUID>",
  "instrument_serial": "26030095",
  "op": "prepare",
  "args": {}
}
```

A lost claim response or repeated poll returns the same claimed command ID. **This is a resume/receipt lookup,
never permission to execute it again.** Serialize device execution with a per-physical-instrument local mutex.
Before executing a received command, check the journal and the current server lease/version. In particular, do
not execute an old locally queued command after an abort or server restart/reconnect.

For that check and journal reconciliation, the bound agent can call
`GET /api/stock/vna/sessions/{session_id}` with its token and `X-Agent-Machine-Id`. It returns `session_id`,
`state`, `version`, `step`, `lease_held`, instrument identity, `kit_definitions`, and command IDs with status and
expected version/step. It exposes only sessions assigned to this PC, even after they become terminal. The selected
command must still be `claimed` with the matching session version/step before execution. Use terminal state and
receipts to reconcile backup cleanup; `job:null` alone is not proof that a lease ended. Ordinary operator reads
still require the human quality permission and cannot be called by the agent token.

## Typed arguments and results

Unknown fields are rejected. No op accepts SCPI text, script text, COM member names, filesystem paths or browser
command arguments. The source of truth for bounds is the Pydantic schema in `vna_sessions.py`.

`Profile`: `start_hz`, `stop_hz` (finite, positive, ordered, at most 1e12); `points` (integer, 2..200001 plus export
budget/capacity/grid checks); `if_bandwidth_hz` (1..1000), `power_dbm` (-30..0); fixed `impedance_ohm=50`,
`sweep="LIN"`, `smoothing=false`, `averaging=false`, `processing="off"`; unique named `traces` (1..4 of S11/S22/S21/S12)
and `vswr_limit` (1..1000). Two-port sessions retain all four traces; one-port sessions use S11 in both orientations.

Every success result contains `serial`. Additional fields are:

| Op | `args` | Success `result` fields beyond `serial` |
|---|---|---|
| `prepare` | `{}` | `ready:true`, `backup_id` (UUID, opaque local journal handle), `state_saved:true`, `kit_assignments_saved:true` |
| `apply_profile` | `profile: Profile` | `profile: Profile`, read back and exactly matching the requested profile |
| `begin_calibration` | `method: acm/solt2/solt1`, `kit_id`, `kit_fingerprint`, `orientation: both/А/Б`, `standards` | `kit_fingerprint` matching ERP, `definition_hash` (64 lower-case hex characters) |
| `measure_standard` | `standard: {kind: OPEN/SHORT/LOAD/THRU, ports:[1] / [2] / [1,2] / [2,1], label, kit_ports:[]}` | `acquired:true` after the blocking acquisition has actually returned |
| `acm_calibrate` | `ports:[1,2]`, corresponding two unique `kit_ports`, `module_serial` from the registry, required `start_hz` and `stop_hz` | `acquired:true`, live `module_serial`, `module_min_hz`, `module_max_hz` after the blocking ECAL call has completed |
| `save_calibration` | `method`, `traces` | matching `method`, `correction:{<each required trace>:"COR"}` |
| `capture` | `profile`, `orientation`, `port_to_end` (e.g. `{"1":"А","2":"Б"}`) | exact readback `profile`, `correction` for every required trace, `artifacts` |
| `abort` | `{}` | `stopped:true`, `calibration_cleared:true` |
| `restore` | `{}` | `restored:true`, only after state/kit restoration and readback succeed |

Calibration `standards` use the same standard schema; an ACM standard has `kind:"ACM"` and the explicit `kit_ports`
permutation. Ports are only 1 or 2; at most 8 standards. Labels are display/manifest matching data, never COM paths.

Before ACM calibration, read `K.ECAL.INFormation`, compare its module serial with `args.module_serial`, and require
`module_min_hz <= start_hz < stop_hz <= module_max_hz`. Return the actual module identity and range in the receipt;
the server checks them again. `save_calibration.result.method` identifies the procedure used (`acm` for ECAL),
not a reading of `K.METHod.TYPE`: that property remains `RESPT` after successful ECAL and is not proof of success.
Select every required trace before reading its correction status; all must be `COR`.

An artifact is `{file_name, content_base64, traces}`. Names match `[A-Za-z0-9_-]{1,100}.s1p` or `.s2p`; no paths.
Up to four artifacts may total at most 2 MiB decoded; the whole result request is bounded at 3 MiB. A `.s1p` has
one named trace. The named traces across artifacts must cover the requested profile exactly, without duplicates.
Export full-resolution, corrected complex samples; never decimate. An `.s2p` uses standard Touchstone matrix
ordering S11, S21, S12, S22, regardless of the requested trace-list ordering.

P2 checks identity, settings, correction claims, bounded bytes and trace metadata, then preserves the original
bytes and server-derived orientation/port mapping with SHA-256. It does **not** assign an acceptance verdict or
replace P4's full Touchstone parsing, sample/band coverage and norm validation. `review` means transport and restore
completed; it does not assert metrological validity.

Send to `POST /api/stock/vna/commands/{command_id}/result` using the bound agent token and
`X-Agent-Machine-Id: pc-1`:

```json
{
  "session_id": "<UUID>",
  "expected_version": 4,
  "expected_step": 0,
  "result": {
    "serial": "26030095", "ready": true, "backup_id": "<UUID>",
    "state_saved": true, "kit_assignments_saved": true
  }
}
```

Alternatively send `error` (nonblank, at most 2000 characters) with no result. A failed `prepare` may also include
the top-level `backup_saved` boolean:

```json
{
  "session_id": "<UUID>",
  "expected_version": 4,
  "expected_step": 0,
  "error": "Preparation failed before saving a backup",
  "backup_saved": false
}
```

Use `false` only for a **known, completed failure before backup**, with no outstanding device operation or changed
state requiring restoration. ERP marks the command failed and the session terminal `failed`, releases its lease,
and schedules no abort/restore. Use `true` after backup: ERP retains the lease in `recovery_required`, and explicit
recovery issues abort/restore. Omission or `null` means unknown and preserves that recovery behavior for existing
agents. Never infer `false` merely from a missing backup after a crash, hung COM call, timeout or partial write.
Only actual JSON booleans (or `null`, equivalent to omission) are accepted. A boolean flag is invalid on successful
results or failures of later operations. Success still requires `backup_id`, `state_saved` and `kit_assignments_saved`.

Other device errors retain the lease and require recovery. Do not claim successful restore when the backup is
missing, the serial differs, or readback fails.
HTTP 403 is identity/permission refusal, 404 a missing/inaccessible object, 409 a state/version/step/capability
conflict, 422 an invalid payload/readback. An invalid result is not a completion; without a valid response the
watchdog eventually requires recovery. On 409 refresh server state, never blindly rerun the operation.

## Acknowledgement loss, cancellation and recovery

Receipts and artifacts are committed with the session transition in one transaction. Reposting the **identical**
finished result/error returns success without advancing the state or duplicating evidence. A different payload
under that ID is rejected. A result for an unclaimed, cancelled or uncertain command is rejected. A duplicate of
an already accepted receipt may be acknowledged after cancellation, but cannot revive its command/session.
`backup_saved`, when boolean, is persisted with the error and participates in receipt identity: adding, removing
or changing it on retry conflicts. A late `backup_saved:false` receipt cannot release an already cancelled or
uncertain command's lease.

Before any first mutation, journal the complete original state (including calibration) and relevant kit/class
assignments. Local backup paths are chosen by the agent and never transmitted as executable arguments. Persist:

- Session ID, physical serial, command ID, op, immutable args hash, expected version/step.
- `received`, `executing`, `completed` or `uncertain`, with timestamps. Persist `executing` before entering COM.
- The exact completed result/error payload until the server has acknowledged it; retry HTTP from the journal.
- Backup UUID and its private local paths, original state/kit hashes, actual verified definition digest and
  restore outcome. Retain backups through successful restore and terminal-session reconciliation.

P3 must journal the exact failed-prepare `backup_saved` value and retry it unchanged. After ERP acknowledges a
known pre-backup failure, reconcile the terminal `failed` state and its receipt before clearing the local ownership
marker; partial files/markers alone do not prove whether device work completed. Agents that do not yet send the
flag keep the conservative recovery behavior.

After a crash in `executing`, the command is uncertain: never reacquire a standard, run ACM, `SAVE` or a capture
blindly under the same ID. Report uncertainty/reconcile with ERP and use explicit recovery.

Abort supersedes the active command and issues `abort`, followed by `restore`. Keep polling independently while
an acquisition blocks. The executor must fence the old worker: confirm it has stopped before restoring or allowing
a new writer. Never queue restoration behind an unbounded blocked COM call and claim completion. An unavailable
device, a hung worker or failed restore keeps ERP in `recovery_required`. `recover` creates a fresh abort/restore
attempt under the same physical lease; there is no force-release browser endpoint.

Normal completion also restores before `review`. After reconnection of standards/DUT, old restored coefficients
do not prove physical calibration validity; show that limitation to the operator. A successful restore means
settings/kit data were restored, not that the former measurement chain is still calibrated.

## S2VNA COM map and commissioning limits

The original map came from the supplied S2VNA 26.1 manual; the installed software is 26.2.2. The ACM and fresh-sweep
entries below incorporate main's real-hardware commissioning of **16.11.2026** and supersede the older unverified
trigger sequence. Let `S=app.SCPI`, `C=S.SENSe(1)`, `K=C.CORRection.COLLect`, `CALC=S.CALCulate(1)`,
`T=CALC.SELected`.

Run COM under the logged-in user's **normal, non-elevated token**, as the agent already does. An elevated COM
client spawned a separate device-less S2VNA instance (`Ready=False`, empty serial) during commissioning. Do not
elevate the agent to work around COM failures.

| Op | Fixed implementation calls / obligations |
|---|---|
| `prepare` | Read `app.Name`, `app.Ready`, `S.SERVice.SWEep.POINts`, `S.SERVice.SWEep.FREQuency.MINimum/MAXimum`. Save with `S.MMEMory.STORe.STYPe="CDST"`, `.STORe.STATe=<local path>`; preserve kit assignments/definitions with `.STORe.CKIT(n)`. Serial is parsed from the live identity. |
| `apply_profile` | `S.ABORt()`, `S.INITiate(1).CONTinuous=false`; `C.SWEep.TYPE="LIN"`, `C.FREQuency.STARt/STOP`, `C.SWEep.POINts`; `C.BANDwidth.RESolution`, `C.AVERage.STATe=false`, `C.CORRection.IMPedance.INPut.MAGNitude=50`; uncouple port power and set each `S.SOURce(1).POWer.PORT(p).LEVel.IMMediate.AMPLitude`, disable slope; set trace count/definitions and select each trace. Disable smoothing, math, conversion, time gate and fixture simulation. Reflection display is SWR; retain transmission complex data; set the ERP limit line. Read back every controlled setting. |
| `begin_calibration` | Manual SOLT: validate kit label and commissioned definitions (`K.CKIT.SELect`, `.LABel`, `.STAN(i).LABel/TYPE/FMINimum/FMAXimum` and coefficients); set verified `CKIT.ORDer` assignments, never inferred slot numbers. `K.CLEar()`, `C.CORRection.TRIGger.FREE.STATe=true`, `K.METHod.SOLT1=1` or `.SOLT2=[1,2]`. ACM: identify the module using `K.ECAL.INFormation` and its commissioned manifest; do not select a manual CKIT method as a substitute for ECAL. |
| `measure_standard` | `K.ACQuire.OPEN=p`, `.SHORt=p`, `.LOAD=p`, or `.THRU=[rx,tx]` for each ordered through direction. Internal calibration trigger is mandatory to avoid BUS-trigger deadlock. |
| `acm_calibrate` | Check live `K.ECAL.INFormation` module serial and band against the session. Set `K.ECAL.ORIentation.STATe = True`, then `K.ECAL.SOLT2 = [1,2]` using a **COM Variant integer array**. This is one blocking full two-port calibration call, measured at about 21 s. Preserve the planned physical port mapping. |
| `save_calibration` | Manual SOLT: call `K.SAVE()` (it calculates coefficients, clears acquisitions and enables correction). ACM: ECAL already performed the calibration; this step verifies it, without another `K.SAVE()` or ECAL acquisition. For each trace call `CALC.PARameter(t).SELect()` then read `CALC.SELected.CORRection.STATus`. Every required trace must read `COR`, never `C?`/`C!`. Do not use `K.METHod.TYPE` as proof: it still reads `RESPT` after ECAL. |
| `capture` | Proven sequence: `S.INITiate(1).CONTinuous = True`; `S.TRIGger.SEQuence.SOURce = "BUS"`; `S.TRIGger.SEQuence.SINGle()`; then read `S.IEEE4882.OPC` and wait for completion. Only then read `C.FREQuency.DATA` and `CALC.TRACe(t).DATA.SDATa` for every required trace. Select each trace and check `CALC.SELected.CORRection.STATus == "COR"`; verify unchanged settings/disabled processing before exporting the fresh sweep. `INITiate(1).IMMEDiate()` alone returned instantly in commissioning and is not a completion barrier. |
| `abort` | `S.ABORt()`, continuous off, `K.CLEar()` for unfinished calibration, plus fencing/termination of the old acquisition worker. |
| `restore` | Recall original state with `S.MMEMory.LOAD.STATe=<journal path>` and preserved kit definitions/assignments with `.LOAD.CKIT(n)` where needed; verify restored settings. Same-session lease and serial are mandatory. |

The older `s2vna-commissioning-2026-11-14.md` record verifies software setting IO and state recall with the instrument
disconnected. Main's 16.11 follow-up adds these supplied real-hardware facts:

- S50180 + ACM2520-111 completed full two-port ECAL. `K.ECAL.INFormation` reported
  `ACM2520.2,26140008,...,100 kHz,20 GHz,...,3.5mm -F-,3.5mm -F-`.
- At 1601 points, 1 kHz IFBW and 10 MHz–18 GHz, ECAL took approximately 21 s; the BUS-triggered four-parameter sweep
  waited 3.9 s through `IEEE4882.OPC`. Two consecutive sweeps differed by at most 0.001 VSWR.
- All required traces read `COR` after selecting them individually, despite `K.METHod.TYPE` remaining `RESPT`.
- The normal user's COM token reached the device; an elevated token did not.

These facts came from main's commissioning brief; the P2 worker did not repeat the hardware measurements.
Manual standard acquisition, a one-port ECAL binding, USB-loss recovery and performance outside the tested profile
are not established by that evidence. Do not generalize the repeatability observation into an accuracy guarantee.
The manual does not establish COM equivalents for error-queue queries, pulse mode or trace-hold controls; P3 must
not invent them. The calibration-acquisition trigger remains internal for manual SOLT; the proven fresh-measurement
sweep uses BUS. These are separate operations.

## P4 integration boundary

`VnaSessionRepository.completed(session_id)` exposes server-owned completed data for `review`/`recorded` sessions.
Artifacts live in `production_vna_artifacts`, independent of warehouse queue retention, with references in that
view. P4 must authorize the operator/product, parse all required traces/orientations, recheck the current norm and
band, and store one atomic attempt. In the **same database transaction**, call
`mark_recorded(session_id, expected_version=..., reference=<attempt ID>, now=...)`. This releases the lease and
records the stable reference. The same recorded reference is idempotent; another reference or stale version is
rejected. There is no public browser action that can mark a session recorded.

Migration `0181_vna_sessions` follows `0180_equipment_catalogs_cutover`; it adds only new tables/indexes and refuses
to downgrade populated session history. Production PostgreSQL migration rehearsal and real-device validation
remain integration/release gates outside the P2 worker's affected-test run.

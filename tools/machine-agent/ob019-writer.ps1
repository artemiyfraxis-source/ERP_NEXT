param(
    [string]$Server = 'http://192.168.1.85',
    [string]$TokenPath = 'D:\fraxis-mb\bridge.token',
    [string]$MbdHost = '127.0.0.1',
    [int]$MbdPort = 5020,
    [string]$MbdScript = 'D:\fraxis-mb\mbd.ps1',
    [string]$BridgeScript = '',
    [string]$LogPath = 'D:\fraxis-mb\commands.log',
    [int]$PollMs = 500,
    [string]$PultCloseTask = 'Fraxis OB019 pult close',
    [string]$PultOpenTask = 'Fraxis OB019 pult open',
    [switch]$Once
)
# ERP command runner of OB.019 (ERP_NEXT, section "Stanki", slice 4): writes the parameters ERP asks for.
# Pull transport like the bridge: POST /api/production/machines/agent/writes/next (the bridge's token, scope
# machine:report, the same PC binding) -> one command {id, blocks: [{addr, values}], bits: [{addr, bit, value}],
# states}; the runner
#   0. asks mbd its version ("version", no bus) and refuses the whole command unless it is $script:MbdMinVersion or
#      newer (a switch of $script:MbdBitVersions needs that newer mbd: the feed-roller lift D604.0 is in mbd 1.3.0's
#      allowedBits only): an old mbd has no "setbit" and a wider "write" list, so a new runner never writes part of a
#      command;
#   1. reads D766 and refuses unless the machine is idle (102 ready / 107 total finished);
#   2. writes each block through the local Modbus service mbd.ps1 ("write" = FC16 + read-back; mbd itself refuses
#      while PXS_HMI holds COM8 and checks its own write allow-list), then each switch bit ("setbit" = read the word,
#      change that bit only, write it back, read back - in one mbd request);
#   3. reports the words it read back: POST .../agent/writes/{id}/result. ERP compares them with what it asked
#      (for a switch only its bit).
# Only the registers of $script:Writable (ERP's writable fields, backend production/machines.py OB019_FIELDS) are ever
# written. Same runtime as the bridge: Windows 7, PowerShell 2.0, ASCII file. The bridge's helpers are dot-sourced.
#
# Machine commands (slice 3, kind "control": Stop, Reset, Start, Clear alarm) come through the same queue, Stop first.
# Each is one mbd "pulse" of a fixed bit of $script:Controls held $script:HoldMs (mbd refreshes the HMI context
# D730=13 + date while holding and clears the bit; proven on OB.019 18.11.2026). Never D599.3: E-stop is the physical
# button only. Checked HERE right before the bit is held: the command is younger than ERP's deadline (counted from the
# moment this runner asked, so ERP's 5 s by the server clock always holds), PXS_HMI is closed, the state D766 allows it
# (and an alarm D782 for Clear alarm). The result goes back with D766/D782 before and after and a reason code; no reply
# from mbd during the hold = "unknown", never "done". One command at a time (the loop is serial).
#
# The operator panel (1.4.0, kind "control", actions hmi_close / hmi_open: "Zakryt / Otkryt pult na PK" on the card).
# COM8 has one master, the vendor HMI PXS_HMI or mbd. These two run while PXS_HMI runs (everything else is refused
# then, as before) and never touch the bus. PXS_HMI lives in the operator's desktop session; this runner is SYSTEM in
# session 0, so it asks the helper tasks $PultCloseTask / $PultOpenTask (ob019-pult.ps1 in the operator's session,
# registered by install-bridge.ps1) to send WM_CLOSE or to start PXS_HMI.exe.
#   hmi_close: not running -> done (method none). Otherwise the helper closes it (WM_CLOSE) and the runner waits
#     $script:HmiWaitMs: gone -> done (graceful); still running (the panel may be asking the operator) -> failed
#     still_open, nothing is killed. With force (the card's "Zakryt prinuditelno" after its own confirmation that the
#     machine stands) -> Stop-Process -Force at once (forced). No helper task -> failed no_helper, nothing is killed.
#     Nothing on this PC says whether the machine cuts while PXS_HMI holds COM8 (its TextLog has only Open-System /
#     Close-System): ERP decides from the last state it read (backend production/machine_control.py).
#   hmi_open: already running -> refused; else the helper starts it, done once the process is seen (started).
$ErrorActionPreference = 'Stop'
$script:Self = $MyInvocation.InvocationName
if (-not $BridgeScript) { $BridgeScript = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'ob019-bridge.ps1' }
. $BridgeScript -Server $Server -TokenPath $TokenPath -MbdHost $MbdHost -MbdPort $MbdPort -MbdScript $MbdScript -LogPath $LogPath -PollMs $PollMs -Once:$Once
# 1.5.0: kind length_correction uses measured D658..659, pulse D600.6 and signed D54..55 read-back.
# 1.5.2 (23.11.2026): kind reset_counters zeroes D645..646 / D649..650 through mbd 1.5.2 'reset_counters' (zeros only).
$script:RunnerVersion = '1.5.2'
$script:ResetMbd = '1.5.2'
$script:CorrectionAlarm = $script:Json.DeserializeObject('"\u041d\u0430 \u0441\u0442\u0430\u043d\u043a\u0435 \u0430\u0432\u0430\u0440\u0438\u044f D782={0} \u2014 \u0441\u043d\u0430\u0447\u0430\u043b\u0430 \u0441\u0431\u0440\u043e\u0441\u044c\u0442\u0435"')
$script:CorrectionAlarmUnreadable = $script:Json.DeserializeObject('"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u0440\u043e\u0447\u0438\u0442\u0430\u0442\u044c \u0430\u0432\u0430\u0440\u0438\u044e \u0441\u0442\u0430\u043d\u043a\u0430 D782 \u2014 \u043e\u0431\u043d\u043e\u0432\u0438\u0442\u0435 \u0434\u0430\u043d\u043d\u044b\u0435"')
$script:CorrectionUnconfirmed = $script:Json.DeserializeObject('"\u041f\u043e\u043f\u0440\u0430\u0432\u043a\u0430 \u043d\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0430 \u0441\u0442\u0430\u043d\u043a\u043e\u043c"')
# Machine commands: the bit held and the machine states (D766) each may run in, empty = any state; "off" = switch bits
# ('addr.bit') that must read 0 right before the pulse (Start with the feed rollers lifted D604.0 is refused). Mirrors
# ALLOWED_STATES and BLOCKED_BY_BITS of backend production/machine_control.py (a backend test compares them).
$script:HoldMs = 1500
$script:Controls = @{
    stop = @{ addr = 599; bit = 2; states = @() }
    start = @{ addr = 599; bit = 0; states = @(102); off = @('604.0') }
    reset = @{ addr = 600; bit = 1; states = @(100, 105, 107) }
    alarm_clear = @{ addr = 600; bit = 4; states = @() }
}
# Words: D20 mode, D22..D28 job and quantities, D32..D43 wire and strip lengths, D940..D969 order number and product;
# the other HMI pages (1.2.0): middle strip D197/D199, slitting D67..D74/D1703..D1705, coiling D391..D395/D1750..D1753,
# marking D401..D432 numbers, D1722 and the texts D440..D484, speeds D100..D104/D120..D123. Switch bits (setbit only,
# never a whole word): middle strip D132.0, feed-roller lift D604.0 (Jog "Wheel", Y18), marking D400.0, D422.0, D434.0..D436.0. Not: D390/D133/D142 (meaning not
# established), D421 (order transfer handshake, map open question 8). Generic writes mirror OB019_WRITTEN_WORDS / _BITS;
# mbd additionally allows D658..659 for the dedicated correction path, never through these generic lists. Never: passwords
# D1682..D1687, SHA-256 D1203..D1211, command bits D599/D600, D730, date D1087..D1089.
$script:MbdMinVersion = '1.2.0'
# Switch words whose bit only a newer mbd may set (its allowedBits): D604 feed-roller lift since mbd 1.3.0.
$script:MbdBitVersions = @{ 604 = '1.3.0' }
$script:Writable = @{}
foreach ($a in @(20, 74, 197, 199, 423) + (22..28) + (32..53) + (67..72) + (100..104) + (120..123) + (391..395) + (401..402) + (404..415) + (430..432) + (440..484) + (940..969) + (1703..1705) + (1722..1723) + (1750..1753)) { $script:Writable[[int]$a] = $true }
$script:WritableBits = @{ 132 = @(0); 400 = @(0); 604 = @(0); 422 = @(0); 434 = @(0); 435 = @(0); 436 = @(0) }
# Words only a newer mbd writes (its allowedWrite): the inner layer D44..D53 since mbd 1.4.0 (ERP: RUNNER_WORDS).
$script:MbdWordVersions = @{}
foreach ($w in (44..53)) { $script:MbdWordVersions[[int]$w] = '1.4.0' }

function Test-BlockWritable($Block) {
    $first = [int]$Block['addr']; $values = @($Block['values'])
    if ($values.Count -lt 1 -or $values.Count -gt 60) { return $false }
    for ($i = 0; $i -lt $values.Count; $i++) {
        if (-not $script:Writable.ContainsKey($first + $i)) { return $false }
        $v = [int]$values[$i]
        if ($v -lt 0 -or $v -gt 65535) { return $false }
    }
    return $true
}

function Test-BitWritable($Bit) {
    $a = [int]$Bit['addr']; $b = [int]$Bit['bit']; $v = [int]$Bit['value']
    if ($v -ne 0 -and $v -ne 1) { return $false }
    return ($script:WritableBits.ContainsKey($a) -and ($script:WritableBits[$a] -contains $b))
}

# mbd's version ("version" needs no bus), '' when mbd does not know the command (before 1.2.0).
function Get-MbdVersion {
    $reply = $script:Json.DeserializeObject((Invoke-MbdLine (ConvertTo-JsonLite @{ cmd = 'version' })))
    if (-not $reply['ok']) { return '' }
    return [string]$reply['result']['version']
}

function Test-MbdCurrent([string]$Version, [string]$Needed = '') {
    if (-not $Needed) { $Needed = $script:MbdMinVersion }
    if (-not $Version) { return $false }
    try { return ([version]$Version) -ge ([version]$Needed) } catch { return $false }
}

# The mbd version a command needs: $script:MbdMinVersion, or newer for a switch of $script:MbdBitVersions or a word of
# $script:MbdWordVersions.
function Get-MbdNeeded($Bits, $Blocks = @()) {
    $needed = $script:MbdMinVersion
    foreach ($bit in @($Bits)) {
        if ($bit -eq $null) { continue }
        $a = [int]$bit['addr']
        if ($script:MbdBitVersions.ContainsKey($a) -and ([version]$script:MbdBitVersions[$a]) -gt ([version]$needed)) { $needed = $script:MbdBitVersions[$a] }
    }
    foreach ($block in @($Blocks)) {
        if ($block -eq $null) { continue }
        $count = @($block['values']).Count
        for ($i = 0; $i -lt $count; $i++) {
            $a = [int]$block['addr'] + $i
            if ($script:MbdWordVersions.ContainsKey($a) -and ([version]$script:MbdWordVersions[$a]) -gt ([version]$needed)) { $needed = $script:MbdWordVersions[$a] }
        }
    }
    return $needed
}

# One command -> @{ ok; readback = @{ "addr" = word }; error }. Never throws.
function Invoke-MachineCommand($Command) {
    $readback = @{}
    try {
        $needed = Get-MbdNeeded $Command['bits'] $Command['blocks']
        $version = Get-MbdVersion
        if (-not (Test-MbdCurrent $version $needed)) { return @{ ok = $false; readback = $readback; error = ('mbd outdated: ' + $(if ($version) { $version } else { 'no version' }) + ', need ' + $needed) } }
        $states = @($Command['states'] | ForEach-Object { [int]$_ })
        $state = [int](Read-Range 766 1)[0]
        if ($states -notcontains $state) { return @{ ok = $false; readback = $readback; error = ('state ' + $state) } }
        $blocks = @($Command['blocks'])
        foreach ($block in $blocks) {
            if (-not (Test-BlockWritable $block)) { return @{ ok = $false; readback = $readback; error = ('refused: D' + $block['addr'] + ' is not writable') } }
        }
        $bits = @($Command['bits'] | Where-Object { $_ -ne $null })
        foreach ($bit in $bits) {
            if (-not (Test-BitWritable $bit)) { return @{ ok = $false; readback = $readback; error = ('refused: D' + $bit['addr'] + '.' + $bit['bit'] + ' is not writable') } }
        }
        foreach ($block in $blocks) {
            $values = [int[]]@($block['values'] | ForEach-Object { [int]$_ })
            $reply = $script:Json.DeserializeObject((Invoke-MbdLine (ConvertTo-JsonLite @{ cmd = 'write'; addr = [int]$block['addr']; values = $values })))
            if (-not $reply['ok']) { return @{ ok = $false; readback = $readback; error = [string]$reply['error'] } }
            $read = @($reply['result']['readback'])
            for ($i = 0; $i -lt $read.Count; $i++) { $readback[[string]([int]$block['addr'] + $i)] = [int]$read[$i] }
        }
        foreach ($bit in $bits) {
            $line = ConvertTo-JsonLite @{ cmd = 'setbit'; addr = [int]$bit['addr']; bit = [int]$bit['bit']; value = [int]$bit['value'] }
            $reply = $script:Json.DeserializeObject((Invoke-MbdLine $line))
            if (-not $reply['ok']) { return @{ ok = $false; readback = $readback; error = [string]$reply['error'] } }
            $readback[[string]([int]$bit['addr'])] = [int](@($reply['result']['readback'])[0])
        }
        return @{ ok = $true; readback = $readback; error = '' }
    } catch {
        return @{ ok = $false; readback = $readback; error = $_.Exception.Message }
    }
}

# --- machine commands (kind "control") ---

function Test-HmiOpen {
    return [bool](Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'PXS_HMI*' })
}

# D766 machine state and D782 alarm in one read (D766..D782).
function Read-MachineState {
    $values = Read-Range 766 17
    if (@($values).Count -ne 17 -or $values[16] -isnot [int] -or $values[16] -lt 0 -or $values[16] -gt 65535) { throw $script:CorrectionAlarmUnreadable }
    return @{ '766' = [int]$values[0]; '782' = [int]$values[16] }
}

function Assert-ControlBit([int]$Address, [int]$Bit) {
    if ($Address -eq 600 -and $Bit -eq 6) { return } # Dedicated length correction only, never a control action.
    foreach ($spec in $script:Controls.Values) { if ($spec['addr'] -eq $Address -and $spec['bit'] -eq $Bit) { return } }
    throw ('runner refused bit D' + $Address + '.' + $Bit)
}

function Invoke-Pulse([int]$Address, [int]$Bit) {
    Assert-ControlBit $Address $Bit
    $line = ConvertTo-JsonLite @{ cmd = 'pulse'; addr = $Address; bit = $Bit; hold_ms = $script:HoldMs }
    return $script:Json.DeserializeObject((Invoke-MbdLine $line))
}

# PXS_HMI FmLineModify: measured length D658..659, pulse D600.6, PLC correction D54..55.
# A separate kind keeps D658 and D54 out of generic parameter writes. No D32 write is made here.
function Invoke-LengthCorrection($Command, [Diagnostics.Stopwatch]$Since) {
    $result = @{ ok = $false; readback = @{}; before = @{}; correction_pulsed = $false; error = ''; outcome = 'failed' }
    $pulseStarted = $false
    try {
        $blocks = @($Command['blocks']); $seen = $Command['seen']; $deadline = [int]$Command['deadline_ms']
        if ($blocks.Count -ne 1 -or [int]$blocks[0]['addr'] -ne 658 -or @($blocks[0]['values']).Count -ne 2 -or @($Command['bits']).Count -ne 0) { throw 'invalid length correction payload' }
        $values = @($blocks[0]['values'])
        foreach ($word in $values) { if ($word -isnot [int] -or $word -lt 0 -or $word -gt 65535) { throw 'invalid measured length' } }
        $raw = [long]$values[0] + 65536 * [long]$values[1]
        if ($raw -le 0 -or $raw -gt 99999999) { throw 'invalid measured length' }
        foreach ($a in @('32', '33', '54', '55')) {
            if ($seen -eq $null -or -not $seen.ContainsKey($a) -or $seen[$a] -isnot [int] -or $seen[$a] -lt 0 -or $seen[$a] -gt 65535) { throw 'missing seen length or correction' }
        }
        if ($Since.ElapsedMilliseconds -ge $deadline) { throw 'length correction expired' }
        if (Test-HmiOpen) { throw 'PXS_HMI is running' }
        $version = Get-MbdVersion
        if (-not (Test-MbdCurrent $version '1.5.1')) { throw ('mbd outdated: ' + $version + ', need 1.5.1') }
        # Recheck all guards before the measured write, then again immediately before the pulse.
        foreach ($phase in @(0, 1)) {
            try { $state = Read-MachineState } catch { throw $script:CorrectionAlarmUnreadable }
            if (@(102, 107) -notcontains $state['766']) { throw ('state ' + $state['766']) }
            if ($state['782'] -isnot [int] -or $state['782'] -lt 0 -or $state['782'] -gt 65535) { throw $script:CorrectionAlarmUnreadable }
            if ($state['782'] -ne 0) { throw ($script:CorrectionAlarm -f $state['782']) }
            foreach ($address in @(32, 54)) {
                $read = Read-Range $address 2
                if (@($read).Count -ne 2) { throw 'short length correction guard read' }
                for ($i = 0; $i -lt 2; $i++) {
                    $key = [string]($address + $i)
                    if ([int]$read[$i] -ne [int]$seen[$key]) { throw ('seen value changed D' + $key) }
                    $result['before'][$key] = [int]$read[$i]
                }
            }
            $commandWord = Read-Range 600 1
            if (@($commandWord).Count -ne 1 -or $commandWord[0] -isnot [int] -or $commandWord[0] -lt 0 -or $commandWord[0] -gt 65535) { throw 'invalid D600 readback' }
            if (($commandWord[0] -band 64) -ne 0) {
                if ($phase -ne 0) { throw 'D600.6 became high before pulse' }
                $recovery = $script:Json.DeserializeObject((Invoke-MbdLine (ConvertTo-JsonLite @{ cmd = 'recover_correction' })))
                if (-not $recovery['ok']) { throw ([string]$recovery['error']) }
                $low = $recovery['result']['after']
                if ($low -isnot [int] -or $low -lt 0 -or $low -gt 65535 -or ($low -band 64) -ne 0) { throw 'D600.6 recovery clear not confirmed' }
            }
            if (Test-HmiOpen) { throw 'PXS_HMI is running' }
            if ($Since.ElapsedMilliseconds -ge $deadline) { throw 'length correction expired' }
            if ($phase -eq 0) {
                $reply = $script:Json.DeserializeObject((Invoke-MbdLine (ConvertTo-JsonLite @{ cmd = 'write'; addr = 658; values = $values })))
                if (-not $reply['ok']) { throw ([string]$reply['error']) }
                $read = @($reply['result']['readback'])
                if ($read.Count -ne 2 -or $read[0] -ne $values[0] -or $read[1] -ne $values[1]) { throw 'D658 readback mismatch' }
                $result['readback']['658'] = [int]$read[0]; $result['readback']['659'] = [int]$read[1]
            }
        }
        $pulseStarted = $true
        $reply = Invoke-Pulse 600 6
        if (-not $reply['ok']) { throw ([string]$reply['error']) }
        $pulse = $reply['result']
        foreach ($key in @('before', 'high', 'after')) {
            if ($pulse[$key] -isnot [int] -or $pulse[$key] -lt 0 -or $pulse[$key] -gt 65535) { throw 'D600.6 fresh pulse not confirmed' }
        }
        if (($pulse['before'] -band 64) -ne 0 -or ($pulse['high'] -band 64) -eq 0) { throw 'D600.6 fresh pulse not confirmed' }
        if ($pulse['set'] -ne $true -or $pulse['cleared'] -ne $true -or $pulse['addr'] -ne 600 -or $pulse['bit'] -ne 6 -or $pulse['after'] -isnot [int] -or $pulse['after'] -lt 0 -or $pulse['after'] -gt 65535 -or ([int]$pulse['after'] -band 64) -ne 0) { throw 'D600.6 pulse not confirmed cleared' }
        $result['correction_pulsed'] = $true
        try { $read = Read-Range 54 2 } catch { throw $script:CorrectionUnconfirmed }
        if (@($read).Count -ne 2) { throw $script:CorrectionUnconfirmed }
        foreach ($word in $read) { if ($word -isnot [int] -or $word -lt 0 -or $word -gt 65535) { throw $script:CorrectionUnconfirmed } }
        $result['readback']['54'] = [int]$read[0]; $result['readback']['55'] = [int]$read[1]
        if ($read[0] -eq $seen['54'] -and $read[1] -eq $seen['55']) { throw $script:CorrectionUnconfirmed }
        $result['ok'] = $true; $result['outcome'] = 'done'
    } catch {
        $result['error'] = $_.Exception.Message
        if ($pulseStarted) { $result['outcome'] = 'unknown' }
    }
    return $result
}

# Kind reset_counters (23.11.2026): "Obnulit schetchiki" / "Novyi tirazh" with zeroing. `reset` = the counter blocks
# (D645 and/or D649, each exactly [0, 0]), `seen` = D645/646/649/650 the operator confirmed, `blocks` / `bits` =
# optional ordinary words and switch bits written after the zeroing (the new batch: D22 target, cleared job texts,
# marking off). Order: version, panel, state, seen counters, then mbd 'reset_counters' (mbd re-checks the state and
# accepts zeros only), then the words, then the bits.
# `before` / readback carry D645..650, so ERP journals the history D647..648 too.
function Invoke-CounterReset($Command, [Diagnostics.Stopwatch]$Since) {
    $result = @{ ok = $false; readback = @{}; before = @{}; error = ''; outcome = 'failed' }
    $sent = $false
    try {
        $reset = @($Command['reset'] | Where-Object { $_ -ne $null })
        $blocks = @($Command['blocks'] | Where-Object { $_ -ne $null })
        $seen = $Command['seen']; $deadline = [int]$Command['deadline_ms']
        $states = @($Command['states'] | ForEach-Object { [int]$_ })
        $bits = @($Command['bits'] | Where-Object { $_ -ne $null })
        if ($reset.Count -lt 1 -or $reset.Count -gt 2) { throw 'invalid counter reset payload' }
        $targets = @()
        foreach ($block in $reset) {
            $a = $block['addr']
            if (-not ($a -is [int]) -or @(645, 649) -notcontains $a -or $targets -contains $a) { throw 'invalid counter reset payload' }
            $values = @($block['values'])
            if ($values.Count -ne 2) { throw 'invalid counter reset payload' }
            foreach ($v in $values) { if (-not ($v -is [int]) -or $v -ne 0) { throw 'counter reset writes only 0' } }
            $targets += $a
        }
        foreach ($block in $blocks) {
            if (-not (Test-BlockWritable $block)) { throw ('refused: D' + $block['addr'] + ' is not writable') }
        }
        foreach ($bit in $bits) {
            if (-not (Test-BitWritable $bit)) { throw ('refused: D' + $bit['addr'] + '.' + $bit['bit'] + ' is not writable') }
        }
        foreach ($key in @('645', '646', '649', '650')) {
            if ($seen -eq $null -or -not $seen.ContainsKey($key) -or $seen[$key] -isnot [int] -or $seen[$key] -lt 0 -or $seen[$key] -gt 65535) { throw 'missing seen counters' }
        }
        if ($Since.ElapsedMilliseconds -ge $deadline) { throw 'counter reset expired' }
        if (Test-HmiOpen) { throw 'PXS_HMI is running' }
        $version = Get-MbdVersion
        if (-not (Test-MbdCurrent $version $script:ResetMbd)) { throw ('mbd outdated: ' + $(if ($version) { $version } else { 'no version' }) + ', need ' + $script:ResetMbd) }
        $state = [int](Read-Range 766 1)[0]
        if ($states -notcontains $state) { throw ('state ' + $state) }
        $read = @(Read-Range 645 6)
        if ($read.Count -ne 6) { throw 'short counter read' }
        for ($i = 0; $i -lt 6; $i++) { $result['before'][[string](645 + $i)] = [int]$read[$i] }
        foreach ($key in @('645', '646', '649', '650')) {
            if ([int]$result['before'][$key] -ne [int]$seen[$key]) { throw ('seen value changed D' + $key) }
        }
        if (Test-HmiOpen) { throw 'PXS_HMI is running' }
        if ($Since.ElapsedMilliseconds -ge $deadline) { throw 'counter reset expired' }
        $sent = $true
        $line = ConvertTo-JsonLite @{ cmd = 'reset_counters'; blocks = @($reset | ForEach-Object { @{ addr = [int]$_['addr']; values = @(0, 0) } }) }
        $reply = $script:Json.DeserializeObject((Invoke-MbdLine $line))
        if (-not $reply['ok']) {
            $message = [string]$reply['error']
            # mbd refuses before any write (panel open, wrong state, not a zero): nothing happened.
            if ($message -like 'busy:*' -or $message -like 'reset_counters refused*') { $sent = $false }
            throw $message
        }
        $after = @($reply['result']['readback'])
        if ($after.Count -ne 6) { throw 'counter reset not confirmed' }
        for ($i = 0; $i -lt 6; $i++) { $result['readback'][[string](645 + $i)] = [int]$after[$i] }
        foreach ($a in $targets) {
            if ([int]$after[$a - 645] -ne 0 -or [int]$after[$a - 644] -ne 0) { throw ('counter reset not confirmed D' + $a) }
        }
        foreach ($block in $blocks) {
            $values = [int[]]@($block['values'] | ForEach-Object { [int]$_ })
            $reply = $script:Json.DeserializeObject((Invoke-MbdLine (ConvertTo-JsonLite @{ cmd = 'write'; addr = [int]$block['addr']; values = $values })))
            if (-not $reply['ok']) { throw ([string]$reply['error']) }
            $words = @($reply['result']['readback'])
            for ($i = 0; $i -lt $words.Count; $i++) { $result['readback'][[string]([int]$block['addr'] + $i)] = [int]$words[$i] }
        }
        foreach ($bit in $bits) {
            $reply = $script:Json.DeserializeObject((Invoke-MbdLine (ConvertTo-JsonLite @{ cmd = 'setbit'; addr = [int]$bit['addr']; bit = [int]$bit['bit']; value = [int]$bit['value'] })))
            if (-not $reply['ok']) { throw ([string]$reply['error']) }
            $result['readback'][[string]([int]$bit['addr'])] = [int](@($reply['result']['readback'])[0])
        }
        $result['ok'] = $true; $result['outcome'] = 'done'
    } catch {
        $result['error'] = $_.Exception.Message
        if ($sent -and $result['readback'].Count -eq 0) { $result['outcome'] = 'unknown' }
    }
    return $result
}

function New-Outcome([string]$Status, [string]$Reason, [string]$Detail, $Before, $After, [string]$Method = '') {
    return @{ outcome = $Status; reason = $Reason; detail = $Detail; before = $Before; after = $After; method = $Method }
}

# --- the operator panel PXS_HMI (hmi_close / hmi_open, 1.4.0) ---
# Mirrors HMI_COMMANDS of backend production/machine_writes.py (a backend test compares them).
$script:HmiCommands = @('hmi_close', 'hmi_open')
$script:HmiWaitMs = 15000

function Get-HmiProcesses { return @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'PXS_HMI*' }) }

# The helper in the operator's session reads where PXS_HMI.exe is from here when it starts it again.
function Save-HmiPath {
    try {
        foreach ($process in (Get-HmiProcesses)) {
            $path = [string]$process.MainModule.FileName
            if ($path) { [IO.File]::WriteAllText((Join-Path (Split-Path -Parent $TokenPath) 'pult.path'), $path, [Text.Encoding]::UTF8); return }
        }
    } catch { }
}

# Run a helper task now; $true when Task Scheduler took it.
function Invoke-PultTask([string]$Name) {
    & schtasks.exe /Run /TN $Name 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

# Wait until PXS_HMI runs ($Running) or is gone; $true when it happened within $Ms.
function Wait-Hmi([bool]$Running, [int]$Ms) {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        if ([bool](Test-HmiOpen) -eq $Running) { return $true }
        if ($watch.ElapsedMilliseconds -ge $Ms) { return $false }
        Start-Sleep -Milliseconds 500
    }
}

function Stop-HmiForced { foreach ($process in (Get-HmiProcesses)) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } }

# hmi_close / hmi_open -> outcome with method (graceful / forced / none / started). Never touches the bus.
function Invoke-HmiCommand($Command) {
    $name = [string]$Command['action']
    if ($name -eq 'hmi_open') {
        if (Test-HmiOpen) { return (New-Outcome 'failed' 'hmi_already_open' 'PXS_HMI is running' $null $null) }
        if (-not (Invoke-PultTask $PultOpenTask)) { return (New-Outcome 'failed' 'no_helper' ('schtasks /Run ' + $PultOpenTask) $null $null) }
        if (Wait-Hmi $true $script:HmiWaitMs) { return (New-Outcome 'done' 'ok' 'started' $null $null 'started') }
        return (New-Outcome 'failed' 'hmi_not_started' ('not running after ' + $script:HmiWaitMs + ' ms') $null $null)
    }
    if (-not (Test-HmiOpen)) { return (New-Outcome 'done' 'ok' 'not running' $null $null 'none') }
    Save-HmiPath
    if ($Command['force'] -eq $true) {
        Write-BridgeLog 'control hmi_close: forced from the card'
        Stop-HmiForced
        if (Wait-Hmi $false 3000) { return (New-Outcome 'done' 'ok' 'forced' $null $null 'forced') }
        return (New-Outcome 'failed' 'hmi_still_open' 'PXS_HMI still running' $null $null)
    }
    if (-not (Invoke-PultTask $PultCloseTask)) { return (New-Outcome 'failed' 'no_helper' ('schtasks /Run ' + $PultCloseTask) $null $null) }
    if (Wait-Hmi $false $script:HmiWaitMs) { return (New-Outcome 'done' 'ok' 'closed' $null $null 'graceful') }
    return (New-Outcome 'failed' 'still_open' ('PXS_HMI still running after ' + $script:HmiWaitMs + ' ms') $null $null)
}

# Run one machine command @{ id; kind = 'control'; action; deadline_ms }; $Since started when the runner asked ERP.
# Never throws.
function Invoke-ControlCommand($Command, [Diagnostics.Stopwatch]$Since) {
    $name = [string]$Command['action']
    $deadline = [int]$Command['deadline_ms']
    if ($script:HmiCommands -contains $name) {
        # The panel commands run while PXS_HMI runs (that is what they are for) and never touch the bus.
        if ($Since.ElapsedMilliseconds -ge $deadline) { return (New-Outcome 'expired' 'too_late' ([string]$Since.ElapsedMilliseconds + ' ms') $null $null) }
        Write-BridgeLog ('control ' + $name)
        try { return (Invoke-HmiCommand $Command) } catch { return (New-Outcome 'unknown' 'pc_error' $_.Exception.Message $null $null) }
    }
    if (-not $script:Controls.ContainsKey($name)) { return (New-Outcome 'failed' 'unknown_command' $name $null $null) }
    $spec = $script:Controls[$name]
    if ($Since.ElapsedMilliseconds -ge $deadline) { return (New-Outcome 'expired' 'too_late' ([string]$Since.ElapsedMilliseconds + ' ms') $null $null) }
    if (Test-HmiOpen) { return (New-Outcome 'failed' 'hmi_open' 'PXS_HMI is running' $null $null) }
    try { $before = Read-MachineState }
    catch {
        $message = $_.Exception.Message
        if ($message -like 'mbd: busy:*') { return (New-Outcome 'failed' 'hmi_open' $message $null $null) }
        return (New-Outcome 'failed' 'mbd_error' $message $null $null)
    }
    $states = @($spec['states'])
    if ($states.Count -gt 0 -and $states -notcontains $before['766']) { return (New-Outcome 'failed' 'state' ('D766=' + $before['766']) $before $before) }
    if ($name -eq 'alarm_clear' -and $before['782'] -eq 0) { return (New-Outcome 'failed' 'no_alarm' 'D782=0' $before $before) }
    foreach ($off in @($spec['off'])) {
        if (-not $off) { continue }
        $pair = ([string]$off).Split('.')
        try { $word = [int](Read-Range ([int]$pair[0]) 1)[0] } catch {
            $message = $_.Exception.Message
            if ($message -like 'mbd: busy:*') { return (New-Outcome 'failed' 'hmi_open' $message $before $before) }
            return (New-Outcome 'failed' 'mbd_error' $message $before $before)
        }
        $mask = [int][math]::Pow(2, [int]$pair[1])
        if (($word -band $mask) -ne 0) { return (New-Outcome 'failed' 'rollers_up' ('D' + $off + '=1') $before $before) }
    }
    if ($Since.ElapsedMilliseconds -ge $deadline) { return (New-Outcome 'expired' 'too_late' ([string]$Since.ElapsedMilliseconds + ' ms') $before $null) }
    Write-BridgeLog ('control ' + $name + ' D' + $spec['addr'] + '.' + $spec['bit'] + ' state ' + $before['766'])
    try { $reply = Invoke-Pulse ([int]$spec['addr']) ([int]$spec['bit']) }
    catch { return (New-Outcome 'unknown' 'no_reply' $_.Exception.Message $before $null) }
    $after = $null
    try { $after = Read-MachineState } catch { }
    if (-not $reply['ok']) {
        $message = [string]$reply['error']
        # mbd refuses before touching the bus when the vendor HMI runs or the bit is not allowed: nothing happened.
        if ($message -like 'busy:*') { return (New-Outcome 'failed' 'hmi_open' $message $before $after) }
        if ($message -like 'pulse not allowed*') { return (New-Outcome 'failed' 'mbd_error' $message $before $after) }
        return (New-Outcome 'unknown' 'mbd_error' $message $before $after)
    }
    $result = $reply['result']
    if (-not $result['set']) { return (New-Outcome 'failed' 'mbd_error' 'bit was not set' $before $after) }
    if (-not $result['cleared']) { return (New-Outcome 'unknown' 'not_cleared' ('D' + $spec['addr'] + '=' + $result['after']) $before $after) }
    return (New-Outcome 'done' 'ok' ('held ' + $result['hold_ms'] + ' ms') $before $after)
}

# POST JSON to ERP -> @{ code; text } (code 0 = no answer). Never throws.
function Invoke-Erp([string]$Path, [string]$Body, [string]$Token) {
    try {
        if ($Server -like 'https:*') { Enable-Tls12 }
        $request = [System.Net.HttpWebRequest]::Create(($Server.TrimEnd('/')) + $Path)
        $request.Method = 'POST'; $request.ContentType = 'application/json; charset=utf-8'; $request.Timeout = 5000
        $request.Headers.Add('Authorization', 'Bearer ' + $Token)
        $bytes = [Text.Encoding]::UTF8.GetBytes($Body)
        $request.ContentLength = $bytes.Length
        $stream = $request.GetRequestStream(); $stream.Write($bytes, 0, $bytes.Length); $stream.Close()
        $response = $request.GetResponse()
        $text = (New-Object IO.StreamReader($response.GetResponseStream(), [Text.Encoding]::UTF8)).ReadToEnd()
        $code = [int]$response.StatusCode; $response.Close()
        return @{ code = $code; text = $text }
    } catch [System.Net.WebException] {
        $failed = $_.Exception.Response
        if ($failed) {
            $code = [int]$failed.StatusCode
            try { $text = (New-Object IO.StreamReader($failed.GetResponseStream())).ReadToEnd() } catch { $text = '' }
            $failed.Close()
            Write-BridgeLog ('ERP ' + $code + ' ' + $Path + ' ' + $text)
            return @{ code = $code; text = $text }
        }
        Write-BridgeLog ('ERP unreachable: ' + $_.Exception.Message)
        return @{ code = 0; text = '' }
    } catch {
        Write-BridgeLog ('ERP error: ' + $_.Exception.Message)
        return @{ code = 0; text = '' }
    }
}

# runner_version (1.2.0): ERP hands a command with switch bits or the other HMI pages only to a runner that names
# 1.2.0 or newer; without it ERP fails such a command unsent (a 1.1.0 runner would write its words but not its bits).
function Get-Identity {
    return @{ inventory = $script:Inventory; machine_id = (Get-MachineId); runner_version = $script:RunnerVersion }
}

# One poll: take a command, run it, report. Returns the run result or $null when there was nothing to do.
function Step-Commands([string]$Token) {
    $since = [Diagnostics.Stopwatch]::StartNew()
    $answer = Invoke-Erp '/api/production/machines/agent/writes/next' (ConvertTo-JsonLite (Get-Identity)) $Token
    if ($answer['code'] -ne 200) { return @{ http = $answer['code'] } }
    $command = $script:Json.DeserializeObject($answer['text'])['command']
    if ($null -eq $command) { return $null }
    if ($command['kind'] -eq 'control') { return (Step-Control $command $since $Token) }
    if ($command['kind'] -eq 'length_correction') { $result = Invoke-LengthCorrection $command $since }
    elseif ($command['kind'] -eq 'reset_counters') { $result = Invoke-CounterReset $command $since }
    else { $result = Invoke-MachineCommand $command }
    Write-BridgeLog ('command ' + $command['id'] + ' ok=' + $result['ok'] + ' ' + $result['error'])
    $body = Get-Identity
    $body['ok'] = [bool]$result['ok']; $body['readback'] = $result['readback']; $body['error'] = [string]$result['error']
    if ($command['kind'] -eq 'length_correction') {
        $body['correction_pulsed'] = [bool]$result['correction_pulsed']; $body['before'] = $result['before']; $body['outcome'] = $result['outcome']
    }
    if ($command['kind'] -eq 'reset_counters') { $body['before'] = $result['before']; $body['outcome'] = $result['outcome'] }
    [void](Invoke-Erp ('/api/production/machines/agent/writes/' + $command['id'] + '/result') (ConvertTo-JsonLite $body) $Token)
    return $result
}

# Run a machine command and report it (retried while ERP does not answer: the result must not be lost).
function Step-Control($Command, [Diagnostics.Stopwatch]$Since, [string]$Token) {
    $outcome = Invoke-ControlCommand $Command $Since
    Write-BridgeLog ('control ' + $Command['id'] + ' ' + $Command['action'] + ' ' + $outcome['outcome'] + ' ' + $outcome['reason'] + ' ' + $outcome['detail'])
    $body = Get-Identity
    $body['ok'] = ($outcome['outcome'] -eq 'done'); $body['readback'] = @{}; $body['error'] = ''
    foreach ($key in @('outcome', 'reason', 'detail', 'before', 'after', 'method')) { $body[$key] = $outcome[$key] }
    $path = '/api/production/machines/agent/writes/' + [string]$Command['id'] + '/result'
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $code = (Invoke-Erp $path (ConvertTo-JsonLite $body) $Token)['code']
        if (@(200, 403, 404, 409, 422) -contains $code) { break }
        Start-Sleep -Milliseconds 500
    }
    return $outcome
}

function Start-Runner {
    if (-not (Test-Path $TokenPath)) { throw ('no token file ' + $TokenPath) }
    $token = ([IO.File]::ReadAllText($TokenPath)).Trim()
    Write-BridgeLog ('commands start ' + $script:RunnerVersion + ' server ' + $Server)
    while ($true) {
        $started = Get-Date
        $step = Step-Commands $token
        if ($Once) { return $step }
        if ($step -ne $null -and ($step['http'] -eq 401 -or $step['http'] -eq 403)) {
            Write-BridgeLog 'ERP refused the command runner (token or PC binding), retry in 60 s'; Start-Sleep -Seconds 60
        }
        $rest = $PollMs - [int]((Get-Date) - $started).TotalMilliseconds
        if ($rest -gt 0) { Start-Sleep -Milliseconds $rest }
    }
}

if ($script:Self -ne '.') {
    while ($true) {
        try { Start-Runner; if ($Once) { break } } catch { Write-BridgeLog ('COMMANDS ERROR ' + $_.Exception.Message); if ($Once) { throw }; Start-Sleep -Seconds 10 }
    }
}

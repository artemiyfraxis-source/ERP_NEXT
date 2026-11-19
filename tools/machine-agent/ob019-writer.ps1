param(
    [string]$Server = 'http://192.168.1.85',
    [string]$TokenPath = 'D:\fraxis-mb\bridge.token',
    [string]$MbdHost = '127.0.0.1',
    [int]$MbdPort = 5020,
    [string]$MbdScript = 'D:\fraxis-mb\mbd.ps1',
    [string]$BridgeScript = '',
    [string]$LogPath = 'D:\fraxis-mb\commands.log',
    [int]$PollMs = 500,
    [switch]$Once
)
# ERP command runner of OB.019 (ERP_NEXT, section "Stanki", slice 4): writes the parameters ERP asks for.
# Pull transport like the bridge: POST /api/production/machines/agent/writes/next (the bridge's token, scope
# machine:report, the same PC binding) -> one command {id, blocks: [{addr, values}], bits: [{addr, bit, value}],
# states}; the runner
#   0. asks mbd its version ("version", no bus) and refuses the whole command unless it is $script:MbdMinVersion or
#      newer: an old mbd has no "setbit" and a wider "write" list, so a new runner never writes part of a command;
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
$ErrorActionPreference = 'Stop'
$script:Self = $MyInvocation.InvocationName
if (-not $BridgeScript) { $BridgeScript = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'ob019-bridge.ps1' }
. $BridgeScript -Server $Server -TokenPath $TokenPath -MbdHost $MbdHost -MbdPort $MbdPort -MbdScript $MbdScript -LogPath $LogPath -PollMs $PollMs -Once:$Once
$script:RunnerVersion = '1.3.0'
# Machine commands: the bit held and the machine states (D766) each may run in, empty = any state. Mirrors
# ALLOWED_STATES of backend production/machine_control.py (a backend test compares them).
$script:HoldMs = 1500
$script:Controls = @{
    stop = @{ addr = 599; bit = 2; states = @() }
    start = @{ addr = 599; bit = 0; states = @(102) }
    reset = @{ addr = 600; bit = 1; states = @(100, 105, 107) }
    alarm_clear = @{ addr = 600; bit = 4; states = @() }
}
# Words: D20 mode, D22..D28 job and quantities, D32..D43 wire and strip lengths, D940..D969 order number and product;
# the other HMI pages (1.2.0): middle strip D197/D199, slitting D67..D74/D1703..D1705, coiling D391..D395/D1750..D1753,
# marking D401..D432 numbers, D1722 and the texts D440..D484, speeds D100..D104/D120..D123. Switch bits (setbit only,
# never a whole word): middle strip D132.0, feed-roller lift D136.0 (D136.0 vs D604.0: bench probe), marking D400.0, D422.0, D434.0..D436.0. Not: D390/D133/D142 (meaning not
# established), D421 (order transfer handshake, map open question 8). Mirrors OB019_WRITTEN_WORDS / _BITS of backend
# production/machines.py and mbd.ps1 allowedWrite / allowedBits (a backend test compares the three). Never: passwords
# D1682..D1687, SHA-256 D1203..D1211, command bits D599/D600, D730, date D1087..D1089.
$script:MbdMinVersion = '1.2.0'
$script:Writable = @{}
foreach ($a in @(20, 74, 197, 199, 423) + (22..28) + (32..43) + (67..72) + (100..104) + (120..123) + (391..395) + (401..402) + (404..415) + (430..432) + (440..484) + (940..969) + (1703..1705) + (1722..1723) + (1750..1753)) { $script:Writable[[int]$a] = $true }
$script:WritableBits = @{ 132 = @(0); 136 = @(0); 400 = @(0); 422 = @(0); 434 = @(0); 435 = @(0); 436 = @(0) }

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

function Test-MbdCurrent([string]$Version) {
    if (-not $Version) { return $false }
    try { return ([version]$Version) -ge ([version]$script:MbdMinVersion) } catch { return $false }
}

# One command -> @{ ok; readback = @{ "addr" = word }; error }. Never throws.
function Invoke-MachineCommand($Command) {
    $readback = @{}
    try {
        $version = Get-MbdVersion
        if (-not (Test-MbdCurrent $version)) { return @{ ok = $false; readback = $readback; error = ('mbd outdated: ' + $(if ($version) { $version } else { 'no version' }) + ', need ' + $script:MbdMinVersion) } }
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
    return @{ '766' = [int]$values[0]; '782' = [int]$values[16] }
}

function Assert-ControlBit([int]$Address, [int]$Bit) {
    foreach ($spec in $script:Controls.Values) { if ($spec['addr'] -eq $Address -and $spec['bit'] -eq $Bit) { return } }
    throw ('runner refused bit D' + $Address + '.' + $Bit)
}

function Invoke-Pulse([int]$Address, [int]$Bit) {
    Assert-ControlBit $Address $Bit
    $line = ConvertTo-JsonLite @{ cmd = 'pulse'; addr = $Address; bit = $Bit; hold_ms = $script:HoldMs }
    return $script:Json.DeserializeObject((Invoke-MbdLine $line))
}

function New-Outcome([string]$Status, [string]$Reason, [string]$Detail, $Before, $After) {
    return @{ outcome = $Status; reason = $Reason; detail = $Detail; before = $Before; after = $After }
}

# Run one machine command @{ id; kind = 'control'; action; deadline_ms }; $Since started when the runner asked ERP.
# Never throws.
function Invoke-ControlCommand($Command, [Diagnostics.Stopwatch]$Since) {
    $name = [string]$Command['action']
    $deadline = [int]$Command['deadline_ms']
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
    $result = Invoke-MachineCommand $command
    Write-BridgeLog ('command ' + $command['id'] + ' ok=' + $result['ok'] + ' ' + $result['error'])
    $body = Get-Identity
    $body['ok'] = [bool]$result['ok']; $body['readback'] = $result['readback']; $body['error'] = [string]$result['error']
    [void](Invoke-Erp ('/api/production/machines/agent/writes/' + $command['id'] + '/result') (ConvertTo-JsonLite $body) $Token)
    return $result
}

# Run a machine command and report it (retried while ERP does not answer: the result must not be lost).
function Step-Control($Command, [Diagnostics.Stopwatch]$Since, [string]$Token) {
    $outcome = Invoke-ControlCommand $Command $Since
    Write-BridgeLog ('control ' + $Command['id'] + ' ' + $Command['action'] + ' ' + $outcome['outcome'] + ' ' + $outcome['reason'] + ' ' + $outcome['detail'])
    $body = Get-Identity
    $body['ok'] = ($outcome['outcome'] -eq 'done'); $body['readback'] = @{}; $body['error'] = ''
    foreach ($key in @('outcome', 'reason', 'detail', 'before', 'after')) { $body[$key] = $outcome[$key] }
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

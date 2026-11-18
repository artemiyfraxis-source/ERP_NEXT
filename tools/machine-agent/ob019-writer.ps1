param(
    [string]$Server = 'http://192.168.1.85',
    [string]$TokenPath = 'D:\fraxis-mb\bridge.token',
    [string]$MbdHost = '127.0.0.1',
    [int]$MbdPort = 5020,
    [string]$MbdScript = 'D:\fraxis-mb\mbd.ps1',
    [string]$BridgeScript = '',
    [string]$LogPath = 'D:\fraxis-mb\commands.log',
    [int]$PollMs = 1000,
    [switch]$Once
)
# ERP command runner of OB.019 (ERP_NEXT, section "Stanki", slice 4): writes the parameters ERP asks for.
# Pull transport like the bridge: POST /api/production/machines/agent/writes/next (the bridge's token, scope
# machine:report, the same PC binding) -> one command {id, blocks: [{addr, values}], states}; the runner
#   1. reads D766 and refuses unless the machine is idle (102 ready / 107 total finished);
#   2. writes each block through the local Modbus service mbd.ps1 ("write" = FC16 + read-back; mbd itself refuses
#      while PXS_HMI holds COM8 and checks its own write allow-list);
#   3. reports the words it read back: POST .../agent/writes/{id}/result. ERP compares them with what it asked.
# Only the registers of $script:Writable (ERP's writable fields, backend production/machines.py OB019_FIELDS) are ever
# written. Same runtime as the bridge: Windows 7, PowerShell 2.0, ASCII file. The bridge's helpers are dot-sourced.
$ErrorActionPreference = 'Stop'
$script:Self = $MyInvocation.InvocationName
if (-not $BridgeScript) { $BridgeScript = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'ob019-bridge.ps1' }
. $BridgeScript -Server $Server -TokenPath $TokenPath -MbdHost $MbdHost -MbdPort $MbdPort -MbdScript $MbdScript -LogPath $LogPath -PollMs $PollMs -Once:$Once
$script:RunnerVersion = '1.0.0'
# D20 mode, D22..D28 job and quantities, D32..D43 wire and strip lengths, D440..D484 marking texts, D940..D969 order
# number and product. Mirrors the writable fields of OB019_FIELDS (backend test checks it).
$script:Writable = @{}
foreach ($a in @(20) + (22..28) + (32..43) + (440..484) + (940..969)) { $script:Writable[[int]$a] = $true }

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

# One command -> @{ ok; readback = @{ "addr" = word }; error }. Never throws.
function Invoke-MachineCommand($Command) {
    $readback = @{}
    try {
        $states = @($Command['states'] | ForEach-Object { [int]$_ })
        $state = [int](Read-Range 766 1)[0]
        if ($states -notcontains $state) { return @{ ok = $false; readback = $readback; error = ('state ' + $state) } }
        $blocks = @($Command['blocks'])
        foreach ($block in $blocks) {
            if (-not (Test-BlockWritable $block)) { return @{ ok = $false; readback = $readback; error = ('refused: D' + $block['addr'] + ' is not writable') } }
        }
        foreach ($block in $blocks) {
            $values = [int[]]@($block['values'] | ForEach-Object { [int]$_ })
            $reply = $script:Json.DeserializeObject((Invoke-MbdLine (ConvertTo-JsonLite @{ cmd = 'write'; addr = [int]$block['addr']; values = $values })))
            if (-not $reply['ok']) { return @{ ok = $false; readback = $readback; error = [string]$reply['error'] } }
            $read = @($reply['result']['readback'])
            for ($i = 0; $i -lt $read.Count; $i++) { $readback[[string]([int]$block['addr'] + $i)] = [int]$read[$i] }
        }
        return @{ ok = $true; readback = $readback; error = '' }
    } catch {
        return @{ ok = $false; readback = $readback; error = $_.Exception.Message }
    }
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

function Get-Identity {
    return @{ inventory = $script:Inventory; machine_id = (Get-MachineId) }
}

# One poll: take a command, run it, report. Returns the run result or $null when there was nothing to do.
function Step-Commands([string]$Token) {
    $answer = Invoke-Erp '/api/production/machines/agent/writes/next' (ConvertTo-JsonLite (Get-Identity)) $Token
    if ($answer['code'] -ne 200) { return @{ http = $answer['code'] } }
    $command = $script:Json.DeserializeObject($answer['text'])['command']
    if ($command -eq $null) { return $null }
    $result = Invoke-MachineCommand $command
    Write-BridgeLog ('command ' + $command['id'] + ' ok=' + $result['ok'] + ' ' + $result['error'])
    $body = Get-Identity
    $body['ok'] = [bool]$result['ok']; $body['readback'] = $result['readback']; $body['error'] = [string]$result['error']
    [void](Invoke-Erp ('/api/production/machines/agent/writes/' + $command['id'] + '/result') (ConvertTo-JsonLite $body) $Token)
    return $result
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

param(
    [string]$Server = 'http://192.168.1.85',
    [string]$TokenPath = 'D:\fraxis-mb\bridge.token',
    [string]$MbdHost = '127.0.0.1',
    [int]$MbdPort = 5020,
    [string]$MbdScript = 'D:\fraxis-mb\mbd.ps1',
    [string]$LogPath = 'D:\fraxis-mb\bridge.log',
    [int]$PollMs = 2000,
    [int]$HeartbeatSec = 5,
    [int]$SettingsSec = 10,
    [switch]$Once
)
# ERP bridge of OB.019 (ERP_NEXT, section "Stanki", slices 1 and 4): READ ONLY. Writes ERP asks for are made by the
# separate command runner ob019-writer.ps1, never by this script.
# Reads an allow-list of PLC registers through the local Modbus service mbd.ps1 (127.0.0.1:5020, the only COM8 owner)
# and reports them to ERP outbound: POST /api/production/machines/agent/report, API token with scope machine:report.
# Sends a snapshot when something changed or every $HeartbeatSec seconds; ERP shows "no link" after ~10 s of silence.
#
# Written for the machine PC as it is (18.11.2026): Windows 7 SP1, PowerShell 2.0 on CLR 2.0, so no
# Invoke-RestMethod / ConvertTo-Json, and SecurityProtocolType knows only Ssl3/Tls (no TLS 1.2). Default server is
# therefore the ERP over plain HTTP on the shop LAN; an https:// server works only where TLS 1.2 is available.
#
# The bridge never writes to the bus: the only mbd command it can send is "read". mbd's "status" is NOT read-only
# (it writes the HMI context D730 and the date D1087..D1089 first), "write" and "pulse" move the machine.
# The file is ASCII on purpose (PowerShell 2 reads BOM-less files in the ANSI code page).
$ErrorActionPreference = 'Stop'
$script:BridgeVersion = '1.1.0'
$script:Proto = 1
# OB.019 in Cyrillic, built from code points to keep this file ASCII.
$script:Inventory = [string][char]0x041E + [string][char]0x0411 + '.019'
# Register ranges: @(first address, count). As few requests as possible: mbd opens COM8 for each one, and while it
# holds the port the vendor HMI cannot start. $script:Ranges are read every cycle (state, job, counters, wire),
# $script:SlowRanges (the other HMI pages: middle strip, slitting, coiling, marking, speeds) every $SettingsSec seconds
# and sent from the last reading in between. Words outside OB019_ALLOWED of backend/app/modules/production/machines.py
# are read (FC3 has no side effects) but never sent (ERP refuses them, 422).
$script:Ranges = @(
    @(20, 62),    # D20 mode, D22..D28 job, D32..D53 wire and inner layer, D60..D74 clamp gaps and slitting
    @(604, 47),   # D604 Jog outputs (bit0 feed-roller lift), D641 UPH, D643 order done, D645 batch done, D649 total done
    @(711, 72),   # D711.0 order lamp, D766 machine state, D782 alarm
    @(938, 32),   # D938 order row, D940..D969 order number, product name
    @(1280, 1)    # D1280 program
)
$script:SlowRanges = @(
    @(100, 43),   # D100..D123 speeds, D132 middle strip, D133/D142 slitting switches
    @(197, 85),   # D197..D281 middle strip corrections and tables
    @(390, 95),   # D390..D395 coiling, D400..D484 marking and its texts
    @(1703, 51)   # D1703/D1705 slitting die, D1722 marking drop, D1750..D1753 coiling limits
)
# D604.0 Wheel (Y18, Jog page): one press lifts the feed rollers, the next lowers (owner bench probe 19.11). Mirrors
# OB019_FEED_WHEEL_ADDRESS of backend production/machines.py; read every cycle by the D604..D650 range above.
$script:FeedWheelAddress = 604
$script:Allowed = @{}
foreach ($a in @(20, 74, 142, 197, 604, 711, 766, 782, 1280) + (22..28) + (32..53) + (60..61) + (67..72) + (100..104) + (120..123) + (132..133) + (199..231) + (250..281) + (390..395) + (400..402) + (404..415) + (421..423) + (430..432) + (434..436) + (440..484) + (641..646) + (649..650) + (938..969) + (1703..1705) + (1722..1723) + (1750..1753)) { $script:Allowed[[int]$a] = $true }
$script:SlowWords = @{}
$script:SlowAt = [datetime]::MinValue
$script:ReadOnlyCommands = @('read')
$script:ServiceVersion = ''

Add-Type -AssemblyName System.Web.Extensions
$script:Json = New-Object System.Web.Script.Serialization.JavaScriptSerializer

# Log rotation (PowerShell 2.0): when the log reaches $MaxBytes it becomes .1, .1 becomes .2 and so on; the oldest
# (.$Keep) is dropped. Same function in mbd.ps1, ob019-bridge.ps1 (also used by ob019-writer.ps1) and ob099-agent.ps1.
function Invoke-LogRotation([string]$Path, [long]$MaxBytes = 5242880, [int]$Keep = 3) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    if ((Get-Item -LiteralPath $Path).Length -lt $MaxBytes) { return $false }
    $oldest = $Path + '.' + $Keep
    if (Test-Path -LiteralPath $oldest) { Remove-Item -Force -LiteralPath $oldest }
    for ($i = $Keep - 1; $i -ge 1; $i--) {
        $from = $Path + '.' + $i
        if (Test-Path -LiteralPath $from) { Move-Item -Force -LiteralPath $from ($Path + '.' + ($i + 1)) }
    }
    Move-Item -Force -LiteralPath $Path ($Path + '.1')
    return $true
}

function Write-BridgeLog([string]$Text) {
    try {
        [void](Invoke-LogRotation $LogPath)
        Add-Content -Path $LogPath -Value ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $Text) -Encoding UTF8
    } catch { }
}

# Own JSON writer (as in mbd.ps1): PowerShell 2 has no ConvertTo-Json. Non-ASCII goes out as \uXXXX.
function ConvertTo-JsonLite($v) {
    if ($v -eq $null) { return 'null' }
    if ($v -is [bool]) { if ($v) { return 'true' } else { return 'false' } }
    if ($v -is [string]) {
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.Append('"')
        foreach ($ch in $v.ToCharArray()) {
            $code = [int]$ch
            if ($ch -eq '"') { [void]$sb.Append('\"') }
            elseif ($ch -eq '\') { [void]$sb.Append('\\') }
            elseif ($code -lt 32 -or $code -gt 126) { [void]$sb.Append('\u' + $code.ToString('x4')) }
            else { [void]$sb.Append($ch) }
        }
        [void]$sb.Append('"')
        return $sb.ToString()
    }
    if ($v -is [int] -or $v -is [long]) { return ([long]$v).ToString([Globalization.CultureInfo]::InvariantCulture) }
    if ($v -is [System.Collections.IDictionary]) {
        $parts = @()
        foreach ($k in ($v.Keys | Sort-Object)) { $parts += ((ConvertTo-JsonLite ([string]$k)) + ':' + (ConvertTo-JsonLite $v[$k])) }
        return '{' + ($parts -join ',') + '}'
    }
    if ($v -is [System.Collections.IEnumerable]) {
        $parts = @()
        foreach ($x in $v) { $parts += (ConvertTo-JsonLite $x) }
        return '[' + ($parts -join ',') + ']'
    }
    return (ConvertTo-JsonLite ([string]$v))
}

function Assert-ReadOnly([string]$Command) {
    if ($script:ReadOnlyCommands -notcontains $Command) { throw ('bridge is read-only, refused mbd command ' + $Command) }
}

# One TCP connection = one JSON request line -> one JSON reply line (mbd.ps1 protocol).
function Invoke-MbdLine([string]$Line) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $wait = $client.BeginConnect($MbdHost, $MbdPort, $null, $null)
        if (-not $wait.AsyncWaitHandle.WaitOne(2000)) { throw ('mbd does not answer on ' + $MbdHost + ':' + $MbdPort) }
        $client.EndConnect($wait)
        $client.ReceiveTimeout = 5000; $client.SendTimeout = 5000
        $stream = $client.GetStream()
        $enc = New-Object System.Text.UTF8Encoding $false
        $writer = New-Object System.IO.StreamWriter($stream, $enc); $writer.AutoFlush = $true
        $reader = New-Object System.IO.StreamReader($stream, $enc)
        $writer.WriteLine($Line)
        $reply = $reader.ReadLine()
        if (-not $reply) { throw 'mbd closed the connection without a reply' }
        return $reply
    } finally {
        $client.Close()
    }
}

function Read-Range([int]$Address, [int]$Count) {
    Assert-ReadOnly 'read'
    $reply = $script:Json.DeserializeObject((Invoke-MbdLine (ConvertTo-JsonLite @{ cmd = 'read'; addr = $Address; count = $Count })))
    if (-not $reply['ok']) { throw (New-Object System.InvalidOperationException ('mbd: ' + [string]$reply['error'])) }
    return ,@($reply['result']['values'])
}

# mbd failure text -> link state ERP shows: the vendor HMI (or a program on the bus list) holds COM8, mbd refuses
# this client, or the PLC does not answer.
function Get-LinkState([string]$Message) {
    if ($Message -like 'mbd: busy:*' -or $Message -like "*Access to the port*" -or $Message -like '*COM8*denied*') { return 'port_busy' }
    if ($Message -like 'mbd: forbidden*') { return 'no_service' }
    if ($Message -like 'mbd: *') { return 'no_plc' }
    return 'no_service'
}

function Read-Ranges($Ranges, $Words) {
    foreach ($range in $Ranges) {
        $values = Read-Range $range[0] $range[1]
        if ($values.Count -ne $range[1]) { throw (New-Object System.InvalidOperationException ('mbd: short reply reading D' + $range[0])) }
        for ($i = 0; $i -lt $range[1]; $i++) {
            $address = $range[0] + $i
            if ($script:Allowed.ContainsKey($address)) { $Words[[string]$address] = [int]$values[$i] }
        }
    }
}

function Read-Snapshot {
    $words = @{}
    try {
        Read-Ranges $script:Ranges $words
        if ($script:SlowWords.Count -eq 0 -or ((Get-Date) - $script:SlowAt).TotalSeconds -ge $SettingsSec) {
            $slow = @{}
            Read-Ranges $script:SlowRanges $slow
            $script:SlowWords = $slow; $script:SlowAt = Get-Date
        }
        foreach ($key in $script:SlowWords.Keys) { $words[$key] = $script:SlowWords[$key] }
        return @{ link = 'ok'; error = ''; registers = $words }
    } catch [System.InvalidOperationException] {
        # mbd answered with an error: the bus is busy, the client is refused or the PLC is silent. The vendor HMI may
        # change settings meanwhile, so the next good cycle reads the slow pages again.
        $script:SlowWords = @{}
        return @{ link = (Get-LinkState $_.Exception.Message); error = $_.Exception.Message; registers = @{} }
    } catch {
        # No answer from mbd at all (not running, not listening on this address, connection dropped).
        $script:SlowWords = @{}
        return @{ link = 'no_service'; error = $_.Exception.Message; registers = @{} }
    }
}

function Get-Fingerprint($Snapshot) {
    return $Snapshot['link'] + '|' + $Snapshot['error'] + '|' + (ConvertTo-JsonLite $Snapshot['registers'])
}

function Test-ShouldSend([string]$Fingerprint, [string]$LastFingerprint, [datetime]$LastSent, [datetime]$Now) {
    return ($Fingerprint -ne $LastFingerprint) -or (($Now - $LastSent).TotalSeconds -ge $HeartbeatSec)
}

function Get-MachineId {
    try { return [string](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid } catch { return '' }
}

function Get-LanIp {
    try {
        foreach ($address in [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName())) {
            if ($address.AddressFamily -eq 'InterNetwork' -and -not $address.ToString().StartsWith('127.')) { return $address.ToString() }
        }
    } catch { }
    return ''
}

function Get-ServiceVersion {
    if ($script:ServiceVersion) { return $script:ServiceVersion }
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $hash = $sha.ComputeHash([IO.File]::ReadAllBytes($MbdScript))
        $script:ServiceVersion = 'mbd ' + (([BitConverter]::ToString($hash)).Replace('-', '').Substring(0, 12).ToLowerInvariant())
        return $script:ServiceVersion
    } catch { return 'mbd' }
}

function New-Report($Snapshot, [string]$Boot, [long]$Seq) {
    return @{
        proto = $script:Proto; inventory = $script:Inventory; machine_id = (Get-MachineId); pc_name = $env:COMPUTERNAME
        ip_lan = (Get-LanIp); bridge_version = $script:BridgeVersion; service_version = (Get-ServiceVersion)
        boot = $Boot; seq = $Seq; link = $Snapshot['link']; error = $Snapshot['error']; registers = $Snapshot['registers']
    }
}

function Enable-Tls12 {
    try { [Net.ServicePointManager]::SecurityProtocol = [Enum]::ToObject([Net.SecurityProtocolType], 3072) }
    catch { throw 'TLS 1.2 is not available in this PowerShell (CLR 2.0): use the ERP on the shop LAN, -Server http://192.168.1.85' }
}

# POST the report; returns the HTTP status (0 = no answer). Never throws.
function Send-Report([string]$Body, [string]$Token) {
    try {
        if ($Server -like 'https:*') { Enable-Tls12 }
        $request = [System.Net.HttpWebRequest]::Create(($Server.TrimEnd('/')) + '/api/production/machines/agent/report')
        $request.Method = 'POST'; $request.ContentType = 'application/json; charset=utf-8'; $request.Timeout = 5000
        $request.Headers.Add('Authorization', 'Bearer ' + $Token)
        $bytes = [Text.Encoding]::UTF8.GetBytes($Body)
        $request.ContentLength = $bytes.Length
        $stream = $request.GetRequestStream(); $stream.Write($bytes, 0, $bytes.Length); $stream.Close()
        $response = $request.GetResponse(); $code = [int]$response.StatusCode; $response.Close()
        return $code
    } catch [System.Net.WebException] {
        $failed = $_.Exception.Response
        if ($failed) {
            $code = [int]$failed.StatusCode
            try { $text = (New-Object IO.StreamReader($failed.GetResponseStream())).ReadToEnd() } catch { $text = '' }
            Write-BridgeLog ('ERP ' + $code + ' ' + $text)
            $failed.Close()
            return $code
        }
        Write-BridgeLog ('ERP unreachable: ' + $_.Exception.Message)
        return 0
    } catch {
        Write-BridgeLog ('ERP error: ' + $_.Exception.Message)
        return 0
    }
}

function Start-Bridge {
    if (-not (Test-Path $TokenPath)) { throw ('no token file ' + $TokenPath) }
    $token = ([IO.File]::ReadAllText($TokenPath)).Trim()
    $boot = [guid]::NewGuid().ToString(); $seq = 0L
    $last = ''; $lastSent = [datetime]::MinValue
    Write-BridgeLog ('start ' + $script:BridgeVersion + ' server ' + $Server + ' mbd ' + $MbdHost + ':' + $MbdPort)
    while ($true) {
        $started = Get-Date
        $snapshot = Read-Snapshot
        $fingerprint = Get-Fingerprint $snapshot
        if (Test-ShouldSend $fingerprint $last $lastSent (Get-Date)) {
            $code = Send-Report (ConvertTo-JsonLite (New-Report $snapshot $boot $seq)) $token
            if ($code -eq 200) { $last = $fingerprint; $lastSent = Get-Date; $seq++ }
            elseif ($code -eq 401 -or $code -eq 403) { Write-BridgeLog 'ERP refused the bridge (token or PC binding), retry in 60 s'; Start-Sleep -Seconds 60 }
        }
        if ($Once) { return $snapshot }
        $rest = $PollMs - [int]((Get-Date) - $started).TotalMilliseconds
        if ($rest -gt 0) { Start-Sleep -Milliseconds $rest }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    while ($true) {
        try { Start-Bridge; if ($Once) { break } } catch { Write-BridgeLog ('BRIDGE ERROR ' + $_.Exception.Message); if ($Once) { throw }; Start-Sleep -Seconds 10 }
    }
}

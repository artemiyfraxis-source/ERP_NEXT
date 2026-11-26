param([int]$Port = 5020, [string]$Allow = '192.168.1.85', [switch]$Mock, [string]$Dir = 'D:\fraxis-mb', [switch]$LocalOnly)
# Fraxis Modbus command service for OB.019 (PowerShell 2.0 compatible).
# One TCP connection = one JSON request line -> one JSON reply line.
# COM8 is opened per request and closed afterwards, so the vendor HMI can still be used when this service is idle.
# -Allow takes one address or a comma list: '192.168.1.85,127.0.0.1' keeps the ERP server's client and also serves the
# local ERP bridge (ob019-bridge.ps1 talks to 127.0.0.1). -LocalOnly (ERP_NEXT tools/machine-agent): listen on
# 127.0.0.1 only and serve only the local bridge. Without either the service behaves exactly as deployed on 18.11.2026.
$ErrorActionPreference = 'Stop'
$script:Self = $MyInvocation.InvocationName
Add-Type -AssemblyName System.Web.Extensions
$script:js = New-Object System.Web.Script.Serialization.JavaScriptSerializer
$script:log = Join-Path $Dir 'mbd.log'
$script:sp = $null
# 1.2.0 (19.11.2026): 'version' (no bus) and 'setbit'; 'write' limited to ERP's writable words.
# 1.3.0 (19.11.2026): 'setbit' also for the feed-roller lift D604.0 (the runner asks 1.3.0 for that bit only).
# 1.4.0 (20.11.2026): 'write' also for the inner layer D44..D53 (the runner asks 1.4.0 for those words only).
# 1.5.0: measured length D658..659 and pulse D600.6, used only by the dedicated correction runner path.
# Registers a 'write' request may set: ERP's writable numbers/texts plus D658..659. Switch words are never
# written whole: 'setbit' reads the word and writes it back with one bit of $script:allowedBits changed, in the same
# request (one request = one COM8 session, requests are served one at a time). The command bits D599/D600, the HMI
# page D730 and the date D1087..D1089 are written only by 'pulse' / Write-HmiContext below, never on request;
# passwords D1682..D1687 and the SHA-256 handshake D1203..D1211 never.
# 1.5.1: verified fresh pulses and D600.6 recovery before accepting requests after restart.
# 1.5.2 (23.11.2026): 'reset_counters' - the only path that writes the PLC counters: exactly 0 to D645..646 (B.Finish,
# done in the bundle) and/or D649..650 (T.Finish, done in total), only while the machine stands (D766 in
# $script:ResetStates), with D645..650 read before and after (D647..648 = history, never written). 'write' still refuses
# D645..650: the counters never enter $script:allowedWrite.
$script:MbdVersion = '1.5.2'
$script:ResetCounters = @(645, 649)
$script:ResetStates = @(100, 102, 106, 107)
$script:allowedWrite = @(@(20, 20), @(22, 28), @(32, 53), @(67, 72), @(74, 74), @(100, 104), @(120, 123), @(197, 197), @(199, 199), @(391, 395), @(401, 402), @(404, 415), @(423, 423), @(430, 432), @(440, 484), @(658, 659), @(940, 969), @(1703, 1705), @(1722, 1723), @(1750, 1753))
$script:allowedBits = @{ 132 = @(0); 400 = @(0); 604 = @(0); 422 = @(0); 434 = @(0); 435 = @(0); 436 = @(0) }
$script:allowedPulse = @{ 599 = @(0, 2); 600 = @(1, 4, 6) }

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

function Write-MbLog([string]$s) {
    $line = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff') + ' ' + $s
    try { [void](Invoke-LogRotation $script:log); Add-Content -Path $script:log -Value $line -Encoding UTF8 } catch { }
}

function Get-Crc([byte[]]$b, [int]$n) {
    $c = 0xFFFF
    for ($i = 0; $i -lt $n; $i++) {
        $c = $c -bxor $b[$i]
        for ($k = 0; $k -lt 8; $k++) {
            if ($c -band 1) { $c = [int][Math]::Floor($c / 2) -bxor 0xA001 } else { $c = [int][Math]::Floor($c / 2) }
        }
    }
    return $c
}

function Test-Busy {
    $p = Get-Process | Where-Object { $_.ProcessName -like 'PXS_HMI*' -or $_.ProcessName -like '*modbus*' -or $_.ProcessName -like '*auto mark*' -or $_.ProcessName -like 'EzCad*' }
    if ($p) { return (($p | ForEach-Object { $_.ProcessName }) -join ',') }
    return ''
}

# --- serial layer -------------------------------------------------------------------------------------------
$script:mockR = @{}
function Invoke-Xfer([byte[]]$body, [int]$exp) {
    if ($Mock) {
        $a = [int]$body[2] * 256 + [int]$body[3]; $n = [int]$body[4] * 256 + [int]$body[5]
        if ($body[1] -eq 0x10) {
            for ($i = 0; $i -lt $n; $i++) { $script:mockR[$a + $i] = [int]$body[7 + 2 * $i] * 256 + [int]$body[8 + 2 * $i] }
            $r = New-Object byte[] 8; [Array]::Copy($body, $r, 6)
        } else {
            $r = New-Object byte[] (5 + 2 * $n); $r[0] = 1; $r[1] = 3; $r[2] = [byte](2 * $n)
            for ($i = 0; $i -lt $n; $i++) { $v = [int]$script:mockR[$a + $i]; $r[3 + 2 * $i] = [byte][Math]::Floor($v / 256); $r[4 + 2 * $i] = [byte]($v -band 255) }
        }
        $c = Get-Crc $r ($r.Length - 2); $r[$r.Length - 2] = [byte]($c -band 255); $r[$r.Length - 1] = [byte][Math]::Floor($c / 256)
        return ,$r
    }
    $f = New-Object byte[] ($body.Length + 2)
    [Array]::Copy($body, $f, $body.Length)
    $c = Get-Crc $body $body.Length
    $f[$body.Length] = [byte]($c -band 255); $f[$body.Length + 1] = [byte][Math]::Floor($c / 256)
    $script:sp.DiscardInBuffer()
    $script:sp.Write($f, 0, $f.Length)
    $buf = New-Object byte[] 300; $got = 0
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($got -lt $exp -and $sw.ElapsedMilliseconds -lt 500) {
        if ($script:sp.BytesToRead -gt 0) { $got += $script:sp.Read($buf, $got, [Math]::Min($script:sp.BytesToRead, 300 - $got)) }
        else { Start-Sleep -Milliseconds 5 }
        if ($got -ge 5 -and ($buf[1] -band 0x80)) { break }
    }
    $r = New-Object byte[] $got
    [Array]::Copy($buf, $r, $got)
    return ,$r
}

function Test-Frame([byte[]]$r) {
    if ($r.Length -lt 5) { return $false }
    $c = Get-Crc $r ($r.Length - 2)
    return ($r[$r.Length - 2] -eq ($c -band 255)) -and ($r[$r.Length - 1] -eq [Math]::Floor($c / 256))
}

function Read-Regs([int]$a, [int]$n) {
    if ($n -lt 1 -or $n -gt 120) { throw "count must be 1..120" }
    $body = [byte[]](1, 3, [byte][Math]::Floor($a / 256), [byte]($a -band 255), 0, [byte]$n)
    for ($t = 1; $t -le 3; $t++) {
        $r = Invoke-Xfer $body (5 + 2 * $n)
        if ($r.Length -eq (5 + 2 * $n) -and (Test-Frame $r) -and $r[1] -eq 3) {
            $w = New-Object int[] $n
            for ($i = 0; $i -lt $n; $i++) { $w[$i] = [int]$r[3 + 2 * $i] * 256 + [int]$r[4 + 2 * $i] }
            return ,$w
        }
        if ($r.Length -ge 5 -and ($r[1] -band 0x80) -and (Test-Frame $r)) { throw ('modbus exception ' + $r[2] + ' reading D' + $a) }
    }
    throw ('no valid reply reading D' + $a)
}

function Write-Regs([int]$a, [int[]]$vals) {
    $n = $vals.Length
    if ($n -lt 1 -or $n -gt 60) { throw "values must be 1..60" }
    $body = New-Object byte[] (7 + 2 * $n)
    $body[0] = 1; $body[1] = 0x10; $body[2] = [byte][Math]::Floor($a / 256); $body[3] = [byte]($a -band 255)
    $body[4] = 0; $body[5] = [byte]$n; $body[6] = [byte](2 * $n)
    for ($i = 0; $i -lt $n; $i++) {
        $v = [int]$vals[$i]
        if ($v -lt 0 -or $v -gt 65535) { throw ('value out of 0..65535 at D' + ($a + $i)) }
        $body[7 + 2 * $i] = [byte][Math]::Floor($v / 256); $body[8 + 2 * $i] = [byte]($v -band 255)
    }
    $r = Invoke-Xfer $body 8
    $ok = ($r.Length -eq 8) -and (Test-Frame $r)
    if ($ok) { for ($i = 0; $i -lt 6; $i++) { if ($r[$i] -ne $body[$i]) { $ok = $false } } }
    if (-not $ok) {
        if ($r.Length -ge 5 -and ($r[1] -band 0x80) -and (Test-Frame $r)) { throw ('modbus exception ' + $r[2] + ' writing D' + $a) }
        throw ('no valid echo writing D' + $a)
    }
}

function Test-WriteAllowed([int]$a, [int]$n) {
    for ($x = $a; $x -lt $a + $n; $x++) {
        $okOne = $false
        foreach ($rg in $script:allowedWrite) { if ($x -ge $rg[0] -and $x -le $rg[1]) { $okOne = $true } }
        if (-not $okOne) { return $false }
    }
    return $true
}

function Write-HmiContext {
    $now = Get-Date
    Write-Regs 730 @(13)
    Write-Regs 1087 @($now.Day, $now.Month, $now.Year)
}

function Get-U32($w, [int]$i) { return [long]$w[$i] + 65536 * [long]$w[$i + 1] }
function Get-I32($w, [int]$i) { $v = Get-U32 $w $i; if ($v -ge 2147483648) { $v -= 4294967296 }; return $v }

function Get-Status {
    $st = Read-Regs 766 1; $al = Read-Regs 782 1; $md = Read-Regs 20 1; $ts = Read-Regs 22 2; $tf = Read-Regs 649 2
    $wire = Read-Regs 32 12; $pr = Read-Regs 400 1; $pos = Read-Regs 660 14
    $names = @{ 100 = 'Please Reset'; 101 = 'Resetting'; 102 = 'Ready'; 103 = 'Running'; 104 = 'Paused'; 105 = 'E-stop'; 106 = 'Batch finish'; 107 = 'Total finish' }
    $axes = @('roller_f', 'roller_b', 'cutter', 'pipe', 'clamp_f', 'clamp_b', 'shift')
    $p = @{}
    for ($i = 0; $i -lt 7; $i++) { $p[$axes[$i]] = (Get-I32 $pos (2 * $i)) / 100.0 }
    return @{
        state = $st[0]; state_name = $names[[int]$st[0]]; alarm = $al[0]; mode = $md[0]
        total_set = Get-U32 $ts 0; total_finish = Get-U32 $tf 0
        length_mm = (Get-U32 $wire 0) / 100.0; strip_head_mm = (Get-U32 $wire 2) / 100.0; pull_head_mm = (Get-U32 $wire 4) / 100.0
        strip_tail_mm = (Get-U32 $wire 6) / 100.0; pull_tail_mm = (Get-U32 $wire 8) / 100.0
        diameter_mm = $wire[10] / 100.0; blade_back_mm = $wire[11] / 100.0; print_word = $pr[0]; positions_mm = $p
    }
}

function Invoke-Pulse([int]$a, [int]$bit, [int]$hold) {
    if (-not $script:allowedPulse.ContainsKey($a) -or -not ($script:allowedPulse[$a] -contains $bit)) { throw ('pulse not allowed: D' + $a + '.' + $bit) }
    if ($hold -lt 100) { $hold = 100 }
    if ($hold -gt 3000) { $hold = 3000 }
    $mask = [int][Math]::Pow(2, $bit)
    $orig = (Read-Regs $a 1)[0]
    if (($orig -band $mask) -ne 0) { throw ('pulse bit already high: D' + $a + '.' + $bit) }
    $set = $false; $cleared = $false
    $high = $null; $after = $null
    try {
        Write-Regs $a @($orig -bor $mask)
        $high = (Read-Regs $a 1)[0]
        $set = (($high -band $mask) -ne 0)
        if (-not $set) { throw 'pulse high readback not confirmed' }
        $sw = [Diagnostics.Stopwatch]::StartNew(); $lastCtx = 0
        while ($sw.ElapsedMilliseconds -lt $hold) {
            if ($sw.ElapsedMilliseconds - $lastCtx -ge 700) { try { Write-HmiContext } catch { }; $lastCtx = $sw.ElapsedMilliseconds }
            Start-Sleep -Milliseconds 50
        }
    } finally {
        for ($t = 1; $t -le 3 -and -not $cleared; $t++) {
            try {
                $current = (Read-Regs $a 1)[0]
                Write-Regs $a @($current -band (65535 - $mask))
                $after = (Read-Regs $a 1)[0]
                $cleared = (($after -band $mask) -eq 0)
            } catch { Start-Sleep -Milliseconds 100 }
        }
    }
    if (-not $cleared) { throw 'pulse clear readback not confirmed' }
    return @{ addr = $a; bit = $bit; hold_ms = $hold; before = $orig; high = $high; set = $set; cleared = $cleared; after = $after }
}

# Recovery may only lower D600.6; it does not expose command words through setbit/write.
function Clear-LengthCorrectionBit {
    $before = (Read-Regs 600 1)[0]
    if (($before -band 64) -ne 0) {
        Write-MbLog 'RECOVERY D600.6 left high; clearing'
        Write-Regs 600 @($before -band (65535 - 64))
        $after = (Read-Regs 600 1)[0]
        if (($after -band 64) -ne 0) { throw 'D600.6 recovery clear not confirmed' }
        Write-MbLog 'RECOVERY D600.6 confirmed low'
    } else { $after = $before }
    return @{ before = $before; after = $after }
}

function Test-BitAllowed([int]$a, [int]$bit) {
    return ($script:allowedBits.ContainsKey($a) -and ($script:allowedBits[$a] -contains $bit))
}

# Read-modify-write of one switch bit: only that bit changes, the word is read in the same COM8 session.
function Invoke-SetBit([int]$a, [int]$bit, [int]$value) {
    if (-not (Test-BitAllowed $a $bit)) { throw ('setbit not allowed: D' + $a + '.' + $bit) }
    $mask = [int][Math]::Pow(2, $bit)
    $orig = (Read-Regs $a 1)[0]
    if ($value) { $word = $orig -bor $mask } else { $word = $orig -band (65535 - $mask) }
    if ($word -ne $orig) { Write-Regs $a @($word) }
    return @{ addr = $a; bit = $bit; value = $value; before = $orig; written = $word; readback = [int[]](Read-Regs $a 1) }
}

# 'reset_counters' request -> the counter words to zero (645 and/or 649). Every value must be the integer 0, exactly two
# per counter (a u32), each counter at most once; anything else is refused before the port opens.
function Get-ResetTargets($Blocks) {
    $targets = @()
    $list = @($Blocks | Where-Object { $_ -ne $null })
    if ($list.Count -lt 1 -or $list.Count -gt 2) { throw 'reset_counters refused: one or two counters' }
    foreach ($block in $list) {
        if (-not ($block -is [System.Collections.IDictionary])) { throw 'reset_counters refused: bad block' }
        $a = $block['addr']
        if (-not ($a -is [int]) -or $script:ResetCounters -notcontains $a) { throw ('reset_counters refused: only D645 / D649, not D' + $a) }
        if ($targets -contains $a) { throw ('reset_counters refused: D' + $a + ' twice') }
        $values = @($block['values'])
        if ($values.Count -ne 2) { throw ('reset_counters refused: D' + $a + ' needs two words') }
        foreach ($v in $values) { if (-not ($v -is [int]) -or $v -ne 0) { throw ('reset_counters refused: only 0 may be written to D' + $a) } }
        $targets += $a
    }
    return ,$targets
}

# Zero the counters in one COM8 session: state check, D645..650 before, the zero writes, D645..650 after.
function Invoke-ResetCounters([int[]]$Targets) {
    $state = (Read-Regs 766 1)[0]
    if ($script:ResetStates -notcontains $state) { throw ('reset_counters refused: state ' + $state) }
    $before = [int[]](Read-Regs 645 6)
    foreach ($a in $Targets) { Write-Regs $a @(0, 0) }
    $after = [int[]](Read-Regs 645 6)
    return @{ state = $state; addr = 645; before = $before; readback = $after; written = [int[]]$Targets }
}

function Invoke-Request($req) {
    $cmd = [string]$req['cmd']
    if (@('read', 'write', 'pulse', 'status', 'setbit', 'version', 'recover_correction', 'reset_counters') -notcontains $cmd) { throw ('unknown cmd ' + $cmd) }
    if ($cmd -eq 'reset_counters') { $targets = Get-ResetTargets $req['blocks'] }
    if ($cmd -eq 'version') { return @{ version = $script:MbdVersion } }
    if ($cmd -eq 'write') {
        $a = [int]$req['addr']; $vals = [int[]]@($req['values'] | ForEach-Object { [int]$_ })
        if (-not (Test-WriteAllowed $a $vals.Length)) { throw ('write not allowed: D' + $a + ' x' + $vals.Length) }
    }
    if ($cmd -eq 'setbit') {
        $a = [int]$req['addr']; $bit = [int]$req['bit']; $value = [int]$req['value']
        if (-not (Test-BitAllowed $a $bit)) { throw ('setbit not allowed: D' + $a + '.' + $bit) }
        if ($value -ne 0 -and $value -ne 1) { throw ('setbit value must be 0 or 1') }
    }
    if (-not $Mock) {
        $busy = Test-Busy
        if ($busy) { throw ('busy: ' + $busy + ' is running, bus refused') }
        $script:sp = New-Object IO.Ports.SerialPort 'COM8', 38400, ([IO.Ports.Parity]::None), 8, ([IO.Ports.StopBits]::One)
        $script:sp.ReadTimeout = 500; $script:sp.WriteTimeout = 500
        $script:sp.Open()
    }
    try {
        switch ($cmd) {
            'recover_correction' { return (Clear-LengthCorrectionBit) }
            'read' { return @{ addr = [int]$req['addr']; values = [int[]](Read-Regs ([int]$req['addr']) ([int]$req['count'])) } }
            'status' { Write-HmiContext; return (Get-Status) }
            'write' {
                Write-HmiContext
                Write-Regs $a $vals
                return @{ addr = $a; written = [int[]]$vals; readback = [int[]](Read-Regs $a $vals.Length) }
            }
            'pulse' { Write-HmiContext; return (Invoke-Pulse ([int]$req['addr']) ([int]$req['bit']) ([int]$req['hold_ms'])) }
            'setbit' { Write-HmiContext; return (Invoke-SetBit $a $bit $value) }
            'reset_counters' { Write-HmiContext; return (Invoke-ResetCounters $targets) }
        }
    } finally {
        if ($script:sp) { try { $script:sp.Close(); $script:sp.Dispose() } catch { }; $script:sp = $null }
    }
}

# Own JSON writer: JavaScriptSerializer on PowerShell 2 trips over PSObject-wrapped values.
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
    if ($v -is [int] -or $v -is [long] -or $v -is [byte] -or $v -is [int16] -or $v -is [uint16] -or $v -is [uint32] -or $v -is [double] -or $v -is [decimal] -or $v -is [single]) {
        return ([double]$v).ToString('R', [Globalization.CultureInfo]::InvariantCulture)
    }
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

function Start-Service2 {
    # Refuse to serve requests until restart recovery has a confirmed low command bit.
    # Invoke-Request retains the HMI/COM8 ownership guard and closes the port on failure.
    [void](Invoke-Request @{ cmd = 'recover_correction' })
    $bind = [System.Net.IPAddress]::Any
    $allowed = @($Allow -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($LocalOnly) { $bind = [System.Net.IPAddress]::Loopback; $allowed = @('127.0.0.1') }
    $listener = New-Object System.Net.Sockets.TcpListener ($bind, $Port)
    $listener.Start()
    Write-MbLog ('mbd ' + $script:MbdVersion + ' listening on ' + $bind + ':' + $Port + ' allow ' + ($allowed -join ',') + ' mock=' + [bool]$Mock)
    while ($true) {
        $client = $listener.AcceptTcpClient()
        $remote = $client.Client.RemoteEndPoint.Address.ToString()
        try {
            $client.ReceiveTimeout = 5000; $client.SendTimeout = 5000
            $stream = $client.GetStream()
            $enc = New-Object System.Text.UTF8Encoding $false
            $writer = New-Object System.IO.StreamWriter($stream, $enc); $writer.AutoFlush = $true
            if ($allowed -notcontains $remote) {
                Write-MbLog ('REJECT ' + $remote)
                $writer.WriteLine('{"ok":false,"error":"forbidden"}')
                continue
            }
            $reader = New-Object System.IO.StreamReader($stream, $enc)
            $line = $reader.ReadLine()
            $sw = [Diagnostics.Stopwatch]::StartNew()
            Write-MbLog ('REQ ' + $remote + ' ' + $line)
            try {
                $req = $script:js.DeserializeObject($line)
                $res = Invoke-Request $req
                $out = ConvertTo-JsonLite @{ ok = $true; ms = $sw.ElapsedMilliseconds; result = $res }
            } catch {
                $out = ConvertTo-JsonLite @{ ok = $false; ms = $sw.ElapsedMilliseconds; error = $_.Exception.Message }
            }
            Write-MbLog ('RES ' + $out)
            $writer.WriteLine($out)
        } catch {
            Write-MbLog ('CONN ERROR ' + $_.Exception.Message)
        } finally {
            try { $client.Close() } catch { }
        }
    }
}

if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir | Out-Null }
# Dot-sourced (tests/mbd.Tests.ps1): functions only, no listener.
if ($script:Self -ne '.') {
    while ($true) {
        try { Start-Service2 } catch { Write-MbLog ('LISTENER ERROR ' + $_.Exception.Message); Start-Sleep -Seconds 5 }
    }
}

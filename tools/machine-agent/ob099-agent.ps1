param(
    [string]$Server = 'http://192.168.1.85',
    [ValidateSet('', '1', '2')][string]$Unit = '',
    [string]$TokenPath = 'D:\fraxis-ob099\agent.token',
    [string]$StatePath = 'D:\fraxis-ob099\state.json',
    [string]$LogPath = 'D:\fraxis-ob099\agent.log',
    [string]$PlcHost = '192.168.250.1',
    [int]$PlcPort = 9600,
    [int]$PlcNode = 1,
    [int]$PcNode = 115,
    [int]$PlcTimeoutMs = 1500,
    [string]$CrimpLogDir = 'D:\BZW\logs',
    [string]$MysqlExe = '',
    [string]$MysqlCredPath = 'D:\fraxis-probe\erp_ro.txt',
    [int]$PollMs = 2000,
    [int]$HeartbeatSec = 5,
    [int]$EventsSec = 5,
    [int]$AlarmsSec = 30,
    [switch]$Once
)
# ERP agent of OB.099, the BZW servo crimp press (ERP_NEXT, section "Stanki", slice 5): READ ONLY, stage 0 of
# docs/data/ob099-erp-control-design-2026-11-18.md. Runs on the press PC (DESKTOP-K6M7O0U, .82, Windows 10,
# PowerShell 5.1) next to BZW2.5, which stays the only program that controls the press.
#
# Reads, never writes:
#  1. PLC Omron CP2E over FINS/UDP (192.168.250.1:9600): only command 01 01 (memory area read), an allow-list of words
#     -> POST /api/production/machines/agent/report (snapshot, as the OB.019 bridge; ERP binds token + PC on first use);
#  2. the per-crimp log of BZW2.5 D:\BZW\logs\yyyyMMdd.txt, incrementally from the byte offset in $StatePath;
#  3. the alarm journal `alarmrecord` of MySQL bzw2.5 as erp_ro (SELECT only; password file on the PC, never in git)
#  -> POST /api/production/machines/agent/events. The offset moves only after ERP accepted the batch, so the log file
#     itself is the queue: no line is lost while the network or ERP is down, a resend is deduplicated by ERP.
# API token scope machine:report. The file is ASCII on purpose (OB.099 in Cyrillic is built from code points).
$ErrorActionPreference = 'Stop'
$script:AgentVersion = '1.0.1'
$script:Proto = 1
$script:Inventory = [string][char]0x041E + [string][char]0x0411 + '.099.' + $Unit

# FINS memory area codes (word access): DM, HR, WR.
$script:Areas = @{ 'D' = 0x82; 'H' = 0xB2; 'W' = 0xB1 }
# Blocks read each cycle: @(area, first word, count). Words outside the allow-list are read but never sent.
$script:Blocks = @(
    @('D', 5005, 43),   # D5005..D5047: screen, crimp height D5006 x0.001 mm, tolerance D5032, height limits D5046/D5047
    @('D', 5100, 44),   # DT5100..DT5143: plan/done counters, blade life DT5108/DT5112 and wear DT5110/DT5114
    @('H', 60, 2),      # H60, H61: options (H60.02 CFM control, H60.07 work mode)
    @('W', 80, 5),      # W80 alarms, W83/W84 statuses
    @('D', 5500, 50)    # D5500..D5549: job strings product, wire, terminal, applicator, equipment no.
)
# Mirrors ALLOWED of backend/app/modules/production/ob099.py (ERP refuses any other word, 422).
$script:Allowed = @('D5005', 'D5006', 'D5008', 'D5010', 'D5012', 'D5013', 'D5021', 'D5022', 'D5032', 'D5036', 'D5046', 'D5047', 'D5100..D5115', 'D5118', 'D5119', 'D5124', 'D5125', 'D5128', 'D5129', 'D5132', 'D5133', 'D5142', 'D5143', 'D5500..D5549', 'H60', 'H61', 'W80', 'W81', 'W82', 'W83', 'W84')
$script:AllowedSet = @{}
foreach ($item in $script:Allowed) {
    if ($item -match '^D(\d+)\.\.D(\d+)$') { foreach ($a in ([int]$Matches[1])..([int]$Matches[2])) { $script:AllowedSet['D' + $a] = $true } }
    else { $script:AllowedSet[$item] = $true }
}
$script:Sid = 0
$script:TextMax = 64
$script:ScanMax = 128
$script:BatchMax = 200
$script:ReadChunk = 262144
# One line of the BZW2.5 log (fields separated by three spaces):
# equipment no.   product   wire   terminal   applicator   [scan code]   peak   yyyy/MM/dd <weekday> HH:mm:ss   OK|NG
$script:LineRe = '^(?<head>.*?)\s+(?<peak>\S+)\s+(?<date>\d{4}/\d{1,2}/\d{1,2})\s+\S+\s+(?<time>\d{1,2}:\d{2}:\d{2})\s+(?<status>OK|NG)\s*$'
$script:SaveTimeRe = '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$'
$script:Invariant = [Globalization.CultureInfo]::InvariantCulture

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

function Write-AgentLog([string]$Text) {
    try {
        [void](Invoke-LogRotation $LogPath)
        Add-Content -Path $LogPath -Value ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $Text) -Encoding UTF8
    } catch { }
}

# --- PLC: FINS/UDP, memory area read only ---

function Assert-FinsReadOnly([byte[]]$Frame) {
    if ($Frame.Length -lt 18 -or $Frame[10] -ne 1 -or $Frame[11] -ne 1) {
        throw 'agent is read-only: only FINS 01 01 (memory area read) may be sent to the PLC'
    }
}

function New-FinsRead([string]$Area, [int]$Address, [int]$Count, [int]$Sid) {
    if (-not $script:Areas.ContainsKey($Area)) { throw ('unknown FINS area ' + $Area) }
    if ($Count -lt 1 -or $Count -gt 499) { throw ('FINS read of ' + $Count + ' words') }
    $code = [int]$script:Areas[$Area]
    [byte[]]$frame = @(0x80, 0x00, 0x02, 0x00, ($PlcNode -band 0xFF), 0x00, 0x00, ($PcNode -band 0xFF), 0x00, ($Sid -band 0xFF),
        0x01, 0x01, $code, (($Address -shr 8) -band 0xFF), ($Address -band 0xFF), 0x00, (($Count -shr 8) -band 0xFF), ($Count -band 0xFF))
    Assert-FinsReadOnly $frame
    return ,$frame
}

function Send-FinsFrame([byte[]]$Frame) {
    Assert-FinsReadOnly $Frame
    $udp = New-Object System.Net.Sockets.UdpClient
    try {
        $udp.Client.ReceiveTimeout = $PlcTimeoutMs
        $udp.Connect($PlcHost, $PlcPort)
        [void]$udp.Send($Frame, $Frame.Length)
        $remote = New-Object System.Net.IPEndPoint -ArgumentList ([System.Net.IPAddress]::Any), 0
        return ,$udp.Receive([ref]$remote)
    } finally {
        $udp.Close()
    }
}

function Read-FinsWords([byte[]]$Reply, [int]$Sid, [int]$Count) {
    if ($Reply.Length -lt 14) { throw (New-Object System.InvalidOperationException 'FINS: short reply') }
    if ($Reply[9] -ne ($Sid -band 0xFF) -or $Reply[10] -ne 1 -or $Reply[11] -ne 1) {
        throw (New-Object System.InvalidOperationException 'FINS: reply to another request')
    }
    if (($Reply[12] -band 0x7F) -ne 0 -or ($Reply[13] -band 0x3F) -ne 0) {
        throw (New-Object System.InvalidOperationException ('FINS: end code ' + $Reply[12].ToString('X2') + $Reply[13].ToString('X2')))
    }
    if ($Reply.Length -ne 14 + 2 * $Count) { throw (New-Object System.InvalidOperationException 'FINS: wrong reply length') }
    $words = New-Object 'int[]' $Count
    for ($i = 0; $i -lt $Count; $i++) { $words[$i] = ([int]$Reply[14 + 2 * $i] -shl 8) -bor [int]$Reply[15 + 2 * $i] }
    return ,$words
}

function Read-PlcSnapshot {
    $registers = @{}
    try {
        foreach ($block in $script:Blocks) {
            $script:Sid = ($script:Sid + 1) % 256
            $sid = $script:Sid
            $values = Read-FinsWords (Send-FinsFrame (New-FinsRead $block[0] $block[1] $block[2] $sid)) $sid $block[2]
            for ($i = 0; $i -lt $block[2]; $i++) {
                $name = $block[0] + ($block[1] + $i)
                if ($script:AllowedSet.ContainsKey($name)) { $registers[$name] = $values[$i] }
            }
        }
        return @{ link = 'ok'; error = ''; registers = $registers }
    } catch {
        # Timeout, unreachable network 192.168.250.x, a FINS error: ERP shows "PLC does not answer".
        return @{ link = 'no_plc'; error = ('FINS: ' + $_.Exception.Message); registers = @{} }
    }
}

# --- per-crimp log ---

function Limit-Text([string]$Text, [int]$Max) {
    $value = ($Text -replace '\s+', ' ').Trim()
    if ($value -eq '-') { $value = '' }
    if ($value.Length -gt $Max) { $value = $value.Substring(0, $Max) }
    return $value
}

function ConvertTo-IsoLocal([datetime]$Local) {
    $offset = [TimeZoneInfo]::Local.GetUtcOffset($Local)
    return (New-Object DateTimeOffset ($Local, $offset)).ToString('yyyy-MM-ddTHH:mm:sszzz', $script:Invariant)
}

# One log line -> a crimp record for ERP, or $null when the line is not a crimp line.
function ConvertFrom-CrimpLine([string]$Line, [string]$Key) {
    $text = $Line.TrimStart([char]0xFEFF).TrimEnd()
    if ($text -notmatch $script:LineRe) { return $null }
    $found = $Matches
    $head = @($found['head'] -split '   ' | ForEach-Object { $_.Trim() })
    if ($head.Count -gt 6) { return $null }
    while ($head.Count -lt 6) { $head += '' }
    $moment = [datetime]::MinValue
    if (-not [datetime]::TryParseExact($found['date'] + ' ' + $found['time'], 'yyyy/M/d H:mm:ss', $script:Invariant,
            [Globalization.DateTimeStyles]::None, [ref]$moment)) { return $null }
    $peak = $null
    $number = 0.0
    if ([double]::TryParse($found['peak'], [Globalization.NumberStyles]::Float, $script:Invariant, [ref]$number) -and $number -ge 0) { $peak = $number }
    return @{
        key = $Key; at = (ConvertTo-IsoLocal $moment); result = $found['status']; peak = $peak
        equipment_no = (Limit-Text $head[0] $script:TextMax); product = (Limit-Text $head[1] $script:TextMax)
        wire = (Limit-Text $head[2] $script:TextMax); terminal = (Limit-Text $head[3] $script:TextMax)
        applicator = (Limit-Text $head[4] $script:TextMax); scan_code = (Limit-Text $head[5] $script:ScanMax)
    }
}

function Get-LogDay([datetime]$Moment) { return $Moment.ToString('yyyyMMdd', $script:Invariant) }

# The next complete lines of the log after the state's offset (at most $Max crimps). The caller saves the returned
# day/offset only after ERP accepted the crimps. When the day's file is read to its end and a later day began, the
# result moves to the next file with no crimps.
function Read-CrimpBatch($State, [datetime]$Now, [int]$Max = $script:BatchMax) {
    $day = [string]$State.day; $offset = [long]$State.offset
    $path = Join-Path $CrimpLogDir ($day + '.txt')
    $crimps = @(); $skipped = 0; $consumed = $offset
    if (Test-Path -LiteralPath $path) {
        $stream = New-Object System.IO.FileStream ($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        try {
            if ($stream.Length -gt $offset) {
                [void]$stream.Seek($offset, [IO.SeekOrigin]::Begin)
                $buffer = New-Object 'byte[]' ([int][Math]::Min($stream.Length - $offset, $script:ReadChunk))
                $read = $stream.Read($buffer, 0, $buffer.Length)
                $start = 0
                for ($i = 0; $i -lt $read -and $crimps.Count -lt $Max; $i++) {
                    if ($buffer[$i] -ne 10) { continue }
                    $line = [Text.Encoding]::UTF8.GetString($buffer, $start, $i - $start)
                    $crimp = ConvertFrom-CrimpLine $line ($day + '@' + ($offset + $start))
                    if ($crimp) { $crimps += $crimp } elseif ($line.Trim()) { $skipped++ }
                    $start = $i + 1
                    $consumed = $offset + $start
                }
            }
        } finally {
            $stream.Close()
        }
    }
    $today = Get-LogDay $Now
    if ($crimps.Count -eq 0 -and $consumed -eq $offset -and $day -lt $today) {
        $later = @(Get-ChildItem -LiteralPath $CrimpLogDir -Filter '*.txt' -ErrorAction SilentlyContinue |
            Where-Object { $_.BaseName -match '^\d{8}$' -and $_.BaseName -gt $day -and $_.BaseName -le $today } |
            Sort-Object BaseName | Select-Object -First 1)
        $next = if ($later.Count) { $later[0].BaseName } else { $today }
        return @{ crimps = @(); skipped = 0; day = $next; offset = 0L }
    }
    return @{ crimps = $crimps; skipped = $skipped; day = $day; offset = [long]$consumed }
}

# --- MySQL alarm journal (SELECT only) ---

function Assert-ReadOnlySql([string]$Sql) {
    if ($Sql -notmatch '^\s*SELECT\s' -or $Sql.Contains(';')) { throw 'agent is read-only: only one SELECT may be sent to MySQL' }
}

function Read-MysqlCredential([string]$Path) {
    $user = 'erp_ro'; $password = ''
    $lines = @([IO.File]::ReadAllLines($Path) | Where-Object { $_.Trim() })
    foreach ($line in $lines) {
        if ($line -match '^\s*(user|username|login)\s*[=:]\s*(.+?)\s*$') { $user = $Matches[2] }
        elseif ($line -match '^\s*(password|pass|pwd)\s*[=:]\s*(.+?)\s*$') { $password = $Matches[2] }
    }
    if (-not $password -and $lines.Count -eq 1) { $password = $lines[0].Trim() }
    if (-not $password) { throw ('no MySQL password in ' + $Path) }
    return @{ user = $user; password = $password }
}

# The press PC of OB.099.1 has MySQL 8.0 on D: (D:\Program Files\MySQL\MySQL Server 8.0\bin, checked 19.11.2026).
function Find-MysqlExe {
    if ($MysqlExe) { return $MysqlExe }
    foreach ($drive in 'C', 'D') {
        $found = @(Get-ChildItem ($drive + ':\Program Files\MySQL\*\bin\mysql.exe') -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($found.Count) { return $found[0].FullName }
    }
    throw 'mysql.exe not found (set -MysqlExe)'
}

# Rows of one SELECT as arrays of fields (mysql --batch: tab-separated, \t \n \\ escaped).
function Invoke-MysqlSelect([string]$Sql) {
    Assert-ReadOnlySql $Sql
    $credential = Read-MysqlCredential $MysqlCredPath
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = (Find-MysqlExe)
    $info.Arguments = '--host=127.0.0.1 --port=3306 --user=' + $credential['user'] + ' --batch --skip-column-names' +
        ' --default-character-set=utf8mb4 --database=bzw2.5 --execute="SET SESSION TRANSACTION READ ONLY; ' + $Sql + '"'
    $info.EnvironmentVariables['MYSQL_PWD'] = $credential['password']
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = New-Object System.Text.UTF8Encoding $false
    $process = [System.Diagnostics.Process]::Start($info)
    $out = $process.StandardOutput.ReadToEnd(); $err = $process.StandardError.ReadToEnd()
    if (-not $process.WaitForExit(15000)) { $process.Kill(); throw 'MySQL: no answer in 15 s' }
    if ($process.ExitCode -ne 0) { throw ('MySQL: ' + (($err -split "`n" | Where-Object { $_ -match 'ERROR' } | Select-Object -First 1))) }
    $rows = @()
    foreach ($line in ($out -split "`r?`n")) {
        if (-not $line) { continue }
        $fields = @($line -split "`t" | ForEach-Object { $_.Replace('\t', ' ').Replace('\n', ' ').Replace('\\', '\') })
        $rows += ,$fields
    }
    return ,$rows
}

# New lines of alarmrecord after the state's SaveTime -> alarm records for ERP and the new SaveTime.
function Read-AlarmBatch([string]$After) {
    if ($After -notmatch $script:SaveTimeRe) { throw ('bad SaveTime in the state: ' + $After) }
    $rows = Invoke-MysqlSelect ("SELECT SaveTime, AlarmInfo FROM alarmrecord WHERE SaveTime > '" + $After + "' ORDER BY SaveTime LIMIT " + $script:BatchMax)
    $alarms = @(); $last = $After
    foreach ($row in $rows) {
        if ($row.Count -lt 2) { continue }
        $saved = ([string]$row[0]).Trim()
        if ($saved.Length -gt 19) { $saved = $saved.Substring(0, 19) }
        if ($saved -notmatch $script:SaveTimeRe) { continue }
        $last = $saved
        $text = Limit-Text ([string]$row[1]) 300
        if (-not $text) { continue }
        $moment = [datetime]::ParseExact($saved, 'yyyy-MM-dd HH:mm:ss', $script:Invariant)
        $alarms += @{ key = ($saved + '|' + $text); at = (ConvertTo-IsoLocal $moment); text = $text }
    }
    return @{ alarms = $alarms; after = $last }
}

# --- state, report ---

function Read-AgentState([datetime]$Now) {
    $state = @{ day = (Get-LogDay $Now); offset = 0L; alarm_after = $Now.Date.AddSeconds(-1).ToString('yyyy-MM-dd HH:mm:ss', $script:Invariant) }
    try {
        if (Test-Path -LiteralPath $StatePath) {
            $saved = [IO.File]::ReadAllText($StatePath) | ConvertFrom-Json
            if ([string]$saved.day -match '^\d{8}$') { $state.day = [string]$saved.day; $state.offset = [long]$saved.offset }
            if ([string]$saved.alarm_after -match $script:SaveTimeRe) { $state.alarm_after = [string]$saved.alarm_after }
        }
    } catch { Write-AgentLog ('state unreadable, starting from today: ' + $_.Exception.Message) }
    return $state
}

function Save-AgentState($State) {
    $temp = $StatePath + '.tmp'
    [IO.File]::WriteAllText($temp, (ConvertTo-Json @{ day = $State.day; offset = $State.offset; alarm_after = $State.alarm_after } -Compress))
    Move-Item -Force -LiteralPath $temp -Destination $StatePath
}

function Get-MachineId {
    try { return [string](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid } catch { return '' }
}

function Get-LanIp {
    try {
        foreach ($address in [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName())) {
            $text = $address.ToString()
            if ($address.AddressFamily -eq 'InterNetwork' -and -not $text.StartsWith('127.') -and -not $text.StartsWith('192.168.250.')) { return $text }
        }
    } catch { }
    return ''
}

function New-Report($Snapshot, [string]$Boot, [long]$Seq) {
    return @{
        proto = $script:Proto; inventory = $script:Inventory; machine_id = (Get-MachineId); pc_name = $env:COMPUTERNAME
        ip_lan = (Get-LanIp); bridge_version = $script:AgentVersion; service_version = 'FINS/UDP'
        boot = $Boot; seq = $Seq; link = $Snapshot['link']; error = $Snapshot['error']; registers = $Snapshot['registers']
    }
}

function New-Events($Crimps, $Alarms) {
    return @{ proto = $script:Proto; inventory = $script:Inventory; machine_id = (Get-MachineId); crimps = @($Crimps); alarms = @($Alarms) }
}

function ConvertTo-Body($Value) { return (ConvertTo-Json $Value -Depth 6 -Compress) }

# POST JSON to ERP; returns the HTTP status (0 = no answer). Never throws.
function Send-Json([string]$Path, [string]$Body, [string]$Token) {
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $request = [System.Net.HttpWebRequest]::Create(($Server.TrimEnd('/')) + $Path)
        $request.Method = 'POST'; $request.ContentType = 'application/json; charset=utf-8'; $request.Timeout = 10000
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
            Write-AgentLog ('ERP ' + $code + ' ' + $Path + ' ' + $text)
            $failed.Close()
            return $code
        }
        Write-AgentLog ('ERP unreachable: ' + $_.Exception.Message)
        return 0
    } catch {
        Write-AgentLog ('ERP error: ' + $_.Exception.Message)
        return 0
    }
}

function Test-ShouldSend([string]$Fingerprint, [string]$LastFingerprint, [datetime]$LastSent, [datetime]$Now) {
    return ($Fingerprint -ne $LastFingerprint) -or (($Now - $LastSent).TotalSeconds -ge $HeartbeatSec)
}

# Send new crimps (and, when due, new alarms) until the log is read up; the state moves only on ERP's 200 (or a 422 of
# a batch ERP will never take, logged and skipped so one bad line cannot stop the press's records).
function Sync-Events($State, [string]$Token, [bool]$WithAlarms) {
    $alarmsDone = -not $WithAlarms
    for ($round = 0; $round -lt 20; $round++) {
        $batch = Read-CrimpBatch $State (Get-Date)
        if ($batch['skipped']) { Write-AgentLog ('skipped ' + $batch['skipped'] + ' non-crimp lines in ' + $batch['day']) }
        $alarmBatch = @{ alarms = @(); after = $State.alarm_after }
        if (-not $alarmsDone) {
            try { $alarmBatch = Read-AlarmBatch $State.alarm_after } catch { Write-AgentLog ('alarms: ' + $_.Exception.Message) }
            $alarmsDone = $alarmBatch['alarms'].Count -lt $script:BatchMax
        }
        $moved = ($batch['day'] -ne $State.day) -or ($batch['offset'] -ne $State.offset) -or ($alarmBatch['after'] -ne $State.alarm_after)
        if (-not $moved) { return $true }
        if ($batch['crimps'].Count -or $alarmBatch['alarms'].Count) {
            $code = Send-Json '/api/production/machines/agent/events' (ConvertTo-Body (New-Events $batch['crimps'] $alarmBatch['alarms'])) $Token
            if ($code -eq 422) { Write-AgentLog ('ERP refused records ' + $batch['day'] + ' from ' + $State.offset + ' to ' + $batch['offset'] + ', skipped') }
            elseif ($code -ne 200) { return $false }
        }
        $State.day = $batch['day']; $State.offset = $batch['offset']; $State.alarm_after = $alarmBatch['after']
        Save-AgentState $State
    }
    return $true
}

function Start-Agent {
    if (-not $Unit) { throw 'set -Unit 1 or 2: the inventory number of this press in the ERP equipment register (OB.099.1 / OB.099.2)' }
    if (-not (Test-Path $TokenPath)) { throw ('no token file ' + $TokenPath) }
    $token = ([IO.File]::ReadAllText($TokenPath)).Trim()
    $state = Read-AgentState (Get-Date)
    $boot = [guid]::NewGuid().ToString(); $seq = 0L; $bound = $false
    $last = ''; $lastSent = [datetime]::MinValue; $nextEvents = [datetime]::MinValue; $nextAlarms = [datetime]::MinValue
    Write-AgentLog ('start ' + $script:AgentVersion + ' server ' + $Server + ' PLC ' + $PlcHost + ':' + $PlcPort + ' log ' + $CrimpLogDir)
    while ($true) {
        $started = Get-Date
        $snapshot = Read-PlcSnapshot
        $fingerprint = $snapshot['link'] + '|' + $snapshot['error'] + '|' + (ConvertTo-Body $snapshot['registers'])
        if (Test-ShouldSend $fingerprint $last $lastSent $started) {
            $code = Send-Json '/api/production/machines/agent/report' (ConvertTo-Body (New-Report $snapshot $boot $seq)) $token
            if ($code -eq 200) { $last = $fingerprint; $lastSent = Get-Date; $seq++; $bound = $true }
            elseif ($code -eq 401 -or $code -eq 403) { Write-AgentLog 'ERP refused the agent (token or PC binding), retry in 60 s'; if (-not $Once) { Start-Sleep -Seconds 60 } }
        }
        if ($bound -and $started -ge $nextEvents) {
            $withAlarms = $started -ge $nextAlarms
            if (Sync-Events $state $token $withAlarms) {
                if ($withAlarms) { $nextAlarms = $started.AddSeconds($AlarmsSec) }
            }
            $nextEvents = $started.AddSeconds($EventsSec)
        }
        if ($Once) { return $snapshot }
        $rest = $PollMs - [int]((Get-Date) - $started).TotalMilliseconds
        if ($rest -gt 0) { Start-Sleep -Milliseconds $rest }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    while ($true) {
        try { Start-Agent; if ($Once) { break } } catch { Write-AgentLog ('AGENT ERROR ' + $_.Exception.Message); if ($Once) { throw }; Start-Sleep -Seconds 10 }
    }
}

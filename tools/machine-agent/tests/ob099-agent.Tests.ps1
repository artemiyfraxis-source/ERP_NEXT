# Pester 3.4+: the OB.099 agent is read-only (FINS 01 01 only, one SELECT only), sends only the allow-list, parses the
# BZW2.5 per-crimp log incrementally and the alarm journal, builds the ERP bodies. The PLC, MySQL and ERP are mocked.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('ob099-' + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
$logs = Join-Path $tmp 'logs'
New-Item -ItemType Directory $logs | Out-Null
. (Join-Path $root 'ob099-agent.ps1') -Unit 1 -LogPath (Join-Path $tmp 'agent.log') -StatePath (Join-Path $tmp 'state.json') -CrimpLogDir $logs -MysqlCredPath (Join-Path $tmp 'erp_ro.txt')

# A FINS reply to a read frame: word i = (first address + i) mod 65536.
function Reply-Words([byte[]]$Frame) {
    $address = ([int]$Frame[13] -shl 8) -bor [int]$Frame[14]
    $count = ([int]$Frame[16] -shl 8) -bor [int]$Frame[17]
    $reply = New-Object 'byte[]' (14 + 2 * $count)
    $reply[0] = 0xC0; $reply[9] = $Frame[9]; $reply[10] = 1; $reply[11] = 1
    for ($i = 0; $i -lt $count; $i++) { $w = ($address + $i) % 65536; $reply[14 + 2 * $i] = ($w -shr 8) -band 0xFF; $reply[15 + 2 * $i] = $w -band 0xFF }
    return ,$reply
}

$line1 = '260122-A2   -   A   A   A   519   2026/11/18 ' + [string][char]0x661F + [char]0x671F + [char]0x4E09 + ' 08:20:37   OK'
$line2 = '260122-A2   61117-4   A   A   A-12   611   2026/11/18 X 08:21:02   NG'
$utf8 = New-Object System.Text.UTF8Encoding $false

Describe 'read only' {
    It 'builds only memory area read frames (01 01) with the area code and address' {
        $frame = New-FinsRead 'D' 5006 1 7
        $frame[10] | Should Be 1; $frame[11] | Should Be 1
        $frame[12] | Should Be 0x82
        (([int]$frame[13] -shl 8) -bor $frame[14]) | Should Be 5006
        $frame[7] | Should Be 115; $frame[4] | Should Be 1; $frame[9] | Should Be 7
        (New-FinsRead 'W' 80 5 1)[12] | Should Be 0xB1
        (New-FinsRead 'H' 60 2 1)[12] | Should Be 0xB2
    }
    It 'refuses to send a write frame (01 02) or any other command' {
        $write = New-FinsRead 'D' 5006 1 1
        $write[11] = 2
        { Assert-FinsReadOnly $write } | Should Throw
        { Send-FinsFrame $write } | Should Throw
        $fill = New-FinsRead 'D' 5006 1 1
        $fill[11] = 3
        { Assert-FinsReadOnly $fill } | Should Throw
    }
    It 'sends only one SELECT to MySQL' {
        foreach ($sql in @('UPDATE craft SET D5006 = 1', 'SELECT 1; DELETE FROM alarmrecord', 'INSERT INTO alarmrecord VALUES (1)')) {
            { Assert-ReadOnlySql $sql } | Should Throw
        }
        { Assert-ReadOnlySql 'SELECT SaveTime, AlarmInfo FROM alarmrecord' } | Should Not Throw
    }
    It 'the agent source carries no FINS write command and no SQL write' {
        $source = [IO.File]::ReadAllText((Join-Path $root 'ob099-agent.ps1'))
        $source -match '0x01,\s*0x0[2-4]\b' | Should Be $false
        $source -match '(?i)\b(INSERT|UPDATE|DELETE|DROP|ALTER|GRANT)\s+(INTO|FROM|TABLE|craft|alarmrecord)' | Should Be $false
    }
}

Describe 'PLC snapshot' {
    It 'reads the five blocks with 01 01 and keeps only allow-listed words' {
        $script:frames = @()
        Mock Send-FinsFrame { $script:frames += ,$Frame; Reply-Words $Frame }
        $snapshot = Read-PlcSnapshot
        $snapshot['link'] | Should Be 'ok'
        $script:frames.Count | Should Be 5
        foreach ($frame in $script:frames) { $frame[10] | Should Be 1; $frame[11] | Should Be 1 }
        $words = $snapshot['registers']
        $words.Count | Should Be $script:AllowedSet.Count
        $words['D5006'] | Should Be 5006
        $words['H60'] | Should Be 60
        $words['W83'] | Should Be 83
        $words.ContainsKey('D5007') | Should Be $false
        $words.ContainsKey('D5116') | Should Be $false
    }
    It 'a silent PLC is no_plc, a FINS error code too' {
        Mock Send-FinsFrame { throw (New-Object System.Net.Sockets.SocketException 10060) }
        $snapshot = Read-PlcSnapshot
        $snapshot['link'] | Should Be 'no_plc'
        $snapshot['registers'].Count | Should Be 0
        Mock Send-FinsFrame { $reply = Reply-Words $Frame; $reply[12] = 0x11; $reply[13] = 0x03; ,$reply }
        (Read-PlcSnapshot)['error'] | Should Match 'end code 1103'
    }
    It 'refuses a reply to another request' {
        Mock Send-FinsFrame { $reply = Reply-Words $Frame; $reply[9] = ($reply[9] + 1) % 256; ,$reply }
        (Read-PlcSnapshot)['link'] | Should Be 'no_plc'
    }
}

Describe 'crimp log' {
    It 'parses a crimp line: peak, local time with offset, OK/NG, the job strings; "-" is empty' {
        $crimp = ConvertFrom-CrimpLine $line1 '20261118@0'
        $crimp['result'] | Should Be 'OK'
        $crimp['peak'] | Should Be 519
        $crimp['at'] | Should Match '^2026-11-18T08:20:37[+-]\d{2}:\d{2}$'
        $crimp['equipment_no'] | Should Be '260122-A2'
        $crimp['product'] | Should Be ''
        $crimp['applicator'] | Should Be 'A'
        $ng = ConvertFrom-CrimpLine $line2 'k'
        ($ng['result'], $ng['product'], $ng['applicator'], $ng['peak']) | Should Be @('NG', '61117-4', 'A-12', 611)
    }
    It 'takes the scan code of the scanner mode and a "-" peak as no peak' {
        $crimp = ConvertFrom-CrimpLine '260122-A2   P   W   T   M   SCAN-77   -   2026/11/18 X 09:00:00   OK' 'k'
        $crimp['scan_code'] | Should Be 'SCAN-77'
        $crimp['peak'] | Should Be $null
    }
    It 'ignores headers and garbage' {
        ConvertFrom-CrimpLine 'equipment   product   wire' 'k' | Should Be $null
        ConvertFrom-CrimpLine '' 'k' | Should Be $null
    }
    It 'reads complete lines from the offset, keys them by file and byte offset, and continues after new lines' {
        $path = Join-Path $logs '20261118.txt'
        [IO.File]::WriteAllText($path, ([string][char]0xFEFF) + $line1 + "`r`n" + 'junk line' + "`r`n" + $line2 + "`r`n" + '260122-A2   -   A', $utf8)
        $now = [datetime]'2026-11-18 12:00:00'
        $state = @{ day = '20261118'; offset = 0L; alarm_after = '2026-11-17 23:59:59' }
        $batch = Read-CrimpBatch $state $now
        $batch['crimps'].Count | Should Be 2
        $batch['skipped'] | Should Be 1
        $batch['crimps'][0]['key'] | Should Be '20261118@0'
        $batch['crimps'][1]['result'] | Should Be 'NG'
        $partial = $utf8.GetByteCount('260122-A2   -   A')
        $batch['offset'] | Should Be ((Get-Item $path).Length - $partial)
        $state.offset = $batch['offset']
        (Read-CrimpBatch $state $now)['crimps'].Count | Should Be 0
        [IO.File]::AppendAllText($path, '   A   A   520   2026/11/18 X 08:22:00   OK' + "`r`n", $utf8)
        $next = Read-CrimpBatch $state $now
        $next['crimps'].Count | Should Be 1
        $next['crimps'][0]['key'] | Should Be ('20261118@' + $state.offset)
        $next['crimps'][0]['peak'] | Should Be 520
    }
    It 'stops at the batch size and moves to the next day only when the old file is read up' {
        $path = Join-Path $logs '20261119.txt'
        [IO.File]::WriteAllText($path, ($line2 + "`n") * 3, $utf8)
        $state = @{ day = '20261119'; offset = 0L; alarm_after = '2026-11-17 23:59:59' }
        $now = [datetime]'2026-11-21 08:00:00'
        $batch = Read-CrimpBatch $state $now 2
        $batch['crimps'].Count | Should Be 2
        $batch['day'] | Should Be '20261119'
        $state.offset = $batch['offset']
        (Read-CrimpBatch $state $now 2)['crimps'].Count | Should Be 1
        $state.offset = (Get-Item $path).Length
        $moved = Read-CrimpBatch $state $now
        ($moved['day'], $moved['offset'], $moved['crimps'].Count) | Should Be @('20261121', 0, 0)  # no file for 20.11
    }
}

Describe 'alarm journal' {
    It 'reads the password file in both forms and never needs the password in the repo' {
        [IO.File]::WriteAllText((Join-Path $tmp 'erp_ro.txt'), 'S3cret-only-on-the-PC')
        (Read-MysqlCredential (Join-Path $tmp 'erp_ro.txt'))['password'] | Should Be 'S3cret-only-on-the-PC'
        [IO.File]::WriteAllText((Join-Path $tmp 'erp_ro.txt'), "user=erp_ro`r`npassword=abc")
        $credential = Read-MysqlCredential (Join-Path $tmp 'erp_ro.txt')
        ($credential['user'], $credential['password']) | Should Be @('erp_ro', 'abc')
    }
    It 'takes rows after the last SaveTime and moves the mark' {
        $script:sql = ''
        Mock Invoke-MysqlSelect {
            $script:sql = $Sql
            $rows = @()
            $rows += ,@('2026-11-18 13:27:03', 'Abnormal terminal crimping')
            $rows += ,@('bad', 'x')
            $rows += ,@('2026-11-18 13:30:00.0', 'The safety guard is not closed')
            ,$rows
        }
        $batch = Read-AlarmBatch '2026-11-18 00:00:00'
        $script:sql | Should Match "WHERE SaveTime > '2026-11-18 00:00:00' ORDER BY SaveTime"
        $batch['alarms'].Count | Should Be 2
        $batch['alarms'][0]['key'] | Should Be '2026-11-18 13:27:03|Abnormal terminal crimping'
        $batch['alarms'][0]['at'] | Should Match '^2026-11-18T13:27:03'
        $batch['after'] | Should Be '2026-11-18 13:30:00'
    }
    It 'refuses a SaveTime mark that is not a time (no SQL from the state file)' {
        { Read-AlarmBatch "2026-11-18' OR '1'='1" } | Should Throw
    }
    It 'finds mysql.exe on D: when MySQL is not on C: (the press PC of OB.099.1)' {
        Mock Get-ChildItem { } -ParameterFilter { $Path -like 'C:*' }
        Mock Get-ChildItem { [pscustomobject]@{ FullName = 'D:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe' } } -ParameterFilter { $Path -like 'D:*' }
        Find-MysqlExe | Should Be 'D:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe'
    }
    It 'uses the pinned -MysqlExe without searching' {
        $MysqlExe = 'E:\pinned\mysql.exe'
        Mock Get-ChildItem { throw 'searched' }
        Find-MysqlExe | Should Be 'E:\pinned\mysql.exe'
        Assert-MockCalled Get-ChildItem -Times 0 -Exactly -Scope It
    }
}

Describe 'mysql.exe runs as SYSTEM only when ordinary users cannot change it' {
    $exe = 'D:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe'
    $admins = @{ sid = 'S-1-5-32-544'; rights = 0x1F01FF }
    $usersRead = @{ sid = 'S-1-5-32-545'; rights = 0x1200A9 }
    $authModify = @{ sid = 'S-1-5-11'; rights = 0x1301BF }
    $authCreateFolders = @{ sid = 'S-1-5-11'; rights = 0x4 }
    It 'trusts a path that users can only read (a drive root may let them create folders)' {
        Mock Get-PathAccessRules { if ($Path -eq 'D:\') { ,@($admins, $usersRead, $authCreateFolders) } else { ,@($admins, $usersRead) } }
        Get-UserWritablePath $exe | Should Be ''
    }
    It 'names the first folder ordinary users can change (D: of the press PC: Authenticated Users Modify)' {
        Mock Get-PathAccessRules { if ($Path -like 'D:\Program Files\*') { ,@($admins, $usersRead) } else { ,@($admins, $authModify) } }
        Get-UserWritablePath $exe | Should Be 'D:\Program Files'
    }
    It 'ignores Modify on the drive root itself (a root cannot be renamed) but not deleting its folders' {
        Mock Get-PathAccessRules { if ($Path -eq 'D:\') { ,@($admins, $authModify) } else { ,@($admins, $usersRead) } }
        Get-UserWritablePath $exe | Should Be ''
        Mock Get-PathAccessRules { if ($Path -eq 'D:\') { ,@($admins, @{ sid = 'S-1-5-11'; rights = 0x40 }) } else { ,@($admins, $usersRead) } }
        Get-UserWritablePath $exe | Should Be 'D:\'
        Mock Get-PathAccessRules { ,@($admins, $usersRead, $authModify) }
        Get-UserWritablePath $exe | Should Be $exe
    }
    It 'refuses a bin folder where users can add files (DLLs next to the exe)' {
        Mock Get-PathAccessRules { if ($Path -like '*\bin') { ,@($admins, @{ sid = 'S-1-1-0'; rights = 0x2 }) } else { ,@($admins, $usersRead) } }
        Get-UserWritablePath $exe | Should Be 'D:\Program Files\MySQL\MySQL Server 8.0\bin'
    }
    It 'skips the alarm read with one warning per hour and never starts mysql.exe' {
        $MysqlExe = $exe
        Mock Get-PathAccessRules { ,@($admins, $authModify) }
        Mock Write-AgentLog { }
        Mock Read-AlarmBatch { throw 'must not read' }
        Mock Send-Json { 200 }
        Mock Get-Date { [datetime]'2026-11-22 10:00:00' }
        $script:NextAclWarning = [datetime]::MinValue
        Test-MysqlExeTrusted ([datetime]'2026-11-22 10:00:00') | Should Be $false
        Test-MysqlExeTrusted ([datetime]'2026-11-22 10:30:00') | Should Be $false
        Assert-MockCalled Write-AgentLog -Times 1 -Exactly -Scope It
        Test-MysqlExeTrusted ([datetime]'2026-11-22 11:00:00') | Should Be $false
        Assert-MockCalled Write-AgentLog -Times 2 -Exactly -Scope It
        $state = @{ day = '20261122'; offset = 0L; alarm_after = '2026-11-21 23:59:59' }
        Sync-Events $state 'token' $true | Should Be $true
        Assert-MockCalled Read-AlarmBatch -Times 0 -Exactly -Scope It
        { Invoke-MysqlSelect 'SELECT 1' } | Should Throw
    }
    It 'warns in Russian' {
        $script:AclWarning | Should Match ('^' + [string][char]0x0416)
    }
}

Describe 'sync and report' {
    It 'sends records, saves the state only after ERP accepted them, keeps it on failure' {
        $path = Join-Path $logs '20261122.txt'
        [IO.File]::WriteAllText($path, $line1 + "`n" + $line2 + "`n", $utf8)
        Mock Get-Date { [datetime]'2026-11-22 10:00:00' }
        Mock Test-MysqlExeTrusted { $true }
        Mock Read-AlarmBatch { @{ alarms = @(@{ key = 'k'; at = '2026-11-22T09:00:00+03:00'; text = 'Device not reset' }); after = '2026-11-22 09:00:00' } }
        Mock Send-Json { 503 }
        $state = @{ day = '20261122'; offset = 0L; alarm_after = '2026-11-21 23:59:59' }
        Sync-Events $state 'token' $true | Should Be $false
        $state.offset | Should Be 0
        $script:bodies = @()
        Mock Send-Json { $script:bodies += $Body; 200 }
        Sync-Events $state 'token' $true | Should Be $true
        $state.offset | Should Be (Get-Item $path).Length
        $state.alarm_after | Should Be '2026-11-22 09:00:00'
        $script:bodies.Count | Should Be 1
        $sent = $script:bodies[0] | ConvertFrom-Json
        $sent.crimps.Count | Should Be 2
        $sent.alarms[0].text | Should Be 'Device not reset'
        $sent.inventory | Should Be ([string][char]0x041E + [string][char]0x0411 + '.099.1')
        $saved = [IO.File]::ReadAllText((Join-Path $tmp 'state.json')) | ConvertFrom-Json
        $saved.offset | Should Be $state.offset
    }
    It 'builds the snapshot body: protocol 1, the press number, FINS words by name' {
        $body = ConvertTo-Body (New-Report @{ link = 'ok'; error = ''; registers = @{ 'D5006' = 2500 } } 'boot-1' 7) | ConvertFrom-Json
        ($body.proto, $body.seq, $body.registers.D5006, $body.service_version) | Should Be @(1, 7, 2500, 'FINS/UDP')
        $body.inventory | Should Be ([string][char]0x041E + [string][char]0x0411 + '.099.1')
    }
    It 'sends on change or after the heartbeat' {
        $now = [datetime]'2026-11-22 10:00:00'
        Test-ShouldSend 'a' 'a' $now $now | Should Be $false
        Test-ShouldSend 'b' 'a' $now $now | Should Be $true
        Test-ShouldSend 'a' 'a' $now.AddSeconds(-5) $now | Should Be $true
    }
}

Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue

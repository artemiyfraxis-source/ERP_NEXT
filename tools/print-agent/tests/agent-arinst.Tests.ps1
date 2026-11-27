# Pester 3.4 / Windows PowerShell 5.1. USB, processes and serial are mocked throughout.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$arinstTestRoot = Join-Path ([IO.Path]::GetTempPath()) ('erp-arinst-tests-' + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($arinstTestRoot)
. (Join-Path $here '..\agent.ps1') -ConfigPath (Join-Path $arinstTestRoot 'config.json') -LogPath (Join-Path $arinstTestRoot 'agent.log')
$script:testSerial = 'arinst:3932198-827215893-540422231'
$script:testUsb = 'USB\VID_0483&PID_5740\TEST'
$script:optionsText = 'SR=1000000,9000000000;BW=0.02,0.05,0.1,0.25,0.5,1;PR=0'

function ConvertFrom-TestHex {
    param([string]$Hex)
    return ,([byte[]]@(for ($i = 0; $i -lt $Hex.Length; $i += 2) { [Convert]::ToByte($Hex.Substring($i, 2), 16) }))
}
function Get-ArinstBenchFrames {
    param([string]$File, [string]$Direction = 'RX')
    return @(Get-Content -LiteralPath (Join-Path $here ('fixtures\arinst-native\' + $File)) | Where-Object { $_.StartsWith($Direction + ' ') } |
        ForEach-Object { ,(ConvertFrom-TestHex $_.Substring(3)) })
}
function New-ArinstTestPort {
    $port = [pscustomobject]@{ Input = (New-Object 'System.Collections.Generic.Queue[byte]')
        Sent = (New-Object 'System.Collections.Generic.List[object]'); Disposed = $false; Opened = $false
        ReplyDevice = 23; SequenceDelta = 1; ReplyCommand = 0; ReplyArgument = 0; BrokenScan = $false
        ChunkLimit = 4096; Prefix = [byte[]]@(); Answers = @{
            # Synthetic identity payloads from confirmed values; options/scans fixtures are verbatim captures.
            0xD008 = (ConvertFrom-TestHex '26003c0015504e3157303620'); 0xD001 = [byte[]]@()
            0xD002 = (ConvertFrom-TestHex '020000000000000000000000'); 0xD003 = [Text.Encoding]::ASCII.GetBytes($script:optionsText) } }
    $port | Add-Member ScriptProperty BytesToRead { $this.Input.Count }
    $port | Add-Member ScriptMethod Open { $this.Opened = $true; $this.Disposed = $false }
    $port | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
    $port | Add-Member ScriptMethod Read { param($buffer, $offset, $count)
        if (-not $this.Input.Count) { throw (New-Object TimeoutException 'mock timeout') }
        $read = [Math]::Min([Math]::Min($count, $this.Input.Count), $this.ChunkLimit)
        for ($i = 0; $i -lt $read; $i++) { $buffer[$offset + $i] = $this.Input.Dequeue() }
        return $read
    }
    $port | Add-Member ScriptMethod Write { param($bytes, $offset, $count)
        [byte[]]$tx = $bytes[$offset..($offset + $count - 1)]; $this.Sent.Add($tx)
        $request = (ConvertFrom-ArinstFrames $tx).frames[0]
        [byte[]]$payload = @()
        if ($request.command -eq 0xDA00) {
            $points = [int]$request.payload[1]; $payload = New-Object byte[] (8 + 16 * $points)
            [BitConverter]::GetBytes([single]$points).CopyTo($payload, 0)
            for ($i = 0; $i -lt $points; $i++) {
                foreach ($j in 0..3) { [BitConverter]::GetBytes([single](0.1 * ($j + 1))).CopyTo($payload, 8 + 16 * $i + 4 * $j) }
            }
            if ($this.BrokenScan) { $payload = $payload[0..($payload.Length - 2)] }
        } elseif ($this.Answers.ContainsKey([int]$request.command)) { $payload = $this.Answers[[int]$request.command] }
        else { return }
        $cmd = if ($this.ReplyCommand) { $this.ReplyCommand } else { $request.command }
        $frame = New-ArinstFrame $cmd $payload (($request.sequence + $this.SequenceDelta) -band 65535) $this.ReplyDevice $this.ReplyArgument
        foreach ($b in $this.Prefix) { $this.Input.Enqueue($b) }
        foreach ($b in $frame) { $this.Input.Enqueue($b) }
    }
    return $port
}
function New-ArinstTestConnection {
    param($Port)
    return @{ port = $Port; timer = [Diagnostics.Stopwatch]::StartNew(); failed = $false; scan_attempted = $false
        sequence = 0; device = 23; wire = (New-Object 'System.Collections.Generic.List[object]')
        options = (ConvertFrom-ArinstOptions ([Text.Encoding]::ASCII.GetBytes($script:optionsText))) }
}
function New-ArinstTestCommand {
    param([string]$Op = 'capture')
    $argsValue = @{}
    if ($Op -in @('capture','measure_standard','apply_profile')) {
        $argsValue.profile = @{ start_hz = 1000000; stop_hz = 2000000; points = 2; if_bandwidth_hz = 1000 }
    }
    if ($Op -in @('capture','measure_standard')) { $argsValue.orientation = 'forward' }
    if ($Op -eq 'measure_standard') { $argsValue.standard = 'LOAD' }
    $command = @{ protocol_version = 3; source = 'arinst'; instrument_serial = $script:testSerial; op = $Op
        command_id = [guid]::NewGuid().ToString(); session_id = [guid]::NewGuid().ToString(); args = $argsValue }
    if ($Op -in @('capture','measure_standard')) {
        Write-VnaJson (Get-ArinstProfilePath $command) @{ serial = $command.instrument_serial; session_id = $command.session_id; profile = $argsValue.profile }
    }
    return $command
}

Describe 'ArinstVL native framing and bench fixtures' {
    It 'builds the reference D008 request byte for byte' {
        ([BitConverter]::ToString((New-ArinstFrame 0xD008))).Replace('-','').ToLowerInvariant() | Should Be 'aa08d00000000000000000f72f0912aa82'
    }
    It 'refuses every command outside the seven-command whitelist' {
        foreach ($cmd in @(0xB004,0xFCB1,0xA001,0xDA0D,0xFAC0,0xDEAD,0xFCA2,0)) {
            { New-ArinstFrame $cmd } | Should Throw 'разрешённом протоколе'
        }
    }
    It 'rejects checksum corruption and resynchronizes to the next valid frame' {
        $valid = New-ArinstFrame 0xD008 ([byte[]]@(1,2,3)) 1 23
        $badHeader = $valid.Clone(); $badHeader[3] = $badHeader[3] -bxor 1
        $badCrc = $valid.Clone(); $badCrc[13] = $badCrc[13] -bxor 1
        (ConvertFrom-ArinstFrames $badHeader).frames.Count | Should Be 0
        (ConvertFrom-ArinstFrames $badCrc).frames.Count | Should Be 0
        $parsed = ConvertFrom-ArinstFrames ([byte[]](@(9,0,170,1) + $badHeader + $badCrc + $valid + $valid[0..6]))
        $parsed.frames.Count | Should Be 1
        $parsed.rejected_headers | Should BeGreaterThan 0
        $parsed.rejected_crc | Should Be 1
        [BitConverter]::ToString($parsed.rest) | Should Be ([BitConverter]::ToString($valid[0..6]))
    }
    It 'parses synthesized identity frames from confirmed bench values' {
        $id = (ConvertFrom-ArinstFrames (ConvertFrom-TestHex 'aa08d00100170000000c00d32f26003c0015504e3157303620122de278')).frames[0]
        $fw = (ConvertFrom-ArinstFrames (ConvertFrom-TestHex 'aa02d00500170000000c00d52f020000000000000000000000c0c811c0')).frames[0]
        ConvertFrom-ArinstIdentityWords $id.payload | Should Be '3932198-827215893-540422231'
        ConvertFrom-ArinstIdentityWords $fw.payload '.' | Should Be '2.0.0'
        { ConvertFrom-ArinstIdentityWords ([byte[]]@(1,2)) } | Should Throw
    }
    It 'validates every verbatim request and response including eight calibration chunks' {
        $rxCount = 0
        foreach ($file in @('bench-run2.txt','bench-run3.txt')) {
            foreach ($direction in @('TX','RX')) {
                foreach ($bytes in (Get-ArinstBenchFrames $file $direction)) {
                    $parsed = ConvertFrom-ArinstFrames $bytes
                    $parsed.frames.Count | Should Be 1
                    $parsed.rest.Length | Should Be 0
                    $parsed.rejected_headers | Should Be 0
                    $parsed.rejected_crc | Should Be 0
                    if ($direction -eq 'RX') { $rxCount++ }
                }
            }
        }
        $rxCount | Should Be 12
    }
    It 'parses real options and preserves advertised bandwidth ordering' {
        $frames = @(Get-ArinstBenchFrames 'bench-run2.txt')
        $options = ConvertFrom-ArinstOptions (ConvertFrom-ArinstFrames $frames[0]).frames[0].payload
        $options.raw | Should Be $script:optionsText
        ($options.scan_range_hz -join ',') | Should Be '1000000,9000000000'
        ($options.bandwidths_hz -join ',') | Should Be '20,50,100,250,500,1000'
        $options.max_slice_points | Should Be 255
        $changed = ConvertFrom-ArinstOptions ([Text.Encoding]::ASCII.GetBytes('SR=2000000,5000000;BW=1,0.075,0.02;MAX_SLICE_POINTS=7'))
        ($changed.bandwidths_hz -join ',') | Should Be '1000,75,20'
        $changed.max_slice_points | Should Be 7
        { ConvertFrom-ArinstOptions ([Text.Encoding]::ASCII.GetBytes('SR=2,1;BW=1')) } | Should Throw
        { ConvertFrom-ArinstOptions ([Text.Encoding]::ASCII.GetBytes('SR=1,2;BW=1;BW=2')) } | Should Throw
        { ConvertFrom-ArinstOptions ([Text.Encoding]::ASCII.GetBytes('SR=1,2;BW=0')) } | Should Throw
    }
    It 'parses both real scans and separates the undocumented trailing pair' {
        $frames = @(Get-ArinstBenchFrames 'bench-run3.txt'); $reply = (ConvertFrom-ArinstFrames $frames[-1]).frames[0]
        $scan = ConvertFrom-ArinstScan $reply.payload 13 0
        $scan.data.S11.Count | Should Be 13
        $scan.data.S21.Count | Should Be 13
        [Math]::Abs($scan.data.S11[0][0] - (-2.8243038654327393)) | Should BeLessThan 0.000001
        $scan.auxiliary | Should Be 0
        $scan.trailer_base64 | Should Be ([Convert]::ToBase64String((ConvertFrom-TestHex '0000803f00000000')))
        $frames = @(Get-ArinstBenchFrames 'bench-run2.txt'); $reply = (ConvertFrom-ArinstFrames $frames[-1]).frames[0]
        (ConvertFrom-ArinstScan $reply.payload 11).data.S11.Count | Should Be 11
        { ConvertFrom-ArinstScan $reply.payload 12 } | Should Throw 'число'
        { ConvertFrom-ArinstScan ([byte[]]($reply.payload + @(0))) 11 } | Should Throw 'число'
    }
    It 'handles individual channel replies and rejects nonfinite values' {
        $payload = ConvertFrom-TestHex '0000803f000000000000003f000080be'
        (ConvertFrom-ArinstScan $payload 1 1).data.S11[0][0] | Should Be 0.5
        (ConvertFrom-ArinstScan $payload 1 2).data.S21[0][1] | Should Be -0.25
        [BitConverter]::GetBytes([single]::NaN).CopyTo($payload,8)
        { ConvertFrom-ArinstScan $payload 1 1 } | Should Throw 'число'
    }
    It 'floors non-divisible integer spans and includes both endpoints' {
        (Get-ArinstFrequencyGrid @{ start_hz = 1000000; stop_hz = 1000010; points = 4; if_bandwidth_hz = 20 }) -join ',' |
            Should Be '1000000,1000003,1000006,1000010'
        { Get-ArinstFrequencyGrid @{ start_hz = 1000000; stop_hz = 1000002; points = 4; if_bandwidth_hz = 20 } } | Should Throw
        { Get-ArinstFrequencyGrid @{ start_hz = 1000000.5; stop_hz = 2000000; points = 4; if_bandwidth_hz = 20 } } | Should Throw
    }
}

Describe 'ArinstVL host profiles and measurements' {
    BeforeEach {
        $script:port = New-ArinstTestPort
        Mock Read-AgentConfig { @{ arinstDevices = @(@{ serial = $script:testSerial; usb_path = $script:testUsb }) } }
        Mock Get-ArinstUsbCandidates { @(@{ usb_path = $script:testUsb; port = 'COM3' }) }
        Mock Get-Process { @() }
        Mock New-ArinstSerialPort { $script:port }
        Mock Save-ArinstObservation {}
        Mock Assert-VnaLease {}
        Mock Test-Path { $false }
    }
    It 'returns exact raw channels and the complete hashed binary transcript' {
        $command = New-ArinstTestCommand
        $result = Invoke-ArinstCommand $command @{}
        ($result.acquisition.measured_channels -join ',') | Should Be 'S11,S21'
        $result.acquisition.data.S11.Count | Should Be 2
        [Math]::Abs($result.acquisition.data.S21[1][1] - 0.4) | Should BeLessThan 0.000001
        $result.acquisition.data.ContainsKey('S12') | Should Be $false
        ($result.acquisition.frequencies_hz -join ',') | Should Be '1000000,2000000'
        $result.acquisition.grid_confirmed | Should Be $true
        $result.acquisition.correction_origin | Should Be 'raw'
        $result.correction_origin | Should Be 'raw'
        $roundTrip = $result | ConvertTo-Json -Depth 70 | ConvertFrom-Json
        ($roundTrip.acquisition.frequencies_hz -is [array]) | Should Be $true
        ($roundTrip.acquisition.frequencies_hz -join ',') | Should Be '1000000,2000000'
        $result.raw_sha256 | Should Be (Get-VnaDigest (ConvertTo-VnaCanonical $result.raw_evidence))
        $script:port.Sent.Count | Should Be 5
        $script:port.Disposed | Should Be $true
        # Optional local export for cross-language receipt verification; never opens hardware.
        if ($env:ARINST_TEST_RECEIPT) { Write-VnaJson $env:ARINST_TEST_RECEIPT @{ command = $command; result = $result } }
    }
    It 'persists profile per session across workers without sending settings' {
        $command = New-ArinstTestCommand 'read_profile'
        (Invoke-ArinstCommand $command @{}).profile | Should Be $null
        $command.op = 'apply_profile'; $command.args = @{ profile = @{ start_hz = 3000000; stop_hz = 4000000; points = 3; if_bandwidth_hz = 50 } }
        (Invoke-ArinstCommand $command @{}).profile.start_hz | Should Be 3000000
        $command.op = 'read_profile'; $command.args = @{}
        (Invoke-ArinstCommand $command @{}).profile.points | Should Be 3
        $script:port.Sent.Count | Should Be 12
        $command.instrument_serial = 'arinst:1-2-3'
        { Read-ArinstProfile $command (New-ArinstTestConnection $script:port).options } | Should Throw 'другой сессии'
    }
    It 'slices 1000 points into at most 255 and sends the exact returned grid' {
        $connection = New-ArinstTestConnection $script:port
        $profile = @{ start_hz = 1000000; stop_hz = 9000000000; points = 1000; if_bandwidth_hz = 20 }
        $scan = Invoke-ArinstScan $connection $profile
        $requests = @($script:port.Sent | ForEach-Object { (ConvertFrom-ArinstFrames $_).frames[0] })
        $counts = @($requests | ForEach-Object { $_.payload[1] })
        ($counts -join ',') | Should Be '255,255,255,235'
        $sent = @(foreach ($request in $requests) {
            [void]($request.command | Should Be 0xDA00)
            for ($i = 0; $i -lt $request.payload[1]; $i++) { [BitConverter]::ToUInt64($request.payload, 2 + 8 * $i) }
        })
        ($sent -join ',') | Should Be ($scan.frequencies_hz -join ',')
        $scan.data.S11.Count | Should Be 1000
        $scan.frequencies_hz[-1] | Should Be 9000000000
    }
    It 'honors smaller slice options and dynamic bandwidth indexes including a one-point slice' {
        $connection = New-ArinstTestConnection $script:port
        $connection.options = ConvertFrom-ArinstOptions ([Text.Encoding]::ASCII.GetBytes('SR=1000000,9000000000;BW=1,0.075,0.02;MAX_SLICE_POINTS=3'))
        $scan = Invoke-ArinstScan $connection @{ start_hz = 1000000; stop_hz = 2000000; points = 7; if_bandwidth_hz = 75 }
        $requests = @($script:port.Sent | ForEach-Object { (ConvertFrom-ArinstFrames $_).frames[0] })
        (@($requests | ForEach-Object { $_.payload[1] }) -join ',') | Should Be '3,3,1'
        (@($requests | ForEach-Object { $_.payload[0] }) -join ',') | Should Be '1,1,1'
        $scan.data.S21.Count | Should Be 7
    }
    It 'checks lease before serial access' {
        Mock Assert-VnaLease { throw 'lease expired' }
        { Invoke-ArinstCommand (New-ArinstTestCommand) @{} } | Should Throw 'lease expired'
        Assert-MockCalled New-ArinstSerialPort -Times 0 -Exactly -Scope It
    }
    It 'rejects malformed profiles and arbitrary arguments before serial access' {
        foreach ($patch in @(@{ points = 1 }, @{ power_dbm = -3 }, @{ start_hz = 1 }, @{ start_hz = 1000000.5 })) {
            $command = New-ArinstTestCommand
            foreach ($key in $patch.Keys) { $command.args.profile[$key] = $patch[$key] }
            { Invoke-ArinstCommand $command @{} } | Should Throw
        }
        $command = New-ArinstTestCommand; $command.args.scpi = '*RST'
        { Invoke-ArinstCommand $command @{} } | Should Throw 'аргументы'
        Assert-MockCalled New-ArinstSerialPort -Times 0 -Exactly -Scope It
    }
    It 'checks advertised SR and BW before applying host state' {
        $script:port.Answers[0xD003] = [Text.Encoding]::ASCII.GetBytes('SR=1000000,1500000;BW=0.02')
        { Invoke-ArinstCommand (New-ArinstTestCommand 'apply_profile') @{} } | Should Throw 'возможностям'
        $script:port.Sent.Count | Should Be 4
        $script:port.Disposed | Should Be $true
    }
    It 'does not scan when the stored profile differs from the command' {
        $command = New-ArinstTestCommand; $command.args.profile.points = 3
        { Invoke-ArinstCommand $command @{} } | Should Throw 'не совпала'
        $script:port.Sent.Count | Should Be 4
    }
    It 'quarantines incomplete acquisition and retains partial wire evidence' {
        $script:port.BrokenScan = $true; $command = New-ArinstTestCommand
        { Invoke-ArinstCommand $command @{} } | Should Throw 'число'
        (Read-VnaJson (Get-VnaQuarantinePath $command)).command_id | Should Be $command.command_id
        $failure = Read-VnaJson (Join-Path (Get-VnaControlRoot) ('arinst-failure-' + $command.command_id + '.json'))
        $failure.evidence.wire.Count | Should Be 5
        $failure.sha256 | Should Match '^[a-f0-9]{64}$'
        $script:port.Disposed | Should Be $true
    }
    It 'blocks measurements after an unknown outcome until a fresh connect from ERP' {
        Mock Test-Path { [IO.File]::Exists($LiteralPath) }
        $command = New-ArinstTestCommand
        Write-VnaJson (Get-VnaQuarantinePath $command) @{ session_id = $command.session_id; serial = $command.instrument_serial
            command_id = $command.command_id; created_at = [DateTime]::UtcNow.ToString('o') }
        { Invoke-ArinstCommand $command @{} } | Should Throw 'откройте новый'
        $script:port.Sent.Count | Should Be 0
        $connect = New-ArinstTestCommand; $connect.op = 'connect'; $connect.args = @{}
        (Invoke-ArinstCommand $connect @{}).license.ContainsKey('trial_remaining_seconds') | Should Be $false
        [IO.File]::Exists((Get-VnaQuarantinePath $command)) | Should Be $false
    }
    It 'releases host profile without claiming RF stopped' {
        $command = New-ArinstTestCommand; $command.op = 'close'; $command.args = @{}
        $result = Invoke-ArinstCommand $command @{}
        $result.closed | Should Be $true
        $result.rf_stopped | Should Be $null
        Read-VnaJson (Get-ArinstProfilePath $command) | Should Be $null
        $script:port.Sent.Count | Should Be 4
    }
    It 'heartbeat uses cached native identity and never opens serial' {
        Mock Read-VnaJson { [pscustomobject]@{ serial = $script:testSerial; usb_path = $script:testUsb
            observed_at = [DateTime]::UtcNow.AddSeconds(-10).ToString('o'); firmware = '2.0.0'; hardware_uid = $script:testSerial.Substring(7)
            license_state = 'not_required'; trial_remaining_seconds = $null } }
        $inventory = @(Get-ArinstControlInventory @(@{ usb_path = $script:testUsb; port = 'COM7' }))
        $inventory.Count | Should Be 1
        $inventory[0].control_ready | Should Be $true
        $inventory[0].ops.Count | Should Be 6
        $inventory[0].license_state | Should Be 'not_required'
        Assert-MockCalled New-ArinstSerialPort -Times 0 -Exactly -Scope It
    }
    It 'keeps polling configured UIDs when the cached observation expires' {
        Mock Get-MachineId { 'pc-1' }; Mock Get-VnaObservation { $null }
        Mock Invoke-ErpApi { @{ job = $null; poll_after_ms = 500 } }
        $script:VnaControlInitialized = $true; $script:VnaWorker = $null
        try { Invoke-ScanPollOnce 'http://test' 'token' | Should Be 'idle' }
        finally { $script:VnaControlInitialized = $false }
        Assert-MockCalled Invoke-ErpApi -Times 1 -Scope It -ParameterFilter {
            $Body.observed_vna_serial -ceq $script:testSerial -and $Body.kinds -contains 'vna_control' }
        Assert-MockCalled New-ArinstSerialPort -Times 0 -Exactly -Scope It
    }
    It 'supervisor retains checkpoint hash when a worker is killed' {
        $command = New-ArinstTestCommand
        $script:entry = [pscustomobject]@{ state = 'executing'; payload = $null }
        Mock Get-VnaCommandPath { Join-Path (Get-VnaControlRoot) 'test-command.json' }
        Mock Read-VnaJson {
            if ($Path -match 'arinst-wire-') { return @{ sha256 = ('a' * 64) } }
            return $script:entry
        }
        Mock Write-VnaJson {}
        $process = [pscustomobject]@{ HasExited = $false; Killed = $false }
        $process | Add-Member ScriptMethod Kill { $this.Killed = $true; $this.HasExited = $true }
        $process | Add-Member ScriptMethod WaitForExit { param($ms) return $true }
        $process | Add-Member ScriptMethod Dispose {}
        Stop-VnaWorker @{ command = $command; process = $process } 'Результат неизвестен'
        $process.Killed | Should Be $true
        $script:entry.state | Should Be 'uncertain'
        $script:entry.evidence_checkpoint.sha256 | Should Be ('a' * 64)
        Assert-MockCalled Write-VnaJson -Times 1 -Scope It -ParameterFilter { $Path -match 'arinst-quarantine-' }
    }
}

Describe 'ArinstVL bounded transport and identity' {
    BeforeEach {
        $script:port = New-ArinstTestPort
        Mock Read-AgentConfig { @{ arinstDevices = @(@{ serial = $script:testSerial; usb_path = $script:testUsb }) } }
        Mock Get-ArinstUsbCandidates { @(@{ usb_path = $script:testUsb; port = 'COM3' }) }
        Mock Get-Process { @() }
        Mock New-ArinstSerialPort { $script:port }
        Mock Save-ArinstObservation {}
    }
    It 'drains leftovers then identifies and fetches options without licence commands' {
        foreach ($b in [byte[]]@(0,255,10,2,0)) { $script:port.Input.Enqueue($b) }
        $connection = Open-ArinstConnection $script:testSerial
        $connection.drain.discarded_bytes | Should Be 5
        $connection.identity.serial | Should Be $script:testSerial
        $connection.license.license_state | Should Be 'not_required'
        $requests = @($script:port.Sent | ForEach-Object { (ConvertFrom-ArinstFrames $_).frames[0] })
        ($requests.command -join ',') | Should Be '53256,53249,53250,53251'
        ($requests.device -join ',') | Should Be '0,0,23,23'
        ($requests.sequence -join ',') | Should Be '0,2,4,6'
        $connection.port.Dispose()
    }
    It 'refuses ArinstVL before opening a port' {
        Mock Get-Process { @([pscustomobject]@{ ProcessName = 'ArinstVL' }) }
        { Open-ArinstConnection $script:testSerial } | Should Throw 'Закройте ArinstVL'
        Assert-MockCalled New-ArinstSerialPort -Times 0 -Exactly -Scope It
    }
    It 'never probes another generic STM32 candidate' {
        Mock Get-ArinstUsbCandidates { @(@{ usb_path = 'USB\VID_0483&PID_5740\OTHER'; port = 'COM4' }) }
        { Open-ArinstConnection $script:testSerial } | Should Throw 'не найден'
        Assert-MockCalled New-ArinstSerialPort -Times 0 -Exactly -Scope It
    }
    It 'refuses mismatched UID and retains evidence before releasing the port' {
        $script:port.Answers[0xD008] = ConvertFrom-TestHex '010000000200000003000000'
        { Open-ArinstConnection $script:testSerial } | Should Throw 'UID не совпадает'
        $script:port.Disposed | Should Be $true
        $script:port.Sent.Count | Should Be 1
        $saved = Read-VnaJson (Join-Path (Get-VnaControlRoot) ('arinst-identify-' + $script:testSerial.Substring(7) + '.json'))
        $saved.sha256 | Should Match '^[a-f0-9]{64}$'
        $saved.evidence.wire[0].complete | Should Be $true
        $saved.evidence.wire[0].rx_base64 | Should Not BeNullOrEmpty
    }
    It 'refuses bootloader and wrong-model replies' {
        foreach ($dev in @(0x1017,22)) {
            $script:port = New-ArinstTestPort; $script:port.ReplyDevice = $dev
            { Open-ArinstConnection $script:testSerial } | Should Throw 'VNA-DL'
            $script:port.Disposed | Should Be $true
        }
    }
    It 'has a finite silence deadline' {
        { Wait-ArinstSilence $script:port 100 5 } | Should Throw 'продолжает передавать'
        $script:port.Sent.Count | Should Be 0
    }
    It 'times out once and never retransmits' {
        $connection = New-ArinstTestConnection $script:port; $script:port.Answers.Remove(0xD008)
        { Invoke-ArinstWire $connection 0xD008 -TimeoutMs 5 } | Should Throw 'результат операции неизвестен'
        { Invoke-ArinstWire $connection 0xD008 -TimeoutMs 5 } | Should Throw 'повтор команды запрещён'
        $script:port.Sent.Count | Should Be 1
    }
    It 'rejects command and sequence mismatches with valid CRCs' {
        $script:port.ReplyCommand = 0xD002
        { Invoke-ArinstWire (New-ArinstTestConnection $script:port) 0xD008 } | Should Throw 'не совпали'
        $script:port = New-ArinstTestPort; $script:port.SequenceDelta = 2
        { Invoke-ArinstWire (New-ArinstTestConnection $script:port) 0xD008 } | Should Throw 'не совпали'
    }
    It 'handles fragments and noise and retains every received byte' {
        $script:port.ChunkLimit = 3; $script:port.Prefix = [byte[]]@(1,2,3,170,0,0)
        $connection = New-ArinstTestConnection $script:port
        (Invoke-ArinstWire $connection 0xD008).command | Should Be 0xD008
        [Convert]::FromBase64String($connection.wire[0].rx_base64).Length | Should Be 35
    }
    It 'rejects unsolicited bytes before write and honors the watchdog' {
        $script:port.Input.Enqueue(255)
        { Invoke-ArinstWire (New-ArinstTestConnection $script:port) 0xD008 } | Should Throw 'неожиданные байты'
        $script:port.Input.Clear()
        $connection = New-ArinstTestConnection $script:port; $connection.timer = @{ ElapsedMilliseconds = 90001 }
        { Invoke-ArinstWire $connection 0xD008 } | Should Throw 'обмен прерван'
        $script:port.Sent.Count | Should Be 0
    }
}

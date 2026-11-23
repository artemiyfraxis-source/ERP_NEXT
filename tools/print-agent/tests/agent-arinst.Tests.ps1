# Pester 3+: every USB, process and serial boundary is mocked; no instrument is opened.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$arinstTestRoot = Join-Path ([IO.Path]::GetTempPath()) ('erp-arinst-tests-' + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($arinstTestRoot)
. (Join-Path $here '..\agent.ps1') -ConfigPath (Join-Path $arinstTestRoot 'config.json') -LogPath (Join-Path $arinstTestRoot 'agent.log')
$script:testSerial = 'arinst:3932198-827215893-540422231'
$script:testUsb = 'USB\VID_0483&PID_5740\TEST'

function New-ArinstTestPort {
    $port = [pscustomobject]@{ Input = (New-Object 'System.Collections.Generic.Queue[byte]')
        Sent = (New-Object 'System.Collections.Generic.List[string]'); Disposed = $false; Opened = $false
        Answers = @{ '*IDN?' = 'Arinst,VNA-DL,3932198-827215893-540422231,2.0.0'; 'SYST:ACT?' = 'TRIAL,600'
            'SENS:FREQ:STAR?' = '1000000'; 'SENS:FREQ:STOP?' = '2000000'; 'SENS:SWE:POIN?' = '2'; 'SENS:BAND?' = '1000'
            'CALC1:FORM?' = 'COMP'; 'CALC2:FORM?' = 'COMP'; '*OPC?' = '1'
            'CALC1:DATA? FDATA' = '0.1,0.2,0.3,0.4'; 'CALC2:DATA? FDATA' = '0.5,0.6,0.7,0.8' } }
    $port | Add-Member ScriptProperty BytesToRead { $this.Input.Count }
    $port | Add-Member ScriptMethod Open { $this.Opened = $true }
    $port | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
    $port | Add-Member ScriptMethod Read { param($buffer, $offset, $count)
        $read = [Math]::Min($count, $this.Input.Count)
        for ($i = 0; $i -lt $read; $i++) { $buffer[$offset + $i] = $this.Input.Dequeue() }
        return $read
    }
    $port | Add-Member ScriptMethod ReadByte {
        if ($this.Input.Count -eq 0) { throw [TimeoutException]::new('mock timeout') }
        return [int]$this.Input.Dequeue()
    }
    $port | Add-Member ScriptMethod Write { param($bytes, $offset, $count)
        $command = [Text.Encoding]::ASCII.GetString($bytes, $offset, $count).TrimEnd([char]10)
        $this.Sent.Add($command)
        if ($this.Answers.ContainsKey($command)) {
            foreach ($b in [Text.Encoding]::ASCII.GetBytes($this.Answers[$command] + "`n")) { $this.Input.Enqueue($b) }
        } elseif ($command -match '^(SENS:[A-Z:]+) ([0-9.]+)$') { $this.Answers[$Matches[1] + '?'] = $Matches[2] }
    }
    return $port
}
function New-ArinstTestConnection {
    param($Port, [bool]$Verified = $true)
    return @{ port = $Port; timer = [Diagnostics.Stopwatch]::StartNew(); failed = $false; mutated = $false
        settings_verified = $Verified; wire = (New-Object 'System.Collections.Generic.List[object]') }
}
function New-ArinstTestCommand {
    param([string]$Op = 'capture')
    $argsValue = @{}
    if ($Op -in @('capture','measure_standard','apply_profile')) {
        $argsValue.profile = @{ start_hz = 1000000; stop_hz = 2000000; points = 2; if_bandwidth_hz = 1000 }
    }
    if ($Op -in @('capture','measure_standard')) { $argsValue.orientation = 'forward' }
    if ($Op -eq 'measure_standard') { $argsValue.standard = 'LOAD' }
    return @{ protocol_version = 3; source = 'arinst'; instrument_serial = $script:testSerial; op = $Op
        command_id = [guid]::NewGuid().ToString(); session_id = [guid]::NewGuid().ToString(); args = $argsValue }
}

Describe 'Arinst: transport and identity' {
    BeforeEach {
        $script:port = New-ArinstTestPort
        Mock Read-AgentConfig { @{ arinstDevices = @(@{ serial = $script:testSerial; usb_path = $script:testUsb; settings_verified = $true }) } }
        Mock Get-ArinstUsbCandidates { @(@{ usb_path = $script:testUsb; port = 'COM3' }) }
        Mock Get-Process { @() }
        Mock New-ArinstSerialPort { $script:port }
        Mock Save-ArinstObservation {}
    }
    It 'drains binary leftovers before the first LF query' {
        foreach ($b in [byte[]]@(0,255,10,2,0)) { $script:port.Input.Enqueue($b) }
        $connection = Open-ArinstConnection $script:testSerial
        $connection.drain.discarded_bytes | Should Be 5
        $connection.identity.serial | Should Be $script:testSerial
        ($script:port.Sent -join '|') | Should Be '*IDN?|SYST:ACT?'
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
    It 'refuses a different UID and releases the exclusive handle' {
        $script:port.Answers['*IDN?'] = 'Arinst,VNA-DL,1-2-3,2.0.0'
        { Open-ArinstConnection $script:testSerial } | Should Throw 'UID не совпадает'
        $script:port.Disposed | Should Be $true
        ($script:port.Sent -join '|') | Should Be '*IDN?'
        $saved = Read-VnaJson (Join-Path (Get-VnaControlRoot) ('arinst-identify-' + $script:testSerial.Substring(7) + '.json'))
        $saved.sha256 | Should Match '^[a-f0-9]{64}$'
        [Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($saved.evidence.wire[0].rx_base64)) | Should Match 'Arinst,VNA-DL,1-2-3'
    }
    It 'refuses non-Arinst and pre-SCPI firmware' {
        { ConvertFrom-ArinstIdn 'Other,VNA-DL,1-2-3,2.0.0' } | Should Throw
        { ConvertFrom-ArinstIdn 'Arinst,VNA-DL,1-2-3,1.9.9' } | Should Throw
        { Assert-ArinstSerial 'arinst:4294967296-2-3' } | Should Throw
        { Assert-ArinstSerial 'arinst:01-2-3' } | Should Throw
    }
    It 'reports locked licence without trying activation or reset' {
        $script:port.Answers['SYST:ACT?'] = 'LOCKED,0'
        $connection = Open-ArinstConnection $script:testSerial
        $connection.license.license_state | Should Be 'LOCKED'
        $connection.license.message | Should Match 'переподключите прибор вручную'
        ($script:port.Sent -join '|') | Should Be '*IDN?|SYST:ACT?'
        $connection.port.Dispose()
    }
    It 'has a finite drain deadline even when silence is not reached' {
        { Wait-ArinstSilence $script:port 100 5 } | Should Throw 'продолжает передавать'
        $script:port.Sent.Count | Should Be 0
    }
    It 'fails a query once and never retries after its deadline' {
        $connection = New-ArinstTestConnection $script:port
        $script:port.Answers.Remove('*IDN?')
        { Invoke-ArinstWire $connection '*IDN?' 5 } | Should Throw 'результат операции неизвестен'
        { Invoke-ArinstWire $connection '*IDN?' 5 } | Should Throw 'повтор команды запрещён'
        $script:port.Sent.Count | Should Be 1
    }
    It 'rejects unsolicited, binary and overlong replies' {
        $connection = New-ArinstTestConnection $script:port
        $script:port.Input.Enqueue(255)
        { Invoke-ArinstWire $connection '*IDN?' } | Should Throw 'неожиданные байты'
        $script:port.Input.Clear(); $connection.failed = $false
        $script:port.Answers['*IDN?'] = [string][char]1
        { Invoke-ArinstWire $connection '*IDN?' } | Should Throw 'нетекстовый'
        $connection.failed = $false
        $script:port.Input.Clear(); $script:port.Answers['*IDN?'] = 'x' * 65537
        { Invoke-ArinstWire $connection '*IDN?' } | Should Throw 'размер'
    }
    It 'has a closed wire vocabulary and commissioning gate' {
        $connection = New-ArinstTestConnection $script:port $false
        foreach ($command in @('*RST','SYST:ACT KEY','ABOR','SOUR:POW 0','SENS:FREQ:STAR 1;*RST')) {
            { Invoke-ArinstWire $connection $command } | Should Throw 'разрешённом протоколе'
        }
        { Invoke-ArinstWire $connection 'INIT' } | Should Throw 'вводе в эксплуатацию'
        $script:port.Sent.Count | Should Be 0
    }
}

Describe 'Arinst: bounded typed measurements' {
    BeforeEach {
        $script:port = New-ArinstTestPort
        Mock Read-AgentConfig { @{ arinstDevices = @(@{ serial = $script:testSerial; usb_path = $script:testUsb; settings_verified = $true }) } }
        Mock Get-ArinstUsbCandidates { @(@{ usb_path = $script:testUsb; port = 'COM3' }) }
        Mock Get-Process { @() }
        Mock New-ArinstSerialPort { $script:port }
        Mock Save-ArinstObservation {}
        Mock Assert-VnaLease {}
        Mock Write-VnaJson {}
        Mock Test-Path { $false }
    }
    It 'returns only measured channels with unknown correction/grid and raw evidence' {
        $result = Invoke-ArinstCommand (New-ArinstTestCommand) @{}
        ($result.acquisition.measured_channels -join ',') | Should Be 'S11,S21'
        $result.acquisition.data.S11.Count | Should Be 2
        $result.acquisition.data.S21[1][1] | Should Be 0.8
        $result.acquisition.data.ContainsKey('S12') | Should Be $false
        $result.acquisition.frequencies_hz | Should Be $null
        $result.acquisition.grid_confirmed | Should Be $false
        $result.acquisition.correction_origin | Should Be 'unknown'
        $result.raw_sha256 | Should Be (Get-VnaDigest (ConvertTo-VnaCanonical $result.raw_evidence))
        @($script:port.Sent | Where-Object { $_ -eq 'INIT' }).Count | Should Be 1
        $script:port.Disposed | Should Be $true
    }
    It 'checks lease before serial access' {
        Mock Assert-VnaLease { throw 'lease expired' }
        { Invoke-ArinstCommand (New-ArinstTestCommand) @{} } | Should Throw 'lease expired'
        Assert-MockCalled New-ArinstSerialPort -Times 0 -Exactly -Scope It
    }
    It 'rejects changed readback, truncated pairs and nonfinite numbers' {
        { ConvertFrom-ArinstPairs '1,2,3' 2 } | Should Throw 'число'
        { ConvertFrom-ArinstPairs 'NaN,0,1,0' 2 } | Should Throw 'число'
        { ConvertFrom-ArinstPairs '1e999,0,1,0' 2 } | Should Throw 'число'
        $script:port.Answers['SENS:SWE:POIN?'] = '3'
        { Invoke-ArinstCommand (New-ArinstTestCommand) @{} } | Should Throw 'не совпала'
        @($script:port.Sent | Where-Object { $_ -eq 'INIT' }).Count | Should Be 0
        $script:port.Disposed | Should Be $true
    }
    It 'rejects one point, unsupported bandwidth, power and arbitrary arguments before serial access' {
        foreach ($patch in @(@{ points = 1 }, @{ if_bandwidth_hz = 75 }, @{ power_dbm = -3 }, @{ start_hz = 1 })) {
            $command = New-ArinstTestCommand
            foreach ($key in $patch.Keys) { $command.args.profile[$key] = $patch[$key] }
            { Invoke-ArinstCommand $command @{} } | Should Throw
        }
        $command = New-ArinstTestCommand; $command.args.scpi = '*RST'
        { Invoke-ArinstCommand $command @{} } | Should Throw 'аргументы'
        Assert-MockCalled New-ArinstSerialPort -Times 0 -Exactly -Scope It
    }
    It 'reports activated and expired trials without any renewal operation' {
        (ConvertFrom-ArinstLicense 'ACTIVATED,0').message | Should Be 'Лицензия активна'
        (ConvertFrom-ArinstLicense 'TRIAL,0').license_state | Should Be 'LOCKED'
        (ConvertFrom-ArinstLicense 'TRIAL,61').message | Should Be 'Пробный режим API — осталось 2 мин'
        { ConvertFrom-ArinstLicense 'TRIAL,601' } | Should Throw
        { ConvertFrom-ArinstLicense 'ACTIVATED,1' } | Should Throw
    }
    It 'does not acquire when local setter persistence is unverified' {
        Mock Read-AgentConfig { @{ arinstDevices = @(@{ serial = $script:testSerial; usb_path = $script:testUsb }) } }
        { Invoke-ArinstCommand (New-ArinstTestCommand) @{} } | Should Throw 'вводе в эксплуатацию'
        ($script:port.Sent -join '|') | Should Be '*IDN?|SYST:ACT?'
        $script:port.Disposed | Should Be $true
    }
    It 'applies only supported settings and verifies all readbacks' {
        $command = New-ArinstTestCommand 'apply_profile'
        $command.args.profile.start_hz = 3000000; $command.args.profile.stop_hz = 4000000
        $result = Invoke-ArinstCommand $command @{}
        $result.profile.start_hz | Should Be 3000000
        $writes = @($script:port.Sent | Where-Object { $_ -match '^SENS:.* ' })
        ($writes -join '|') | Should Be 'SENS:FREQ:STOP 4000000|SENS:FREQ:STAR 3000000|SENS:SWE:POIN 2|SENS:BAND 1000'
    }
    It 'does not claim RF was stopped when closing a lease' {
        $result = Invoke-ArinstCommand (New-ArinstTestCommand 'close') @{}
        $result.closed | Should Be $true
        $result.rf_stopped | Should Be $null
        ($script:port.Sent -join '|') | Should Be '*IDN?|SYST:ACT?'
    }
    It 'quarantines uncertain acquisition separately from Planar and retains evidence' {
        $script:port.Answers['*OPC?'] = '0'
        $command = New-ArinstTestCommand
        { Invoke-ArinstCommand $command @{} } | Should Throw 'не подтверждено'
        Assert-MockCalled Write-VnaJson -Times 1 -Scope It -ParameterFilter { $Path -match 'arinst-quarantine-' }
        Assert-MockCalled Write-VnaJson -Times 1 -Scope It -ParameterFilter { $Path -match 'arinst-failure-' }
        $script:port.Disposed | Should Be $true
    }
    It 'heartbeat reads only cached identity and never opens serial' {
        Mock Read-VnaJson { [pscustomobject]@{ serial = $script:testSerial; usb_path = $script:testUsb
            observed_at = [DateTime]::UtcNow.AddSeconds(-10).ToString('o'); firmware = '2.0.0'; hardware_uid = $script:testSerial.Substring(7)
            license_state = 'TRIAL'; trial_remaining_seconds = 600 } }
        $inventory = @(Get-ArinstControlInventory @(@{ usb_path = $script:testUsb; port = 'COM7' }))
        $inventory.Count | Should Be 1
        $inventory[0].control_ready | Should Be $true
        $inventory[0].trial_remaining_seconds | Should Be 600
        Assert-MockCalled New-ArinstSerialPort -Times 0 -Exactly -Scope It
    }
    It 'keeps polling a configured UID for explicit connect after its cached observation expires' {
        Mock Get-MachineId { 'pc-1' }
        Mock Get-VnaObservation { $null }
        Mock Invoke-ErpApi { @{ job = $null; poll_after_ms = 500 } }
        $script:VnaControlInitialized = $true; $script:VnaWorker = $null
        try { Invoke-ScanPollOnce 'http://test' 'token' | Should Be 'idle' }
        finally { $script:VnaControlInitialized = $false }
        Assert-MockCalled Invoke-ErpApi -Times 1 -Scope It -ParameterFilter {
            $Body.observed_vna_serial -ceq $script:testSerial -and $Body.kinds -contains 'vna_control' }
        Assert-MockCalled New-ArinstSerialPort -Times 0 -Exactly -Scope It
    }
    It 'supervisor retains the partial evidence hash when the worker is killed' {
        $command = New-ArinstTestCommand
        $script:entry = [pscustomobject]@{ state = 'executing'; payload = $null }
        Mock Get-VnaCommandPath { Join-Path (Get-VnaControlRoot) 'test-command.json' }
        Mock Read-VnaJson {
            if ($Path -match 'arinst-wire-') { return @{ sha256 = ('a' * 64) } }
            return $script:entry
        }
        $process = [pscustomobject]@{ HasExited = $false; Killed = $false }
        $process | Add-Member ScriptMethod Kill { $this.Killed = $true; $this.HasExited = $true }
        $process | Add-Member ScriptMethod WaitForExit { param($ms) return $true }
        $process | Add-Member ScriptMethod Dispose {}
        Stop-VnaWorker @{ command = $command; process = $process } 'Результат неизвестен'
        $process.Killed | Should Be $true
        $script:entry.state | Should Be 'uncertain'
        $script:entry.evidence_checkpoint.sha256 | Should Be ('a' * 64)
        $script:entry.evidence_checkpoint.parser_status | Should Be 'incomplete'
        Assert-MockCalled Write-VnaJson -Times 1 -Scope It -ParameterFilter { $Path -match 'arinst-quarantine-' }
    }
}

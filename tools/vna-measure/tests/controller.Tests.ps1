# Pester 3.x; offline only. Importing the console never connects to COM or ERP.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here '..\vna-measure.ps1')

function New-SafeVnaContext {
    $time = [datetimeoffset]::UtcNow
    $device = [pscustomobject]@{ Ready = $true; VnaSerial = 'TEST-VNA'; AcmSerial = 'TEST-ACM'; VnaTemperature = 30.2; VnaTemperatures = @(30.2, 31.4); AcmTemperature = 28.1 }
    $cal = [pscustomobject]@{ Id = 'cal-1'; Time = $time.AddMinutes(-10).ToString('o'); VnaSerial = 'TEST-VNA'; AcmSerial = 'TEST-ACM';
        VnaTemperature = 30.2; VnaTemperatures = @(30.2,31.4); AcmTemperature = 28.1; LoadCheck = 'pass' }
    $data = New-VnaData -Frequencies @(10e6,18e9) -S11 @([Numerics.Complex]::new(0.01,0),[Numerics.Complex]::new(0.02,0)) `
        -S21 @([Numerics.Complex]::new(0.98,0),[Numerics.Complex]::new(0.97,0)) -S12 @([Numerics.Complex]::new(0.98,0),[Numerics.Complex]::new(0.97,0)) `
        -S22 @([Numerics.Complex]::new(0.02,0),[Numerics.Complex]::new(0.01,0))
    $states = @{ S11 = 'COR'; S21 = 'COR'; S12 = 'COR'; S22 = 'COR' }
    $sweep = [pscustomobject]@{ Id = 'sweep-1'; CapturedAt = $time.AddMinutes(-1).ToString('o'); Data = $data; TraceStatus = $states.Clone(); DeviceInfo = $device.PSObject.Copy() }
    return [pscustomobject]@{ Sweep = $sweep; Calibration = $cal; Device = $device; States = $states; Now = $time }
}

Describe 'Adapter chain and card series' {
    It 'parses direct, left and two fixtures without accepting stray text' {
        (ConvertFrom-VnaChain 'напрямую').Left | Should BeNullOrEmpty
        (ConvertFrom-VnaChain 'слева си.009').Left | Should Be 'СИ.009'
        $chain = ConvertFrom-VnaChain 'слева СИ.009, справа СИ.008'
        $chain.Left | Should Be 'СИ.009'
        $chain.Right | Should Be 'СИ.008'
        (ConvertFrom-VnaChain 'справа СИ.008').Right | Should Be 'СИ.008'
        { ConvertFrom-VnaChain 'слева СИ.009 garbage' } | Should Throw
        { ConvertFrom-VnaChain ' ' } | Should Throw
        { ConvertFrom-VnaChain 'идеальный неизвестный' } | Should Throw
    }
    It 'selects the weakest connector series in a mixed card name' {
        (Get-VnaSeriesFmaxHz 'ADP1A-35F-35F SMA') | Should Be 18e9
        (Get-VnaSeriesFmaxHz 'SN-312 SMA male - N female') | Should Be 18e9
        (Get-VnaSeriesFmaxHz 'N-type male - N female') | Should Be 18e9
        (Get-VnaSeriesFmaxHz 'ADP TNC male - TNC female') | Should Be 11e9
        (Get-VnaSeriesFmaxHz 'ST-321 SMA female - TNC male') | Should Be 11e9
        (Get-VnaSeriesFmaxHz 'TN-322') | Should Be 11e9
        (Get-VnaSeriesFmaxHz 'SB-311 SMA BNC') | Should Be 4e9
        (Get-VnaSeriesFmaxHz 'TB-322 TNC BNC') | Should Be 4e9
        (Get-VnaSeriesFmaxHz 'B-312') | Should Be 4e9
    }
    It 'rejects reassigned inventory numbers or edited physical serial after preview' {
        $measured = [pscustomobject]@{id='id1';serial_number='serial1'}
        { Assert-VnaCardIdentity $measured ([pscustomobject]@{id='id2';serial_number='serial1'}) } | Should Throw
        { Assert-VnaCardIdentity $measured ([pscustomobject]@{id='id1';serial_number='serial2'}) } | Should Throw
        { Assert-VnaCardIdentity $measured ([pscustomobject]@{id='id1';serial_number='serial1'}) } | Should Not Throw
    }
}

Describe 'Measurement safety before attachment' {
    BeforeEach { $script:ctx = New-SafeVnaContext }
    It 'accepts a fresh corrected sweep with a passed load check' {
        { Assert-VnaMeasurementSafe $script:ctx.Sweep $script:ctx.Calibration $script:ctx.Device $script:ctx.States -Now $script:ctx.Now } | Should Not Throw
    }
    It 'refuses a missing trace' {
        $script:ctx.Sweep.Data.S12 = $null
        { Assert-VnaMeasurementSafe $script:ctx.Sweep $script:ctx.Calibration $script:ctx.Device $script:ctx.States } | Should Throw
    }
    It 'refuses an uncorrected live trace and an uncorrected captured trace' {
        $script:ctx.States.S21 = 'C?'
        { Assert-VnaMeasurementSafe $script:ctx.Sweep $script:ctx.Calibration $script:ctx.Device $script:ctx.States } | Should Throw
        $script:ctx.States.S21 = 'COR'; $script:ctx.Sweep.TraceStatus.S12 = 'C!'
        { Assert-VnaMeasurementSafe $script:ctx.Sweep $script:ctx.Calibration $script:ctx.Device $script:ctx.States } | Should Throw
    }
    It 'refuses calibration older than four hours, including after operator delay' {
        { Assert-VnaMeasurementSafe $script:ctx.Sweep $script:ctx.Calibration $script:ctx.Device $script:ctx.States -Now $script:ctx.Now.AddHours(4) } | Should Throw
    }
    It 'refuses temperature drift of either VNA sensor' {
        $script:ctx.Device.VnaTemperatures = @(30.2,36.5)
        { Assert-VnaMeasurementSafe $script:ctx.Sweep $script:ctx.Calibration $script:ctx.Device $script:ctx.States } | Should Throw
    }
    It 'refuses drift recorded at capture even if live temperature recovered' {
        $script:ctx.Sweep.DeviceInfo.VnaTemperatures = @(36,31.4)
        { Assert-VnaMeasurementSafe $script:ctx.Sweep $script:ctx.Calibration $script:ctx.Device $script:ctx.States } | Should Throw
    }
    It 'refuses unknown temperature and changed instrument identity' {
        $script:ctx.Device.AcmTemperature = $null
        { Assert-VnaMeasurementSafe $script:ctx.Sweep $script:ctx.Calibration $script:ctx.Device $script:ctx.States } | Should Throw
        $script:ctx = New-SafeVnaContext; $script:ctx.Device.VnaSerial = 'OTHER'
        { Assert-VnaMeasurementSafe $script:ctx.Sweep $script:ctx.Calibration $script:ctx.Device $script:ctx.States } | Should Throw
    }
    It 'refuses unverified or failed load checks' {
        $script:ctx.Calibration.LoadCheck = 'not_checked'
        { Assert-VnaMeasurementSafe $script:ctx.Sweep $script:ctx.Calibration $script:ctx.Device $script:ctx.States } | Should Throw
        $script:ctx.Calibration.LoadCheck = 'fail'
        { Assert-VnaMeasurementSafe $script:ctx.Sweep $script:ctx.Calibration $script:ctx.Device $script:ctx.States } | Should Throw
    }
    It 'refuses a sweep predating calibration' {
        $script:ctx.Sweep.CapturedAt = $script:ctx.Now.AddHours(-1).ToString('o')
        { Assert-VnaMeasurementSafe $script:ctx.Sweep $script:ctx.Calibration $script:ctx.Device $script:ctx.States } | Should Throw
    }
    It 'accepts CLR numeric temperatures in Russian locale' {
        $culture = [Threading.Thread]::CurrentThread.CurrentCulture
        try { [Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo('ru-RU'); { Assert-VnaTemperature 30.2 } | Should Not Throw }
        finally { [Threading.Thread]::CurrentThread.CurrentCulture = $culture }
    }
}

Describe 'Durable raw-sweep ledger' {
    BeforeEach { $script:ctx = New-SafeVnaContext; $script:ledgerFile = Join-Path $TestDrive ([guid]::NewGuid().ToString() + '.jsonl') }
    It 'blocks the same data under a different sweep ID after reservation, even without upload success' {
        $hash = Reserve-VnaSweep $script:ctx.Sweep 'СИ.035' $script:ledgerFile 'raw.s2p'
        $hash.Length | Should Be 64
        $script:ctx.Sweep.Id = 'different-guid'
        { Assert-VnaMeasurementSafe $script:ctx.Sweep $script:ctx.Calibration $script:ctx.Device $script:ctx.States -LedgerPath $script:ledgerFile } | Should Throw
        { Reserve-VnaSweep $script:ctx.Sweep 'СИ.036' $script:ledgerFile 'raw2.s2p' } | Should Throw
        @(Get-VnaJournal $script:ledgerFile).Count | Should Be 1
    }
    It 'detects corrupt or incomplete ledger rather than treating it as empty' {
        [IO.File]::WriteAllText($script:ledgerFile, '{truncated')
        { Reserve-VnaSweep $script:ctx.Sweep 'СИ.035' $script:ledgerFile 'raw.s2p' } | Should Throw
        [IO.File]::WriteAllText($script:ledgerFile, '{"Event":"reserved"}')
        { Reserve-VnaSweep $script:ctx.Sweep 'СИ.035' $script:ledgerFile 'raw.s2p' } | Should Throw
    }
    It 'records calibration attempts and load verdicts as correlated append-only events' {
        Add-VnaJournalEvent $script:ledgerFile @{Event='calibration';Id='cal1';Result='pass';LoadCheck='not_checked'}
        Add-VnaJournalEvent $script:ledgerFile @{Event='load_check';CalibrationId='cal1';LoadCheck='fail'}
        $events = @(Get-VnaJournal $script:ledgerFile)
        $events.Count | Should Be 2
        $events[0].LoadCheck | Should Be 'not_checked'
        $events[1].CalibrationId | Should Be 'cal1'
    }
}

Describe 'Offline console workflow' {
    It 'never restores a foreign backup when startup rejects its identity' {
        $folder = Join-Path $TestDrive 'foreign'
        [void][IO.Directory]::CreateDirectory($folder)
        $state = Join-Path $folder 'foreign.sta'; [IO.File]::WriteAllText($state, 'foreign')
        @{ VnaSerial = 'OTHER-VNA'; Path = $state } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $folder 'operator-backup.json') -Encoding UTF8
        Mock Confirm-VnaAction { $true }
        Mock Restore-VnaState { throw 'MUST NOT RESTORE' }
        { Start-VnaMeasure -Offline -DataDirectory $folder } | Should Throw
        Assert-MockCalled Confirm-VnaAction -Times 0 -Exactly -Scope It
        Assert-MockCalled Restore-VnaState -Times 0 -Exactly -Scope It
        (Test-Path -LiteralPath (Join-Path $folder 'operator-backup.json')) | Should Be $true
    }
    It 'rechecks backup ownership immediately before restore' {
        $connection = [pscustomobject]@{ Offline=$true; Released=$false; Device=[pscustomobject]@{Ready=$true;VnaSerial='CURRENT'}; BoundSerial='CURRENT' }
        Mock Restore-VnaState {}
        { Restore-VnaOperatorState $connection ([pscustomobject]@{VnaSerial='OTHER';Path='old.sta'}) } | Should Throw
        Assert-MockCalled Restore-VnaState -Times 0 -Exactly -Scope It
    }
}

Describe 'Offline recalibration lifecycle' {
    It 'preserves the first backup across recalibration and invalidates calibration after a failed attempt' {
        $script:answers = New-Object 'System.Collections.Generic.Queue[string]'
        foreach ($answer in @('2','д','2','д','4','0','д')) { $script:answers.Enqueue($answer) }
        Mock Read-Host { if ($script:answers.Count -eq 0) { throw 'Unexpected extra prompt' }; return $script:answers.Dequeue() }
        Mock New-VnaApplication { throw 'REAL COM FORBIDDEN' }
        Mock New-VnaErpSession { throw 'LOGIN MUST NOT BE ATTEMPTED' }
        $script:calibrations = 0
        Mock Invoke-VnaCalibration {
            $script:calibrations++
            if ($script:calibrations -eq 2) { throw 'Calibration failed' }
            $Connection.Calibrated = $true
            foreach ($name in 'S11','S21','S12','S22') { $Connection.TraceStatus[$name] = 'COR' }
        }
        $folder = Join-Path $TestDrive 'recalibrate'
        Start-VnaMeasure -Offline -DataDirectory $folder
        @(Get-ChildItem -LiteralPath $folder -Filter 'operator-*.sta').Count | Should Be 1
        $events = @(Get-VnaJournal (Join-Path $folder 'calibrations.jsonl'))
        $events.Count | Should Be 2
        $events[0].Result | Should Be 'pass'
        $events[1].Result | Should Be 'fail'
        Assert-MockCalled New-VnaErpSession -Times 0 -Exactly -Scope It
        $script:answers.Count | Should Be 0
    }
}

Describe 'Offline restart recovery' {
    It 'offers restoration of the pending backup without querying a disconnected ACM' {
        $folder = Join-Path $TestDrive 'restart'
        [void][IO.Directory]::CreateDirectory($folder)
        $mockFolder = Join-Path $folder 'mock'; Initialize-VnaOfflineDemo $mockFolder
        $state = Join-Path $folder 'operator-original.sta'; [IO.File]::WriteAllText($state, '{}')
        @{ VnaSerial='MOCK-VNA'; Path=$state } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $folder 'operator-backup.json') -Encoding UTF8
        Mock Get-VnaDeviceInfo { throw 'ACM disconnected' }
        Mock Read-Host { '0' }
        Mock Confirm-VnaAction { $true }
        Start-VnaMeasure -Offline -DataDirectory $folder
        (Test-Path -LiteralPath (Join-Path $folder 'operator-backup.json')) | Should Be $false
        Assert-MockCalled Get-VnaDeviceInfo -Times 0 -Exactly -Scope It
        Assert-MockCalled Confirm-VnaAction -Times 1 -Exactly -Scope It
    }
}

Describe 'Offline journal failure' {
    It 'blocks adapter work when the passing load check could not be journaled' {
        $script:answers = New-Object 'System.Collections.Generic.Queue[string]'
        foreach ($answer in @('2','д','3','','','4','0','д')) { $script:answers.Enqueue($answer) }
        Mock Read-Host { if ($script:answers.Count -eq 0) { throw 'Unexpected extra prompt' }; return $script:answers.Dequeue() }
        Mock New-VnaErpSession { throw 'LOGIN MUST NOT BE ATTEMPTED' }
        $folder = Join-Path $TestDrive 'unlogged-load'
        $script:journalWrites = 0
        Mock Add-VnaJournalEvent {
            if ($Event.Event -eq 'load_check') { throw 'Disk write failed' }
            $script:journalWrites++
        }
        Start-VnaMeasure -Offline -DataDirectory $folder
        $script:journalWrites | Should Be 1
        $script:answers.Count | Should Be 0
        Assert-MockCalled New-VnaErpSession -Times 0 -Exactly -Scope It
    }
}

Describe 'Offline complete session' {
    It 'calibrates, load-checks, captures, attaches, and restores without COM or HTTP' {
        $script:answers = New-Object 'System.Collections.Generic.Queue[string]'
        foreach ($answer in @('2','д','3','','','4','СИ.035','напрямую','','','д','0','д')) { $script:answers.Enqueue($answer) }
        Mock Read-Host { if ($script:answers.Count -eq 0) { throw 'Unexpected extra prompt' }; return $script:answers.Dequeue() }
        Mock New-VnaApplication { throw 'REAL COM FORBIDDEN' }
        Mock Invoke-RestMethod { throw 'NETWORK FORBIDDEN' }
        Mock Invoke-WebRequest { throw 'NETWORK FORBIDDEN' }
        $folder = Join-Path $TestDrive 'console'
        Start-VnaMeasure -Offline -DataDirectory $folder
        $script:answers.Count | Should Be 0
        $db = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $folder 'mock\instruments.json') | ConvertFrom-Json
        @($db.instruments[0].files).Count | Should Be 1
        $db.instruments[0].files[0].file_name | Should Match 'Замер ВАЦ S50180, АКМ, .*СИ\.035\.s2p'
        @(Get-VnaJournal (Join-Path $folder 'used-sweeps.jsonl')).Count | Should Be 2
        $events = @(Get-VnaJournal (Join-Path $folder 'calibrations.jsonl'))
        $events.Count | Should Be 2
        $events[1].LoadCheck | Should Be 'pass'
        (Test-Path -LiteralPath (Join-Path $folder 'operator-backup.json')) | Should Be $false
        Assert-MockCalled New-VnaApplication -Times 0 -Exactly
        Assert-MockCalled Invoke-RestMethod -Times 0 -Exactly
        Assert-MockCalled Invoke-WebRequest -Times 0 -Exactly
    }
}

Describe 'Display failure during a measurement' {
    It 'warns and still measures when the item screen cannot be set' {
        $script:answers = New-Object 'System.Collections.Generic.Queue[string]'
        foreach ($answer in @('2','д','3','','','4','СИ.035','напрямую','','','н','0','д')) { $script:answers.Enqueue($answer) }
        Mock Read-Host { if ($script:answers.Count -eq 0) { throw 'Unexpected extra prompt' }; return $script:answers.Dequeue() }
        Mock New-VnaApplication { throw 'REAL COM FORBIDDEN' }
        Mock Invoke-RestMethod { throw 'NETWORK FORBIDDEN' }
        Mock Invoke-WebRequest { throw 'NETWORK FORBIDDEN' }
        Mock Set-VnaMeasureScreen { throw 'S2VNA display error' }
        $script:displayOutput = New-Object 'System.Collections.Generic.List[string]'
        Mock Write-Host { param($Object); $script:displayOutput.Add([string]$Object) }
        Start-VnaMeasure -Offline -DataDirectory (Join-Path $TestDrive 'display-failure')
        $output = $script:displayOutput -join "`n"
        $output | Should Match 'Экран замера не перестроен: S2VNA display error'
        $output | Should Match 'КСВН сторона 1: '
        $output | Should Match 'Локальный результат: '
        $script:answers.Count | Should Be 0
    }
}

Describe 'Instrument state verdicts' {
    BeforeEach {
        $script:stateContext = New-SafeVnaContext
        $script:stateContext.Calibration | Add-Member NoteProperty Result 'pass'
        $traces = @(foreach ($spec in Get-VnaScreenTraces) {
            $trace = [ordered]@{ Parameter = $spec.Parameter; Format = $spec.Format; PDIVision = $null; RLEVel = $null; RPOSition = $null }
            foreach ($member in $spec.Scale.Keys) { $trace[$member] = $spec.Scale[$member] }
            [pscustomobject]$trace
        })
        $script:stateConnection = [pscustomobject]@{ Offline = $true; Released = $false; Device = $script:stateContext.Device
            Traces = $traces; TraceStatus = $script:stateContext.States.Clone(); ProfileApplied = $true; Calibrated = $true }
        $script:stateOutput = New-Object 'System.Collections.Generic.List[string]'
        Mock Write-Host { param($Object); $script:stateOutput.Add([string]$Object) }
        Mock New-VnaApplication { throw 'REAL COM FORBIDDEN' }
    }
    It 'prints trace definitions formats scales COR age and a valid verdict without changing scales' {
        $before = $script:stateConnection.Traces | ConvertTo-Json -Compress
        Show-VnaInstrumentState $script:stateConnection $script:stateContext.Calibration -Now $script:stateContext.Now
        $output = $script:stateOutput -join "`n"
        $output | Should Match 'Трасс: 12'
        $output | Should Match 'S11, SWR; шкала:'
        $output | Should Match 'S21, MLOG; шкала:'
        $output | Should Match 'коррекция: COR'
        $output | Should Match 'Возраст калибровки: 10'
        $output | Should Match 'Калибровка действует'
        $output | Should Match 'Экран замера стандартный'
        $output | Should Match 'S11, SMIT; шкала:'
        $output | Should Match 'S21, GDEL; шкала:'
        ($script:stateConnection.Traces | ConvertTo-Json -Compress) | Should Be $before
    }
    It 'reports missing calibration even when all live traces are COR' {
        Show-VnaInstrumentState $script:stateConnection $null
        ($script:stateOutput -join "`n") | Should Match 'Калибровки нет — выполните пункт 2'
        ($script:stateOutput -join "`n") | Should Not Match 'Калибровка действует'
    }
    It 'reports a lost live correction despite a fresh passing calibration record' {
        $script:stateConnection.TraceStatus.S12 = 'C?'
        Show-VnaInstrumentState $script:stateConnection $script:stateContext.Calibration
        $output = $script:stateOutput -join "`n"
        $output | Should Match 'S12, MLOG;.*коррекция: нет'
        $output | Should Match 'Калибровки нет — выполните пункт 2'
        $output | Should Not Match 'Калибровка действует'
    }
    It 'reports nonstandard scales separately from a valid calibration' {
        $script:stateConnection.Traces[1].RPOSition = 5
        Show-VnaInstrumentState $script:stateConnection $script:stateContext.Calibration
        ($script:stateOutput -join "`n") | Should Match 'Экран не стандартный — пункт 6'
        ($script:stateOutput -join "`n") | Should Match 'Калибровка действует'
    }
    It 'reports expired calibration and its age' {
        Show-VnaInstrumentState $script:stateConnection $script:stateContext.Calibration -Now $script:stateContext.Now.AddHours(4)
        ($script:stateOutput -join "`n") | Should Match 'Возраст калибровки: 250'
        ($script:stateOutput -join "`n") | Should Match 'Калибровки нет — выполните пункт 2'
    }
    It 'rejects identity changes unknown temperatures and thermal drift' {
        $script:stateConnection.Device.VnaSerial = 'REPLACEMENT'
        Show-VnaInstrumentState $script:stateConnection $script:stateContext.Calibration
        ($script:stateOutput -join "`n") | Should Not Match 'Калибровка действует'
        $script:stateConnection.Device.VnaSerial = 'TEST-VNA'
        $script:stateConnection.Device.AcmTemperature = $null
        Show-VnaInstrumentState $script:stateConnection $script:stateContext.Calibration
        ($script:stateOutput -join "`n") | Should Not Match 'Калибровка действует'
        $script:stateConnection.Device.AcmTemperature = 28.1
        $script:stateConnection.Device.VnaTemperatures = @(30.2, 40)
        Show-VnaInstrumentState $script:stateConnection $script:stateContext.Calibration
        ($script:stateOutput -join "`n") | Should Not Match 'Калибровка действует'
    }
    It 'prints actual trace count and rejects an incomplete layout' {
        $script:stateConnection.Traces = @($script:stateConnection.Traces[0])
        $script:stateConnection.ProfileApplied = $false
        Show-VnaInstrumentState $script:stateConnection $script:stateContext.Calibration
        ($script:stateOutput -join "`n") | Should Match 'Трасс: 1'
        ($script:stateOutput -join "`n") | Should Match 'Калибровки нет — выполните пункт 2'
    }
    It 'still prints traces and a missing calibration verdict with disconnected ACM telemetry' {
        Mock Get-VnaDeviceInfo { throw 'АКМ не подключён' }
        Show-VnaInstrumentState $script:stateConnection $script:stateContext.Calibration
        ($script:stateOutput -join "`n") | Should Match 'АКМ не подключён'
        ($script:stateOutput -join "`n") | Should Match 'Трасс: 12'
        ($script:stateOutput -join "`n") | Should Match 'коррекция: COR'
        ($script:stateOutput -join "`n") | Should Match 'Калибровки нет — выполните пункт 2'
        ($script:stateOutput -join "`n") | Should Not Match 'Калибровка действует'
    }
}

Describe 'Offline chart menu and lost correction' {
    It 'applies the menu six screen and restores the initial display without recalibrating' {
        $script:answers = New-Object 'System.Collections.Generic.Queue[string]'
        foreach ($answer in @('6','1','5','1','0')) { $script:answers.Enqueue($answer) }
        Mock Read-Host { if ($script:answers.Count -eq 0) { throw 'Unexpected extra prompt' }; $script:answers.Dequeue() }
        Mock Invoke-VnaCalibration { throw 'MUST NOT CALIBRATE' }
        $script:menuOutput = New-Object 'System.Collections.Generic.List[string]'
        Mock Write-Host { param($Object); $script:menuOutput.Add([string]$Object) }
        Start-VnaMeasure -Offline -DataDirectory (Join-Path $TestDrive 'chart-menu')
        ($script:menuOutput -join "`n") | Should Match 'Экран замера применён и проверен'
        ($script:menuOutput -join "`n") | Should Match 'Экран замера стандартный'
        ($script:menuOutput -join "`n") | Should Match 'Экран не стандартный — пункт 6'
        (Test-Path -LiteralPath (Join-Path $TestDrive 'chart-menu\screen-backup.json')) | Should Be $false
        Assert-MockCalled Invoke-VnaCalibration -Times 0 -Exactly -Scope It
        $script:answers.Count | Should Be 0
    }
    It 'returns only the previous screen with item eight and keeps the full operator backup' {
        $script:answers = New-Object 'System.Collections.Generic.Queue[string]'
        foreach ($answer in @('6','8','1','0','н')) { $script:answers.Enqueue($answer) }
        Mock Read-Host { if ($script:answers.Count -eq 0) { throw 'Unexpected extra prompt' }; $script:answers.Dequeue() }
        Mock Invoke-VnaCalibration { throw 'MUST NOT CALIBRATE' }
        $script:menuOutput = New-Object 'System.Collections.Generic.List[string]'
        Mock Write-Host { param($Object); $script:menuOutput.Add([string]$Object) }
        $folder = Join-Path $TestDrive 'screen-menu'
        Start-VnaMeasure -Offline -DataDirectory $folder
        $output = $script:menuOutput -join "`n"
        $output | Should Match 'Прежний экран возвращён; калибровка не тронута'
        $output | Should Match 'Трасс: 4'
        $output | Should Match 'Экран не стандартный — пункт 6'
        (Test-Path -LiteralPath (Join-Path $folder 'screen-backup.json')) | Should Be $false
        (Test-Path -LiteralPath (Join-Path $folder 'operator-backup.json')) | Should Be $true
        $script:answers.Count | Should Be 0
    }
}

Describe 'Offline correction loss after calibration and load check' {
    It 'refuses an adapter sweep after passing calibration and LOAD if live COR disappears' {
        $script:answers = New-Object 'System.Collections.Generic.Queue[string]'
        foreach ($answer in @('2','д','3','','','4','СИ.035','напрямую','','','0','д')) { $script:answers.Enqueue($answer) }
        $script:loseCorrection = $false
        Mock Read-Host {
            if ($script:answers.Count -eq 0) { throw 'Unexpected extra prompt' }
            $answer = $script:answers.Dequeue()
            if ($answer -eq '4') { $script:loseCorrection = $true }
            return $answer
        }
        Mock Get-VnaTraceStatus {
            param($Connection)
            $states = $Connection.TraceStatus.Clone()
            if ($script:loseCorrection) { $states.S21 = 'C?' }
            return $states
        }
        $script:refusalOutput = New-Object 'System.Collections.Generic.List[string]'
        Mock Write-Host { param($Object); $script:refusalOutput.Add([string]$Object) }
        Mock New-VnaApplication { throw 'REAL COM FORBIDDEN' }
        Mock Invoke-RestMethod { throw 'NETWORK FORBIDDEN' }
        Mock Invoke-WebRequest { throw 'NETWORK FORBIDDEN' }
        $folder = Join-Path $TestDrive 'lost-correction'
        Start-VnaMeasure -Offline -DataDirectory $folder
        $events = @(Get-VnaJournal (Join-Path $folder 'calibrations.jsonl'))
        $events[0].Result | Should Be 'pass'
        $events[1].LoadCheck | Should Be 'pass'
        ($script:refusalOutput -join "`n") | Should Match 'Нет полной коррекции S21; калибровки нет — выполните пункт 2'
        (Test-Path -LiteralPath (Join-Path $folder 'raw')) | Should Be $false
        (Test-Path -LiteralPath (Join-Path $folder 'used-sweeps.jsonl')) | Should Be $false
        $script:answers.Count | Should Be 0
        Assert-MockCalled New-VnaApplication -Times 0 -Exactly -Scope It
    }
}

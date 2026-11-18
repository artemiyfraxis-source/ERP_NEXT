# Pester 3.x. All device boundaries are mocked; never activate S2VNA.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here '..\s2vna.ps1')
. (Join-Path $here '..\dsp.ps1')

function New-TestS2Connection {
    return [pscustomobject]@{ Offline = $false; Released = $false; ProfileApplied = $true; Calibrated = $true
        App = [pscustomobject]@{ Ready = $true; Name = 'Planar, S50180, TEST-VNA, test' } }
}
function New-TestS2Data {
    $frequencies = New-Object double[] 1601
    $reflection = New-Object 'System.Numerics.Complex[]' 1601
    $transmission = New-Object 'System.Numerics.Complex[]' 1601
    for ($index = 0; $index -lt 1601; $index++) {
        $frequencies[$index] = 10e6 + (18e9 - 10e6) * $index / 1600
        $reflection[$index] = [System.Numerics.Complex]::new(0.01,0)
        $transmission[$index] = [System.Numerics.Complex]::new(0.98,-0.01)
    }
    return [pscustomobject]@{ Frequencies = $frequencies; S11 = $reflection; S21 = $transmission; S12 = $transmission; S22 = $reflection }
}

Describe 'S2VNA information and strict profile checks' {
    It 'parses commissioned ECAL CSV fields and frequency units' {
        $module = ConvertFrom-VnaAcmInfo 'ACM2520.2,TEST-ACM,25.1,factory,2026-01-01,100 kHz,20 GHz,1001,3.5mm -F-,3.5mm -F-'
        $module.Serial | Should Be 'TEST-ACM'
        $module.MinHz | Should Be 100000
        $module.MaxHz | Should Be 20000000000
    }
    It 'refuses incomplete or inverted module bands' {
        { ConvertFrom-VnaAcmInfo 'bad,module,25,factory,date,20 GHz,100 kHz' } | Should Throw
        { ConvertFrom-VnaAcmInfo 'bad' } | Should Throw
    }
    It 'reads the two VNA sensors separately from the ACM temperature' {
        Mock Invoke-VnaComIo {
            param($Connection,$Path,$Mode,$Value)
            switch ($Path) {
                'SENSe(1).CORRection.COLLect.ECAL.INFormation' { return 'ACM2520.2,TEST-ACM,24,factory,date,100 kHz,20 GHz' }
                'SYSTem.TEMPerature.SENSor(1)' { return 31.5 }
                'SYSTem.TEMPerature.SENSor(2)' { return 32.5 }
                'SYSTem.COMMunicate.ECAL.TEMPerature.SENSor' { return 24.5 }
                default { throw "Unexpected $Path" }
            }
        }
        $info = Get-VnaDeviceInfo (New-TestS2Connection)
        $info.VnaSerial | Should Be 'TEST-VNA'
        $info.VnaTemperature | Should Be 31.5
        $info.VnaTemperatures[1] | Should Be 32.5
        $info.AcmTemperature | Should Be 24.5
    }
    It 'keeps failed temperature reads unknown instead of inventing zero' {
        Mock Invoke-VnaComIo {
            param($Connection,$Path)
            if ($Path -like '*INFormation') { return 'ACM2520.2,TEST-ACM,24,factory,date,100 kHz,20 GHz' }
            throw 'sensor unavailable'
        }
        $info = Get-VnaDeviceInfo (New-TestS2Connection)
        ($null -eq $info.VnaTemperature) | Should Be $true
        ($null -eq $info.AcmTemperature) | Should Be $true
    }
    It 'accepts numeric COM telemetry under a Russian decimal locale' {
        $culture = [Threading.Thread]::CurrentThread.CurrentCulture
        try {
            [Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo('ru-RU')
            (ConvertTo-VnaTemperature ([double]31.5)) | Should Be 31.5
            (ConvertTo-VnaTemperature ([decimal]24.5)) | Should Be 24.5
            (ConvertTo-VnaTemperature '25.5') | Should Be 25.5
            ($null -eq (ConvertTo-VnaTemperature ([double]::NaN))) | Should Be $true
        } finally { [Threading.Thread]::CurrentThread.CurrentCulture = $culture }
    }
    It 'refuses restore to a replacement instrument before any COM command' {
        $backup = Join-Path $TestDrive 'replacement.sta'
        [IO.File]::WriteAllText($backup, 'test-state')
        $connection = New-TestS2Connection
        $null = Assert-VnaInstrumentIdentity $connection
        $connection.App.Name = 'Planar, S50180, REPLACEMENT, test'
        Mock Invoke-VnaComIo { throw 'No COM mutation allowed' }
        { Restore-VnaState $connection $backup } | Should Throw
        Assert-MockCalled Invoke-VnaComIo -Times 0 -Exactly -Scope It
    }
    It 'can restore the original VNA when ACM telemetry is disconnected' {
        $backup = Join-Path $TestDrive 'disconnected-acm.sta'
        [IO.File]::WriteAllText($backup, 'test-state')
        $connection = New-TestS2Connection
        $null = Assert-VnaInstrumentIdentity $connection
        Mock Get-VnaDeviceInfo { throw 'ACM disconnected' }
        Mock Invoke-VnaComIo {}
        Restore-VnaState $connection $backup
        $connection.Calibrated | Should Be $false
        Assert-MockCalled Get-VnaDeviceInfo -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-VnaComIo -Times 1 -Exactly -Scope It -ParameterFilter { $Path -eq 'MMEMory.LOAD.STATe' -and $Mode -eq 'set' }
    }
    It 'rejects a serial change during backup' {
        $backup = Join-Path $TestDrive 'swapped-during-backup.sta'
        $connection = New-TestS2Connection
        Mock Get-VnaDeviceInfo { [pscustomobject]@{Ready=$true;VnaSerial='TEST-VNA';AcmSerial='TEST-ACM';AcmMinHz=100000;AcmMaxHz=20e9} }
        Mock Invoke-VnaComIo {
            param($Connection,$Path,$Mode,$Value)
            if ($Path -eq 'MMEMory.STORe.STATe' -and $Mode -eq 'set') {
                [IO.File]::WriteAllText($Value, 'state')
                $Connection.App.Name = 'Planar, S50180, REPLACEMENT, test'
            }
        }
        { Backup-VnaState $connection $backup } | Should Throw
        $connection.BoundSerial | Should Be 'TEST-VNA'
    }
    It 'rejects missing or changed controlled readbacks' {
        { Assert-VnaSetting $null $false 'processing' } | Should Throw
        { Assert-VnaSetting $true $false 'processing' } | Should Throw
        { Assert-VnaSetting 'LOG' 'LIN' 'sweep' } | Should Throw
    }
    It 'refuses duplicate and missing traces' {
        Mock Invoke-VnaComIo {
            param($Connection,$Path)
            if ($Path -like '*COUNt') { return 4 }
            if ($Path -like '*DEFine') { return 'S11' }
            if ($Path -like '*STATus') { return 'COR' }
        }
        { Get-VnaTraceStatus (New-TestS2Connection) } | Should Throw
    }
    It 'requires COR on each parameter' {
        $connection = [pscustomobject]@{ Offline = $true; TraceStatus = @{S11='COR';S21='COR';S12='C?';S22='COR'} }
        { Assert-VnaCorrection $connection } | Should Throw
        $connection.TraceStatus.Remove('S12')
        { Assert-VnaCorrection $connection } | Should Throw
    }
    It 'does not retry a failed ECAL acquisition' {
        $script:ecalCalls = 0
        Mock Assert-VnaDeviceReady { [pscustomobject]@{VnaSerial='TEST-VNA';AcmSerial='TEST-ACM'} }
        Mock Assert-VnaProfile {}
        Mock Invoke-VnaComIo {
            param($Connection,$Path,$Mode,$Value)
            if ($Path -like '*SOLT2') { $script:ecalCalls++; throw 'ambiguous device timeout' }
        }
        $connection = New-TestS2Connection
        { Invoke-VnaCalibration $connection } | Should Throw
        $script:ecalCalls | Should Be 1
        $connection.Calibrated | Should Be $false
    }
    It 'does not acquire a sweep before a local calibration' {
        Mock Assert-VnaDeviceReady { [pscustomobject]@{VnaSerial='TEST-VNA';AcmSerial='TEST-ACM'} }
        Mock Assert-VnaProfile {}
        Mock Assert-VnaCorrection { @{S11='COR';S21='COR';S12='COR';S22='COR'} }
        Mock Invoke-VnaComIo { throw 'No capture call expected' }
        $connection = New-TestS2Connection; $connection.Calibrated = $false
        { Get-VnaSweep $connection } | Should Throw
        Assert-MockCalled Invoke-VnaComIo -Times 0 -Exactly -Scope It
    }
    It 'checks the linear grid and complex trace length' {
        $data = New-TestS2Data
        Assert-VnaSweepData $data
        $data.Frequencies[10] = $data.Frequencies[9]
        { Assert-VnaSweepData $data } | Should Throw
        $data = New-TestS2Data; $data.S21 = @($data.S21[0])
        { Assert-VnaSweepData $data } | Should Throw
    }
}

Describe 'S2VNA offline lifecycle' {
    BeforeEach {
        $script:offline = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory $script:offline | Out-Null
        $device = @{ Ready=$true;VnaName='Planar, S50180, TEST-VNA, mock';VnaSerial='TEST-VNA';AcmSerial='TEST-ACM'
            AcmMinHz=100000;AcmMaxHz=20e9;VnaTemperature=30;VnaTemperatures=@(30,31);AcmTemperature=24 }
        [IO.File]::WriteAllText((Join-Path $script:offline 'device.json'), ($device | ConvertTo-Json), [Text.Encoding]::UTF8)
        Write-VnaTouchstone -Path (Join-Path $script:offline 'sweep.s2p') -Data (New-TestS2Data)
        Mock New-VnaApplication { throw 'Offline must never activate COM' }
    }
    It 'backs up once, calibrates and captures different sweep receipts without COM' {
        $connection = New-VnaConnection -OfflineDirectory $script:offline
        $backup = Join-Path $script:offline 'operator.sta'
        Backup-VnaState $connection $backup
        { Backup-VnaState $connection $backup } | Should Throw
        Set-VnaProfile $connection
        $info = Invoke-VnaCalibration $connection
        $info.AcmSerial | Should Be 'TEST-ACM'
        $first = Get-VnaSweep $connection
        $second = Get-VnaSweep $connection
        $first.Data.Frequencies.Count | Should Be 1601
        ($first.Id -ne $second.Id) | Should Be $true
        $first.TraceStatus.S22 | Should Be 'COR'
        Assert-MockCalled New-VnaApplication -Times 0 -Exactly -Scope It
    }
    It 'invalidates calibration and profile after restore' {
        $connection = New-VnaConnection -OfflineDirectory $script:offline
        $backup = Join-Path $script:offline 'operator.sta'
        Backup-VnaState $connection $backup
        Set-VnaProfile $connection
        $null = Invoke-VnaCalibration $connection
        Restore-VnaState $connection $backup
        $connection.Calibrated | Should Be $false
        $connection.ProfileApplied | Should Be $false
        { Get-VnaSweep $connection } | Should Throw
    }
    It 'applies profile scales and restores the operator scales without overwriting them' {
        $connection = New-VnaConnection -OfflineDirectory $script:offline
        $connection.Traces[0].PDIVision = 7.5
        $original = $connection.Traces | ConvertTo-Json -Compress
        $backup = Join-Path $script:offline 'operator-scales.sta'
        Backup-VnaState $connection $backup
        Set-VnaProfile $connection
        Assert-VnaScreen @(Get-VnaTraces $connection)
        $connection.Traces.Count | Should Be 4
        $connection.Traces[0].PDIVision | Should Be 0.02
        $connection.Traces[1].PDIVision | Should Be 0.05
        Restore-VnaState $connection $backup
        ($connection.Traces | ConvertTo-Json -Compress) | Should Be $original
        { Assert-VnaScreen @(Get-VnaTraces $connection) } | Should Throw
    }
    It 'refuses an uncorrected offline trace after calibration' {
        $connection = New-VnaConnection -OfflineDirectory $script:offline
        Set-VnaProfile $connection
        $null = Invoke-VnaCalibration $connection
        $connection.TraceStatus.S12 = 'C!'
        { Get-VnaSweep $connection } | Should Throw
    }
    It 'rejects a missing local sweep and unready device' {
        $connection = New-VnaConnection -OfflineDirectory $script:offline
        Set-VnaProfile $connection
        $null = Invoke-VnaCalibration $connection
        Remove-Item -LiteralPath (Join-Path $script:offline 'sweep.s2p')
        { Get-VnaSweep $connection } | Should Throw
        $connection.Device.Ready = $false
        { Invoke-VnaCalibration $connection } | Should Throw
    }
    It 'refuses a serial change between offline operations including restore' {
        $connection = New-VnaConnection -OfflineDirectory $script:offline
        $backup = Join-Path $script:offline 'original.sta'
        Backup-VnaState $connection $backup
        $connection.Device.VnaSerial = 'REPLACEMENT'
        { Set-VnaProfile $connection } | Should Throw
        { Restore-VnaState $connection $backup } | Should Throw
    }
    It 'closes the connection without terminating the S2VNA application' {
        $connection = New-VnaConnection -OfflineDirectory $script:offline
        Release-VnaConnection $connection
        Release-VnaConnection $connection
        $connection.Released | Should Be $true
        { Get-VnaDeviceInfo $connection } | Should Throw
    }
}

Describe 'S2VNA measurement screen over mocked COM' {
    BeforeEach {
        $script:chartConnection = New-TestS2Connection
        $script:VnaActivePreset = $script:VnaDefaultPreset; $script:VnaActiveScales = $null
        $script:com = @{ 'CALCulate(1).PARameter(1).COUNt' = 4; 'DISPlay.SPLit' = 2; 'DISPlay.WINDow(1).SPLit' = 8
            'DISPlay.WINDow(1).TITLe.DATA' = 'operator'; 'DISPlay.WINDow(1).TITLe.STATe' = $true }
        foreach ($index in 1..4) {
            $script:com["CALCulate(1).PARameter($index).DEFine"] = @('S11','S21','S12','S22')[$index - 1]
            $script:com["T$index.FORMat"] = 'MLOG'
            $script:com["DISPlay.WINDow(1).TRACe($index).Y.SCALe.PDIVision"] = 10.0
            $script:com["DISPlay.WINDow(1).TRACe($index).Y.SCALe.RLEVel"] = 0.0
            $script:com["DISPlay.WINDow(1).TRACe($index).Y.SCALe.RPOSition"] = 5.0
            $script:com["T$index.LIMit.DATA"] = $(if ($index -eq 1) { [double[]]@(1, 1, 1e9, 2e9, 1.5, 1.5) } else { [double[]]@(0) })
            $script:com["T$index.LIMit.STATe"] = $index -eq 1
            $script:com["T$index.LIMit.DISPlay.STATe"] = $index -eq 1
        }
        $script:com['T1.MARKer(1).STATe'] = $true; $script:com['T1.MARKer(1).X'] = 2e9
        $script:com['T1.MARKer(2).STATe'] = $true; $script:com['T1.MARKer(2).X'] = 7e9
        $script:comSelected = 1
        $script:comWrites = New-Object 'System.Collections.Generic.List[string]'
        $script:ignoreScaleWrite = $false
        Mock Invoke-VnaComIo {
            param($Connection,$Path,$Mode,$Value)
            if ($Path -match '^CALCulate\(1\)\.PARameter\((\d+)\)\.SELect$') { $script:comSelected = [int]$Matches[1]; return }
            $key = $Path -replace '^CALCulate\(1\)\.SELected\.', "T$($script:comSelected)."
            if ($key -like '*CORRection.STATus') { return 'COR' }
            if ($Mode -eq 'call') { $script:comWrites.Add($Path); if ($Path -like '*Y.SCALe.AUTO') { $script:com[$Path] = 1 + [int]$script:com[$Path] }; return }
            if ($Mode -eq 'set') {
                $script:comWrites.Add($Path)
                if ($script:ignoreScaleWrite -and $key -like 'DISPlay.WINDow(1).TRACe*') { return }
                if ($key -match '^(T\d+)\.MARKer\((\d+)\)\.STATe$') {
                    # S2VNA: switching marker n on switches on 1..n; switching it off switches off n..15.
                    $trace = $Matches[1]; $number = [int]$Matches[2]
                    $range = if ($Value) { 1..$number } else { $number..15 }
                    foreach ($marker in $range) { $script:com["$trace.MARKer($marker).STATe"] = [bool]$Value }
                    return
                }
                $script:com[$key] = $Value
                return
            }
            return $script:com[$key]
        }
        $script:screenLimits = [pscustomobject]@{ Title = 'СИ.035 · КСВН ≤ 1,05 + 0,005·F'
            Swr = @([pscustomobject]@{ StartHz = 5e8; StopHz = 18e9; From = 1.0525; To = 1.14 },
                    [pscustomobject]@{ StartHz = 1e7; StopHz = 18e9; From = 1.3; To = 1.3 })
            Loss = @([pscustomobject]@{ Type = 2; StartHz = 5e8; StopHz = 18e9; From = -0.0212; To = -0.1273 }) }
    }
    It 'shows twelve graphs, SWR limit lines and classic-frequency markers without touching sweep or calibration' {
        Set-VnaScreen $script:chartConnection $script:screenLimits -Preset 'Подробно (инженер)'
        $script:com['CALCulate(1).PARameter(1).COUNt'] | Should Be 12
        $script:com['DISPlay.WINDow(1).SPLit'] | Should Be 15
        $script:com['DISPlay.SPLit'] | Should Be 1
        ((1..12 | ForEach-Object { $script:com["CALCulate(1).PARameter($_).DEFine"] }) -join ',') | Should Be 'S11,S21,S12,S22,S11,S21,S21,S22,S11,S21,S12,S22'
        ((1..12 | ForEach-Object { $script:com["T$_.FORMat"] }) -join ',') | Should Be 'SWR,MLOG,MLOG,SWR,SMIT,PHAS,GDEL,SMIT,MLOG,MLOG,MLOG,MLOG'
        (@($script:com['T1.LIMit.DATA']) -join ',') | Should Be '2,1,500000000,18000000000,1.0525,1.14,1,10000000,18000000000,1.3,1.3'
        (@($script:com['T4.LIMit.DATA'])).Count | Should Be 11
        $script:com['T1.LIMit.STATe'] | Should Be $true
        (@($script:com['T2.LIMit.DATA']) -join ',') | Should Be '1,2,500000000,18000000000,-0.0212,-0.1273'
        (@($script:com['T3.LIMit.DATA']) -join ',') | Should Be '1,2,500000000,18000000000,-0.0212,-0.1273'
        $script:com['T2.LIMit.STATe'] | Should Be $true
        $script:com['T5.LIMit.DISPlay.STATe'] | Should Be $false
        $script:com['T10.LIMit.STATe'] | Should Be $false
        $script:com['DISPlay.WINDow(1).TRACe(7).Y.SCALe.AUTO'] | Should Be 1
        $script:com['T9.LIMit.STATe'] | Should Be $false
        foreach ($trace in 1..12) {
            $script:com["T$trace.MARKer(5).STATe"] | Should Be $true
            $script:com["T$trace.MARKer(6).STATe"] | Should Be $false
            $script:com["T$trace.MARKer(1).X"] | Should Be 1e9
            $script:com["T$trace.MARKer(5).X"] | Should Be 18e9
        }
        $script:com['DISPlay.WINDow(1).TITLe.DATA'] | Should Be 'СИ.035 · КСВН ≤ 1,05 + 0,005·F'
        $script:com['DISPlay.WINDow(1).TITLe.STATe'] | Should Be $true
        $script:com['DISPlay.WINDow(1).TRACe(10).Y.SCALe.PDIVision'] | Should Be 5
        # SWR top = max(norm × 1,15; 1,20): the highest line here is 1,30 → 1,495 → 1,50; adapter loss 0…−0,5 dB.
        $script:com['DISPlay.WINDow(1).TRACe(1).Y.SCALe.PDIVision'] | Should Be 0.05
        $script:com['DISPlay.WINDow(1).TRACe(2).Y.SCALe.PDIVision'] | Should Be 0.05
        @($script:comWrites | Where-Object { $_ -match 'SENSe|SOURce|CORRection|TRIGger|INITiate|MMEMory|CKIT' }).Count | Should Be 0
    }
    It 'shows the calibration check as two big SWR graphs with the 1,06 line and hidden transmission traces' {
        Set-VnaScreen $script:chartConnection $null -Preset 'Проверка калибровки (нагрузка)'
        $script:com['CALCulate(1).PARameter(1).COUNt'] | Should Be 4
        $script:com['DISPlay.WINDow(1).SPLit'] | Should Be 3
        ((1..4 | ForEach-Object { $script:com["DISPlay.WINDow(1).TRACe($_).STATe"] }) -join ',') | Should Be 'True,False,False,True'
        (@($script:com['T1.LIMit.DATA']) -join ',') | Should Be '1,1,10000000,18000000000,1.06,1.06'
        (@($script:com['T4.LIMit.DATA']) -join ',') | Should Be '1,1,10000000,18000000000,1.06,1.06'
        $script:com['DISPlay.WINDow(1).TRACe(1).Y.SCALe.PDIVision'] | Should Be 0.01
        ((1..4 | ForEach-Object { $script:com["T$_.SMOothing.STATe"] }) -join ',') | Should Be 'False,False,False,False'
    }
    It 'shows the flex check with memory traces and stores data to memory on request' {
        Set-VnaScreen $script:chartConnection $null -Preset 'Проверка кабелей (шевеление)'
        ((1..4 | ForEach-Object { $script:com["T$_.FORMat"] }) -join ',') | Should Be 'SWR,SWR,MLOG,PHAS'
        ((1..4 | ForEach-Object { $script:com["DISPlay.WINDow(1).TRACe($_).MEMory.STATe"] }) -join ',') | Should Be 'True,True,True,True'
        ((1..4 | ForEach-Object { $script:com["DISPlay.WINDow(1).TRACe($_).Y.SCALe.PDIVision"] }) -join ',') | Should Be '0.01,0.01,0.03,36'
        Save-VnaTraceMemory $script:chartConnection
        @($script:comWrites | Where-Object { $_ -like '*MATH.MEMorize' }).Count | Should Be 4
    }
    It 'clears limit lines and the title when no norm is known' {
        Set-VnaScreen $script:chartConnection $null
        $script:com['T1.LIMit.STATe'] | Should Be $false
        $script:com['T1.LIMit.DISPlay.STATe'] | Should Be $false
        @($script:comWrites | Where-Object { $_ -like '*LIMit.DATA' }).Count | Should Be 0
        $script:com['DISPlay.WINDow(1).TITLe.STATe'] | Should Be $false
    }
    It 'refuses success when the instrument ignores a scale write' {
        $script:ignoreScaleWrite = $true
        { Set-VnaScreen $script:chartConnection $script:screenLimits } | Should Throw 'шкала S11.PDIVision'
    }
    It 'reads correction of the four measurement traces among the twelve graphs' {
        Set-VnaScreen $script:chartConnection $script:screenLimits -Preset 'Подробно (инженер)'
        $states = Get-VnaTraceStatus $script:chartConnection
        ($states.Keys | Sort-Object) -join ',' | Should Be 'S11,S12,S21,S22'
    }
    It 'returns the previous screen exactly and leaves calibration and sweep alone' {
        $path = Join-Path $TestDrive 'screen.json'
        $before = @{}; foreach ($key in $script:com.Keys) { $before[$key] = (@($script:com[$key]) -join ',') }
        Backup-VnaScreen $script:chartConnection $path
        { Backup-VnaScreen $script:chartConnection $path } | Should Throw
        Set-VnaScreen $script:chartConnection $script:screenLimits
        Restore-VnaScreen $script:chartConnection $path
        foreach ($key in $before.Keys) {
            # COM cannot write an empty limit table: a graph that had none keeps the new lines hidden and untested.
            if ($key -like '*LIMit.DATA' -and $before[$key] -eq '0') { $script:com[$key.Replace('DATA', 'STATe')] | Should Be $false; continue }
            "$key=$(@($script:com[$key]) -join ',')" | Should Be "$key=$($before[$key])"
        }
        $script:com['T1.MARKer(2).STATe'] | Should Be $true
        $script:com['T1.MARKer(2).X'] | Should Be 7e9
        $script:com['T1.MARKer(3).STATe'] | Should Be $false
        $script:com['T2.MARKer(1).STATe'] | Should Be $false
        $saved = [IO.File]::ReadAllText($path) | ConvertFrom-Json
        (@($saved.Traces[0].Markers | ForEach-Object { $_.Number }) -join ',') | Should Be '1,2'
        @($script:comWrites | Where-Object { $_ -match 'SENSe|SOURce|CORRection|TRIGger|INITiate|MMEMory|CKIT' }).Count | Should Be 0
    }
    It 'refuses to restore a screen saved on another instrument' {
        $path = Join-Path $TestDrive 'other.json'
        Backup-VnaScreen $script:chartConnection $path
        $other = New-TestS2Connection; $other.App.Name = 'Planar, S50180, OTHER-VNA, test'
        { Restore-VnaScreen $other $path } | Should Throw 'другого прибора'
    }
}

Describe 'S2VNA live correction loss before acquisition' {
    It 'reads live COR before triggering despite a calibrated connection' {
        Mock Assert-VnaDeviceReady { [pscustomobject]@{VnaSerial='TEST-VNA';AcmSerial='TEST-ACM'} }
        Mock Assert-VnaProfile {}
        Mock Invoke-VnaComIo {
            param($Connection,$Path,$Mode,$Value)
            if ($Path -like '*COUNt') { return 4 }
            if ($Path -match 'PARameter\(([1-4])\).DEFine$') { return @('S11','S21','S12','S22')[[int]$Matches[1] - 1] }
            if ($Path -like '*SELect') { $script:selectedParameter = $Path; return }
            if ($Path -like '*STATus') {
                if ($script:selectedParameter -like '*PARameter(3).*') { return 'C?' }
                return 'COR'
            }
            throw "Unexpected IO: $Path"
        }
        { Get-VnaSweep (New-TestS2Connection) } | Should Throw 'выполните пункт 2'
        Assert-MockCalled Invoke-VnaComIo -Times 4 -Exactly -Scope It -ParameterFilter { $Path -like '*CORRection.STATus' }
        Assert-MockCalled Invoke-VnaComIo -Times 0 -Exactly -Scope It -ParameterFilter { $Path -like 'TRIGger*' -or $Path -like 'INITiate*' -or $Path -like '*SDATa' }
    }
}

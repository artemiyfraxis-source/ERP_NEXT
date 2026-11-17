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
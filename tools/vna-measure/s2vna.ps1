# S2VNA bindings for the commissioned S50180 two-port ACM profile. No device calls at import.
Add-Type -AssemblyName System.Numerics

function New-VnaApplication {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            throw 'Запустите программу обычным двойным щелчком, без прав администратора.'
        }
    } finally { $identity.Dispose() }
    return New-Object -ComObject 'S2VNA.Application'
}

function New-VnaConnection {
    param([string]$OfflineDirectory)
    if ($OfflineDirectory) {
        $directory = [IO.Path]::GetFullPath($OfflineDirectory)
        $devicePath = Join-Path $directory 'device.json'
        if (-not [IO.File]::Exists($devicePath)) { throw 'Offline: нет device.json.' }
        $device = [IO.File]::ReadAllText($devicePath, [Text.Encoding]::UTF8) | ConvertFrom-Json
        $states = @{}
        foreach ($name in 'S11','S21','S12','S22') {
            $states[$name] = if ($device.TraceStatus -and $device.TraceStatus.PSObject.Properties[$name]) { [string]$device.TraceStatus.$name } else { 'C?' }
        }
        return [pscustomobject]@{ Offline = $true; Directory = $directory; Device = $device; App = $null
            TraceStatus = $states; ProfileApplied = $false; Calibrated = $false; Released = $false; BoundSerial = $null }
    }
    return [pscustomobject]@{ Offline = $false; Directory = $null; Device = $null; App = (New-VnaApplication)
        TraceStatus = $null; ProfileApplied = $false; Calibrated = $false; Released = $false; BoundSerial = $null }
}

function Invoke-VnaComIo {
    param($Connection, [string]$Path, [ValidateSet('get','set','call')][string]$Mode = 'get', $Value)
    if ($Connection.Offline -or $Connection.Released) { throw 'COM недоступен в этом соединении.' }
    # Only driver-owned literals reach this helper, never operator-supplied expressions.
    $parts = $Path.Split('.'); $node = $Connection.App.SCPI
    for ($index = 0; $index -lt $parts.Count; $index++) {
        $part = $parts[$index]; $last = $index -eq $parts.Count - 1
        if ($part -match '^([A-Za-z0-9]+)\(([0-9]+)\)$') {
            $member = $Matches[1]; $argument = [int]$Matches[2]
            $node = $node.$member($argument)
        } elseif (-not $last) { $node = $node.$part }
        elseif ($Mode -eq 'set') { $node.$part = $Value; return }
        elseif ($Mode -eq 'call') { $null = $node.$part(); return }
        else { return $node.$part }
    }
    return $node
}

function ConvertFrom-VnaAcmInfo {
    param([string]$Info)
    $rows = @($Info | ConvertFrom-Csv -Header (0..30 | ForEach-Object { "f$_" }))
    if ($rows.Count -ne 1) { throw 'Не прочитаны сведения АКМ.' }
    $row = $rows[0]; $band = @()
    foreach ($field in @($row.f5, $row.f6)) {
        if ($field -notmatch '^\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\s*([kMG]?Hz)?\s*$') { throw 'Не прочитана полоса АКМ.' }
        $value = [double]::Parse($Matches[1], [Globalization.CultureInfo]::InvariantCulture)
        $scale = switch -CaseSensitive ($Matches[2]) { 'kHz' { 1e3 } 'MHz' { 1e6 } 'GHz' { 1e9 } default { 1 } }
        $band += $value * $scale
    }
    if (-not $row.f0 -or -not $row.f1 -or $band[0] -lt 0 -or $band[0] -ge $band[1]) { throw 'Не прочитаны номер или полоса АКМ.' }
    return [pscustomobject]@{ Model = $row.f0.Trim(); Serial = $row.f1.Trim(); MinHz = $band[0]; MaxHz = $band[1] }
}

function Get-VnaDeviceInfo {
    param($Connection)
    if ($Connection.Released) { throw 'Соединение закрыто.' }
    if ($Connection.Offline) {
        $device = $Connection.Device
        $sensors = if ($device.VnaTemperatures) { @($device.VnaTemperatures) } else { @($device.VnaTemperature, $null) }
        return [pscustomobject]@{ Ready = $device.Ready; VnaSerial = $device.VnaSerial; VnaName = $device.VnaName
            AcmSerial = $device.AcmSerial; AcmInfo = $device.AcmInfo; AcmMinHz = $device.AcmMinHz; AcmMaxHz = $device.AcmMaxHz
            VnaTemperature = $sensors[0]; VnaTemperatures = $sensors; AcmTemperature = $device.AcmTemperature }
    }
    $name = [string]$Connection.App.Name; $fields = $name.Split(',')
    $serial = if ($fields.Count -ge 3) { $fields[2].Trim() } else { $null }
    $raw = [string](Invoke-VnaComIo $Connection 'SENSe(1).CORRection.COLLect.ECAL.INFormation')
    $module = ConvertFrom-VnaAcmInfo $raw
    $sensors = @()
    foreach ($sensorIndex in 1,2) {
        # Missing telemetry stays unknown; it must never become a fabricated 0 C.
        try { $reading = Invoke-VnaComIo $Connection "SYSTem.TEMPerature.SENSor($sensorIndex)"; $sensors += ConvertTo-VnaTemperature $reading }
        catch { $sensors += $null }
    }
    try { $acmTemperature = ConvertTo-VnaTemperature (Invoke-VnaComIo $Connection 'SYSTem.COMMunicate.ECAL.TEMPerature.SENSor') }
    catch { $acmTemperature = $null }
    return [pscustomobject]@{ Ready = [bool]$Connection.App.Ready; VnaSerial = $serial; VnaName = $name
        AcmSerial = $module.Serial; AcmInfo = $raw; AcmMinHz = $module.MinHz; AcmMaxHz = $module.MaxHz
        VnaTemperature = $sensors[0]; VnaTemperatures = $sensors; AcmTemperature = $acmTemperature }
}

function ConvertTo-VnaTemperature {
    param($Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) { return $null }
    $number = 0.0
    $typeCode = [int][Type]::GetTypeCode($Value.GetType())
    if ($typeCode -ge 5 -and $typeCode -le 15) {
        # COM already returns numeric Variants. Stringifying would use the current decimal separator.
        $number = [double]$Value
    } elseif (-not [double]::TryParse([string]$Value, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$number)) {
        return $null
    }
    if ([double]::IsNaN($number) -or [double]::IsInfinity($number) -or $number -lt -50 -or $number -gt 150) { return $null }
    return $number
}

function Assert-VnaInstrumentIdentity {
    param($Connection)
    if ($Connection.Released) { throw 'Соединение закрыто.' }
    # Restore must remain possible with the ACM disconnected. Read only the VNA identity here.
    if ($Connection.Offline) {
        $ready = $Connection.Device.Ready
        $serial = [string]$Connection.Device.VnaSerial
    } else {
        $ready = $Connection.App.Ready
        $parts = ([string]$Connection.App.Name).Split(',')
        $serial = if ($parts.Count -ge 3) { $parts[2].Trim() } else { '' }
    }
    if ($ready -ne $true -or [string]::IsNullOrWhiteSpace($serial)) { throw 'Прибор не готов или его серийный номер не прочитан.' }
    if (-not $Connection.PSObject.Properties['BoundSerial']) { $Connection | Add-Member NoteProperty BoundSerial $null }
    if ([string]::IsNullOrWhiteSpace([string]$Connection.BoundSerial)) { $Connection.BoundSerial = $serial }
    if ($serial -cne $Connection.BoundSerial) { throw 'Прибор заменён: серийный номер отличается от начального. Операция запрещена.' }
    return $serial
}

function Assert-VnaDeviceReady {
    param($Connection)
    $boundSerial = Assert-VnaInstrumentIdentity $Connection
    $info = Get-VnaDeviceInfo $Connection
    if ($info.VnaSerial -cne $boundSerial) { throw 'Прибор заменён при чтении сведений.' }
    if ($info.Ready -ne $true -or [string]::IsNullOrWhiteSpace($info.VnaSerial) -or [string]::IsNullOrWhiteSpace($info.AcmSerial)) {
        throw 'Прибор или АКМ не готовы; серийные номера должны быть прочитаны.'
    }
    if ($info.AcmMinHz -gt 10e6 -or $info.AcmMaxHz -lt 18e9 -or $info.AcmMaxHz -le $info.AcmMinHz) { throw 'АКМ не покрывает профиль 10 МГц–18 ГГц.' }
    return $info
}

function Backup-VnaState {
    param($Connection, [string]$Path)
    if ([IO.File]::Exists($Path)) { throw 'Резервный файл уже существует: перезапись запрещена.' }
    $null = Assert-VnaDeviceReady $Connection
    if ($Connection.Offline) {
        $saved = @{ profile_applied = $Connection.ProfileApplied; calibrated = $Connection.Calibrated }
        [IO.File]::WriteAllText($Path, ($saved | ConvertTo-Json), [Text.Encoding]::UTF8)
    } else {
        Invoke-VnaComIo $Connection 'MMEMory.STORe.STYPe' set 'CDST'
        Invoke-VnaComIo $Connection 'MMEMory.STORe.STATe' set ([IO.Path]::GetFullPath($Path))
        $null = Invoke-VnaComIo $Connection 'IEEE4882.OPC'
    }
    $null = Assert-VnaInstrumentIdentity $Connection
    if (-not [IO.File]::Exists($Path) -or (Get-Item -LiteralPath $Path).Length -le 0) { throw 'S2VNA не создал резервную копию состояния.' }
}

function Get-VnaProfileSettings {
    $settings = [ordered]@{
        'SENSe(1).SWEep.TYPE' = 'LIN'; 'SENSe(1).FREQuency.STARt' = 10e6; 'SENSe(1).FREQuency.STOP' = 18e9
        'SENSe(1).SWEep.POINts' = 1601; 'SENSe(1).BANDwidth.RESolution' = 1000; 'SENSe(1).AVERage.STATe' = $false
        'SENSe(1).CORRection.IMPedance.INPut.MAGNitude' = 50; 'SOURce(1).POWer.PORT(1).COUPle' = $false
        'CALCulate(1).PARameter(1).COUNt' = 4; 'CALCulate(1).FSIMulator.STATe' = $false
    }
    foreach ($portNumber in 1,2) {
        $settings["SOURce(1).POWer.PORT($portNumber).LEVel.IMMediate.AMPLitude"] = 0
        $settings["SOURce(1).POWer.PORT($portNumber).LEVel.SLOPe.STATe"] = $false
    }
    return $settings
}

function Assert-VnaSetting {
    param($Actual, $Expected, [string]$Path)
    if ($null -eq $Actual -or $null -eq $Expected) { throw "Не прочитана настройка $Path." }
    if ($Expected -is [bool]) {
        if ($Actual -notin @($true,$false,0,1) -or [bool]$Actual -ne $Expected) { throw "Изменена настройка $Path." }
    } elseif ($Expected -is [string]) {
        if (([string]$Actual).Trim().ToUpperInvariant() -cne $Expected.ToUpperInvariant()) { throw "Изменена настройка $Path." }
    } elseif ([double]$Actual -ne [double]$Expected) { throw "Изменена настройка $Path." }
}

function Assert-VnaProfile {
    param($Connection, [switch]$Apply)
    if ($Connection.Offline) {
        if ($Apply) { $Connection.ProfileApplied = $true; $Connection.Calibrated = $false }
        if (-not $Connection.ProfileApplied) { throw 'Профиль не установлен; выполните калибровку.' }
        return
    }
    $settings = Get-VnaProfileSettings
    foreach ($path in $settings.Keys) {
        if ($Apply) { Invoke-VnaComIo $Connection $path set $settings[$path] }
        Assert-VnaSetting (Invoke-VnaComIo $Connection $path) $settings[$path] $path
    }
    $definitions = @('S11','S21','S12','S22')
    for ($traceIndex = 1; $traceIndex -le 4; $traceIndex++) {
        $parameter = $definitions[$traceIndex - 1]
        $path = "CALCulate(1).PARameter($traceIndex).DEFine"
        if ($Apply) { Invoke-VnaComIo $Connection $path set $parameter }
        Assert-VnaSetting (Invoke-VnaComIo $Connection $path) $parameter $path
        Invoke-VnaComIo $Connection "CALCulate(1).PARameter($traceIndex).SELect" call
        $format = if ($parameter -in @('S11','S22')) { 'SWR' } else { 'MLOG' }
        $traceSettings = [ordered]@{ FORMat = $format; 'SMOothing.STATe' = $false; 'MATH.FUNCtion' = 'OFF'
            'CONVersion.STATe' = $false; 'FILTer.GATE.TIME.STATe' = $false }
        foreach ($tail in $traceSettings.Keys) {
            $path = 'CALCulate(1).SELected.' + $tail
            if ($Apply) { Invoke-VnaComIo $Connection $path set $traceSettings[$tail] }
            Assert-VnaSetting (Invoke-VnaComIo $Connection $path) $traceSettings[$tail] $path
        }
    }
}

function Set-VnaProfile {
    param($Connection)
    $null = Assert-VnaDeviceReady $Connection
    if (-not $Connection.Offline) {
        Invoke-VnaComIo $Connection 'ABORt' call
        Invoke-VnaComIo $Connection 'INITiate(1).CONTinuous' set $false
    }
    Assert-VnaProfile $Connection -Apply
    $Connection.ProfileApplied = $true; $Connection.Calibrated = $false
}

function Get-VnaTraceStatus {
    param($Connection)
    if ($Connection.Offline) { return $Connection.TraceStatus.Clone() }
    $result = @{}; $count = [int](Invoke-VnaComIo $Connection 'CALCulate(1).PARameter(1).COUNt')
    if ($count -ne 4) { throw 'Требуются ровно четыре трассы S11/S21/S12/S22.' }
    for ($traceIndex = 1; $traceIndex -le $count; $traceIndex++) {
        $parameter = ([string](Invoke-VnaComIo $Connection "CALCulate(1).PARameter($traceIndex).DEFine")).Trim().ToUpperInvariant()
        if ($parameter -notin @('S11','S21','S12','S22') -or $result.ContainsKey($parameter)) { throw 'Отсутствует требуемая трасса или найдена повторная.' }
        Invoke-VnaComIo $Connection "CALCulate(1).PARameter($traceIndex).SELect" call
        $result[$parameter] = ([string](Invoke-VnaComIo $Connection 'CALCulate(1).SELected.CORRection.STATus')).Trim().ToUpperInvariant()
    }
    return $result
}

function Assert-VnaCorrection {
    param($Connection)
    $states = Get-VnaTraceStatus $Connection
    foreach ($parameter in 'S11','S21','S12','S22') {
        if (-not $states.ContainsKey($parameter) -or $states[$parameter] -cne 'COR') { throw "Нет полной коррекции $parameter; выполните калибровку." }
    }
    if (-not $Connection.Offline) { Assert-VnaSetting (Invoke-VnaComIo $Connection 'SENSe(1).CORRection.STATe') $true 'коррекция канала' }
    return $states
}

function Invoke-VnaCalibration {
    param($Connection)
    $infoBefore = Assert-VnaDeviceReady $Connection
    Assert-VnaProfile $Connection
    $Connection.Calibrated = $false
    if ($Connection.Offline) {
        foreach ($parameter in 'S11','S21','S12','S22') { $Connection.TraceStatus[$parameter] = 'COR' }
    } else {
        Invoke-VnaComIo $Connection 'SENSe(1).CORRection.COLLect.ECAL.ORIentation.STATe' set $true
        # One blocking acquisition; never retry an ambiguous COM failure.
        Invoke-VnaComIo $Connection 'SENSe(1).CORRection.COLLect.ECAL.SOLT2' set ([int[]]@(1,2))
        $null = Invoke-VnaComIo $Connection 'IEEE4882.OPC'
    }
    $null = Assert-VnaCorrection $Connection
    Assert-VnaProfile $Connection
    $infoAfter = Assert-VnaDeviceReady $Connection
    if ($infoBefore.VnaSerial -cne $infoAfter.VnaSerial -or $infoBefore.AcmSerial -cne $infoAfter.AcmSerial) { throw 'Прибор или АКМ заменены во время калибровки.' }
    $Connection.Calibrated = $true
    return $infoAfter
}

function Assert-VnaSweepData {
    param($Data)
    $frequencies = @($Data.Frequencies)
    if ($frequencies.Count -ne 1601) { throw 'Прочитана неполная сетка частот: требуется 1601 точка.' }
    for ($pointIndex = 0; $pointIndex -lt $frequencies.Count; $pointIndex++) {
        $expected = 10e6 + (18e9 - 10e6) * $pointIndex / 1600
        if ([double]::IsNaN($frequencies[$pointIndex]) -or [double]::IsInfinity($frequencies[$pointIndex]) -or
            [Math]::Abs($frequencies[$pointIndex] - $expected) -gt [Math]::Max(0.001, $expected * 1e-10)) { throw 'Сетка частот не соответствует калибровочному профилю.' }
    }
    foreach ($parameter in 'S11','S21','S12','S22') {
        if (@($Data.$parameter).Count -ne $frequencies.Count) { throw "Неполная трасса $parameter." }
        foreach ($sample in $Data.$parameter) {
            if ($sample -isnot [System.Numerics.Complex] -or [double]::IsNaN($sample.Real) -or [double]::IsInfinity($sample.Real) -or
                [double]::IsNaN($sample.Imaginary) -or [double]::IsInfinity($sample.Imaginary)) { throw "Некорректные комплексные данные $parameter." }
        }
    }
}

function Get-VnaSweep {
    param($Connection)
    $infoBefore = Assert-VnaDeviceReady $Connection
    Assert-VnaProfile $Connection
    $null = Assert-VnaCorrection $Connection
    if (-not $Connection.Calibrated) { throw 'В этом запуске нет действующей калибровки.' }
    if ($Connection.Offline) {
        $path = Join-Path $Connection.Directory 'sweep.s2p'
        if (-not [IO.File]::Exists($path)) { throw 'Offline: нет sweep.s2p.' }
        $data = Read-VnaTouchstone -Path $path
    } else {
        Invoke-VnaComIo $Connection 'INITiate(1).CONTinuous' set $true
        Invoke-VnaComIo $Connection 'TRIGger.SEQuence.SOURce' set 'BUS'
        Invoke-VnaComIo $Connection 'TRIGger.SEQuence.SINGle' call
        $complete = Invoke-VnaComIo $Connection 'IEEE4882.OPC'
        if ([string]$complete -notin @('1','True')) { throw 'S2VNA не подтвердил завершение нового свипа.' }
        $values = @{ Frequencies = [double[]]@(Invoke-VnaComIo $Connection 'SENSe(1).FREQuency.DATA') }
        for ($traceIndex = 1; $traceIndex -le 4; $traceIndex++) {
            $parameter = ([string](Invoke-VnaComIo $Connection "CALCulate(1).PARameter($traceIndex).DEFine")).Trim().ToUpperInvariant()
            $raw = @(Invoke-VnaComIo $Connection "CALCulate(1).TRACe($traceIndex).DATA.SDATa")
            if ($raw.Count -ne 2 * $values.Frequencies.Count) { throw "Неполные данные трассы $parameter." }
            $samples = New-Object 'System.Numerics.Complex[]' $values.Frequencies.Count
            for ($pointIndex = 0; $pointIndex -lt $samples.Length; $pointIndex++) {
                $samples[$pointIndex] = New-Object System.Numerics.Complex([double]$raw[2*$pointIndex], [double]$raw[2*$pointIndex+1])
            }
            $values[$parameter] = $samples
        }
        $data = [pscustomobject]$values
    }
    Assert-VnaSweepData $data
    Assert-VnaProfile $Connection
    $states = Assert-VnaCorrection $Connection
    $infoAfter = Assert-VnaDeviceReady $Connection
    if ($infoBefore.VnaSerial -cne $infoAfter.VnaSerial -or $infoBefore.AcmSerial -cne $infoAfter.AcmSerial) { throw 'Прибор или АКМ заменены во время свипа.' }
    return [pscustomobject]@{ Id = [guid]::NewGuid().ToString('D'); CapturedAt = [DateTimeOffset]::UtcNow.ToString('o')
        Data = $data; TraceStatus = $states; DeviceInfo = $infoAfter }
}

function Restore-VnaState {
    param($Connection, [string]$Path)
    if (-not [IO.File]::Exists($Path) -or (Get-Item -LiteralPath $Path).Length -le 0) { throw 'Резервный файл не найден или пуст.' }
    $null = Assert-VnaInstrumentIdentity $Connection
    $Connection.Calibrated = $false; $Connection.ProfileApplied = $false
    if (-not $Connection.Offline) {
        Invoke-VnaComIo $Connection 'MMEMory.LOAD.STATe' set ([IO.Path]::GetFullPath($Path))
        $null = Invoke-VnaComIo $Connection 'IEEE4882.OPC'
        Invoke-VnaComIo $Connection 'INITiate(1).CONTinuous' set $true
    }
}

function Release-VnaConnection {
    param($Connection)
    if ($null -eq $Connection -or $Connection.Released) { return }
    $Connection.Released = $true
    if (-not $Connection.Offline -and $null -ne $Connection.App -and [Runtime.InteropServices.Marshal]::IsComObject($Connection.App)) {
        $null = [Runtime.InteropServices.Marshal]::FinalReleaseComObject($Connection.App)
    }
    $Connection.App = $null
}
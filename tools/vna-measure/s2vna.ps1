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
        $traces = @($device.Traces)
        if (-not $device.Traces) {
            $traces = @(foreach ($name in 'S11','S21','S12','S22') {
                [pscustomobject]@{ Parameter = $name; Format = $(if ($name -in 'S11','S22') { 'SWR' } else { 'MLOG' })
                    PDIVision = 1.0; RLEVel = 0.0; RPOSition = 5 }
            })
        }
        return [pscustomobject]@{ Offline = $true; Directory = $directory; Device = $device; App = $null
            Traces = $traces; TraceStatus = $states; ProfileApplied = $false; Calibrated = $false; Released = $false; BoundSerial = $null }
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
        $saved = @{ profile_applied = $Connection.ProfileApplied; calibrated = $Connection.Calibrated
            traces = $Connection.Traces; trace_status = $Connection.TraceStatus }
        [IO.File]::WriteAllText($Path, ($saved | ConvertTo-Json -Depth 6), [Text.Encoding]::UTF8)
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
        'CALCulate(1).PARameter(1).COUNt' = @(Get-VnaScreenTraces).Count; 'CALCulate(1).FSIMulator.STATe' = $false
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
        if ($Apply) {
            $Connection.ProfileApplied = $true; $Connection.Calibrated = $false
            $Connection.Traces = @(foreach ($trace in Get-VnaScreenTraces) {
                [pscustomobject]@{ Parameter = $trace.Parameter; Format = $trace.Format; PDIVision = 1.0; RLEVel = 0.0; RPOSition = 5 }
            })
        }
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
    param($Connection, $Limits)
    $null = Assert-VnaDeviceReady $Connection
    if (-not $Connection.Offline) {
        Invoke-VnaComIo $Connection 'ABORt' call
        Invoke-VnaComIo $Connection 'INITiate(1).CONTinuous' set $false
    }
    Assert-VnaProfile $Connection -Apply
    $Connection.ProfileApplied = $true; $Connection.Calibrated = $false
    Set-VnaScreen $Connection $Limits
}

function Get-VnaTraces {
    param($Connection)
    if ($Connection.Released) { throw 'Соединение закрыто.' }
    $count = if ($Connection.Offline) { @($Connection.Traces).Count } else { [int](Invoke-VnaComIo $Connection 'CALCulate(1).PARameter(1).COUNt') }
    for ($traceIndex = 1; $traceIndex -le $count; $traceIndex++) {
        if ($Connection.Offline) {
            $trace = $Connection.Traces[$traceIndex - 1]
            [pscustomobject]@{ Index = $traceIndex; Parameter = $trace.Parameter; Format = $trace.Format
                PDIVision = $trace.PDIVision; RLEVel = $trace.RLEVel; RPOSition = $trace.RPOSition
                Correction = $Connection.TraceStatus[$trace.Parameter] }
        } else {
            $parameter = ([string](Invoke-VnaComIo $Connection "CALCulate(1).PARameter($traceIndex).DEFine")).Trim().ToUpperInvariant()
            Invoke-VnaComIo $Connection "CALCulate(1).PARameter($traceIndex).SELect" call
            $scalePath = "DISPlay.WINDow(1).TRACe($traceIndex).Y.SCALe"
            [pscustomobject]@{ Index = $traceIndex; Parameter = $parameter
                Format = ([string](Invoke-VnaComIo $Connection 'CALCulate(1).SELected.FORMat')).Trim().ToUpperInvariant()
                PDIVision = (Read-VnaComOptional $Connection "$scalePath.PDIVision")
                RLEVel = (Read-VnaComOptional $Connection "$scalePath.RLEVel")
                RPOSition = (Read-VnaComOptional $Connection "$scalePath.RPOSition")
                Correction = ([string](Invoke-VnaComIo $Connection 'CALCulate(1).SELected.CORRection.STATus')).Trim().ToUpperInvariant() }
        }
    }
}

# Measurement screen (owner 18.11: «all graphs», wide 2560x1080 monitor): one channel, twelve graphs in three rows of
# four (S2VNA graph layout 15, each graph ~600x290 px; checked live on the ОТК VNA PC 18.11). Columns follow the ports:
#   КСВН S11             | S21, дБ (0…−3)       | S12, дБ (0…−3)       | КСВН S22
#   Smith S11            | фаза S21             | ГВЗ S21              | Smith S22
#   возвр. потери S11, дБ | S21, дБ обзор (0…−50) | S12, дБ обзор (0…−50) | возвр. потери S22, дБ
# Traces 1-4 are the measurement traces (Get-VnaSweep reads them by index). Markers 1/3/6/12/18 GHz on every graph
# (S2VNA couples markers of the same number across graphs by default). Limit lines go on the two SWR graphs only:
# rf-norms.json has no insertion-loss norm. Display only: sweep, calibration and calibration kit stay untouched.
$script:VnaScreenSplit = 15
$script:VnaScreenMarkersHz = @(1e9, 3e9, 6e9, 12e9, 18e9)

function Get-VnaScreenTraces {
    $swr = [ordered]@{ PDIVision = 0.1; RLEVel = 1.0; RPOSition = 0 }
    $loss = [ordered]@{ PDIVision = 0.3; RLEVel = 0.0; RPOSition = 10 }
    $smith = [ordered]@{ PDIVision = 1.0 }
    $overview = [ordered]@{ PDIVision = 5.0; RLEVel = 0.0; RPOSition = 10 }
    return @(
        [pscustomobject]@{ Index = 1; Parameter = 'S11'; Format = 'SWR';  Scale = $swr;  SwrLimit = $true }
        [pscustomobject]@{ Index = 2; Parameter = 'S21'; Format = 'MLOG'; Scale = $loss; SwrLimit = $false }
        [pscustomobject]@{ Index = 3; Parameter = 'S12'; Format = 'MLOG'; Scale = $loss; SwrLimit = $false }
        [pscustomobject]@{ Index = 4; Parameter = 'S22'; Format = 'SWR';  Scale = $swr;  SwrLimit = $true }
        [pscustomobject]@{ Index = 5; Parameter = 'S11'; Format = 'SMIT'; Scale = $smith; SwrLimit = $false }
        [pscustomobject]@{ Index = 6; Parameter = 'S21'; Format = 'PHAS'; Scale = [ordered]@{ PDIVision = 36.0; RLEVel = 0.0; RPOSition = 5 }; SwrLimit = $false }
        [pscustomobject]@{ Index = 7; Parameter = 'S21'; Format = 'GDEL'; Scale = [ordered]@{ PDIVision = 2e-10; RLEVel = 0.0; RPOSition = 0 }; SwrLimit = $false }
        [pscustomobject]@{ Index = 8; Parameter = 'S22'; Format = 'SMIT'; Scale = $smith; SwrLimit = $false }
        [pscustomobject]@{ Index = 9; Parameter = 'S11'; Format = 'MLOG'; Scale = $overview; SwrLimit = $false }
        [pscustomobject]@{ Index = 10; Parameter = 'S21'; Format = 'MLOG'; Scale = $overview; SwrLimit = $false }
        [pscustomobject]@{ Index = 11; Parameter = 'S12'; Format = 'MLOG'; Scale = $overview; SwrLimit = $false }
        [pscustomobject]@{ Index = 12; Parameter = 'S22'; Format = 'MLOG'; Scale = $overview; SwrLimit = $false }
    )
}

function Get-VnaChartScaleSettings {
    param([string]$Parameter)
    $trace = @(Get-VnaScreenTraces | Where-Object { $_.Parameter -eq $Parameter -and $_.Index -le 4 })
    if ($trace.Count -ne 1) { throw "Неизвестная трасса $Parameter. Выполните пункт 6." }
    return $trace[0].Scale
}

function ConvertTo-VnaLimitData {
    param([object[]]$Segments)
    # S2VNA limit table: N, then per segment type (1 = upper), start and stop stimulus, start and stop response.
    $values = New-Object 'System.Collections.Generic.List[double]'
    $values.Add(@($Segments).Count)
    foreach ($segment in @($Segments)) {
        foreach ($value in 1, $segment.StartHz, $segment.StopHz, $segment.From, $segment.To) { $values.Add([double]$value) }
    }
    return [double[]]$values.ToArray()
}

function Assert-VnaScreen {
    param([object[]]$Traces)
    $expected = @(Get-VnaScreenTraces)
    if ($Traces.Count -ne $expected.Count) { throw "Экран замера: нужно графиков $($expected.Count), сейчас $($Traces.Count); выполните пункт 6." }
    foreach ($spec in $expected) {
        $trace = $Traces[$spec.Index - 1]
        Assert-VnaSetting $trace.Parameter $spec.Parameter "график $($spec.Index): параметр"
        Assert-VnaSetting $trace.Format $spec.Format "график $($spec.Index): формат $($spec.Parameter)"
        foreach ($member in $spec.Scale.Keys) { Assert-VnaSetting $trace.$member $spec.Scale[$member] "шкала $($spec.Parameter).$member (график $($spec.Index))" }
    }
}

function Set-VnaScreen {
    param($Connection, $Limits)
    $null = Assert-VnaInstrumentIdentity $Connection
    $segments = if ($Limits) { @($Limits.Swr) } else { @() }
    $title = if ($Limits -and $Limits.Title) { [string]$Limits.Title } else { '' }
    if ($title.Length -gt 256) { $title = $title.Substring(0, 256) }
    $specs = @(Get-VnaScreenTraces)
    if ($Connection.Offline) {
        $Connection.Traces = @(foreach ($spec in $specs) {
            $trace = [ordered]@{ Parameter = $spec.Parameter; Format = $spec.Format; PDIVision = $null; RLEVel = $null; RPOSition = $null
                Limit = $(if ($spec.SwrLimit) { $segments } else { @() }); Markers = $script:VnaScreenMarkersHz }
            foreach ($member in $spec.Scale.Keys) { $trace[$member] = $spec.Scale[$member] }
            [pscustomobject]$trace
        })
        if (-not $Connection.PSObject.Properties['Screen']) { $Connection | Add-Member NoteProperty Screen $null }
        $Connection.Screen = [pscustomobject]@{ Split = $script:VnaScreenSplit; Title = $title }
    } else {
        Invoke-VnaComIo $Connection 'DISPlay.SPLit' set 1
        Invoke-VnaComIo $Connection 'CALCulate(1).PARameter(1).COUNt' set $specs.Count
        Invoke-VnaComIo $Connection 'DISPlay.WINDow(1).SPLit' set $script:VnaScreenSplit
        $limitData = ConvertTo-VnaLimitData $segments
        $markers = $script:VnaScreenMarkersHz.Count
        foreach ($spec in $specs) {
            $index = $spec.Index
            Invoke-VnaComIo $Connection "CALCulate(1).PARameter($index).DEFine" set $spec.Parameter
            Invoke-VnaComIo $Connection "CALCulate(1).PARameter($index).SELect" call
            Invoke-VnaComIo $Connection 'CALCulate(1).SELected.FORMat' set $spec.Format
            foreach ($member in $spec.Scale.Keys) { Invoke-VnaComIo $Connection "DISPlay.WINDow(1).TRACe($index).Y.SCALe.$member" set $spec.Scale[$member] }
            $withLimit = $spec.SwrLimit -and $segments.Count -gt 0
            # S2VNA rejects an empty table (N = 0) over COM: a graph without a norm keeps its table, hidden and not tested.
            if ($withLimit) { Invoke-VnaComIo $Connection 'CALCulate(1).SELected.LIMit.DATA' set $limitData }
            Invoke-VnaComIo $Connection 'CALCulate(1).SELected.LIMit.DISPlay.STATe' set $withLimit
            Invoke-VnaComIo $Connection 'CALCulate(1).SELected.LIMit.STATe' set $withLimit
            Invoke-VnaComIo $Connection "CALCulate(1).SELected.MARKer($markers).STATe" set $true
            Invoke-VnaComIo $Connection "CALCulate(1).SELected.MARKer($($markers + 1)).STATe" set $false
            for ($marker = 1; $marker -le $markers; $marker++) {
                Invoke-VnaComIo $Connection "CALCulate(1).SELected.MARKer($marker).X" set $script:VnaScreenMarkersHz[$marker - 1]
            }
            if ($withLimit) {
                $readback = @(Invoke-VnaComIo $Connection 'CALCulate(1).SELected.LIMit.DATA')
                if ($readback.Count -ne $limitData.Count -or [int]$readback[0] -ne $segments.Count) { throw "S2VNA не принял линии допуска графика $index." }
            }
        }
        Invoke-VnaComIo $Connection 'DISPlay.WINDow(1).TITLe.DATA' set $title
        Invoke-VnaComIo $Connection 'DISPlay.WINDow(1).TITLe.STATe' set ([bool]$title)
        Assert-VnaSetting (Invoke-VnaComIo $Connection 'DISPlay.WINDow(1).SPLit') $script:VnaScreenSplit 'расположение графиков'
    }
    Assert-VnaScreen @(Get-VnaTraces $Connection)
}

function Read-VnaComOptional {
    param($Connection, [string]$Path)
    try { return Invoke-VnaComIo $Connection $Path } catch { return $null }
}

function Backup-VnaScreen {
    param($Connection, [string]$Path)
    # Saves exactly what Set-VnaScreen writes, so «Вернуть прежний экран» keeps the calibration of this run.
    if ([IO.File]::Exists($Path)) { throw 'Копия экрана уже существует: перезапись запрещена.' }
    $serial = Assert-VnaInstrumentIdentity $Connection
    if ($Connection.Offline) {
        $screen = if ($Connection.PSObject.Properties['Screen']) { $Connection.Screen } else { $null }
        $saved = [ordered]@{ VnaSerial = $serial; Traces = @($Connection.Traces); Screen = $screen }
    } else {
        $count = [int](Invoke-VnaComIo $Connection 'CALCulate(1).PARameter(1).COUNt')
        $traces = @(for ($index = 1; $index -le $count; $index++) {
            $parameter = [string](Invoke-VnaComIo $Connection "CALCulate(1).PARameter($index).DEFine")
            Invoke-VnaComIo $Connection "CALCulate(1).PARameter($index).SELect" call
            $markers = @()
            for ($marker = 1; $marker -le 15; $marker++) {
                if (-not [bool](Read-VnaComOptional $Connection "CALCulate(1).SELected.MARKer($marker).STATe")) { break }
                $markers += [double](Invoke-VnaComIo $Connection "CALCulate(1).SELected.MARKer($marker).X")
            }
            $scalePath = "DISPlay.WINDow(1).TRACe($index).Y.SCALe"
            [ordered]@{ Parameter = $parameter.Trim(); Format = ([string](Invoke-VnaComIo $Connection 'CALCulate(1).SELected.FORMat')).Trim()
                PDIVision = (Read-VnaComOptional $Connection "$scalePath.PDIVision"); RLEVel = (Read-VnaComOptional $Connection "$scalePath.RLEVel")
                RPOSition = (Read-VnaComOptional $Connection "$scalePath.RPOSition")
                LimitData = @(Read-VnaComOptional $Connection 'CALCulate(1).SELected.LIMit.DATA' | ForEach-Object { [double]$_ })
                LimitDisplay = [bool](Read-VnaComOptional $Connection 'CALCulate(1).SELected.LIMit.DISPlay.STATe')
                LimitState = [bool](Read-VnaComOptional $Connection 'CALCulate(1).SELected.LIMit.STATe'); Markers = $markers }
        })
        $saved = [ordered]@{ VnaSerial = $serial; ChannelSplit = (Invoke-VnaComIo $Connection 'DISPlay.SPLit')
            Split = (Invoke-VnaComIo $Connection 'DISPlay.WINDow(1).SPLit'); TitleState = [bool](Invoke-VnaComIo $Connection 'DISPlay.WINDow(1).TITLe.STATe')
            Title = [string](Invoke-VnaComIo $Connection 'DISPlay.WINDow(1).TITLe.DATA'); Traces = $traces }
    }
    [IO.File]::WriteAllText($Path, ($saved | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
}

function Restore-VnaScreen {
    param($Connection, [string]$Path)
    if (-not [IO.File]::Exists($Path)) { throw 'Копия прежнего экрана не найдена.' }
    $saved = [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8) | ConvertFrom-Json
    $serial = Assert-VnaInstrumentIdentity $Connection
    if ([string]$saved.VnaSerial -cne $serial) { throw 'Копия экрана снята с другого прибора. Восстановление запрещено.' }
    $traces = @($saved.Traces)
    if ($traces.Count -lt 1) { throw 'Копия экрана пуста.' }
    if ($Connection.Offline) {
        $Connection.Traces = $traces
        if ($Connection.PSObject.Properties['Screen']) { $Connection.Screen = $saved.Screen }
        return
    }
    Invoke-VnaComIo $Connection 'DISPlay.SPLit' set ([int]$saved.ChannelSplit)
    Invoke-VnaComIo $Connection 'CALCulate(1).PARameter(1).COUNt' set $traces.Count
    Invoke-VnaComIo $Connection 'DISPlay.WINDow(1).SPLit' set ([int]$saved.Split)
    for ($index = 1; $index -le $traces.Count; $index++) {
        $trace = $traces[$index - 1]
        Invoke-VnaComIo $Connection "CALCulate(1).PARameter($index).DEFine" set ([string]$trace.Parameter)
        Invoke-VnaComIo $Connection "CALCulate(1).PARameter($index).SELect" call
        Invoke-VnaComIo $Connection 'CALCulate(1).SELected.FORMat' set ([string]$trace.Format)
        foreach ($member in 'PDIVision', 'RLEVel', 'RPOSition') {
            if ($null -eq $trace.$member) { continue }
            # RPOSition is a COM Long; a Double there fails with «Specified cast is not valid» (seen live 18.11).
            $value = if ($member -eq 'RPOSition') { [int]$trace.$member } else { [double]$trace.$member }
            Invoke-VnaComIo $Connection "DISPlay.WINDow(1).TRACe($index).Y.SCALe.$member" set $value
        }
        $limit = [double[]]@($trace.LimitData | ForEach-Object { [double]$_ })
        if ($limit.Count -ge 6) { Invoke-VnaComIo $Connection 'CALCulate(1).SELected.LIMit.DATA' set $limit }
        Invoke-VnaComIo $Connection 'CALCulate(1).SELected.LIMit.DISPlay.STATe' set ([bool]$trace.LimitDisplay)
        Invoke-VnaComIo $Connection 'CALCulate(1).SELected.LIMit.STATe' set ([bool]$trace.LimitState)
        $markers = @($trace.Markers)
        if ($markers.Count -eq 0) { Invoke-VnaComIo $Connection 'CALCulate(1).SELected.MARKer(1).STATe' set $false; continue }
        Invoke-VnaComIo $Connection "CALCulate(1).SELected.MARKer($($markers.Count)).STATe" set $true
        if ($markers.Count -lt 15) { Invoke-VnaComIo $Connection "CALCulate(1).SELected.MARKer($($markers.Count + 1)).STATe" set $false }
        for ($marker = 1; $marker -le $markers.Count; $marker++) { Invoke-VnaComIo $Connection "CALCulate(1).SELected.MARKer($marker).X" set ([double]$markers[$marker - 1]) }
    }
    Invoke-VnaComIo $Connection 'DISPlay.WINDow(1).TITLe.DATA' set ([string]$saved.Title)
    Invoke-VnaComIo $Connection 'DISPlay.WINDow(1).TITLe.STATe' set ([bool]$saved.TitleState)
}

function Get-VnaTraceStatus {
    param($Connection)
    if ($Connection.Offline) { return $Connection.TraceStatus.Clone() }
    $result = @{}; $count = [int](Invoke-VnaComIo $Connection 'CALCulate(1).PARameter(1).COUNt')
    # Traces 1-4 carry the measurement; the further graphs of the screen only display it.
    if ($count -lt 4) { throw 'Требуются трассы S11/S21/S12/S22 на местах 1–4; выполните пункт 6.' }
    for ($traceIndex = 1; $traceIndex -le 4; $traceIndex++) {
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
        if (-not $states.ContainsKey($parameter) -or $states[$parameter] -cne 'COR') { throw "Нет полной коррекции $parameter; калибровки нет — выполните пункт 2." }
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
    } else {
        $saved = [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8) | ConvertFrom-Json
        if ($saved.traces) { $Connection.Traces = @($saved.traces) }
        if ($saved.trace_status) {
            $Connection.TraceStatus = @{}
            foreach ($property in $saved.trace_status.PSObject.Properties) { $Connection.TraceStatus[$property.Name] = $property.Value }
        }
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

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
        'SENSe(1).SWEep.POINts' = 1601; 'SENSe(1).AVERage.STATe' = $false
        'SENSe(1).CORRection.IMPedance.INPut.MAGNitude' = 50; 'SOURce(1).POWer.PORT(1).COUPle' = $false
        'CALCulate(1).FSIMulator.STATe' = $false; 'SENSe(1).CORRection.EXTension.STATe' = $false
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
            $Connection.Traces = @(foreach ($trace in Get-VnaScreenTraces $script:VnaDefaultPreset) {
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
    # IFBW 1 kHz, or 300 Hz «точный отсчёт»; both keep the calibration (the profile calibrates at 1 kHz).
    if ($Apply) { Invoke-VnaComIo $Connection 'SENSe(1).BANDwidth.RESolution' set 1000 }
    if ([double](Invoke-VnaComIo $Connection 'SENSe(1).BANDwidth.RESolution') -notin 1000, 300) { throw 'Изменена полоса ПЧ: допустимо 1 кГц или 300 Гц.' }
    # Traces 1–4 carry the measurement in every measuring preset; further traces only display.
    if ($Apply -and [int](Invoke-VnaComIo $Connection 'CALCulate(1).PARameter(1).COUNt') -lt 4) { Invoke-VnaComIo $Connection 'CALCulate(1).PARameter(1).COUNt' set 4 }
    if ([int](Invoke-VnaComIo $Connection 'CALCulate(1).PARameter(1).COUNt') -lt 4) { throw 'Нужны трассы S11/S21/S12/S22 на местах 1–4; выберите экран замера (пункт 6).' }
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
            $actual = Invoke-VnaComIo $Connection $path
            # СТО ФРАК-002-2026 п. 11.1: smoothing lowers VSWR peaks — the measurement is refused, not warned.
            if ($tail -eq 'SMOothing.STATe' -and $null -ne $actual -and (Test-VnaSettingOn $actual)) {
                throw "Трасса ${parameter}: включено сглаживание — замер не принимается; выключите сглаживание (пункт 6)."
            }
            Assert-VnaSetting $actual $traceSettings[$tail] $path
        }
    }
}

function Set-VnaProfile {
    param($Connection, $Limits, [string]$Preset = $script:VnaDefaultPreset)
    $null = Assert-VnaDeviceReady $Connection
    if (-not $Connection.Offline) {
        Invoke-VnaComIo $Connection 'ABORt' call
        Invoke-VnaComIo $Connection 'INITiate(1).CONTinuous' set $false
    }
    Assert-VnaProfile $Connection -Apply
    $Connection.ProfileApplied = $true; $Connection.Calibrated = $false
    Set-VnaScreen $Connection $Limits -Preset $Preset
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

# Screen presets (owner 18.11): a named, fixed set of graphs and stimulus per check. S2VNA places trace n in graph
# ((n − 1) mod G) + 1 for G graphs (checked live 18.11), so hidden traces share a graph with visible ones. Measuring
# presets keep traces 1–4 = S11, S21, S12, S22 (Get-VnaSweep and the COR check read them); the others only display.
# Common stimulus of every preset: 1601 points, IFBW 1 kHz («точный отсчёт» 300 Hz), 0 dBm, averaging off (option 4×),
# smoothing off on every trace (forbidden for acceptance: it hides ripple), port extension, time domain and gating off.
# Changing span, points or power after a calibration makes it invalid; graphs, scales, IFBW and averaging do not.
$script:VnaScreenMarkersHz = @(1e9, 3e9, 6e9, 12e9, 18e9)
$script:VnaPresetPoints = 1601
$script:VnaPresetPowerDbm = 0
$script:VnaDefaultPreset = 'ОТК — переход'
if (-not (Get-Variable -Name VnaActivePreset -Scope Script -ErrorAction SilentlyContinue)) { $script:VnaActivePreset = $script:VnaDefaultPreset }
if (-not (Get-Variable -Name VnaActiveScales -Scope Script -ErrorAction SilentlyContinue)) { $script:VnaActiveScales = $null }

function New-VnaPresetTrace {
    param([string]$Parameter, [string]$Format, $Scale, [string]$Limit, [switch]$Hidden, [switch]$Memory, [switch]$AutoScale)
    return [pscustomobject]@{ Parameter = $Parameter; Format = $Format; Scale = $Scale; Limit = $(if ($Limit) { $Limit } else { $null })
        Hidden = [bool]$Hidden; Memory = [bool]$Memory; AutoScale = [bool]$AutoScale }
}

function Get-VnaScreenPresets {
    # Every window has an explicit scale (owner 18.11): nothing is left over from the previous screen. 10 divisions.
    $swrNorm = [ordered]@{ NormTop = $true; PDIVision = 0.02; RLEVel = 1.0; RPOSition = 0 }  # 1,00…max(норма×1,15; 1,20)
    $swrAssembly = [ordered]@{ PDIVision = 0.05; RLEVel = 1.0; RPOSition = 0 }               # 1,00–1,50
    $fine = [ordered]@{ PDIVision = 0.01; RLEVel = 1.0; RPOSition = 0 }                      # 1,00–1,10
    $lossAdapter = [ordered]@{ PDIVision = 0.05; RLEVel = 0.0; RPOSition = 10 }              # 0…−0,5 дБ
    $lossAssembly = [ordered]@{ PDIVision = 0.3; RLEVel = 0.0; RPOSition = 10 }              # 0…−3 дБ
    $lossFlex = [ordered]@{ PDIVision = 0.03; RLEVel = 0.0; RPOSition = 10 }                 # 0…−0,3 дБ
    $overview = [ordered]@{ PDIVision = 5.0; RLEVel = 0.0; RPOSition = 10 }                  # −50…0 дБ
    $smith = [ordered]@{ PDIVision = 1.0 }                                                   # Вольперт-Смит, полная шкала 1
    $phase = [ordered]@{ PDIVision = 36.0; RLEVel = 0.0; RPOSition = 5 }                     # ±180°
    $full = [pscustomobject]@{ StartHz = 10e6; StopHz = 18e9 }
    $presets = @(
        [pscustomobject]@{ Name = 'ОТК — переход'; Purpose = 'замер переходника (пункт 4)'; Measuring = $true; Stimulus = $full; Split = 8
            Traces = @((New-VnaPresetTrace S11 SWR $swrNorm Swr), (New-VnaPresetTrace S21 MLOG $lossAdapter Loss),
                       (New-VnaPresetTrace S12 MLOG $lossAdapter Loss), (New-VnaPresetTrace S22 SWR $swrNorm Swr)) }
        [pscustomobject]@{ Name = 'ОТК — кабельная сборка'; Purpose = 'сборка с двух сторон, полоса и норма по ЭТК'; Measuring = $false; Stimulus = $null; Split = 5
            Traces = @((New-VnaPresetTrace S11 SWR $swrAssembly Assembly), (New-VnaPresetTrace S21 MLOG $lossAssembly), (New-VnaPresetTrace S22 SWR $swrAssembly Assembly)) }
        [pscustomobject]@{ Name = 'ОТК — один порт'; Purpose = 'сборка с одной стороны, на другом конце нагрузка (ЭТК CTRL_VSWR_coax)'; Measuring = $false; Stimulus = $null; Split = 3
            Traces = @((New-VnaPresetTrace S11 SWR $swrAssembly Assembly), (New-VnaPresetTrace S11 MLOG $overview)) }
        [pscustomobject]@{ Name = 'Проверка калибровки (нагрузка)'; Purpose = 'нагрузка 6650F27 на каждом порту (пункт 3)'; Measuring = $true; Stimulus = $full; Split = 3
            Traces = @((New-VnaPresetTrace S11 SWR $fine Cal), (New-VnaPresetTrace S21 MLOG $lossAdapter -Hidden),
                       (New-VnaPresetTrace S12 MLOG $lossAdapter -Hidden), (New-VnaPresetTrace S22 SWR $fine Cal)) }
        [pscustomobject]@{ Name = 'Проверка кабелей (шевеление)'; Purpose = 'запомнить графики, изогнуть кабель, сравнить'; Measuring = $false; Stimulus = $full; Split = 8
            Traces = @((New-VnaPresetTrace S11 SWR $fine -Memory), (New-VnaPresetTrace S22 SWR $fine -Memory),
                       (New-VnaPresetTrace S21 MLOG $lossFlex -Memory), (New-VnaPresetTrace S21 PHAS $phase -Memory)) }
        [pscustomobject]@{ Name = 'Подробно (инженер)'; Purpose = 'все графики: КСВН, потери, Вольперт-Смит, фаза, ГВЗ, возвратные потери'; Measuring = $true; Stimulus = $full; Split = 15
            Traces = @((New-VnaPresetTrace S11 SWR $swrNorm Swr), (New-VnaPresetTrace S21 MLOG $lossAdapter Loss), (New-VnaPresetTrace S12 MLOG $lossAdapter Loss),
                       (New-VnaPresetTrace S22 SWR $swrNorm Swr), (New-VnaPresetTrace S11 SMIT $smith), (New-VnaPresetTrace S21 PHAS $phase),
                       (New-VnaPresetTrace S21 GDEL ([ordered]@{}) -AutoScale), (New-VnaPresetTrace S22 SMIT $smith),
                       (New-VnaPresetTrace S11 MLOG $overview), (New-VnaPresetTrace S21 MLOG $overview), (New-VnaPresetTrace S12 MLOG $overview),
                       (New-VnaPresetTrace S22 MLOG $overview)) }
    )
    foreach ($preset in $presets) { for ($i = 0; $i -lt $preset.Traces.Count; $i++) { $preset.Traces[$i] | Add-Member NoteProperty Index ($i + 1) -Force } }
    return $presets
}

function Get-VnaScreenPreset {
    param([string]$Name = $script:VnaActivePreset)
    $preset = @(Get-VnaScreenPresets | Where-Object { $_.Name -ceq $Name })
    if ($preset.Count -ne 1) { throw "Нет экрана «$Name»." }
    return $preset[0]
}

function Get-VnaScreenTraces {
    param([string]$Preset = $script:VnaActivePreset)
    return @((Get-VnaScreenPreset $Preset).Traces)
}

function Format-VnaPresetParameters {
    param($Preset, $Stimulus)
    $span = if ($Stimulus) { '{0}–{1} МГц' -f ($Stimulus.StartHz / 1e6), ($Stimulus.StopHz / 1e6) } elseif ($Preset.Stimulus) {
        '{0}–{1} МГц' -f ($Preset.Stimulus.StartHz / 1e6), ($Preset.Stimulus.StopHz / 1e6) } else { 'полоса по ЭТК' }
    $scales = (@($Preset.Traces) | ForEach-Object { Format-VnaScaleText $_ }) -join '; '
    return "$span; шкалы: $scales; $script:VnaPresetPoints точек; ПЧ 1 кГц (точный отсчёт 300 Гц); $script:VnaPresetPowerDbm дБм; усреднение откл. (4× по выбору); сглаживание откл.; расширение портов, временная область и стробирование откл."
}

function Set-VnaPresetStimulus {
    param($Connection, $Preset, $Stimulus, [switch]$Precise, [switch]$Average)
    # Returns $true when span, points or power changed: a calibration made before is then invalid.
    if (-not $Stimulus) { $Stimulus = $Preset.Stimulus }
    if (-not $Stimulus) { throw "Для экрана «$($Preset.Name)» укажите полосу по ЭТК." }
    $wanted = [ordered]@{ 'SENSe(1).FREQuency.STARt' = [double]$Stimulus.StartHz; 'SENSe(1).FREQuency.STOP' = [double]$Stimulus.StopHz
        'SENSe(1).SWEep.POINts' = $script:VnaPresetPoints; 'SOURce(1).POWer.PORT(1).LEVel.IMMediate.AMPLitude' = $script:VnaPresetPowerDbm
        'SOURce(1).POWer.PORT(2).LEVel.IMMediate.AMPLitude' = $script:VnaPresetPowerDbm }
    $display = [ordered]@{ 'SENSe(1).SWEep.TYPE' = 'LIN'; 'SENSe(1).BANDwidth.RESolution' = $(if ($Precise) { 300 } else { 1000 })
        'SENSe(1).AVERage.STATe' = [bool]$Average; 'SENSe(1).CORRection.EXTension.STATe' = $false; 'CALCulate(1).FSIMulator.STATe' = $false }
    if ($Average) { $display['SENSe(1).AVERage.COUNt'] = 4 }
    if ($Connection.Offline) {
        if (-not $Connection.PSObject.Properties['Stimulus']) { $Connection | Add-Member NoteProperty Stimulus $null }
        # Offline starts on the calibration profile's band (10 MHz–18 GHz).
        $before = if ($Connection.Stimulus) { $Connection.Stimulus } else { [pscustomobject]@{ StartHz = 10e6; StopHz = 18e9 } }
        $Connection.Stimulus = [pscustomobject]@{ StartHz = [double]$Stimulus.StartHz; StopHz = [double]$Stimulus.StopHz; Precise = [bool]$Precise; Average = [bool]$Average }
        return [bool]($before -and ($before.StartHz -ne $Connection.Stimulus.StartHz -or $before.StopHz -ne $Connection.Stimulus.StopHz))
    }
    $changed = $false
    foreach ($path in $wanted.Keys) {
        if ([double](Invoke-VnaComIo $Connection $path) -ne [double]$wanted[$path]) { $changed = $true; Invoke-VnaComIo $Connection $path set $wanted[$path] }
    }
    foreach ($path in $display.Keys) { Invoke-VnaComIo $Connection $path set $display[$path] }
    return $changed
}

function Resolve-VnaTraceScale {
    param($Spec, [object[]]$SwrSegments)
    # Scale members actually written for a trace: the SWR top of an adapter screen follows the item's norm,
    # max(norm × 1,15; 1,20), rounded up to 0,05; without a norm 1,00–1,20.
    $scale = [ordered]@{}
    foreach ($member in $Spec.Scale.Keys) { if ($member -ne 'NormTop') { $scale[$member] = $Spec.Scale[$member] } }
    if ($Spec.Scale.Contains('NormTop')) {
        $norm = 0.0
        foreach ($segment in @($SwrSegments)) { $norm = [math]::Max($norm, [math]::Max([double]$segment.From, [double]$segment.To)) }
        $top = [math]::Max($norm * 1.15, 1.20)
        $top = [math]::Ceiling([math]::Round($top * 20, 6)) / 20
        $scale['PDIVision'] = [math]::Round(($top - 1.0) / 10, 4)
    }
    return $scale
}

function Format-VnaScaleText {
    param($Spec)
    $scale = Resolve-VnaTraceScale $Spec @()
    $name = @{ SWR = 'КСВН'; MLOG = 'дБ'; PHAS = 'фаза'; GDEL = 'ГВЗ'; SMIT = 'Вольперт-Смит' }[$Spec.Format]
    $text = switch ($Spec.Format) {
        'SWR' { if ($Spec.Scale.Contains('NormTop')) { '1,00…max(норма×1,15; 1,20)' } else { '1,00–{0:0.00}' -f (1 + 10 * $scale.PDIVision) } }
        'MLOG' { '0…−{0:0.###} дБ' -f (10 * $scale.PDIVision) }
        'PHAS' { '±180°' }
        'GDEL' { 'авто после каждой развёртки' }
        default { 'по умолчанию' }
    }
    $flags = @(); if ($Spec.Hidden) { $flags += 'скрыт' }; if ($Spec.Memory) { $flags += 'с памятью' }
    return ("{0} {1} {2}" -f $Spec.Parameter, $name, $text).Replace('.', ',') + $(if ($flags) { ' (' + ($flags -join ', ') + ')' } else { '' })
}

function Get-VnaScreenLimitSegments {
    param($Preset, $Limits, [string]$Kind)
    if ($Kind -eq 'Cal') {
        # Calibration check: the LOAD must stay ≤ 1,06 over the whole calibrated band (same number as item 3).
        $stimulus = if ($Preset.Stimulus) { $Preset.Stimulus } else { [pscustomobject]@{ StartHz = 10e6; StopHz = 18e9 } }
        return @([pscustomobject]@{ StartHz = $stimulus.StartHz; StopHz = $stimulus.StopHz; From = 1.06; To = 1.06 })
    }
    if ($Limits -and $Limits.PSObject.Properties[$Kind]) { return @($Limits.$Kind) }
    return @()
}

function Get-VnaChartScaleSettings {
    param([string]$Parameter)
    $trace = @(Get-VnaScreenTraces | Where-Object { $_.Parameter -eq $Parameter -and $_.Index -le 4 })
    if ($trace.Count -ne 1) { throw "Неизвестная трасса $Parameter. Выполните пункт 6." }
    return Resolve-VnaTraceScale $trace[0] @()
}

function ConvertTo-VnaLimitData {
    param([object[]]$Segments)
    # S2VNA limit table: N, then per segment type (1 = upper), start and stop stimulus, start and stop response.
    $values = New-Object 'System.Collections.Generic.List[double]'
    $values.Add(@($Segments).Count)
    foreach ($segment in @($Segments)) {
        # Type 1 = upper limit (SWR), 2 = lower limit (S21/S12 in dB against an insertion-loss norm).
        $type = if ($segment.PSObject.Properties['Type'] -and $segment.Type) { [int]$segment.Type } else { 1 }
        foreach ($value in $type, $segment.StartHz, $segment.StopHz, $segment.From, $segment.To) { $values.Add([double]$value) }
    }
    return [double[]]$values.ToArray()
}

function Assert-VnaScreen {
    param([object[]]$Traces, [string]$Preset = $script:VnaActivePreset)
    $expected = @(Get-VnaScreenTraces $Preset)
    if ($Traces.Count -ne $expected.Count) { throw "Экран замера: нужно графиков $($expected.Count), сейчас $($Traces.Count); выполните пункт 6." }
    $applied = if ($Preset -ceq $script:VnaActivePreset -and $script:VnaActiveScales) { $script:VnaActiveScales } else { @{} }
    foreach ($spec in $expected) {
        $trace = $Traces[$spec.Index - 1]
        $scale = if ($applied.ContainsKey($spec.Index)) { $applied[$spec.Index] } else { Resolve-VnaTraceScale $spec @() }
        Assert-VnaSetting $trace.Parameter $spec.Parameter "график $($spec.Index): параметр"
        Assert-VnaSetting $trace.Format $spec.Format "график $($spec.Index): формат $($spec.Parameter)"
        foreach ($member in $scale.Keys) { Assert-VnaSetting $trace.$member $scale[$member] "шкала $($spec.Parameter).$member (график $($spec.Index))" }
    }
}

function Set-VnaScreen {
    param($Connection, $Limits, [string]$Preset = $script:VnaActivePreset)
    $null = Assert-VnaInstrumentIdentity $Connection
    $presetSpec = Get-VnaScreenPreset $Preset
    $byKind = @{}
    foreach ($kind in 'Swr', 'Loss', 'Assembly', 'Cal') { $byKind[$kind] = @(Get-VnaScreenLimitSegments $presetSpec $Limits $kind) }
    $title = if ($Limits -and $Limits.Title) { [string]$Limits.Title } else { '' }
    if ($title.Length -gt 256) { $title = $title.Substring(0, 256) }
    $specs = @($presetSpec.Traces)
    $split = $presetSpec.Split
    $scales = @{}
    foreach ($spec in $specs) { $scales[$spec.Index] = Resolve-VnaTraceScale $spec $byKind['Swr'] }
    if ($Connection.Offline) {
        $Connection.Traces = @(foreach ($spec in $specs) {
            $trace = [ordered]@{ Parameter = $spec.Parameter; Format = $spec.Format; PDIVision = $null; RLEVel = $null; RPOSition = $null
                Limit = $(if ($spec.Limit) { $byKind[$spec.Limit] } else { @() }); Markers = $script:VnaScreenMarkersHz
                Hidden = $spec.Hidden; Memory = $spec.Memory }
            foreach ($member in $scales[$spec.Index].Keys) { $trace[$member] = $scales[$spec.Index][$member] }
            [pscustomobject]$trace
        })
        if (-not $Connection.PSObject.Properties['Screen']) { $Connection | Add-Member NoteProperty Screen $null }
        $Connection.Screen = [pscustomobject]@{ Split = $split; Title = $title; Preset = $presetSpec.Name }
    } else {
        Invoke-VnaComIo $Connection 'DISPlay.SPLit' set 1
        Invoke-VnaComIo $Connection 'CALCulate(1).PARameter(1).COUNt' set $specs.Count
        Invoke-VnaComIo $Connection 'DISPlay.WINDow(1).SPLit' set $split
        $markers = $script:VnaScreenMarkersHz.Count
        foreach ($spec in $specs) {
            $index = $spec.Index
            Invoke-VnaComIo $Connection "CALCulate(1).PARameter($index).DEFine" set $spec.Parameter
            Invoke-VnaComIo $Connection "CALCulate(1).PARameter($index).SELect" call
            Invoke-VnaComIo $Connection 'CALCulate(1).SELected.FORMat' set $spec.Format
            # Smoothing hides ripple; time domain and gating change what is measured: always off.
            Invoke-VnaComIo $Connection 'CALCulate(1).SELected.SMOothing.STATe' set $false
            Invoke-VnaComIo $Connection 'CALCulate(1).SELected.TRANsform.TIME.STATe' set $false
            Invoke-VnaComIo $Connection 'CALCulate(1).SELected.FILTer.GATE.TIME.STATe' set $false
            Invoke-VnaComIo $Connection "DISPlay.WINDow(1).TRACe($index).STATe" set (-not $spec.Hidden)
            Invoke-VnaComIo $Connection "DISPlay.WINDow(1).TRACe($index).MEMory.STATe" set ([bool]$spec.Memory)
            foreach ($member in $scales[$index].Keys) { Invoke-VnaComIo $Connection "DISPlay.WINDow(1).TRACe($index).Y.SCALe.$member" set $scales[$index][$member] }
            # @(...) around the whole if: a single PSCustomObject has no .Count in Windows PowerShell 5.1.
            $segments = @(if ($spec.Limit) { $byKind[$spec.Limit] })
            $withLimit = $segments.Count -gt 0
            $limitData = ConvertTo-VnaLimitData $segments
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
        Update-VnaScreenAutoScale $Connection $presetSpec.Name
        Assert-VnaSetting (Invoke-VnaComIo $Connection 'DISPlay.WINDow(1).SPLit') $split 'расположение графиков'
    }
    $script:VnaActivePreset = $presetSpec.Name
    $script:VnaActiveScales = $scales
    Assert-VnaScreen @(Get-VnaTraces $Connection) $presetSpec.Name
}

function Update-VnaScreenAutoScale {
    param($Connection, [string]$Preset = $script:VnaActivePreset)
    if ($Connection.Offline) { return }
    foreach ($spec in @(Get-VnaScreenTraces $Preset | Where-Object { $_.AutoScale })) {
        Invoke-VnaComIo $Connection "DISPlay.WINDow(1).TRACe($($spec.Index)).Y.SCALe.AUTO" call
    }
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
            # All 15 ordinary markers (16 is the reference marker, never touched): number and position of each one on.
            $markers = @(for ($marker = 1; $marker -le 15; $marker++) {
                if ([bool](Read-VnaComOptional $Connection "CALCulate(1).SELected.MARKer($marker).STATe")) {
                    [ordered]@{ Number = $marker; X = [double](Invoke-VnaComIo $Connection "CALCulate(1).SELected.MARKer($marker).X") }
                }
            })
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
        # S2VNA chains markers (n on switches 1..n on, n off switches n..15 off), so the markers on are always 1..n.
        $markers = @($trace.Markers | Where-Object { $null -ne $_ })
        $highest = 0; foreach ($marker in $markers) { $highest = [math]::Max($highest, [int]$marker.Number) }
        Invoke-VnaComIo $Connection 'CALCulate(1).SELected.MARKer(1).STATe' set $false
        if ($highest -gt 0) { Invoke-VnaComIo $Connection "CALCulate(1).SELected.MARKer($highest).STATe" set $true }
        foreach ($marker in $markers) { Invoke-VnaComIo $Connection "CALCulate(1).SELected.MARKer($([int]$marker.Number)).X" set ([double]$marker.X) }
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

# СТО ФРАК-002-2026 п. 11.5 (РЭ ч. 1 п. 3.3.3): a calibration recomputed onto another grid — «К?» interpolated,
# «К!» extrapolated — lowers accuracy; such a measurement is refused with its own reason, never written.
function Get-VnaCorrectionRefusal {
    param([string]$Parameter, $Status)
    $code = ([string]$Status).Trim().ToUpperInvariant()
    if ($code -ceq 'C?' -or $code -ceq 'RC?') { return "Трасса ${Parameter}: калибровка интерполирована («К?») — замер не принимается; выполните пункт 2 (калибровку) на сетке замера." }
    if ($code -ceq 'C!' -or $code -ceq 'RC!') { return "Трасса ${Parameter}: калибровка экстраполирована («К!») — замер не принимается; выполните пункт 2 (калибровку) на полосе замера." }
    return ''
}

function Test-VnaSettingOn {
    param($Value)
    if ($Value -is [bool]) { return $Value }
    return ([string]$Value).Trim().ToUpperInvariant() -in @('1', 'ON', 'TRUE')
}

function Assert-VnaCorrection {
    param($Connection)
    $states = Get-VnaTraceStatus $Connection
    foreach ($parameter in 'S11','S21','S12','S22') {
        if ($states.ContainsKey($parameter) -and ($refusal = Get-VnaCorrectionRefusal $parameter $states[$parameter])) { throw $refusal }
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

function Save-VnaTraceMemory {
    param($Connection)
    # Data → memory on every graph of the active preset (before bending the cables); memory is shown next to data.
    $null = Assert-VnaInstrumentIdentity $Connection
    if ($Connection.Offline) { foreach ($trace in $Connection.Traces) { $trace | Add-Member NoteProperty Memorized $true -Force }; return }
    $count = @(Get-VnaScreenTraces).Count
    for ($index = 1; $index -le $count; $index++) {
        Invoke-VnaComIo $Connection "CALCulate(1).PARameter($index).SELect" call
        Invoke-VnaComIo $Connection 'CALCulate(1).SELected.MATH.MEMorize' call
        Invoke-VnaComIo $Connection "DISPlay.WINDow(1).TRACe($index).MEMory.STATe" set $true
    }
}

function Get-VnaWarmupMinutes {
    param($Connection)
    # S2VNA start time stands in for the VNA power-on (the VNA is powered and connected before S2VNA finds it).
    if ($Connection.Offline) { return $null }
    $process = @(Get-Process S2VNA -ErrorAction SilentlyContinue | Sort-Object StartTime | Select-Object -First 1)
    if (-not $process -or -not $process[0].StartTime) { return $null }
    return ((Get-Date) - $process[0].StartTime).TotalMinutes
}

function Save-VnaPresetFiles {
    param($Connection, [string]$Directory, $Stimuli, [string]$AutorecallPath)
    # One S2VNA state file per preset, settings only (MMEM:STOR:STYP STATe — no calibration table, no data, РЭ ч. 2
    # «MMEM:STOR:STYP»). Applies each preset with its stimulus: the calibration of this run stops being valid.
    # Existing files are never overwritten. Autorecall.sta (\State, loaded at every S2VNA start) only if it is absent.
    $null = Assert-VnaInstrumentIdentity $Connection
    if ($Connection.Offline) { throw 'Файлы экранов пишет только S2VNA.' }
    [void][IO.Directory]::CreateDirectory($Directory)
    $previousType = [string](Invoke-VnaComIo $Connection 'MMEMory.STORe.STYPe')
    $written = @()
    try {
        Invoke-VnaComIo $Connection 'MMEMory.STORe.STYPe' set 'STAT'
        foreach ($preset in Get-VnaScreenPresets) {
            $path = Join-Path $Directory ($preset.Name + '.sta')
            if ([IO.File]::Exists($path)) { continue }
            $stimulus = if ($preset.Stimulus) { $preset.Stimulus } else { $Stimuli }
            $null = Set-VnaPresetStimulus $Connection $preset $stimulus
            Set-VnaScreen $Connection $null -Preset $preset.Name
            Invoke-VnaComIo $Connection 'MMEMory.STORe.STATe' set $path
            $null = Invoke-VnaComIo $Connection 'IEEE4882.OPC'
            $written += $path
        }
        if ($AutorecallPath -and -not [IO.File]::Exists($AutorecallPath)) {
            $default = Get-VnaScreenPreset $script:VnaDefaultPreset
            $null = Set-VnaPresetStimulus $Connection $default $default.Stimulus
            Set-VnaScreen $Connection $null -Preset $default.Name
            Invoke-VnaComIo $Connection 'MMEMory.STORe.STATe' set $AutorecallPath
            $null = Invoke-VnaComIo $Connection 'IEEE4882.OPC'
            $written += $AutorecallPath
        }
    } finally { if ($previousType) { Invoke-VnaComIo $Connection 'MMEMory.STORe.STYPe' set $previousType } }
    $Connection.Calibrated = $false
    return $written
}

#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Offline,
    [string]$OfflineDirectory,
    [string]$DataDirectory,
    [string]$ConfigPath,
    [Alias('fmax')][ValidateRange(10000000,18000000000)][double]$FmaxHz = 18e9
)

$script:VnaToolVersion = '1.1.0'
. (Join-Path $PSScriptRoot 'dsp.ps1')
. (Join-Path $PSScriptRoot 's2vna.ps1')
. (Join-Path $PSScriptRoot 'erp.ps1')
. (Join-Path $PSScriptRoot 'norms.ps1')

function ConvertFrom-VnaChain {
    param([Parameter(Mandatory)][string]$Text)
    $value = $Text.Trim()
    if ($value -match '^(напрямую|1)$') { return [pscustomobject]@{ Left = $null; Right = $null; Description = 'напрямую' } }
    if ($value -notmatch '^(?:слева\s+(СИ\.\d+))?(?:\s*,?\s*справа\s+(СИ\.\d+))?$' -or -not $value) {
        throw 'Цепочка: напрямую / слева СИ.009 / слева СИ.009, справа СИ.008.'
    }
    $left = $Matches[1]; $right = $Matches[2]
    if (-not $left -and -not $right) { throw 'Пустая цепочка.' }
    return [pscustomobject]@{ Left = "$left".ToUpperInvariant(); Right = "$right".ToUpperInvariant(); Description = $value }
}

function Get-VnaSeriesFmaxHz {
    param([Parameter(Mandatory)][string]$Name)
    # Match complete connector labels and the adapter series used in the ERP cards.
    if ($Name -match '(?i)\bBNC\b|\b(?:SB|TB|B)-\d') { return 4e9 }
    if ($Name -match '(?i)\bTNC\b|\bN(?:-type|[ -](?:вилка|розетка|male|female))?\b|\b(?:SN|ST|TN|T|N)-\d') { return 11e9 }
    return 18e9
}

function Add-VnaJournalEvent {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Event)
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Event | ConvertTo-Json -Depth 12 -Compress) + "`n")
    $stream = [IO.FileStream]::new($Path, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}

function Get-VnaJournal {
    param([Parameter(Mandatory)][string]$Path)
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in [IO.File]::ReadAllLines($Path, [Text.Encoding]::UTF8)) {
            if (-not $line.Trim()) { continue }
            try { $entry = $line | ConvertFrom-Json -ErrorAction Stop } catch { throw "Журнал повреждён: $Path. Запись запрещена." }
            if (-not $entry) { throw "Журнал повреждён: $Path." }
            $entry
        }
    }
}

function Get-VnaRawHash {
    param([Parameter(Mandatory)]$Data)
    $ci = [Globalization.CultureInfo]::InvariantCulture
    $builder = [Text.StringBuilder]::new()
    for ($index = 0; $index -lt $Data.Frequencies.Count; $index++) {
        [void]$builder.Append(([double]$Data.Frequencies[$index]).ToString('R', $ci)).Append('|')
        foreach ($name in 'S11', 'S21', 'S12', 'S22') {
            [void]$builder.Append($Data.$name[$index].Real.ToString('R', $ci)).Append(',').Append($Data.$name[$index].Imaginary.ToString('R', $ci)).Append('|')
        }
    }
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($builder.ToString())))).Replace('-', '').ToLowerInvariant() }
    finally { $algorithm.Dispose() }
}

function Assert-VnaTemperature {
    param($Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) { throw 'Температура не прочитана; повторите калибровку.' }
    $number = 0.0
    $numeric = $Value -is [double] -or $Value -is [single] -or $Value -is [decimal] -or $Value -is [int] -or $Value -is [long]
    if ($numeric) { $number = [double]$Value }
    if ((-not $numeric -and -not [double]::TryParse([string]$Value, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$number)) -or
        [double]::IsNaN($number) -or [double]::IsInfinity($number) -or $number -lt -40 -or $number -gt 125) { throw 'Недостоверная температура; повторите калибровку.' }
}

function Assert-VnaMeasurementSafe {
    param([Parameter(Mandatory)]$Sweep, $Calibration, [Parameter(Mandatory)]$DeviceInfo,
        [Parameter(Mandatory)]$TraceStatus, [string]$LedgerPath, [datetimeoffset]$Now = [datetimeoffset]::UtcNow,
        [switch]$SkipLoadCheck)
    if (-not $Calibration) { throw 'Нет действующей калибровки; выполните калибровку АКМ.' }
    $calTime = [datetimeoffset]::Parse($Calibration.Time, [Globalization.CultureInfo]::InvariantCulture)
    if ($calTime -gt $Now -or ($Now - $calTime).TotalHours -gt 4) { throw 'Калибровка старше 4 часов или часы изменены; повторите калибровку.' }
    $captureTime = [datetimeoffset]::Parse($Sweep.CapturedAt, [Globalization.CultureInfo]::InvariantCulture)
    if ($captureTime -lt $calTime -or $captureTime -gt $Now) { throw 'Развёртка не относится к текущей калибровке.' }
    foreach ($identity in 'VnaSerial', 'AcmSerial') {
        if (-not $DeviceInfo.$identity -or $DeviceInfo.$identity -cne $Calibration.$identity -or $Sweep.DeviceInfo.$identity -cne $Calibration.$identity) {
            throw 'Прибор или АКМ сменился; повторите калибровку.'
        }
    }
    if (-not $DeviceInfo.Ready) { throw 'Прибор не готов.' }
    foreach ($info in @($DeviceInfo, $Sweep.DeviceInfo, $Calibration)) {
        Assert-VnaTemperature $info.VnaTemperature
        Assert-VnaTemperature $info.AcmTemperature
        if (@($info.VnaTemperatures).Count -ne 2) { throw 'Не прочитаны оба датчика температуры ВАЦ.' }
        foreach ($temperature in $info.VnaTemperatures) { Assert-VnaTemperature $temperature }
    }
    foreach ($info in @($DeviceInfo, $Sweep.DeviceInfo)) {
        for ($sensor = 0; $sensor -lt 2; $sensor++) {
            if ([math]::Abs([double]$info.VnaTemperatures[$sensor] - [double]$Calibration.VnaTemperatures[$sensor]) -gt 1) {
                throw 'Температура ушла больше чем на 1 °C — откалибруйте заново.'
            }
        }
    }
    foreach ($name in 'S11', 'S21', 'S12', 'S22') {
        if ($TraceStatus[$name] -cne 'COR' -or $Sweep.TraceStatus[$name] -cne 'COR' -or
            $null -eq $Sweep.Data.$name -or $Sweep.Data.$name.Count -ne $Sweep.Data.Frequencies.Count) {
            throw "Трасса $name отсутствует или без COR; повторите калибровку."
        }
    }
    if ($Sweep.Data.Frequencies.Count -lt 2) { throw 'Недостаточно точек развёртки.' }
    Assert-VnaData $Sweep.Data
    if (-not $SkipLoadCheck -and $Calibration.LoadCheck -ne 'pass') { throw 'Нужен успешный контроль нагрузкой на обоих портах.' }
    if ($LedgerPath) {
        $hash = Get-VnaRawHash $Sweep.Data
        foreach ($entry in @(Get-VnaJournal $LedgerPath)) {
            if (-not $entry.Hash -or -not $entry.SweepId) { throw 'Неполная запись журнала развёрток. Запись запрещена.' }
            if ($entry.Hash -eq $hash -or $entry.SweepId -eq $Sweep.Id) { throw 'Развёртка уже использована или зарезервирована; нужен новый замер.' }
        }
    }
}

function Reserve-VnaSweep {
    param($Sweep, [string]$Number, [string]$LedgerPath, [string]$RawPath)
    $hash = Get-VnaRawHash $Sweep.Data
    foreach ($entry in @(Get-VnaJournal $LedgerPath)) {
        if (-not $entry.Hash -or -not $entry.SweepId -or $entry.Hash -eq $hash -or $entry.SweepId -eq $Sweep.Id) { throw 'Развёртка уже использована или журнал повреждён.' }
    }
    Add-VnaJournalEvent $LedgerPath ([ordered]@{ Event = 'reserved'; Time = [datetimeoffset]::UtcNow.ToString('o'); Hash = $hash; SweepId = $Sweep.Id; Number = $Number; RawPath = $RawPath })
    return $hash
}

function Confirm-VnaAction {
    param([string]$Prompt)
    return (Read-Host "$Prompt (д/н)") -match '^(д|да|y|yes)$'
}

function Show-VnaDevice {
    param($Info)
    Write-Host "Готов: $($Info.Ready); $($Info.VnaName); серийный № $($Info.VnaSerial)"
    Write-Host "АКМ: $($Info.AcmInfo); № $($Info.AcmSerial); диапазон $($Info.AcmMinHz / 1e9)–$($Info.AcmMaxHz / 1e9) ГГц"
    Write-Host "Температура ВАЦ: $($Info.VnaTemperatures -join ' / ') °C; АКМ: $($Info.AcmTemperature) °C"
}

function Show-VnaInstrumentState {
    param($Connection, $Calibration, [datetimeoffset]$Now = [datetimeoffset]::UtcNow)
    $info = $null
    try { $info = Get-VnaDeviceInfo $Connection; Show-VnaDevice $info }
    catch { Write-Host "Не удалось прочитать сведения прибора и АКМ: $($_.Exception.Message)" }
    $traces = @(Get-VnaTraces $Connection)
    Write-Host "Трасс: $($traces.Count)"
    foreach ($trace in $traces) {
        $correction = if ($trace.Correction -ceq 'COR') { 'COR' } else { 'нет' }
        Write-Host "Трасса $($trace.Index): $($trace.Parameter), $($trace.Format); шкала: деление=$($trace.PDIVision), уровень=$($trace.RLEVel), позиция=$($trace.RPOSition); коррекция: $correction"
    }
    try { Assert-VnaScreen $traces; Write-Host 'Экран замера стандартный.' }
    catch { Write-Host 'Экран не стандартный — пункт 6' }
    try {
        if (-not $Calibration) { throw 'В этом запуске калибровка не выполнялась.' }
        $calTime = [datetimeoffset]::Parse($Calibration.Time, [Globalization.CultureInfo]::InvariantCulture)
        Write-Host ('Возраст калибровки: {0:N1} мин (предел 240 мин); контроль LOAD: {1}' -f ($Now - $calTime).TotalMinutes, $Calibration.LoadCheck)
        if (-not $Connection.Calibrated -or $Calibration.Result -ne 'pass') { throw 'Калибровка не завершена.' }
        if ($calTime -gt $Now -or ($Now - $calTime).TotalHours -gt 4) { throw 'Калибровка старше 4 часов или часы изменены.' }
        foreach ($identity in 'VnaSerial','AcmSerial') {
            if (-not $info.$identity -or $info.$identity -cne $Calibration.$identity) { throw 'Прибор или АКМ сменился.' }
        }
        if (-not $info.Ready) { throw 'Прибор не готов.' }
        foreach ($reading in @($info, $Calibration)) {
            Assert-VnaTemperature $reading.VnaTemperature
            Assert-VnaTemperature $reading.AcmTemperature
            if (@($reading.VnaTemperatures).Count -ne 2) { throw 'Не прочитаны оба датчика температуры ВАЦ.' }
            foreach ($temperature in $reading.VnaTemperatures) { Assert-VnaTemperature $temperature }
        }
        for ($sensorIndex = 0; $sensorIndex -lt 2; $sensorIndex++) {
            if ([math]::Abs([double]$info.VnaTemperatures[$sensorIndex] - [double]$Calibration.VnaTemperatures[$sensorIndex]) -gt 1) { throw 'Температура ушла больше чем на 1 °C — откалибруйте заново.' }
        }
        Assert-VnaProfile $Connection
        $null = Assert-VnaCorrection $Connection
        Write-Host 'Калибровка действует'
    } catch {
        Write-Host 'Калибровки нет — выполните пункт 2'
        Write-Host $_.Exception.Message
    }
}

function Set-VnaMeasureScreen {
    param($Connection, $Limits, [string]$BackupPath, [string]$Preset = $script:VnaDefaultPreset)
    # The operator's screen is saved once, before the first change; «8» puts it back without touching calibration.
    if (-not (Test-Path -LiteralPath $BackupPath)) { Backup-VnaScreen $Connection $BackupPath }
    Set-VnaScreen $Connection $Limits -Preset $Preset
    if ($Limits -and $Limits.Title) { Write-Host "Экран: $($Limits.Title)" }
}

function Select-VnaPreset {
    param($Norms)
    # «6 Экран»: every preset with its fixed parameters; band and assembly norm come from the ETK when the check needs them.
    $presets = @(Get-VnaScreenPresets)
    for ($i = 0; $i -lt $presets.Count; $i++) {
        Write-Host ("{0} {1} — {2}. {3}" -f ($i + 1), $presets[$i].Name, $presets[$i].Purpose, (Format-VnaPresetParameters $presets[$i] $null))
    }
    $choice = 0
    if (-not [int]::TryParse((Read-Host 'Номер экрана').Trim(), [ref]$choice) -or $choice -lt 1 -or $choice -gt $presets.Count) { throw 'Нет такого экрана.' }
    $preset = $presets[$choice - 1]; $stimulus = $null; $limits = $null
    if (-not $preset.Stimulus) {
        $band = (Read-Host 'Полоса по ЭТК, МГц (например 1–6000)').Trim()
        if ($band -notmatch '^(\d+(?:[.,]\d+)?)\s*[–-]\s*(\d+(?:[.,]\d+)?)$') { throw 'Полоса: два числа в МГц через тире.' }
        $low = [double]::Parse($Matches[1].Replace(',', '.'), [Globalization.CultureInfo]::InvariantCulture) * 1e6
        $high = [double]::Parse($Matches[2].Replace(',', '.'), [Globalization.CultureInfo]::InvariantCulture) * 1e6
        if ($low -lt 1e5 -or $high -gt 18e9 -or $high -le $low) { throw 'Полоса вне диапазона ВАЦ 0,1–18000 МГц.' }
        $stimulus = [pscustomobject]@{ StartHz = $low; StopHz = $high }
    }
    if (@($preset.Traces | Where-Object { $_.Limit -eq 'Assembly' }).Count) {
        $norm = (Read-Host 'Норма КСВН сборки по ЭТК (Enter — без линии)').Trim()
        if ($norm) {
            $value = 0.0
            if (-not [double]::TryParse($norm.Replace(',', '.'), [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$value) -or $value -le 1) { throw 'Норма КСВН — число больше 1.' }
            $span = if ($stimulus) { $stimulus } else { $preset.Stimulus }
            $limits = [pscustomobject]@{ Title = ('КСВН сборки ≤ {0} по ЭТК' -f $norm); Assembly = @([pscustomobject]@{ StartHz = $span.StartHz; StopHz = $span.StopHz; From = $value; To = $value }) }
        }
    }
    $mode = (Read-Host 'Enter — обычный (ПЧ 1 кГц); т — точный отсчёт (ПЧ 300 Гц); у — усреднение 4×').Trim()
    return [pscustomobject]@{ Preset = $preset; Stimulus = $stimulus; Limits = $limits; Precise = ($mode -match '^т'); Average = ($mode -match '^у') }
}

function Restore-VnaOperatorState {
    param($Connection, $Backup)
    $serial = Assert-VnaInstrumentIdentity $Connection
    if (-not $Backup -or $Backup.VnaSerial -cne $serial) { throw 'Резервная копия принадлежит другому прибору. Восстановление запрещено.' }
    Restore-VnaState -Connection $Connection -Path $Backup.Path
}

function Assert-VnaCardIdentity {
    param($Measured, $Current)
    if (-not $Measured -or -not $Current -or [string]$Measured.id -cne [string]$Current.id -or
        [string]$Measured.serial_number -cne [string]$Current.serial_number) { throw 'Карточка или серийный номер изменились после замера. Начните замер заново.' }
}

function Get-VnaFixtureData {
    param($Session, [string]$Number, [string]$CacheDirectory, [double]$BandHz)
    $fixture = Get-VnaInstrument -Session $Session -Number $Number
    Write-Host "Оснастка $Number`: $($fixture.name); серийный № $($fixture.serial_number)"
    $path = Get-VnaFixtureFile -Session $Session -Instrument $fixture -CacheDirectory $CacheDirectory
    $data = Select-VnaBand -Data (Read-VnaTouchstone -Path $path) -FmaxHz $BandHz
    $reverse = Confirm-VnaAction 'Оснастка стоит обратно: её сторона 2 обращена к порту 1 ВАЦ?'
    if ($reverse) {
        $data = [pscustomobject]@{ Frequencies = $data.Frequencies; S11 = $data.S22; S21 = $data.S12; S12 = $data.S21; S22 = $data.S11 }
    }
    return [pscustomobject]@{ Instrument = $fixture; Reverse = $reverse; Data = $data; Description = "$Number (обратно=$reverse, файл=$([IO.Path]::GetFileName($path)))" }
}

function Initialize-VnaOfflineDemo {
    param([string]$Directory)
    [void][IO.Directory]::CreateDirectory($Directory)
    $device = [ordered]@{ Ready = $true; VnaName = 'Planar, S50180, MOCK-VNA, mock'; VnaSerial = 'MOCK-VNA'; AcmSerial = 'MOCK-ACM';
        AcmInfo = 'SYNTHETIC OFFLINE ACM'; AcmMinHz = 10e6; AcmMaxHz = 18e9; VnaTemperature = 30; VnaTemperatures = @(30, 31); AcmTemperature = 29 }
    $device | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Directory 'device.json') -Encoding UTF8
    @{ instruments = @(
        @{ id = 'offline-035'; inventory_number = 'СИ.035'; name = 'Учебный переходник SMA–SMA'; serial_number = 'MOCK-DUT'; files = @(); mating_ends = @('A', 'B'); mating_resource = 500 },
        @{ id = 'offline-100'; inventory_number = 'СИ.100'; name = 'Учебный кабель порта 1'; serial_number = 'MOCK-C1'; files = @(); mating_ends = @('A', 'B'); mating_resource = $null },
        @{ id = 'offline-101'; inventory_number = 'СИ.101'; name = 'Учебный кабель порта 2'; serial_number = 'MOCK-C2'; files = @(); mating_ends = @('A', 'B'); mating_resource = $null },
        @{ id = 'offline-104'; inventory_number = 'СИ.104'; name = 'Учебный АКМ'; serial_number = 'MOCK-ACM'; files = @(); mating_ends = @('A', 'B'); mating_resource = $null }) } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Directory 'instruments.json') -Encoding UTF8
    $frequencies = [double[]]::new(1601); $reflection = [Numerics.Complex[]]::new(1601); $transmission = [Numerics.Complex[]]::new(1601)
    for ($index = 0; $index -lt 1601; $index++) {
        $frequencies[$index] = 10e6 + $index * (18e9 - 10e6) / 1600
        $reflection[$index] = [Numerics.Complex]::new(0.02, 0)
        $transmission[$index] = [Numerics.Complex]::FromPolarCoordinates(0.98, -$index / 1600.0)
    }
    Write-VnaTouchstone -Path (Join-Path $Directory 'sweep.s2p') -Data ([pscustomobject]@{ Frequencies = $frequencies; S11 = $reflection; S21 = $transmission; S12 = $transmission; S22 = $reflection }) -Comments @('SYNTHETIC OFFLINE DATA - NOT A MEASUREMENT')
}

function Connect-VnaErp {
    param([switch]$Offline, [string]$OfflineDirectory, [string]$ConfigPath)
    if ($Offline) { return New-VnaErpSession -OfflineDirectory $OfflineDirectory }
    $config = Read-VnaConfig -Path $ConfigPath
    $login = Read-Host 'Логин ERP'; $password = Read-Host 'Пароль ERP' -AsSecureString
    try { return New-VnaErpSession -ServerUrl $config.server_url -Login $login -Password $password } finally { $password = $null }
}

function Start-VnaMeasure {
    param([switch]$Offline, [string]$OfflineDirectory, [string]$DataDirectory, [string]$ConfigPath, [double]$FmaxHz = 18e9)
    $ErrorActionPreference = 'Stop'
    if (-not $DataDirectory) { $DataDirectory = Join-Path $PSScriptRoot $(if ($Offline) { 'data\offline' } else { 'data' }) }
    if (-not $ConfigPath) { $ConfigPath = Join-Path $PSScriptRoot 'config.json' }
    if (-not $Offline) {
        $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
        if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Запустите обычным двойным щелчком, без прав администратора: S2VNA COM требует обычный сеанс.' }
    }
    [void][IO.Directory]::CreateDirectory($DataDirectory)
    $script:VnaActivePreset = $script:VnaDefaultPreset; $script:VnaActiveScales = $null
    $lock = $null; $deviceMutex = $null; $deviceLocked = $false; $connection = $null; $backup = $null; $session = $null; $calibration = $null
    $backupPointer = Join-Path $DataDirectory 'operator-backup.json'
    $screenBackup = Join-Path $DataDirectory 'screen-backup.json'
    $calLog = Join-Path $DataDirectory 'calibrations.jsonl'; $ledger = Join-Path $DataDirectory 'used-sweeps.jsonl'
    # Matings wait here until the ERP confirms them (see matings.ps1); item 7 resends them.
    $pendingDirectory = Join-Path $DataDirectory 'pending-matings'
    $config = $null; $matingSetup = $null; $matingTarget = $null; $previousMounts = $null
    try {
        $lock = [IO.File]::Open((Join-Path $DataDirectory 'tool.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        if ($Offline) {
            Write-Host 'УЧЕБНЫЙ РЕЖИМ: только локальные данные, прибор и ERP не используются.' -ForegroundColor Yellow
            if (-not $OfflineDirectory) {
                $OfflineDirectory = Join-Path $DataDirectory 'mock'
                if (-not (Test-Path -LiteralPath $OfflineDirectory)) { Initialize-VnaOfflineDemo $OfflineDirectory }
            }
            $connection = New-VnaConnection -OfflineDirectory $OfflineDirectory
            $matingSetup = Get-VnaMatingSetup $null; $matingTarget = Get-VnaMatingTarget -OfflineDirectory $OfflineDirectory
        } else { $connection = New-VnaConnection }
        # Boot and recovery must also work when the ACM USB cable is disconnected.
        $boundSerial = Assert-VnaInstrumentIdentity $connection
        if (-not $Offline) {
            $sha = [Security.Cryptography.SHA256]::Create()
            try { $digest = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($boundSerial)))).Replace('-', '').ToLowerInvariant() } finally { $sha.Dispose() }
            $deviceMutex = [Threading.Mutex]::new($false, ('Global\FraxisVna-' + $digest))
            try { $deviceLocked = $deviceMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $deviceLocked = $true }
            if (-not $deviceLocked) { throw 'Прибор занят другим сеансом. Завершите сеанс ERP/другого инструмента.' }
        }
        if (Test-Path -LiteralPath $backupPointer) {
            $candidateBackup = Get-Content -Raw -Encoding UTF8 -LiteralPath $backupPointer | ConvertFrom-Json
            if (-not $candidateBackup -or $candidateBackup.VnaSerial -cne $boundSerial -or -not $candidateBackup.Path -or -not (Test-Path -LiteralPath $candidateBackup.Path -PathType Leaf)) { throw 'Сохранённое состояние принадлежит другому прибору или файл утрачен. Проверьте operator-backup.json.' }
            $backup = $candidateBackup
            Write-Host "Есть невосстановленное состояние оператора: $($backup.Path). Калибровка прошлого запуска не используется."
        }
        $norms = $null
        try { $norms = Read-VnaNorms (Find-VnaNormsFile $PSScriptRoot) } catch { Write-Host $_.Exception.Message -ForegroundColor Yellow }
        if (-not $norms) { Write-Host 'Нет rf-norms.json рядом с программой: линии допуска на экране не показываются.' -ForegroundColor Yellow }
        $running = $true
        while ($running) {
            Write-Host "`n1 Проверить прибор и АКМ`n2 Калибровка АКМ`n3 Контроль нагрузкой`n4 Замер переходника`n5 Вернуть настройки оператора`n6 Экран: выбрать вариант`n7 Отправить сочленения в ERP`n8 Вернуть прежний экран`n9 Шевеление: запомнить графики (до изгиба)`n10 Записать экраны в файлы S2VNA`n0 Выход"
            try {
                switch (Read-Host 'Выберите действие') {
                    '1' { Show-VnaInstrumentState $connection $calibration }
                    '2' {
                        if (-not (Confirm-VnaAction 'Подключите АКМ к концам тракта портов 1 и 2. Начать калибровку?')) { break }
                        $calibration = $null
                        if (-not $matingSetup) { $config = Read-VnaConfig -Path $ConfigPath; $matingSetup = Get-VnaMatingSetup $config; $matingTarget = Get-VnaMatingTarget -ServerUrl $config.server_url }
                        $previousMounts = $null  # The cables were on the ACM: the next measurement mates them again.
                        $attempt = [ordered]@{ Id = [guid]::NewGuid().ToString(); Event = 'calibration'; Time = [datetimeoffset]::UtcNow.ToString('o'); User = [Environment]::UserName;
                            Method = 'acm-solt2'; VnaSerial = $null; AcmSerial = $null; VnaTemperature = $null; VnaTemperatures = @(); AcmTemperature = $null; LoadCheck = 'not_checked'; Result = 'fail' }
                        # The ACM is connected now, whatever the calibration result: the mating is counted.
                        $null = Save-VnaPendingMatings $pendingDirectory $matingTarget ('vna-calibration-' + $attempt.Id) (New-VnaCalibrationMatings $matingSetup)
                        if ($session) { $null = Sync-VnaPendingMatings $session $pendingDirectory }
                        try {
                            $info = Assert-VnaDeviceReady $connection
                            foreach ($field in 'VnaSerial', 'AcmSerial', 'VnaTemperature', 'VnaTemperatures', 'AcmTemperature') { $attempt[$field] = $info.$field }
                            if (-not $backup) {
                                $backupPath = Join-Path $DataDirectory ('operator-' + [guid]::NewGuid().ToString() + '.sta')
                                Backup-VnaState -Connection $connection -Path $backupPath
                                $backup = [pscustomobject]@{ Path = $backupPath; VnaSerial = $info.VnaSerial }
                                $backup | ConvertTo-Json | Set-Content -LiteralPath $backupPointer -Encoding UTF8
                            }
                            if (-not (Test-Path -LiteralPath $screenBackup)) { Backup-VnaScreen $connection $screenBackup }
                            $warmup = Get-VnaWarmupMinutes $connection
                            if ($null -ne $warmup -and $warmup -lt 40) { Write-Host ('S2VNA работает {0:N0} мин: прогрев ВАЦ по РЭ — 40 мин, АКМ — 15 мин.' -f $warmup) -ForegroundColor Yellow }
                            Set-VnaProfile $connection (Get-VnaScreenLimits $norms $null)
                            $null = Invoke-VnaCalibration $connection
                            $info = Get-VnaDeviceInfo $connection
                            foreach ($field in 'VnaSerial', 'AcmSerial', 'VnaTemperature', 'VnaTemperatures', 'AcmTemperature') { $attempt[$field] = $info.$field }
                            $status = Get-VnaTraceStatus $connection
                            foreach ($name in 'S11', 'S21', 'S12', 'S22') { if ($status[$name] -cne 'COR') { throw "Нет COR на $name." } }
                            foreach ($temperature in @($info.VnaTemperatures) + @($info.AcmTemperature)) { Assert-VnaTemperature $temperature }
                            $attempt.Time = [datetimeoffset]::UtcNow.ToString('o'); $attempt.Result = 'pass'
                            Show-VnaDevice $info
                            Write-Host 'Калибровка завершена. Выполните контроль нагрузкой (3).'
                        } finally {
                            Add-VnaJournalEvent $calLog $attempt
                            if ($attempt.Result -eq 'pass') { $calibration = [pscustomobject]$attempt }
                        }
                    }
                    '3' {
                        if (-not $calibration) { throw 'Сначала выполните калибровку АКМ.' }
                        $calibration.LoadCheck = 'fail'; $values = @(); $passed = $false
                        try { Set-VnaMeasureScreen $connection $null $screenBackup 'Проверка калибровки (нагрузка)' }
                        catch { Write-Host "Экран проверки не поставлен: $($_.Exception.Message)" -ForegroundColor Yellow }
                        try {
                            foreach ($port in 1, 2) {
                                [void](Read-Host "Подключите LOAD к порту $port на плоскости калибровки; второй порт свободен. Нажмите Enter")
                                $sweep = Get-VnaSweep $connection
                                try { Update-VnaScreenAutoScale $connection } catch { }  # group delay autoscale: display only, never blocks
                                Assert-VnaMeasurementSafe -Sweep $sweep -Calibration $calibration -DeviceInfo (Get-VnaDeviceInfo $connection) -TraceStatus (Get-VnaTraceStatus $connection) -SkipLoadCheck
                                $summary = Get-VnaSummary -Data $sweep.Data
                                $value = if ($port -eq 1) { $summary.Vswr1 } else { $summary.Vswr2 }
                                $values += $value
                                Write-Host ('Порт {0}: макс. КСВН {1:N4}' -f $port, $value)
                            }
                            $passed = @($values | Where-Object { $_ -gt 1.06 -or [double]::IsNaN($_) }).Count -eq 0
                            if ($passed) { Write-Host 'Контроль нагрузкой пройден.' }
                            else { Write-Host 'Не годен для работы: КСВН нагрузки > 1,06. Проверьте соединения и повторите калибровку.' -ForegroundColor Red }
                        } finally {
                            Add-VnaJournalEvent $calLog ([ordered]@{ Event = 'load_check'; CalibrationId = $calibration.Id; Time = [datetimeoffset]::UtcNow.ToString('o'); User = [Environment]::UserName;
                                Method = 'acm-solt2'; VnaSerial = $calibration.VnaSerial; AcmSerial = $calibration.AcmSerial; VnaTemperatures = $calibration.VnaTemperatures; AcmTemperature = $calibration.AcmTemperature; LoadCheck = $(if ($passed) { 'pass' } else { 'fail' }); Vswr = $values })
                            if ($passed) { $calibration.LoadCheck = 'pass' }
                        }
                    }
                    '4' {
                        if (-not $calibration -or $calibration.LoadCheck -ne 'pass') { throw 'Нужна калибровка и успешный контроль нагрузкой.' }
                        if (-not $session) {
                            $session = Connect-VnaErp -Offline:$Offline -OfflineDirectory $OfflineDirectory -ConfigPath $ConfigPath
                            if (-not $matingSetup) { $config = Read-VnaConfig -Path $ConfigPath; $matingSetup = Get-VnaMatingSetup $config }
                            $matingTarget = Get-VnaMatingTarget -Session $session
                        }
                        $null = Sync-VnaPendingMatings $session $pendingDirectory
                        $number = (Read-Host 'Номер переходника (СИ.035)').Trim().ToUpperInvariant()
                        if ($number -notmatch '^СИ\.\d+$') { throw 'Введите номер вида СИ.035.' }
                        $item = Get-VnaInstrument -Session $session -Number $number
                        Write-Host "$number`: $($item.name); серийный № $($item.serial_number)"
                        Write-Host "Разъёмы: $($item.name) $($item.description)"
                        $chain = ConvertFrom-VnaChain (Read-Host 'Цепочка: напрямую / слева СИ.009 / слева СИ.009, справа СИ.008')
                        if ($number -eq $chain.Left -or $number -eq $chain.Right -or ($chain.Left -and $chain.Left -eq $chain.Right)) { throw 'Изделие и оснастка должны быть разными экземплярами.' }
                        $band = [math]::Min($FmaxHz, (Get-VnaSeriesFmaxHz $item.name))
                        $entered = Read-Host ('Верхняя частота, ГГц [{0}] (Enter — оставить)' -f ($band / 1e9))
                        if ($entered.Trim()) {
                            $parsed = 0.0
                            if (-not [double]::TryParse($entered.Replace(',', '.'), [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$parsed) -or
                                [double]::IsNaN($parsed) -or [double]::IsInfinity($parsed) -or $parsed -le 0.01 -or $parsed -gt $FmaxHz / 1e9) { throw 'Частота должна быть > 0,01 ГГц и не выше -FmaxHz.' }
                            $band = $parsed * 1e9
                        }
                        try { Set-VnaMeasureScreen $connection (Get-VnaScreenLimits $norms $item (Get-VnaItemInterfaces $session $item) $band) $screenBackup }
                        catch {
                            # Display only: the sweep below still checks traces 1–4 (definitions, formats, COR) itself.
                            Write-Host "Экран замера не перестроен: $($_.Exception.Message) Замер продолжается; графики можно поставить пунктом 6." -ForegroundColor Yellow
                        }
                        $left = $null; $right = $null; $descriptions = @(); $fixtures = @($null, $null)
                        if ($chain.Left) { $fixture = Get-VnaFixtureData $session $chain.Left (Join-Path $DataDirectory 'fixtures') $band; $left = $fixture.Data; $fixtures[0] = $fixture; $descriptions += "слева $($fixture.Description)" }
                        if ($chain.Right) { $fixture = Get-VnaFixtureData $session $chain.Right (Join-Path $DataDirectory 'fixtures') $band; $right = $fixture.Data; $fixtures[1] = $fixture; $descriptions += "справа $($fixture.Description)" }
                        $matings = Get-VnaMeasurementMatings $session $matingSetup $item $fixtures $previousMounts
                        $known = Select-VnaKnownMatings $session $matings.Entries
                        if ($known.Exhausted -and -not (Confirm-VnaAction 'Ресурс сочленений исчерпан. Всё равно выполнить замер?')) { break }
                        [void](Read-Host 'Установите переходник в показанную цепочку. Сторона 1 — к порту 1 ВАЦ. Нажмите Enter для нового замера')
                        $sweep = Get-VnaSweep $connection
                        try { Update-VnaScreenAutoScale $connection } catch { }  # group delay autoscale: display only, never blocks
                        Assert-VnaMeasurementSafe -Sweep $sweep -Calibration $calibration -DeviceInfo (Get-VnaDeviceInfo $connection) -TraceStatus (Get-VnaTraceStatus $connection) -LedgerPath $ledger
                        # The measurement is done: the chain was mated, whether or not the file goes to the card.
                        $null = Save-VnaPendingMatings $pendingDirectory $matingTarget ('vna-measurement-' + $sweep.Id) $known.Entries
                        $previousMounts = $matings.Mounts
                        $sent = Sync-VnaPendingMatings $session $pendingDirectory
                        if ($sent.Sent) { Write-Host 'Сочленения записаны в ERP.' }
                        $rawDir = Join-Path $DataDirectory 'raw'; [void][IO.Directory]::CreateDirectory($rawDir)
                        $rawPath = Join-Path $rawDir ($sweep.Id + '.s2p')
                        Write-VnaTouchstone -Path $rawPath -Data $sweep.Data -Comments @('Raw corrected four-trace sweep; ' + $sweep.CapturedAt)
                        $data = Remove-VnaFixtures -Data (Select-VnaBand -Data $sweep.Data -FmaxHz $band) -Left $left -Right $right
                        $summary = Get-VnaSummary $data
                        Write-Host ('КСВН сторона 1: {0:N4}; сторона 2: {1:N4}; потери макс.: {2:N4} дБ; до {3:N3} ГГц' -f $summary.Vswr1, $summary.Vswr2, $summary.LossDb, ($summary.FmaxHz / 1e9))
                        $fileName = 'Замер ВАЦ S50180, АКМ, {0:dd.MM.yyyy}, {1}.s2p' -f (Get-Date), $number
                        $outDir = Join-Path $DataDirectory 'results'; [void][IO.Directory]::CreateDirectory($outDir)
                        $outPath = Join-Path $outDir ($sweep.Id + '.s2p')
                        $comments = @("$number $($item.name); serial=$($item.serial_number)", "VNA serial=$($sweep.DeviceInfo.VnaSerial); ACM serial=$($sweep.DeviceInfo.AcmSerial)",
                            "Temperatures VNA=$($sweep.DeviceInfo.VnaTemperatures -join '/'); ACM=$($sweep.DeviceInfo.AcmTemperature) degC", "Chain: $($chain.Description); $($descriptions -join '; '); stored port 1 faces VNA port 1",
                            "Operator=$([Environment]::UserName); tool=$script:VnaToolVersion; calibration=$($calibration.Time); load_check=$($calibration.LoadCheck)", "Raw=$($sweep.Id); captured=$($sweep.CapturedAt); deembedding=T-matrix; fmax_Hz=$band")
                        if ($Offline) { $comments += 'SYNTHETIC OFFLINE DATA - NOT A MEASUREMENT' }
                        Write-VnaTouchstone -Path $outPath -Data $data -Comments $comments
                        Write-Host "Локальный результат: $outPath"
                        if (-not (Confirm-VnaAction 'Записать в карточку?')) { break }
                        $freshItem = Get-VnaInstrument -Session $session -Number $number
                        Assert-VnaCardIdentity -Measured $item -Current $freshItem
                        $item = $freshItem
                        $allowDuplicate = $false
                        if (@($item.files | Where-Object { $_.file_name -eq $fileName }).Count -gt 0) {
                            $allowDuplicate = Confirm-VnaAction 'Файл с таким именем уже есть. Добавить новую версию с тем же именем, сохранив прежнюю?'
                            if (-not $allowDuplicate) { break }
                        }
                        # Re-read after the potentially long human confirmation; reserve durably before POST.
                        Assert-VnaProfile $connection
                        $null = Assert-VnaCorrection $connection
                        Assert-VnaMeasurementSafe -Sweep $sweep -Calibration $calibration -DeviceInfo (Get-VnaDeviceInfo $connection) -TraceStatus (Get-VnaTraceStatus $connection) -LedgerPath $ledger
                        $hash = Reserve-VnaSweep -Sweep $sweep -Number $number -LedgerPath $ledger -RawPath $rawPath
                        $null = Add-VnaInstrumentFile -Session $session -Instrument $item -FileName $fileName -Path $outPath -AllowDuplicate:$allowDuplicate
                        Add-VnaJournalEvent $ledger ([ordered]@{ Event = 'uploaded'; Time = [datetimeoffset]::UtcNow.ToString('o'); Hash = $hash; SweepId = $sweep.Id; Number = $number; FileName = $fileName })
                        Write-Host 'Файл добавлен в карточку.'
                    }
                    '5' {
                        if (-not $backup) { Write-Host 'Нет сохранённого состояния.'; break }
                        $calibration = $null
                        Restore-VnaOperatorState -Connection $connection -Backup $backup
                        Remove-Item -LiteralPath $backupPointer -ErrorAction Stop
                        Remove-Item -LiteralPath $screenBackup -ErrorAction SilentlyContinue
                        $backup = $null
                        Write-Host 'Настройки оператора восстановлены. Для следующего замера нужна новая калибровка.'
                    }
                    '6' {
                        if (-not $backup) {
                            $serial = Assert-VnaInstrumentIdentity $connection
                            $backupPath = Join-Path $DataDirectory ('operator-' + [guid]::NewGuid().ToString() + '.sta')
                            Backup-VnaState -Connection $connection -Path $backupPath
                            $backup = [pscustomobject]@{ Path = $backupPath; VnaSerial = $serial }
                            $backup | ConvertTo-Json | Set-Content -LiteralPath $backupPointer -Encoding UTF8
                        }
                        $chosen = Select-VnaPreset $norms
                        $changed = Set-VnaPresetStimulus $connection $chosen.Preset $chosen.Stimulus -Precise:$chosen.Precise -Average:$chosen.Average
                        Set-VnaMeasureScreen $connection $chosen.Limits $screenBackup $chosen.Preset.Name
                        Write-Host "Экран «$($chosen.Preset.Name)»: $(Format-VnaPresetParameters $chosen.Preset $chosen.Stimulus)"
                        if ($changed -and $calibration) {
                            $calibration = $null; $connection.Calibrated = $false
                            Write-Host 'Калибровка не действует — откалибруйте заново (пункт 2).' -ForegroundColor Yellow
                        }
                    }
                    '7' {
                        if (-not $session) {
                            $session = Connect-VnaErp -Offline:$Offline -OfflineDirectory $OfflineDirectory -ConfigPath $ConfigPath
                            $matingTarget = Get-VnaMatingTarget -Session $session
                        }
                        $sent = Sync-VnaPendingMatings $session $pendingDirectory
                        Write-Host "Сочленения: отправлено пакетов $($sent.Sent), отклонено $($sent.Rejected), ожидают $($sent.Left)."
                    }
                    '8' {
                        if (-not (Test-Path -LiteralPath $screenBackup)) { Write-Host 'Прежний экран не сохранялся.'; break }
                        Restore-VnaScreen $connection $screenBackup
                        Remove-Item -LiteralPath $screenBackup -ErrorAction Stop
                        Write-Host 'Прежний экран возвращён; калибровка не тронута. Для замера снова выберите 6.'
                    }
                    '9' {
                        Save-VnaTraceMemory $connection
                        Write-Host 'Графики запомнены: изгибайте кабель — память и текущие графики видны вместе.'
                    }
                    '10' {
                        if (-not (Confirm-VnaAction 'Каждый экран будет поставлен со своей полосой; калибровка этого запуска перестанет действовать. Записать файлы?')) { break }
                        $folder = 'C:\VNA\Экраны'
                        $band = [pscustomobject]@{ StartHz = 1e6; StopHz = 6e9 }
                        $files = @(Save-VnaPresetFiles $connection $folder $band 'C:\VNA\S2VNA\State\Autorecall.sta')
                        $calibration = $null
                        Write-Host "Записано файлов: $($files.Count) (существующие не перезаписаны). Калибровка не действует — откалибруйте заново."
                    }
                    '0' { $running = $false }
                    default { Write-Host 'Выберите пункт 0–10.' }
                }
            } catch { Write-Host $_.Exception.Message -ForegroundColor Red }
        }
    } finally {
        try {
            if ($backup -and $connection -and (Confirm-VnaAction 'Вернуть настройки оператора перед выходом?')) {
                Restore-VnaOperatorState -Connection $connection -Backup $backup
                Remove-Item -LiteralPath $backupPointer -ErrorAction Stop
                Remove-Item -LiteralPath $screenBackup -ErrorAction SilentlyContinue
                Write-Host 'Настройки оператора восстановлены.'
            }
        } finally {
            if ($connection) { Release-VnaConnection $connection }
            if ($deviceLocked) { $deviceMutex.ReleaseMutex() }
            if ($deviceMutex) { $deviceMutex.Dispose() }
            if ($lock) { $lock.Dispose() }
        }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    Start-VnaMeasure -Offline:$Offline -OfflineDirectory $OfflineDirectory -DataDirectory $DataDirectory -ConfigPath $ConfigPath -FmaxHz $FmaxHz
}

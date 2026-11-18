# Calibration checks and accuracy control (СТО ФРАК-002-2026, 11.4, 14; form В.1) for PowerShell 5.1.
# Pure comparisons first, then the «Журнал проверок ВАЦ» queue: every record is written to disk, then sent to the
# VNA card in the ERP (a repeated record with the same source is ignored there), like the mating batches.

$script:VnaLoadLimit = 1.06
$script:VnaConfidenceTxLimitDb = 0.1
$script:VnaFlexLimitDb = 0.05
$script:VnaS21LimitDb = 0.1
$script:VnaCheckKinds = [ordered]@{
    session = 'Проверка калибровки: доверительный тест АКМ и LOAD'
    flex = 'Проверка тракта на изгиб'
    control_standard = 'Контрольная мера (еженедельно)'
    kit_standards = 'Меры 6650F27 (ежемесячно)'
}

function Get-VnaDb {
    param([System.Numerics.Complex]$Value)
    if ($Value.Magnitude -le 0) { return [double]::NegativeInfinity }
    return 20 * [math]::Log10($Value.Magnitude)
}

function Assert-VnaSameGrid {
    param($First, $Second, [string]$What)
    if (@($First.Frequencies).Count -ne @($Second.Frequencies).Count -or @($First.Frequencies).Count -lt 2) { throw "$What`: другая сетка частот — нужен тот же профиль (1601 точка)." }
    for ($index = 0; $index -lt $First.Frequencies.Count; $index++) {
        if ([math]::Abs([double]$First.Frequencies[$index] - [double]$Second.Frequencies[$index]) -gt [math]::Max(1.0, [double]$First.Frequencies[$index] * 1e-9)) {
            throw "$What`: частоты не совпадают с профилем замера."
        }
    }
}

function Get-VnaVswrUncertainty {
    param([double]$FrequencyHz, [double]$Vswr)
    # 4.1 (РЭ ч. 1, табл. 23): ±1,5·КСВН % до 10 ГГц, ±2,0·КСВН % выше.
    return $(if ($FrequencyHz -le 10e9) { 0.015 } else { 0.02 }) * $Vswr
}

function Measure-VnaConfidence {
    param([Parameter(Mandatory)]$Data, [Parameter(Mandatory)]$Memory)
    # 11.4: «Данные/Память» in dB. Transmission ±0,1 дБ; reflection from −15 to 0 dB: ±0,4 дБ до 10 ГГц, ±0,5 дБ выше.
    Assert-VnaSameGrid $Data $Memory 'Доверительный тест'
    $tx = 0.0; $rx = $null; $passed = $true
    for ($index = 0; $index -lt $Data.Frequencies.Count; $index++) {
        $frequency = [double]$Data.Frequencies[$index]
        foreach ($name in 'S21', 'S12') {
            $deviation = [math]::Abs((Get-VnaDb $Data.$name[$index]) - (Get-VnaDb $Memory.$name[$index]))
            if ([double]::IsNaN($deviation) -or [double]::IsInfinity($deviation)) { $deviation = [double]::PositiveInfinity }
            $tx = [math]::Max($tx, $deviation)
        }
        foreach ($name in 'S11', 'S22') {
            $reference = Get-VnaDb $Memory.$name[$index]
            if ($reference -lt -15) { continue }
            $deviation = [math]::Abs((Get-VnaDb $Data.$name[$index]) - $reference)
            if ([double]::IsNaN($deviation) -or [double]::IsInfinity($deviation)) { $deviation = [double]::PositiveInfinity }
            $rx = if ($null -eq $rx) { $deviation } else { [math]::Max($rx, $deviation) }
            if ($deviation -gt $(if ($frequency -le 10e9) { 0.4 } else { 0.5 })) { $passed = $false }
        }
    }
    if ($tx -gt $script:VnaConfidenceTxLimitDb) { $passed = $false }
    return [pscustomobject]@{ Passed = $passed; TxDevDb = $tx; RxDevDb = $rx }
}

function Compare-VnaFlex {
    param([Parameter(Mandatory)]$Before, [Parameter(Mandatory)]$After)
    # 14: thru between the cable ends, cables straight, then bent as in work: S21 stays within ±0,05 дБ.
    Assert-VnaSameGrid $Before $After 'Проверка на изгиб'
    $s21 = 0.0; $vswr = 0.0
    for ($index = 0; $index -lt $Before.Frequencies.Count; $index++) {
        foreach ($name in 'S21', 'S12') { $s21 = [math]::Max($s21, [math]::Abs((Get-VnaDb $After.$name[$index]) - (Get-VnaDb $Before.$name[$index]))) }
        foreach ($name in 'S11', 'S22') { $vswr = [math]::Max($vswr, [math]::Abs((Get-VnaVswr $After.$name[$index]) - (Get-VnaVswr $Before.$name[$index]))) }
    }
    return [pscustomobject]@{ S21DevDb = $s21; VswrDev = $vswr; Passed = ($s21 -le $script:VnaFlexLimitDb) }
}

function Compare-VnaControlStandard {
    param([Parameter(Mandatory)]$Measured, [Parameter(Mandatory)]$Reference)
    # 14, weekly: VSWR of both sides and S21/S12 against the stored reference curve; deviation ≤ U (4.1).
    Assert-VnaSameGrid $Measured $Reference 'Контрольная мера'
    $worstRatio = -1.0; $vswrDev = 0.0; $vswrLimit = 0.0; $s21 = 0.0
    for ($index = 0; $index -lt $Measured.Frequencies.Count; $index++) {
        $frequency = [double]$Measured.Frequencies[$index]
        foreach ($name in 'S11', 'S22') {
            $refVswr = Get-VnaVswr $Reference.$name[$index]
            $deviation = [math]::Abs((Get-VnaVswr $Measured.$name[$index]) - $refVswr)
            $limit = Get-VnaVswrUncertainty $frequency $refVswr
            if ([double]::IsInfinity($deviation) -or [double]::IsNaN($deviation)) { $deviation = 1000.0 }
            if ($deviation / $limit -gt $worstRatio) { $worstRatio = $deviation / $limit; $vswrDev = $deviation; $vswrLimit = $limit }
        }
        foreach ($name in 'S21', 'S12') { $s21 = [math]::Max($s21, [math]::Abs((Get-VnaDb $Measured.$name[$index]) - (Get-VnaDb $Reference.$name[$index]))) }
    }
    if ([double]::IsInfinity($s21) -or [double]::IsNaN($s21)) { $s21 = 1000.0 }
    return [pscustomobject]@{ VswrDev = $vswrDev; VswrLimit = $vswrLimit; S21DevDb = $s21; S21LimitDb = $script:VnaS21LimitDb
        Passed = ($vswrDev -le $vswrLimit -and $s21 -le $script:VnaS21LimitDb) }
}

function New-VnaComplex { param([double]$Re, [double]$Im = 0) return [System.Numerics.Complex]::new($Re, $Im) }

function Get-VnaKitStandardResponse {
    param([Parameter(Mandatory)]$Standard, [Parameter(Mandatory)][double]$FrequencyHz, [double]$SystemZ0 = 50)
    # Offset model of a coaxial standard (S2VNA kit definition): C0…C3 ×1e-15/-27/-36/-45 Ф, L0…L3 ×1e-12/-24/-33/-42 Гн,
    # delay in s, offset loss in Ω/s, offset Z0. Returns Γ of a one-port standard or S21 of a thru.
    $omega = 2 * [math]::PI * $FrequencyHz
    $root = [math]::Sqrt($FrequencyHz / 1e9)
    $z0 = [double]$Standard.Z0; $delay = [double]$Standard.Delay; $loss = [double]$Standard.Loss
    $alpha = $loss * $delay / (2 * $z0) * $root
    $gammaL = New-VnaComplex $alpha ($omega * $delay + $alpha)
    if ($Standard.Type -eq 'THRU') { return [System.Numerics.Complex]::Exp([System.Numerics.Complex]::Negate($gammaL)) }
    $zc = New-VnaComplex ($z0 + $loss / (2 * $omega) * $root) (-$loss / (2 * $omega) * $root)
    $f = $FrequencyHz
    switch ($Standard.Type) {
        'OPEN' {
            $c = ([double]$Standard.C0 * 1e-15) + ([double]$Standard.C1 * 1e-27) * $f + ([double]$Standard.C2 * 1e-36) * $f * $f + ([double]$Standard.C3 * 1e-45) * $f * $f * $f
            $gammaT = if ($c -eq 0) { New-VnaComplex 1 } else {
                $zt = [System.Numerics.Complex]::Divide((New-VnaComplex 1), (New-VnaComplex 0 ($omega * $c)))
                [System.Numerics.Complex]::Divide($zt - $zc, $zt + $zc) }
        }
        'SHORT' {
            $l = ([double]$Standard.L0 * 1e-12) + ([double]$Standard.L1 * 1e-24) * $f + ([double]$Standard.L2 * 1e-33) * $f * $f + ([double]$Standard.L3 * 1e-42) * $f * $f * $f
            $zt = New-VnaComplex 0 ($omega * $l)
            $gammaT = [System.Numerics.Complex]::Divide($zt - $zc, $zt + $zc)
        }
        'LOAD' { $zt = New-VnaComplex $SystemZ0; $gammaT = [System.Numerics.Complex]::Divide($zt - $zc, $zt + $zc) }
        default { throw "Неизвестный тип меры $($Standard.Type)." }
    }
    $zr = New-VnaComplex $SystemZ0
    $gamma1 = [System.Numerics.Complex]::Divide($zc - $zr, $zc + $zr)
    $e = [System.Numerics.Complex]::Exp([System.Numerics.Complex]::Multiply((New-VnaComplex -2), $gammaL))
    $one = New-VnaComplex 1
    $numerator = $gamma1 * ($one - $e - $gamma1 * $gammaT) + $e * $gammaT
    $denominator = $one - $gamma1 * ($e * $gamma1 + $gammaT * ($one - $e))
    return [System.Numerics.Complex]::Divide($numerator, $denominator)
}

function Get-VnaKitTolerance {
    param($Config)
    # δ — погрешность описания меры (МП п. 10.6.61). LOAD: описание Г = 0, |Г| ≤ 0,05 (РЭ ч. 1, табл. 13). Для OPEN, SHORT
    # и перемычек погрешность описания в наборе документов не указана: без config.json δ = 0, критерий строже — только U.
    $tolerance = [pscustomobject]@{ reflection_db = 0.0; load_gamma = 0.05; thru_db = 0.0 }
    if ($Config -and $Config.PSObject.Properties.Name -contains 'kit_description_uncertainty') {
        foreach ($name in 'reflection_db', 'load_gamma', 'thru_db') {
            $value = $Config.kit_description_uncertainty.$name
            if ($null -ne $value) {
                if ([double]$value -lt 0) { throw 'kit_description_uncertainty: значения не могут быть отрицательными.' }
                $tolerance.$name = [double]$value
            }
        }
    }
    return $tolerance
}

function Compare-VnaKitStandard {
    param([Parameter(Mandatory)]$Standard, [int]$Port, [Parameter(Mandatory)][object[]]$Sweeps, [Parameter(Mandatory)]$Tolerance)
    # Each of the three connections (rotated by 120°) must hold on its own: the worst connection decides.
    $worst = [pscustomobject]@{ Ratio = 0.0; Deviation = 0.0; Limit = 1.0 }
    foreach ($data in $Sweeps) {
        for ($index = 0; $index -lt $data.Frequencies.Count; $index++) {
            $frequency = [double]$data.Frequencies[$index]
            $model = Get-VnaKitStandardResponse -Standard $Standard -FrequencyHz $frequency
            $pairs = @()
            if ($Standard.Type -eq 'THRU') {
                $limit = [math]::Sqrt([math]::Pow($script:VnaS21LimitDb, 2) + [math]::Pow($Tolerance.thru_db, 2))
                foreach ($name in 'S21', 'S12') { $pairs += , @([math]::Abs((Get-VnaDb $data.$name[$index]) - (Get-VnaDb $model)), $limit) }
            } else {
                $measured = if ($Port -eq 1) { $data.S11[$index] } else { $data.S22[$index] }
                if ($Standard.Type -eq 'LOAD') {
                    $u = (Get-VnaVswrUncertainty $frequency 1.0) / 2
                    $pairs += , @([math]::Abs($measured.Magnitude - $model.Magnitude), [math]::Sqrt($u * $u + [math]::Pow($Tolerance.load_gamma, 2)))
                } else {
                    $u = if ($frequency -le 10e9) { 0.4 } else { 0.5 }
                    $pairs += , @([math]::Abs((Get-VnaDb $measured) - (Get-VnaDb $model)), [math]::Sqrt($u * $u + [math]::Pow($Tolerance.reflection_db, 2)))
                }
            }
            foreach ($pair in $pairs) {
                $deviation = if ([double]::IsNaN($pair[0]) -or [double]::IsInfinity($pair[0])) { 1000.0 } else { $pair[0] }
                if ($deviation / $pair[1] -gt $worst.Ratio) { $worst = [pscustomobject]@{ Ratio = $deviation / $pair[1]; Deviation = $deviation; Limit = $pair[1] } }
            }
        }
    }
    return $worst
}

function ConvertTo-VnaRounded { param($Value) if ($null -eq $Value) { return $null } return [math]::Round([double]$Value, 6) }

function New-VnaCheckRecord {
    param([Parameter(Mandatory)][ValidateSet('session', 'flex', 'control_standard', 'kit_standards')][string]$Kind,
        [Parameter(Mandatory)]$Calibration, [hashtable]$Values = @{}, [string]$Note = '', [string]$Profile = '')
    # Rounded once here; the verdict is computed from the very numbers the ERP receives and rechecks.
    $record = [ordered]@{ source = 'vna-check-' + [guid]::NewGuid().ToString(); vna_serial = [string]$Calibration.VnaSerial; kind = $Kind
        passed = $false; performed_at = [datetimeoffset]::UtcNow.ToString('o'); operator = [Environment]::UserName
        calibration_id = [string]$Calibration.Id; acm_serial = [string]$Calibration.AcmSerial; profile = $Profile
        vna_temperature_c = ConvertTo-VnaRounded $Calibration.VnaTemperature; acm_temperature_c = ConvertTo-VnaRounded $Calibration.AcmTemperature
        note = $Note }
    foreach ($key in $Values.Keys) {
        $value = $Values[$key]
        $record[$key] = if ($value -is [bool] -or $value -is [string] -or $null -eq $value) { $value } else { ConvertTo-VnaRounded $value }
    }
    $record.passed = switch ($Kind) {
        'session' { [bool]$record.confidence_passed -and $null -ne $record.load_p1_vswr -and $null -ne $record.load_p2_vswr -and
            $record.load_p1_vswr -le $script:VnaLoadLimit -and $record.load_p2_vswr -le $script:VnaLoadLimit }
        'flex' { $record.s21_dev_db -le $script:VnaFlexLimitDb }
        'control_standard' { $record.vswr_dev -le $record.vswr_limit -and $record.s21_dev_db -le $record.s21_limit_db }
        'kit_standards' { $record.kit_ratio -le 1 }
    }
    return $record
}

function Save-VnaPendingCheck {
    param([string]$Directory, [string]$Target, $Record)
    if ([string]$Record.source -notmatch '^vna-check-[a-f0-9-]{36}$') { throw 'Недопустимый идентификатор проверки.' }
    [void][IO.Directory]::CreateDirectory($Directory)
    $path = Join-Path $Directory ($Record.source + '.json')
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(([ordered]@{ target = $Target; check = $Record } | ConvertTo-Json -Depth 10))
    $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    return $path
}

function Get-VnaOfflineChecks {
    param($Session)
    $path = Join-Path $Session.OfflineDirectory 'checks.json'
    if (-not (Test-Path -LiteralPath $path)) { return @() }
    return @(Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Send-VnaCheck {
    param($Session, $Check)
    if (-not $Session.Offline) { return Invoke-VnaErpRequest -Session $Session -ApiPath '/api/production/vna-checks' -Method Post -Body $Check }
    $checks = @(Get-VnaOfflineChecks $Session)
    if (@($checks | Where-Object { $_.source -ceq $Check.source }).Count) { return [pscustomobject]@{ replayed = $true } }
    $path = Join-Path $Session.OfflineDirectory 'checks.json'
    [IO.File]::WriteAllText($path, (ConvertTo-Json -InputObject @($checks + [pscustomobject]$Check) -Depth 10), (New-Object Text.UTF8Encoding($false)))
    return [pscustomobject]@{ replayed = $false }
}

function Sync-VnaPendingChecks {
    param($Session, [string]$Directory)
    $result = [pscustomobject]@{ Sent = 0; Rejected = 0; Left = 0 }
    if (-not (Test-Path -LiteralPath $Directory)) { return $result }
    $target = Get-VnaMatingTarget -Session $Session
    foreach ($file in @(Get-ChildItem -LiteralPath $Directory -Filter '*.json' -File | Sort-Object LastWriteTimeUtc, Name)) {
        $pending = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($pending.target -cne $target) { $result.Left++; continue }
        try {
            $null = Send-VnaCheck -Session $Session -Check $pending.check
            Remove-Item -LiteralPath $file.FullName -ErrorAction Stop
            $result.Sent++
        } catch {
            if ((Get-VnaHttpStatus $_) -in @(404, 422)) {
                $rejected = Join-Path $Directory 'rejected'
                [void][IO.Directory]::CreateDirectory($rejected)
                Move-Item -LiteralPath $file.FullName -Destination (Join-Path $rejected $file.Name) -Force -ErrorAction Stop
                Write-Host "ERP не принял проверку $($pending.check.source): $($_.Exception.Message). Запись отложена в $rejected — проверьте зав. № в карточке ВАЦ." -ForegroundColor Red
                $result.Rejected++
            } else {
                $result.Left++
                Write-Host "Проверки не отправлены (нет связи с ERP?): $($_.Exception.Message). Остались в $Directory — повторите пунктом 7." -ForegroundColor Yellow
                break
            }
        }
    }
    return $result
}

function Get-VnaCheckDue {
    param([object[]]$Records, [datetimeoffset]$Now = [datetimeoffset]::UtcNow)
    # The same rule as the ERP card: weekly control +7 days, monthly kit check +1 calendar month after the last pass.
    $items = @()
    foreach ($kind in 'control_standard', 'kit_standards') {
        $own = @($Records | Where-Object { $_.kind -eq $kind } | Sort-Object { [datetimeoffset]::Parse([string]$_.performed_at, [Globalization.CultureInfo]::InvariantCulture) })
        $status = 'never'; $due = $null
        if ($own.Count) {
            $passes = @($own | Where-Object { $_.passed })
            if (-not $own[-1].passed) { $status = 'failed' }
            else {
                $last = [datetimeoffset]::Parse([string]$passes[-1].performed_at, [Globalization.CultureInfo]::InvariantCulture)
                $due = if ($kind -eq 'control_standard') { $last.AddDays(7) } else { $last.AddMonths(1) }
                $status = if ($Now -gt $due) { 'overdue' } else { 'ok' }
            }
        }
        $items += [pscustomobject]@{ kind = $kind; label = $script:VnaCheckKinds[$kind]; status = $status; due_at = $(if ($due) { $due.ToString('o') } else { $null }) }
    }
    return $items
}

function Get-VnaCheckStatus {
    param($Session, [string]$VnaSerial)
    if ($Session.Offline) { return @(Get-VnaCheckDue (Get-VnaOfflineChecks $Session)) }
    $response = Invoke-VnaErpRequest -Session $Session -ApiPath ('/api/production/vna-checks/by-serial/' + [Uri]::EscapeDataString($VnaSerial))
    return @($response.due)
}

function Format-VnaCheckReminders {
    param([object[]]$Due)
    foreach ($item in @($Due)) {
        $label = $script:VnaCheckKinds[[string]$item.kind]
        switch ([string]$item.status) {
            'ok' { "{0}: в срок, до {1:dd.MM.yyyy}" -f $label, ([datetimeoffset]::Parse([string]$item.due_at, [Globalization.CultureInfo]::InvariantCulture).ToLocalTime()) }
            'overdue' { "{0}: контроль просрочен" -f $label }
            'never' { "{0}: контроль просрочен — ещё не выполнялся" -f $label }
            'failed' { "{0}: последняя проверка не пройдена — замеры остановить" -f $label }
        }
    }
}

function Assert-VnaControlNotFailed {
    param([object[]]$Due)
    $failed = @($Due | Where-Object { $_.status -eq 'failed' })
    if ($failed.Count) {
        throw ('Последняя проверка «{0}» не пройдена — замеры остановлены до выяснения причины (СТО ФРАК-002-2026, разд. 14). Проверьте калибровку и кабели и повторите проверку.' -f $script:VnaCheckKinds[[string]$failed[0].kind])
    }
}

# Mating accounting for PowerShell 5.1. A/B always refer to the registry RF sides.

function Get-VnaMatingSetup {
    param($Config)
    $cables = @(@{ inventory_number = 'СИ.100'; end = 'B' }, @{ inventory_number = 'СИ.101'; end = 'B' })
    $acm = 'СИ.104'
    if ($Config -and $Config.PSObject.Properties.Name -contains 'mating_cables') { $cables = @($Config.mating_cables) }
    if ($Config -and $Config.PSObject.Properties.Name -contains 'mating_acm') { $acm = [string]$Config.mating_acm }
    if ($cables.Count -ne 2 -or $acm -notmatch '^СИ\.\d+$') { throw 'Для сочленений укажите два кабеля и номер АКМ вида СИ.104.' }
    foreach ($cable in $cables) {
        if ($cable.inventory_number -notmatch '^СИ\.\d+$' -or $cable.end -cnotin @('A', 'B')) { throw 'Для каждого кабеля нужны inventory_number и конец end: A или B.' }
    }
    if ($cables[0].inventory_number -eq $cables[1].inventory_number -or $acm -in @($cables.inventory_number)) { throw 'Кабели и АКМ должны быть разными экземплярами.' }
    return [pscustomobject]@{ Cables = $cables; Acm = $acm }
}

function Get-VnaMatingSummary {
    param($Session, $Instrument)
    if (-not $Session.Offline) {
        $id = [Uri]::EscapeDataString([string]$Instrument.id)
        return Invoke-VnaErpRequest $Session "/api/production/instruments/$id/matings"
    }
    $resource = $Instrument.mating_resource
    $events = @()
    $path = Join-Path $Session.OfflineDirectory 'matings.json'
    if (Test-Path -LiteralPath $path) {
        $batches = @(Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json)
        $events = @($batches | ForEach-Object { $_.entries } | Where-Object { $_.instrument_id -eq $Instrument.id })
    }
    $ends = @(foreach ($end in @($Instrument.mating_ends)) {
        $total = [int](($events | Where-Object { $_.end -eq $end } | Measure-Object delta -Sum).Sum)
        $percent = $null; $status = 'unknown'
        if ($resource -gt 0) {
            $percent = 100.0 * $total / $resource
            $status = if ($percent -ge 100) { 'exhausted' } elseif ($percent -ge 80) { 'warning' } else { 'ok' }
        }
        [pscustomobject]@{ end = $end; total = $total; percent_used = $percent; status = $status; initial_recorded = $false }
    })
    return [pscustomobject]@{ instrument_id = $Instrument.id; resource = $resource; ends = $ends; events = $events }
}

function Send-VnaMatingBatch {
    param($Session, $Batch)
    if (-not $Session.Offline) { return Invoke-VnaErpRequest $Session '/api/production/matings/batch' -Method Post -Body $Batch }
    $path = Join-Path $Session.OfflineDirectory 'matings.json'
    $batches = @()
    if (Test-Path -LiteralPath $path) { $batches = @(Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json) }
    $existing = @($batches | Where-Object { $_.source -ceq $Batch.source })
    if ($existing.Count) {
        if (($existing[0].entries | ConvertTo-Json -Depth 10 -Compress) -cne ($Batch.entries | ConvertTo-Json -Depth 10 -Compress)) { throw 'Источник сочленений уже использован с другим содержимым.' }
        return [pscustomobject]@{ replayed = $true }
    }
    if (-not $Batch.source -or @($Batch.entries).Count -eq 0) { throw 'Пустой пакет сочленений.' }
    $instruments = @(Get-VnaInstrumentList $Session)
    foreach ($entry in $Batch.entries) {
        $instrument = @($instruments | Where-Object { $_.id -ceq $entry.instrument_id })
        if ($instrument.Count -ne 1 -or $entry.end -cnotin @($instrument[0].mating_ends) -or $entry.delta -ne 1 -or $entry.kind -cnotin @('measurement','calibration')) { throw 'Некорректное сочленение в локальном пакете.' }
    }
    $temporary = $path + '.tmp'
    [IO.File]::WriteAllText($temporary, (ConvertTo-Json -InputObject @($batches + $Batch) -Depth 20), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $path -Force -ErrorAction Stop
    return [pscustomobject]@{ replayed = $false }
}

function New-VnaMatingEntry {
    param($Instrument, [ValidateSet('A','B')][string]$End, [ValidateSet('measurement','calibration')][string]$Kind)
    return [ordered]@{ instrument_id = [string]$Instrument.id; end = $End; delta = 1; kind = $Kind }
}

function Confirm-VnaMatingResources {
    param($Session, [object[]]$Entries)
    $exhausted = $false
    foreach ($group in @($Entries | Group-Object instrument_id)) {
        $instrument = @(Get-VnaInstrumentList $Session | Where-Object { $_.id -ceq $group.Name })
        if ($instrument.Count -ne 1) { throw 'Не найдена карточка элемента тракта.' }
        $summary = Get-VnaMatingSummary $Session $instrument[0]
        foreach ($end in @($group.Group.end | Select-Object -Unique)) {
            $state = @($summary.ends | Where-Object { $_.end -ceq $end })
            if ($state.Count -ne 1) { throw "В карточке $($instrument[0].inventory_number) не задан конец $end. Уточните ВЧ-интерфейсы." }
            if ($state[0].status -in @('warning','exhausted')) {
                $label = if ($state[0].status -eq 'exhausted') { 'Ресурс исчерпан — заменить' } else { 'Ресурс почти исчерпан' }
                Write-Host "$($instrument[0].inventory_number), $end`: $($state[0].total) / $($summary.resource). $label" -ForegroundColor Yellow
                if ($state[0].status -eq 'exhausted') { $exhausted = $true }
            }
        }
    }
    if ($exhausted -and -not (Confirm-VnaAction 'Продолжить с элементом, ресурс которого исчерпан?')) { throw 'Работа отменена оператором.' }
}

function Get-VnaMeasurementMatings {
    param($Session, $Setup, $Dut, [object[]]$Fixtures, $PreviousMounts)
    $entries = @(New-VnaMatingEntry $Dut A measurement; New-VnaMatingEntry $Dut B measurement)
    $mounts = @()
    for ($side = 0; $side -lt 2; $side++) {
        $cable = Get-VnaInstrument $Session $Setup.Cables[$side].inventory_number
        $fixture = $Fixtures[$side]
        $mount = if ($fixture) { "$($cable.id)|$($Setup.Cables[$side].end)|$($fixture.Instrument.id)|$($fixture.Reverse)" } else { '' }
        $retained = $fixture -and $PreviousMounts -and $PreviousMounts[$side] -ceq $mount
        if (-not $retained) { $entries += New-VnaMatingEntry $cable $Setup.Cables[$side].end measurement }
        if ($fixture) {
            # A forward fixture has A facing port 1 regardless of which side of DUT it occupies.
            $inner = if (($side -eq 0) -xor [bool]$fixture.Reverse) { 'B' } else { 'A' }
            $outer = if ($inner -eq 'A') { 'B' } else { 'A' }
            $entries += New-VnaMatingEntry $fixture.Instrument $inner measurement
            if (-not $retained) { $entries += New-VnaMatingEntry $fixture.Instrument $outer measurement }
        }
        $mounts += $mount
    }
    return [pscustomobject]@{ Entries = $entries; Mounts = $mounts }
}

function Get-VnaMatingTarget {
    param($Session)
    if ($Session.Offline) { return 'offline:' + [IO.Path]::GetFullPath($Session.OfflineDirectory) }
    return $Session.ServerUrl
}

function Save-VnaPendingMatings {
    param($Session, [string]$Directory, [string]$Source, [object[]]$Entries, $Upload)
    [void][IO.Directory]::CreateDirectory($Directory)
    $path = Join-Path $Directory ($Source + '.json')
    if ($Source -notmatch '^vna-(measurement|calibration)-[a-f0-9-]+$' -or (Test-Path -LiteralPath $path)) { throw 'Недопустимый или повторный идентификатор замера.' }
    $pending = [ordered]@{ target = Get-VnaMatingTarget $Session; batch = @{ source = $Source; entries = $Entries }; upload = $Upload }
    $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($pending | ConvertTo-Json -Depth 20)); $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    return $path
}

function Sync-VnaPendingMatings {
    param($Session, [string]$Path)
    $pending = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($pending.target -cne (Get-VnaMatingTarget $Session)) { throw 'Ожидающий пакет относится к другому ERP или другой учебной базе.' }
    if ($pending.upload) {
        $item = Get-VnaInstrument $Session $pending.upload.instrument.inventory_number
        Assert-VnaCardIdentity $pending.upload.instrument $item
        # The run UUID in the attachment name allows recovery after a lost POST response.
        if (@($item.files | Where-Object { $_.file_name -ceq $pending.upload.file_name }).Count -eq 0) {
            $null = Add-VnaInstrumentFile $Session $item $pending.upload.file_name $pending.upload.path
        }
    }
    $null = Send-VnaMatingBatch $Session $pending.batch
    Remove-Item -LiteralPath $Path -ErrorAction Stop
}

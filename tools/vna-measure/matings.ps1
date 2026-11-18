# Mating accounting for PowerShell 5.1. A/B are the sides of an instrument in its ERP card (RF interface order).
# Every calibration or measurement run is one batch with its own source; the ERP ignores a repeated batch, so a
# batch is written to disk first and sent afterwards — a lost answer or a closed program never loses or doubles it.

function Get-VnaMatingSetup {
    param($Config)
    # Owner's kit 12.11: C50SMNM.2 cables СИ.100/СИ.101 stay on the VNA ports, АКМ ACM2520-111 is СИ.104.
    $cables = @([pscustomobject]@{ inventory_number = 'СИ.100'; end = 'B' }, [pscustomobject]@{ inventory_number = 'СИ.101'; end = 'B' })
    $acm = 'СИ.104'
    if ($Config -and $Config.PSObject.Properties.Name -contains 'mating_cables') { $cables = @($Config.mating_cables) }
    if ($Config -and $Config.PSObject.Properties.Name -contains 'mating_acm') { $acm = [string]$Config.mating_acm }
    if ($cables.Count -ne 2 -or $acm -notmatch '^СИ\.\d+$') { throw 'Для учёта сочленений укажите в config.json два кабеля (mating_cables) и номер АКМ вида СИ.104 (mating_acm).' }
    foreach ($cable in $cables) {
        if ([string]$cable.inventory_number -notmatch '^СИ\.\d+$' -or [string]$cable.end -cnotin @('A', 'B')) { throw 'Для каждого кабеля нужны inventory_number вида СИ.100 и сторона end: A или B (к изделию).' }
    }
    if ($cables[0].inventory_number -eq $cables[1].inventory_number -or $acm -in @($cables | ForEach-Object { $_.inventory_number })) { throw 'Кабели и АКМ должны быть разными экземплярами.' }
    return [pscustomobject]@{ Cables = $cables; Acm = $acm }
}

function Get-VnaMatingTarget {
    param($Session, [string]$OfflineDirectory, [string]$ServerUrl)
    if ($Session) {
        if ($Session.Offline) { return 'offline:' + [IO.Path]::GetFullPath($Session.OfflineDirectory) }
        return 'erp:' + $Session.ServerUrl
    }
    if ($OfflineDirectory) { return 'offline:' + [IO.Path]::GetFullPath($OfflineDirectory) }
    if ($ServerUrl) { return 'erp:' + (Assert-VnaServerUrl -ServerUrl $ServerUrl) }
    throw 'Не определено, в какой ERP записывать сочленения.'
}

function New-VnaMatingEntry {
    param($Instrument, [string]$Number, [ValidateSet('A', 'B')][string]$End, [ValidateSet('measurement', 'calibration')][string]$Kind)
    if ($Instrument) { return [ordered]@{ instrument_id = [string]$Instrument.id; end = $End; delta = 1; kind = $Kind } }
    return [ordered]@{ inventory_number = $Number; end = $End; delta = 1; kind = $Kind }
}

function New-VnaCalibrationMatings {
    param($Setup)
    # The ACM is mated on both ports and each cable's device end is mated to it. No ERP request is needed here.
    $entries = @((New-VnaMatingEntry -Number $Setup.Acm -End A -Kind calibration), (New-VnaMatingEntry -Number $Setup.Acm -End B -Kind calibration))
    foreach ($cable in $Setup.Cables) { $entries += New-VnaMatingEntry -Number $cable.inventory_number -End $cable.end -Kind calibration }
    return $entries
}

function Get-VnaMeasurementMatings {
    param($Session, $Setup, $Dut, [object[]]$Fixtures, [object[]]$PreviousMounts)
    $entries = @((New-VnaMatingEntry -Instrument $Dut -End A -Kind measurement), (New-VnaMatingEntry -Instrument $Dut -End B -Kind measurement))
    $mounts = @()
    for ($side = 0; $side -lt 2; $side++) {
        $cable = Get-VnaInstrument -Session $Session -Number $Setup.Cables[$side].inventory_number
        $fixture = $Fixtures[$side]
        $mount = if ($fixture) { "$($cable.id)|$($Setup.Cables[$side].end)|$($fixture.Instrument.id)|$($fixture.Reverse)" } else { '' }
        # A fixture left on the cable since the previous measurement is not re-mated to it.
        $retained = $fixture -and $PreviousMounts -and $PreviousMounts[$side] -ceq $mount
        if (-not $retained) { $entries += New-VnaMatingEntry -Instrument $cable -End $Setup.Cables[$side].end -Kind measurement }
        if ($fixture) {
            # A forward fixture has its side A toward VNA port 1, whichever side of the DUT it stands on.
            $inner = if (($side -eq 0) -xor [bool]$fixture.Reverse) { 'B' } else { 'A' }
            $outer = if ($inner -eq 'A') { 'B' } else { 'A' }
            $entries += New-VnaMatingEntry -Instrument $fixture.Instrument -End $inner -Kind measurement
            if (-not $retained) { $entries += New-VnaMatingEntry -Instrument $fixture.Instrument -End $outer -Kind measurement }
        }
        $mounts += $mount
    }
    return [pscustomobject]@{ Entries = $entries; Mounts = $mounts }
}

function Get-VnaOfflineMatings {
    param($Session)
    $path = Join-Path $Session.OfflineDirectory 'matings.json'
    if (-not (Test-Path -LiteralPath $path)) { return @() }
    # PowerShell 5.1 returns a JSON array as one object: enumerate it explicitly.
    return @((Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json) | ForEach-Object { $_ })
}

function Get-VnaMatingSummary {
    param($Session, $Instrument)
    if (-not $Session.Offline) {
        $id = [Uri]::EscapeDataString([string]$Instrument.id)
        return Invoke-VnaErpRequest -Session $Session -ApiPath "/api/production/instruments/$id/matings"
    }
    $resource = $Instrument.mating_resource
    $events = @(Get-VnaOfflineMatings $Session | ForEach-Object { $_.entries } | Where-Object { $_.instrument_id -ceq [string]$Instrument.id })
    $sides = if ($Instrument.PSObject.Properties.Name -contains 'mating_ends') { @($Instrument.mating_ends) } else { @('A', 'B') }
    $ends = @(foreach ($end in $sides) {
        $total = [int](@($events | Where-Object { $_.end -ceq $end }) | Measure-Object delta -Sum).Sum
        $percent = $null; $status = 'unknown'
        if ($resource -gt 0) {
            $percent = [math]::Round(100.0 * $total / $resource, 1)
            $status = if ($total -ge $resource) { 'exhausted' } elseif ($total * 5 -ge $resource * 4) { 'warning' } else { 'ok' }
        }
        [pscustomobject]@{ end = $end; total = $total; percent_used = $percent; status = $status }
    })
    return [pscustomobject]@{ instrument_id = [string]$Instrument.id; resource = $resource; ends = $ends }
}

function Select-VnaKnownMatings {
    param($Session, [object[]]$Entries)
    # Shows the resource of every mated item and leaves out a side the card does not have (no second RF interface).
    $instruments = @(Get-VnaInstrumentList -Session $Session)
    $kept = @(); $exhausted = $false
    foreach ($group in @($Entries | Group-Object { $_.instrument_id })) {
        $instrument = @($instruments | Where-Object { [string]$_.id -ceq $group.Name })
        if ($instrument.Count -ne 1) { throw 'Не найдена карточка элемента тракта.' }
        $summary = Get-VnaMatingSummary $Session $instrument[0]
        foreach ($entry in $group.Group) {
            $state = @($summary.ends | Where-Object { $_.end -ceq $entry.end })
            if ($state.Count -ne 1) {
                Write-Host "$($instrument[0].inventory_number): в карточке нет стороны $($entry.end) — сочленение не учтено. Укажите оба ВЧ-интерфейса." -ForegroundColor Yellow
                continue
            }
            $kept += $entry
            $resource = if ($summary.resource) { " / $($summary.resource)" } else { '' }
            if ($state[0].status -eq 'exhausted') {
                Write-Host "$($instrument[0].inventory_number), сторона $($entry.end): $($state[0].total)$resource. Ресурс исчерпан — заменить." -ForegroundColor Red
                $exhausted = $true
            } elseif ($state[0].status -eq 'warning') {
                Write-Host "$($instrument[0].inventory_number), сторона $($entry.end): $($state[0].total)$resource. Ресурс почти исчерпан." -ForegroundColor Yellow
            }
        }
    }
    return [pscustomobject]@{ Entries = $kept; Exhausted = $exhausted }
}

function Send-VnaMatingBatch {
    param($Session, $Batch)
    if (-not $Session.Offline) { return Invoke-VnaErpRequest -Session $Session -ApiPath '/api/production/matings/batch' -Method Post -Body $Batch }
    if (-not $Batch.source -or @($Batch.entries).Count -eq 0) { throw [InvalidOperationException]::new('Пустой пакет сочленений.') }
    $instruments = @(Get-VnaInstrumentList -Session $Session)
    $resolved = @(foreach ($entry in @($Batch.entries)) {
        $instrument = @(if (-not [string]::IsNullOrEmpty([string]$entry.instrument_id)) { $instruments | Where-Object { [string]$_.id -ceq [string]$entry.instrument_id } }
            else { $instruments | Where-Object { $_.inventory_number -ceq [string]$entry.inventory_number } })
        $sides = if ($instrument.Count -eq 1 -and $instrument[0].PSObject.Properties.Name -contains 'mating_ends') { @($instrument[0].mating_ends) } else { @('A', 'B') }
        if ($instrument.Count -ne 1 -or [string]$entry.end -cnotin $sides -or [int]$entry.delta -lt 1 -or [string]$entry.kind -cnotin @('measurement', 'calibration')) {
            throw [InvalidOperationException]::new('Некорректное сочленение в учебном пакете.')
        }
        [ordered]@{ instrument_id = [string]$instrument[0].id; end = [string]$entry.end; delta = [int]$entry.delta; kind = [string]$entry.kind }
    })
    $canonical = (@($resolved | ForEach-Object { $_ | ConvertTo-Json -Compress } | Sort-Object) -join "`n")
    $batches = @(Get-VnaOfflineMatings $Session)
    $existing = @($batches | Where-Object { $_.source -ceq $Batch.source })
    if ($existing.Count) {
        $previous = (@($existing[0].entries | ForEach-Object { [ordered]@{ instrument_id = $_.instrument_id; end = $_.end; delta = [int]$_.delta; kind = $_.kind } | ConvertTo-Json -Compress } | Sort-Object) -join "`n")
        if ($previous -cne $canonical) { throw [InvalidOperationException]::new('Этот замер уже записан с другим составом сочленений.') }
        return [pscustomobject]@{ replayed = $true }
    }
    $path = Join-Path $Session.OfflineDirectory 'matings.json'
    $temporary = $path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllText($temporary, (ConvertTo-Json -InputObject @($batches + [pscustomobject]@{ source = $Batch.source; entries = $resolved }) -Depth 20), (New-Object Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $temporary -Destination $path -Force -ErrorAction Stop
    } finally { if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -ErrorAction Stop } }
    return [pscustomobject]@{ replayed = $false }
}

function Save-VnaPendingMatings {
    param([string]$Directory, [string]$Target, [string]$Source, [object[]]$Entries)
    if ($Source -notmatch '^vna-(measurement|calibration)-[a-f0-9-]{36}$') { throw 'Недопустимый идентификатор замера.' }
    if (@($Entries).Count -eq 0) { return $null }
    [void][IO.Directory]::CreateDirectory($Directory)
    $path = Join-Path $Directory ($Source + '.json')
    $pending = [ordered]@{ target = $Target; batch = [ordered]@{ source = $Source; entries = @($Entries) } }
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(($pending | ConvertTo-Json -Depth 20))
    # CreateNew: one run writes its batch once; a repeated source is a program error, not a second batch.
    $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    return $path
}

function Get-VnaHttpStatus {
    param($ErrorRecord)
    $response = $ErrorRecord.Exception.Response
    if ($response -and $response.PSObject.Properties.Name -contains 'StatusCode') { return [int]$response.StatusCode }
    return $null
}

function Sync-VnaPendingMatings {
    param($Session, [string]$Directory)
    # Sends every waiting batch of this ERP (or this training folder); a batch the ERP refuses goes to «rejected».
    $result = [pscustomobject]@{ Sent = 0; Rejected = 0; Left = 0 }
    if (-not (Test-Path -LiteralPath $Directory)) { return $result }
    $target = Get-VnaMatingTarget -Session $Session
    foreach ($file in @(Get-ChildItem -LiteralPath $Directory -Filter '*.json' -File | Sort-Object LastWriteTimeUtc, Name)) {
        $pending = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($pending.target -cne $target) { $result.Left++; continue }
        try {
            $null = Send-VnaMatingBatch -Session $Session -Batch $pending.batch
            Remove-Item -LiteralPath $file.FullName -ErrorAction Stop
            $result.Sent++
        } catch {
            $status = Get-VnaHttpStatus $_
            if ($_.Exception -is [InvalidOperationException] -or $status -in @(404, 409, 422)) {
                $rejected = Join-Path $Directory 'rejected'
                [void][IO.Directory]::CreateDirectory($rejected)
                Move-Item -LiteralPath $file.FullName -Destination (Join-Path $rejected $file.Name) -Force -ErrorAction Stop
                Write-Host "ERP не принял сочленения $($pending.batch.source): $($_.Exception.Message). Пакет отложен в $rejected; проверьте карточки." -ForegroundColor Red
                $result.Rejected++
            } else {
                $result.Left++
                Write-Host "Сочленения не отправлены (нет связи с ERP?): $($_.Exception.Message). Остались в $Directory — повторите пунктом 7." -ForegroundColor Yellow
                break
            }
        }
    }
    return $result
}

# Golden vectors for backend/tests/production/test_vna_bench_math.py: the numbers tools/vna-measure (dsp.ps1,
# checks.ps1, norms.ps1, s2vna.ps1) gives for fixed inputs. Windows PowerShell 5.1, no COM, no network:
#   powershell -NoProfile -File backend/tests/production/fixtures/vna_parity/generate.ps1
# Inputs are stored next to the outputs, so the Python port is fed the very same numbers.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tool = [IO.Path]::GetFullPath((Join-Path $here '..\..\..\..\..\tools\vna-measure'))
$repo = [IO.Path]::GetFullPath((Join-Path $tool '..\..'))
. (Join-Path $tool 'dsp.ps1')
. (Join-Path $tool 's2vna.ps1')
. (Join-Path $tool 'erp.ps1')
. (Join-Path $tool 'checks.ps1')
. (Join-Path $tool 'norms.ps1')
$culture = [Globalization.CultureInfo]::InvariantCulture

function ConvertTo-Golden {
    param($Value)
    # Every double travels as a G17 string: no JSON serializer may round it.
    if ($null -eq $Value) { return $null }
    if ($Value -is [System.Numerics.Complex]) { return , @($Value.Real.ToString('G17', $culture), $Value.Imaginary.ToString('G17', $culture)) }
    if ($Value -is [double] -or $Value -is [single] -or $Value -is [decimal]) { return ([double]$Value).ToString('G17', $culture) }
    if ($Value -is [bool] -or $Value -is [string] -or $Value -is [int] -or $Value -is [long]) { return $Value }
    if ($Value -is [System.Collections.IDictionary]) {
        $out = [ordered]@{}
        foreach ($key in $Value.Keys) { $out[[string]$key] = ConvertTo-Golden $Value[$key] }
        return $out
    }
    if ($Value -is [System.Collections.IEnumerable]) {
        $list = New-Object System.Collections.ArrayList
        foreach ($item in $Value) { [void]$list.Add((ConvertTo-Golden $item)) }
        return , $list.ToArray()
    }
    $out = [ordered]@{}
    foreach ($property in $Value.PSObject.Properties) { $out[$property.Name] = ConvertTo-Golden $property.Value }
    return $out
}

function New-Data {
    param([double[]]$Frequencies, [scriptblock]$Value)
    $data = @{ Frequencies = $Frequencies }
    foreach ($name in 'S11', 'S21', 'S12', 'S22') {
        $values = New-Object 'System.Numerics.Complex[]' $Frequencies.Count
        for ($i = 0; $i -lt $Frequencies.Count; $i++) { $values[$i] = & $Value $name $i $Frequencies[$i] }
        $data[$name] = $values
    }
    return (New-VnaData @data)
}

function Get-Grid { param([int]$Points) return [double[]]@(for ($i = 0; $i -lt $Points; $i++) { 10e6 + (18e9 - 10e6) * $i / ($Points - 1) }) }
function Polar { param([double]$M, [double]$P) return [System.Numerics.Complex]::FromPolarCoordinates($M, $P) }
function DbFactor { param([double]$Db) return [math]::Pow(10, $Db / 20) }

$golden = [ordered]@{ generator = 'tools/vna-measure via generate.ps1'; dsp = [ordered]@{}; checks = [ordered]@{}; norms = [ordered]@{} }

# ---- dsp.ps1 ----
$touchstone = @()
foreach ($text in @(
    "# GHZ S MA R 50`n! fixture`n1 0.1 90 0.5 -90 ! first part`n0.5 -90 0.2 180",
    "# MHZ S DB R 50`n1D1 -20 0 -6.020599913279624 0 -6.020599913279624 0 -20 0",
    "! two points`n# KHZ S RI R 50`n10 0.01 -0.02 0.9 0.1 0.9 0.1 0.03 0.04`n20.5 1e-3 2E-3 0.8 -0.3 0.79 -0.31 -0.05 0.06",
    "# HZ S DB R 50`n1e9 -30 45 -0.5 -170 -0.6 -171 -25 -45`n2e9 -28 50 -0.7 160 -0.8 159 -24 -40")) {
    $path = Join-Path $env:TEMP ('vna-golden-' + [guid]::NewGuid().ToString() + '.s2p')
    [IO.File]::WriteAllText($path, $text)
    try { $touchstone += [ordered]@{ text = $text; data = (Read-VnaTouchstone $path) } } finally { Remove-Item -LiteralPath $path }
}
$golden.dsp.touchstone = $touchstone

$grid = Get-Grid 11
$dut = New-Data $grid { param($n, $i, $f) if ($n -in 'S11', 'S22') { Polar (0.03 + 0.002 * $i) (0.7 * $i + ($n -eq 'S22')) } else { Polar (0.9 - 0.01 * $i) (-0.31 * $i - 0.01 * ($n -eq 'S12')) } }
$left = New-Data $grid { param($n, $i, $f) if ($n -in 'S11', 'S22') { Polar (0.06 + 0.001 * $i) (1.1 * $i) } else { Polar (0.95 - 0.004 * $i) (-0.2 * $i - 0.03 * ($n -eq 'S12')) } }
$right = New-Data $grid { param($n, $i, $f) if ($n -in 'S11', 'S22') { Polar (0.08 - 0.002 * $i) (-0.4 * $i + 0.5) } else { Polar (0.92 - 0.006 * $i) (-0.25 * $i) } }
$measured = New-Data $grid { param($n, $i, $f)
    $lt = ConvertTo-VnaTMatrix $left.S11[$i] $left.S21[$i] $left.S12[$i] $left.S22[$i]
    $dt = ConvertTo-VnaTMatrix $dut.S11[$i] $dut.S21[$i] $dut.S12[$i] $dut.S22[$i]
    $rt = ConvertTo-VnaTMatrix $right.S11[$i] $right.S21[$i] $right.S12[$i] $right.S22[$i]
    $s = ConvertFrom-VnaTMatrix (Multiply-VnaTMatrix (Multiply-VnaTMatrix $lt $dt) $rt)
    $s[@{ S11 = 0; S21 = 1; S12 = 2; S22 = 3 }[$n]] }
$golden.dsp.deembed = [ordered]@{ measured = $measured; left = $left; right = $right
    both = (Remove-VnaFixtures -Data $measured -Left $left -Right $right)
    left_only = (Remove-VnaFixtures -Data $measured -Left $left)
    right_only = (Remove-VnaFixtures -Data $measured -Right $right) }
$golden.dsp.band = [ordered]@{ data = $dut; fmax_hz = 7.3e9; result = (Select-VnaBand -Data $dut -FmaxHz 7.3e9) }
$summaries = @()
foreach ($data in @($dut, $measured, (New-Data $grid { param($n, $i, $f) if ($n -eq 'S12' -and $i -eq 4) { Polar 0.25 0 } elseif ($n -in 'S11', 'S22') { Polar 0.1 0 } else { Polar 0.5 0 } }))) {
    $summaries += [ordered]@{ data = $data; summary = (Get-VnaSummary $data) }
}
$golden.dsp.summary = $summaries

# ---- checks.ps1 (tool U: 1,5·КСВН % to 10 GHz, 2,0·КСВН % above) ----
$grid = Get-Grid 21
$confidence = @()
foreach ($case in @(
    @{ m = { param($n, $i, $f) Polar 0.5 0 }; d = { param($n, $i, $f) if ($n -in 'S21', 'S12') { Polar (0.5 * (DbFactor 0.09)) 0 } else { Polar 0.5 0 } } },
    @{ m = { param($n, $i, $f) Polar 0.5 0 }; d = { param($n, $i, $f) if ($n -in 'S21', 'S12') { Polar (0.5 * (DbFactor 0.11)) 0 } else { Polar 0.5 0 } } },
    @{ m = { param($n, $i, $f) if ($n -in 'S11', 'S22') { Polar 0.01 0 } else { Polar 0.5 0 } }; d = { param($n, $i, $f) if ($n -in 'S11', 'S22') { Polar 0.03 0 } else { Polar 0.5 0 } } },
    @{ m = { param($n, $i, $f) Polar 0.5 0 }; d = { param($n, $i, $f) if ($n -in 'S11', 'S22') { Polar (0.5 * (DbFactor 0.45)) 0 } else { Polar 0.5 0 } } },
    # 0,45 dB of reflection only above 10 GHz: inside 0,5 dB there.
    @{ m = { param($n, $i, $f) Polar 0.4 (0.3 * $i) }; d = { param($n, $i, $f) if ($n -in 'S11', 'S22' -and $f -gt 10e9) { Polar (0.4 * (DbFactor 0.45)) (0.3 * $i) } else { Polar 0.4 (0.3 * $i + 0.01) } } },
    # Mixed: reflections crossing −15 dB, transmission drifting with frequency.
    @{ m = { param($n, $i, $f) if ($n -in 'S11', 'S22') { Polar (0.1 + 0.03 * $i) (0.2 * $i) } else { Polar (0.95 - 0.02 * $i) (-0.4 * $i) } }
       d = { param($n, $i, $f) if ($n -in 'S11', 'S22') { Polar ((0.1 + 0.03 * $i) * (DbFactor (0.02 * $i - 0.1))) (0.2 * $i + 0.05) } else { Polar ((0.95 - 0.02 * $i) * (DbFactor (0.004 * $i - 0.03))) (-0.4 * $i) } } })) {
    $memory = New-Data $grid $case.m; $data = New-Data $grid $case.d
    $confidence += [ordered]@{ data = $data; memory = $memory; result = (Measure-VnaConfidence -Data $data -Memory $memory) }
}
$golden.checks.confidence = $confidence

$flex = @()
$before = New-Data $grid { param($n, $i, $f) if ($n -in 'S11', 'S22') { Polar (0.01 + 0.001 * $i) (0.5 * $i) } else { Polar (0.98 - 0.003 * $i) (-0.6 * $i) } }
foreach ($shift in 0.04, 0.06, 0.0) {
    $after = New-Data $grid { param($n, $i, $f) if ($n -in 'S11', 'S22') { Polar (0.012 + 0.001 * $i) (0.5 * $i + 0.1) } else { Polar ((0.98 - 0.003 * $i) * (DbFactor ($shift * [math]::Sin($i)))) (-0.6 * $i) } }
    $flex += [ordered]@{ before = $before; after = $after; result = (Compare-VnaFlex $before $after) }
}
$golden.checks.flex = $flex

$control = @()
$reference = New-Data $grid { param($n, $i, $f) if ($n -in 'S11', 'S22') { Polar (0.02 + 0.0005 * $i) (0.9 * $i) } else { Polar (0.985 - 0.002 * $i) (-0.7 * $i) } }
foreach ($case in @(@{ r = 0.002; t = 0.02 }, @{ r = 0.012; t = 0.02 }, @{ r = 0.002; t = 0.12 }, @{ r = 0.0; t = 0.0 })) {
    $measuredControl = New-Data $grid { param($n, $i, $f) if ($n -in 'S11', 'S22') { Polar (0.02 + 0.0005 * $i + $case.r * [math]::Cos($i)) (0.9 * $i) } else { Polar ((0.985 - 0.002 * $i) * (DbFactor ($case.t * [math]::Sin(0.7 * $i)))) (-0.7 * $i) } }
    $control += [ordered]@{ measured = $measuredControl; reference = $reference; result = (Compare-VnaControlStandard $measuredControl $reference) }
}
$golden.checks.control_standard = $control

# Kit standards: S2VNA definition values (C ×1e-15/-27/-36/-45 F, L ×1e-12/-24/-33/-42 H, delay s, loss Ω/s).
$kit = @(
    [pscustomobject]@{ Type = 'OPEN'; C0 = 49.43; C1 = -310.13; C2 = 23.17; C3 = -0.16; L0 = 0; L1 = 0; L2 = 0; L3 = 0; Delay = 2.9243e-11; Loss = 2.2e9; Z0 = 50 },
    [pscustomobject]@{ Type = 'SHORT'; C0 = 0; C1 = 0; C2 = 0; C3 = 0; L0 = 2.0765; L1 = -108.54; L2 = 2.1705; L3 = -0.01; Delay = 3.1785e-11; Loss = 2.36e9; Z0 = 50 },
    [pscustomobject]@{ Type = 'LOAD'; C0 = 0; C1 = 0; C2 = 0; C3 = 0; L0 = 0; L1 = 0; L2 = 0; L3 = 0; Delay = 0; Loss = 0; Z0 = 50 },
    [pscustomobject]@{ Type = 'THRU'; C0 = 0; C1 = 0; C2 = 0; C3 = 0; L0 = 0; L1 = 0; L2 = 0; L3 = 0; Delay = 1.2e-10; Loss = 1.5e9; Z0 = 50.2 },
    [pscustomobject]@{ Type = 'OPEN'; C0 = 0; C1 = 0; C2 = 0; C3 = 0; L0 = 0; L1 = 0; L2 = 0; L3 = 0; Delay = 5e-11; Loss = 0; Z0 = 50 })
$responses = @()
foreach ($standard in $kit) {
    $responses += [ordered]@{ standard = $standard; frequencies = $grid; values = @(foreach ($f in $grid) { Get-VnaKitStandardResponse -Standard $standard -FrequencyHz $f }) }
}
$golden.checks.kit_response = $responses

$kitCompare = @()
$tolerances = @((Get-VnaKitTolerance $null), [pscustomobject]@{ reflection_db = 0.1; load_gamma = 0.03; thru_db = 0.05 })
$kitGrid = Get-Grid 11
foreach ($standard in $kit[0..3]) {
    foreach ($port in $(if ($standard.Type -eq 'THRU') { 0 } else { 1, 2 })) {
      foreach ($amplitude in 0.002, 0.004) {
        $sweeps = @()
        foreach ($connection in 0, 1, 2) {
            $sweeps += , (New-Data $kitGrid { param($n, $i, $f)
                $model = Get-VnaKitStandardResponse -Standard $standard -FrequencyHz $f
                $wobble = $amplitude * ($connection + 1) * [math]::Sin(0.9 * $i + $port)
                if ($standard.Type -eq 'THRU') { if ($n -in 'S21', 'S12') { $model * (DbFactor (10 * $wobble)) } else { Polar 0.02 0 } }
                elseif ($standard.Type -eq 'LOAD') { if ($n -in 'S11', 'S22') { $model + (Polar (0.01 + 5 * [math]::Abs($wobble)) (0.3 * $i)) } else { Polar 0.001 0 } }
                else { if ($n -in 'S11', 'S22') { $model * (DbFactor (60 * $wobble)) } else { Polar 0.001 0 } } })
        }
        foreach ($tolerance in $tolerances) {
            $kitCompare += [ordered]@{ standard = $standard; port = $port; sweeps = $sweeps; tolerance = $tolerance
                result = (Compare-VnaKitStandard -Standard $standard -Port $port -Sweeps $sweeps -Tolerance $tolerance) }
        }
      }
    }
}
$golden.checks.kit_compare = $kitCompare

$rounded = @()
foreach ($value in 1.0600001, 0.1234564999, -0.0000004, 1000.5, [double]::PositiveInfinity, [double]::NaN, 12.3456785) {
    $rounded += [ordered]@{ value = $value; rounded = (ConvertTo-VnaRounded $value) }
}
$golden.checks.rounded = $rounded

# ---- norms.ps1 + s2vna.ps1: limit lines and the norm-following SWR scale, for every norm of the repository file ----
$norms = Read-VnaNorms (Join-Path $repo 'backend\data\rf-norms.json')
$ids = @()
foreach ($maker in @($norms.makers)) { foreach ($group in @($maker.groups)) { $ids += [string]$group.id } }
foreach ($entry in @($norms.type_norms)) { $ids += [string]$entry.id }
foreach ($sheet in @($norms.mil_adapters)) { $ids += [string]$sheet.id }
$swrSpec = (Get-VnaScreenPreset 'ОТК — переход').Traces[0]
$limits = @()
foreach ($id in $ids + @('', 'no-such-norm')) {
    foreach ($band in 18e9, 11e9, 6e9, 1e9) {
        $item = [pscustomobject]@{ inventory_number = 'СИ.100'; name = 'Переходник'; doc_designation = '' }
        $result = Get-VnaScreenLimits $norms $item ([pscustomobject]@{ interfaces = @(); norm_id = $id }) $band
        $limits += [ordered]@{ norm_id = $id; number = 'СИ.100'; name = 'Переходник'; band_hz = $band; start_hz = 10e6
            swr = @($result.Swr); loss = @($result.Loss); title = $result.Title; swr_scale = (Resolve-VnaTraceScale $swrSpec @($result.Swr)) }
    }
}
$foreign = Get-VnaScreenLimits $norms ([pscustomobject]@{ inventory_number = 'СИ.050'; name = 'Переходник F вилка – BNC розетка 75 Ом'; doc_designation = '' }) ([pscustomobject]@{ interfaces = @(); norm_id = 'iec-61169-8-bnc' }) 4e9
$limits += [ordered]@{ norm_id = 'iec-61169-8-bnc'; number = 'СИ.050'; name = 'Переходник F вилка – BNC розетка 75 Ом'; band_hz = 4e9; start_hz = 10e6
    swr = @($foreign.Swr); loss = @($foreign.Loss); title = $foreign.Title; swr_scale = (Resolve-VnaTraceScale $swrSpec @($foreign.Swr)) }
$golden.norms.limits = $limits

$presets = @()
foreach ($preset in Get-VnaScreenPresets) {
    $presets += [ordered]@{ name = $preset.Name; split = $preset.Split; stimulus = $preset.Stimulus
        traces = @(foreach ($trace in $preset.Traces) { [ordered]@{ param = $trace.Parameter; format = $trace.Format; limit = $trace.Limit
            hidden = $trace.Hidden; memory = $trace.Memory; auto = $trace.AutoScale; scale = (Resolve-VnaTraceScale $trace @()) } }) }
}
$golden.norms.presets = $presets
$golden.norms.markers_hz = $script:VnaScreenMarkersHz

# The norms the lines above were drawn from: the test reads this copy, so a later rf-norms.json edit does not stale it.
Copy-Item -LiteralPath (Join-Path $repo 'backend\data\rf-norms.json') -Destination (Join-Path $here 'rf-norms-snapshot.json') -Force
$json = (ConvertTo-Golden $golden) | ConvertTo-Json -Depth 30 -Compress
[IO.File]::WriteAllText((Join-Path $here 'vna-measure-golden.json'), $json, (New-Object Text.UTF8Encoding($false)))

# Passport norms for the measurement screen. Reads backend/data/rf-norms.json — the same file and the same choice as
# backend/app/modules/production/instrument_norms.py: the maker spec of the series, else the MIL-PRF-55339 sheet of the
# connector pair, else no type norm; the shop requirement (1.30) is always shown and never presented as a type norm.
# Dot-source; no network or files touched on import.

function Find-VnaNormsFile {
    param([Parameter(Mandatory = $true)][string]$ToolDirectory)
    # A copy next to the tool wins (VNA PC without a repository checkout), else the repository file.
    foreach ($candidate in (Join-Path $ToolDirectory 'rf-norms.json'), (Join-Path $ToolDirectory '..\..\backend\data\rf-norms.json')) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return [IO.Path]::GetFullPath($candidate) }
    }
    return $null
}

function Read-VnaNorms {
    param([string]$Path)
    if (-not $Path) { return $null }
    $norms = [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8) | ConvertFrom-Json
    $shop = $norms.shop_requirement
    if (-not $shop -or -not ($shop.vswr_max -is [double] -or $shop.vswr_max -is [int] -or $shop.vswr_max -is [decimal]) -or
        [double]$shop.vswr_max -le 1 -or -not $norms.makers -or -not $norms.mil_adapters) { throw "Файл норм $Path повреждён." }
    return $norms
}

function ConvertFrom-VnaConnectorName {
    param([string]$Name)
    # Port of rf_interfaces.parse_interfaces: explicit «series + gender» pairs only; returns series and impedance.
    $series = '(?<series>RP[- ]?SMA|SMA|3[.,]5\s*(?:mm|мм)|TNC|BNC|N|F|тип\s+III)'
    $connector = '(?<![\w])' + $series + '[\s-]*(?<gender>female|male|вилка|розетка)(?![\w])'
    $options = [Text.RegularExpressions.RegexOptions]::IgnoreCase
    $expanded = [regex]::Replace([string]$Name, $connector + '\s*[–—-]\s*(?<second>female|male|вилка|розетка)(?![\w])',
        { param($m) $m.Value.Substring(0, $m.Groups['second'].Index - $m.Index) + $m.Groups['series'].Value + ' ' + $m.Groups['second'].Value }, $options)
    $found = @([regex]::Matches($expanded, $connector, $options))
    $tokens = [regex]::Matches($expanded, '(?<![\w])(?:RP[- ]?SMA|SMA|3[.,]5\s*(?:mm|мм)|TNC|BNC|N|F|тип\s+III)(?![\w])', $options).Count
    $genders = [regex]::Matches($expanded, '(?<![\w])(?:female|male|вилка|розетка)(?![\w])', $options).Count
    if ($found.Count -lt 1 -or $found.Count -gt 2 -or $tokens -ne $found.Count -or $genders -ne $found.Count) { return @() }
    $ohms = @([regex]::Matches([string]$Name, '(?<![\d.])(\d+(?:[.,]\d+)?)\s*(?:Ω|Ом|ohm)\b', $options) |
        ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
    $impedance = if ($ohms.Count -eq 1) { [double]::Parse($ohms[0].Replace(',', '.'), [Globalization.CultureInfo]::InvariantCulture) } else { $null }
    return @(foreach ($match in $found) {
        $text = $match.Groups['series'].Value.ToUpperInvariant().Replace(' ', '')
        if ($text.StartsWith('3')) { $text = '3.5mm' } elseif ($text.StartsWith('ТИП')) { $text = 'тип III' } elseif ($text -eq 'RPSMA') { $text = 'RP-SMA' }
        [pscustomobject]@{ series = $text; impedance_ohm = $impedance }
    })
}

function Get-VnaItemPorts {
    param([string]$Name, [object[]]$Interfaces)
    # The card's RF interfaces win; without them the name is parsed. Two 50-ohm ends or nothing.
    $ports = @($Interfaces | Where-Object { $_ })
    if ($ports.Count -eq 0) { $ports = @(ConvertFrom-VnaConnectorName $Name) }
    $foreign = @($ports | Where-Object { $null -ne $_.impedance_ohm -and [double]$_.impedance_ohm -ne 50 } | ForEach-Object { [double]$_.impedance_ohm })
    if ($foreign.Count) { return [pscustomobject]@{ Series = $null; ForeignOhm = $foreign[0] } }
    if ($ports.Count -ne 2) { return [pscustomobject]@{ Series = $null; ForeignOhm = $null } }
    $series = [string[]]@($ports | ForEach-Object { [string]$_.series }); [Array]::Sort($series, [StringComparer]::Ordinal)
    return [pscustomobject]@{ Series = $series; ForeignOhm = $null }
}

function Test-VnaSamePorts {
    param([object[]]$Ports, [string[]]$Series)
    $sorted = [string[]]@($Ports | ForEach-Object { [string]$_ }); [Array]::Sort($sorted, [StringComparer]::Ordinal)
    return ($sorted -join '|') -ceq ($Series -join '|')
}

function Get-VnaItemNorm {
    param($Norms, [string]$Name, [string]$Model, [string[]]$Series)
    if (-not $Norms -or -not $Series) { return $null }
    $text = ("$Name $Model").ToUpperInvariant()
    $maker = @($Norms.makers | Where-Object { $text -cmatch ('(?<![A-Z0-9])' + [regex]::Escape(([string]$_.match).ToUpperInvariant())) }) | Select-Object -First 1
    if ($maker) {
        $group = @($maker.groups | Where-Object { Test-VnaSamePorts $_.ports $Series }) | Select-Object -First 1
        if ($group) { return [pscustomobject]@{ Reference = [string]$maker.title; Title = [string]$group.title; FromGhz = [double]$group.from_ghz; Segments = @($group.segments)
            Loss = $(if ($group.PSObject.Properties['loss']) { $group.loss } else { $null }) } }
    }
    $sheet = @($Norms.mil_adapters | Where-Object { Test-VnaSamePorts $_.ports $Series }) | Select-Object -First 1
    if ($sheet) { return [pscustomobject]@{ Reference = [string]$sheet.sheet; Title = [string]$sheet.title; FromGhz = [double]$sheet.from_ghz; Segments = @($sheet.segments)
        Loss = $(if ($sheet.PSObject.Properties['loss']) { $sheet.loss } else { $null }) } }
    return $null
}

function Format-VnaNumber {
    param([double]$Value, [int]$Digits = 3)
    $text = $Value.ToString('F' + $Digits, [Globalization.CultureInfo]::InvariantCulture).TrimEnd('0').TrimEnd('.')
    return $text.Replace('.', ',')
}

function Get-VnaLossLines {
    param($Loss, [string]$Reference, [double]$StartHz, [double]$BandHz)
    # Lower lines for S21/S12 in dB: −(a + k·√F). A chord of the convex −k·√F lies above the curve, so the straight
    # S2VNA pieces are slightly stricter than the norm (≤ 0,002 dB with 12 pieces per band).
    $lines = @(); $texts = @()
    if (-not $Loss) { return [pscustomobject]@{ Lines = @(); Text = 'потери: нормы нет' } }
    $low = if ($null -ne $Loss.from_ghz) { [double]$Loss.from_ghz } else { 0.0 }
    foreach ($segment in @($Loss.segments | Where-Object { $_ })) {
        $a = if ($null -ne $segment.a) { [double]$segment.a } else { 0.0 }; $k = if ($null -ne $segment.k) { [double]$segment.k } else { 0.0 }
        $top = [double]$segment.to_ghz
        $from = [math]::Max($low * 1e9, $StartHz); $to = [math]::Min($top * 1e9, $BandHz)
        if ($to -gt $from) {
            $pieces = if ($k) { 12 } else { 1 }
            for ($piece = 0; $piece -lt $pieces; $piece++) {
                $start = $from + ($to - $from) * $piece / $pieces; $stop = $from + ($to - $from) * ($piece + 1) / $pieces
                $lines += [pscustomobject]@{ Type = 2; StartHz = $start; StopHz = $stop
                    From = -($a + $k * [math]::Sqrt($start / 1e9)); To = -($a + $k * [math]::Sqrt($stop / 1e9)) }
            }
            $formula = @(); if ($a) { $formula += Format-VnaNumber $a }; if ($k) { $formula += "$(Format-VnaNumber $k)·√F" }
            $texts += "≤ $($formula -join ' + ') дБ ($(Format-VnaNumber ($from / 1e9) 2)–$(Format-VnaNumber ($to / 1e9) 2) ГГц)"
        }
        $low = $top
    }
    foreach ($point in @($Loss.points | Where-Object { $_ })) {
        $at = [double]$point.ghz * 1e9; $value = [double]$point.max_db
        if ($at -lt $StartHz -or $at -gt $BandHz) { continue }
        $lines += [pscustomobject]@{ Type = 2; StartHz = [math]::Max($StartHz, $at - 5e7); StopHz = [math]::Min($BandHz, $at + 5e7); From = -$value; To = -$value }
        $texts += "≤ $(Format-VnaNumber $value) дБ на $(Format-VnaNumber ($at / 1e9) 2) ГГц"
    }
    if (-not $texts.Count) { return [pscustomobject]@{ Lines = @(); Text = "потери: норма $Reference не покрывает полосу замера" } }
    return [pscustomobject]@{ Lines = $lines; Text = "потери $($texts -join '; ') — $Reference" }
}

function Get-VnaScreenLimits {
    param($Norms, $Item, [object[]]$Interfaces, [double]$BandHz = 18e9, [double]$StartHz = 10e6)
    # Upper SWR lines for the SWR graphs and the window title. A segment a + b·F is linear in F, so a straight
    # S2VNA limit segment between its ends is exact.
    $number = if ($Item) { "$($Item.inventory_number) · " } else { '' }
    if (-not $Norms) { return [pscustomobject]@{ Swr = @(); Loss = @(); Title = "${number}нормы не загружены (нет rf-norms.json) — линий допуска нет" } }
    $shop = [double]$Norms.shop_requirement.vswr_max; $shopLabel = [string]$Norms.shop_requirement.label
    $parts = @(); $segments = @(); $loss = [pscustomobject]@{ Lines = @(); Text = 'потери: нормы нет' }
    if ($Item) {
        $ports = Get-VnaItemPorts ([string]$Item.name) $Interfaces
        if ($null -ne $ports.ForeignOhm) {
            return [pscustomobject]@{ Swr = @(); Loss = @(); Title = "${number}$(Format-VnaNumber $ports.ForeignOhm) Ом — не для 50 Ом тракта; линий допуска нет" }
        }
        $norm = Get-VnaItemNorm $Norms ([string]$Item.name) ([string]$Item.doc_designation) $ports.Series
        if ($norm) {
            $low = $norm.FromGhz; $texts = @()
            foreach ($segment in $norm.Segments) {
                $a = [double]$segment.a; $b = if ($null -ne $segment.b) { [double]$segment.b } else { 0.0 }; $top = [double]$segment.to_ghz
                $from = [math]::Max($low * 1e9, $StartHz); $to = [math]::Min($top * 1e9, $BandHz)
                if ($to -gt $from) {
                    $segments += [pscustomobject]@{ StartHz = $from; StopHz = $to; From = $a + $b * $from / 1e9; To = $a + $b * $to / 1e9 }
                    $formula = if ($b) { "$(Format-VnaNumber $a) + $(Format-VnaNumber $b)·F" } else { Format-VnaNumber $a }
                    $texts += "≤ $formula ($(Format-VnaNumber ($from / 1e9) 2)–$(Format-VnaNumber ($to / 1e9) 2) ГГц)"
                }
                $low = $top
            }
            $loss = Get-VnaLossLines $norm.Loss $norm.Reference $StartHz $BandHz
            if ($texts.Count) { $parts += "КСВН $($texts -join '; ') — $($norm.Reference)" }
            else { $parts += "норма $($norm.Reference) не покрывает полосу замера" }
        } else { $parts += 'норма на тип не найдена' }
    }
    $segments += [pscustomobject]@{ StartHz = $StartHz; StopHz = $BandHz; From = $shop; To = $shop }
    $parts += "КСВН ≤ $($shop.ToString('0.00', [Globalization.CultureInfo]::InvariantCulture).Replace('.', ',')) — $shopLabel до $(Format-VnaNumber ($BandHz / 1e9) 2) ГГц"
    $parts += $loss.Text
    return [pscustomobject]@{ Swr = $segments; Loss = @($loss.Lines); Title = $number + ($parts -join ' · ') }
}

function Get-VnaItemInterfaces {
    param($Session, $Instrument)
    # The card's RF interfaces (/rf-interfaces); none offline or on a read error — then the name decides.
    if (-not $Session -or $Session.Offline -or -not $Instrument -or -not $Instrument.id) { return @() }
    try {
        $response = Invoke-VnaErpRequest -Session $Session -ApiPath "/api/production/instruments/$($Instrument.id)/rf-interfaces"
        return @($response.rf.interfaces)
    } catch {
        Write-Host 'ВЧ-интерфейсы карточки не прочитаны; норма выбрана по наименованию.' -ForegroundColor Yellow
        return @()
    }
}

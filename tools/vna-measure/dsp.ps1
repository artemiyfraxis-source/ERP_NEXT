# Numerical helpers for two-port, 50 ohm Touchstone data. Trace order is S11,S21,S12,S22.
Add-Type -AssemblyName System.Numerics

function Test-VnaFinite {
    param([double]$Value)
    return (-not [double]::IsNaN($Value) -and -not [double]::IsInfinity($Value))
}

function Assert-VnaData {
    param([Parameter(Mandatory)]$Data)
    if ($null -eq $Data.Frequencies -or @($Data.Frequencies).Count -eq 0) { throw 'Нет частотных точек.' }
    $count = @($Data.Frequencies).Count
    foreach ($name in 'S11','S21','S12','S22') {
        if ($null -eq $Data.$name -or @($Data.$name).Count -ne $count) { throw "Нет полных данных $name." }
    }
    $previous = -1.0
    for ($index = 0; $index -lt $count; $index++) {
        $frequency = [double]$Data.Frequencies[$index]
        if (-not (Test-VnaFinite $frequency) -or $frequency -le 0 -or $frequency -le $previous) {
            throw 'Частоты должны быть конечными, положительными и строго возрастающими.'
        }
        $previous = $frequency
        foreach ($name in 'S11','S21','S12','S22') {
            $value = [System.Numerics.Complex]$Data.$name[$index]
            if (-not (Test-VnaFinite $value.Real) -or -not (Test-VnaFinite $value.Imaginary)) {
                throw "Неконечное значение $name на частоте $frequency."
            }
        }
    }
}

function New-VnaData {
    param([double[]]$Frequencies, [System.Numerics.Complex[]]$S11, [System.Numerics.Complex[]]$S21,
        [System.Numerics.Complex[]]$S12, [System.Numerics.Complex[]]$S22)
    $data = [pscustomobject]@{ Frequencies=$Frequencies; S11=$S11; S21=$S21; S12=$S12; S22=$S22 }
    Assert-VnaData $data
    return $data
}

function Read-VnaTouchstone {
    param([Parameter(Mandatory)][string]$Path)
    $culture = [Globalization.CultureInfo]::InvariantCulture
    $scale = 0.0
    $format = ''
    $tokens = New-Object 'System.Collections.Generic.List[string]'
    foreach ($raw in [IO.File]::ReadAllLines($Path)) {
        $line = ($raw -split '!',2)[0].Trim()
        if (-not $line) { continue }
        if ($line.StartsWith('#')) {
            if ($format -or $tokens.Count) { throw 'Повторная строка формата Touchstone.' }
            $options = $line.Substring(1).Trim().ToUpperInvariant() -split '\s+'
            if ($options.Count -ne 5 -or $options[1] -ne 'S' -or $options[2] -notin 'RI','MA','DB' -or $options[3] -ne 'R') {
                throw 'Нужен Touchstone 1.x: # HZ S RI R 50 (также MA/DB и KHZ/MHZ/GHZ).'
            }
            $scales = @{ HZ=1.0; KHZ=1e3; MHZ=1e6; GHZ=1e9 }
            $reference = 0.0
            if (-not $scales.ContainsKey($options[0]) -or
                -not [double]::TryParse($options[4], [Globalization.NumberStyles]::Float, $culture, [ref]$reference) -or
                $reference -ne 50) { throw 'Поддерживаются только S-параметры 50 Ом с известной единицей частоты.' }
            $scale = $scales[$options[0]]
            $format = $options[2]
            continue
        }
        if (-not $format -or $line.StartsWith('[')) { throw 'Неподдерживаемый формат Touchstone; требуется файл двухпортового замера 1.x.' }
        foreach ($token in ($line -split '\s+')) { $tokens.Add($token) }
    }
    if (-not $format -or $tokens.Count -eq 0 -or $tokens.Count % 9 -ne 0) { throw 'Неполная строка двухпортовых данных Touchstone.' }
    $frequencyList = New-Object 'System.Collections.Generic.List[double]'
    $traces = @{}
    foreach ($name in 'S11','S21','S12','S22') { $traces[$name] = New-Object 'System.Collections.Generic.List[System.Numerics.Complex]' }
    for ($offset = 0; $offset -lt $tokens.Count; $offset += 9) {
        $row = New-Object 'double[]' 9
        for ($column = 0; $column -lt 9; $column++) {
            $number = 0.0
            $text = $tokens[$offset+$column] -replace '[dD]','E'
            if (-not [double]::TryParse($text, [Globalization.NumberStyles]::Float, $culture, [ref]$number) -or -not (Test-VnaFinite $number)) {
                throw "Некорректное число в Touchstone: $text."
            }
            $row[$column] = $number
        }
        $frequencyList.Add($row[0] * $scale)
        $names = @('S11','S21','S12','S22')
        for ($traceIndex = 0; $traceIndex -lt 4; $traceIndex++) {
            $first = $row[1+2*$traceIndex]
            $second = $row[2+2*$traceIndex]
            if ($format -eq 'RI') { $value = [System.Numerics.Complex]::new($first,$second) }
            else {
                if ($format -eq 'MA' -and $first -lt 0) { throw 'Отрицательный модуль S-параметра.' }
                $magnitude = $first
                if ($format -eq 'DB') { $magnitude = [math]::Pow(10,$first/20) }
                $value = [System.Numerics.Complex]::FromPolarCoordinates($magnitude,$second*[math]::PI/180)
            }
            $traces[$names[$traceIndex]].Add($value)
        }
    }
    return (New-VnaData -Frequencies $frequencyList.ToArray() -S11 $traces.S11.ToArray() -S21 $traces.S21.ToArray() -S12 $traces.S12.ToArray() -S22 $traces.S22.ToArray())
}

function Write-VnaTouchstone {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Data, [string[]]$Comments=@())
    Assert-VnaData $Data
    $culture = [Globalization.CultureInfo]::InvariantCulture
    $builder = New-Object Text.StringBuilder
    foreach ($comment in $Comments) {
        foreach ($line in ($comment -split '\r?\n')) { [void]$builder.AppendLine('! ' + $line) }
    }
    [void]$builder.AppendLine('# HZ S RI R 50')
    for ($index=0; $index -lt $Data.Frequencies.Count; $index++) {
        $row = @(([double]$Data.Frequencies[$index]).ToString('G17',$culture))
        foreach ($name in 'S11','S21','S12','S22') {
            $value = [System.Numerics.Complex]$Data.$name[$index]
            $row += $value.Real.ToString('G17',$culture)
            $row += $value.Imaginary.ToString('G17',$culture)
        }
        [void]$builder.AppendLine(($row -join ' '))
    }
    [IO.File]::WriteAllText($Path,$builder.ToString(),[Text.UTF8Encoding]::new($false))
}

# A flattened 2x2 matrix is [a,b,c,d], representing rows [a,b] and [c,d].
function ConvertTo-VnaTMatrix {
    param([System.Numerics.Complex]$S11, [System.Numerics.Complex]$S21,
        [System.Numerics.Complex]$S12, [System.Numerics.Complex]$S22)
    if ($S21.Magnitude -lt 1e-15) { throw 'Нельзя преобразовать в T-матрицу: S21 близок к нулю.' }
    return ,([System.Numerics.Complex[]]@(($S12-($S11*$S22/$S21)),($S11/$S21),(-$S22/$S21),([System.Numerics.Complex]::One/$S21)))
}

function ConvertFrom-VnaTMatrix {
    param([System.Numerics.Complex[]]$Matrix)
    if ($Matrix.Count -ne 4 -or $Matrix[3].Magnitude -lt 1e-15) { throw 'Вырожденная T-матрица.' }
    return ,([System.Numerics.Complex[]]@(($Matrix[1]/$Matrix[3]),([System.Numerics.Complex]::One/$Matrix[3]),(($Matrix[0]*$Matrix[3]-$Matrix[1]*$Matrix[2])/$Matrix[3]),(-$Matrix[2]/$Matrix[3])))
}

function Multiply-VnaTMatrix {
    param([System.Numerics.Complex[]]$Left,[System.Numerics.Complex[]]$Right)
    if ($Left.Count -ne 4 -or $Right.Count -ne 4) { throw 'Нужны две матрицы 2x2.' }
    return ,([System.Numerics.Complex[]]@(($Left[0]*$Right[0]+$Left[1]*$Right[2]),($Left[0]*$Right[1]+$Left[1]*$Right[3]),($Left[2]*$Right[0]+$Left[3]*$Right[2]),($Left[2]*$Right[1]+$Left[3]*$Right[3])))
}

function Invert-VnaTMatrix {
    param([System.Numerics.Complex[]]$Matrix)
    if ($Matrix.Count -ne 4) { throw 'Нужна матрица 2x2.' }
    $determinant = $Matrix[0]*$Matrix[3]-$Matrix[1]*$Matrix[2]
    if ($determinant.Magnitude -lt 1e-15) { throw 'Нельзя вычесть оснастку: вырожденная T-матрица.' }
    return ,([System.Numerics.Complex[]]@(($Matrix[3]/$determinant),(-$Matrix[1]/$determinant),(-$Matrix[2]/$determinant),($Matrix[0]/$determinant)))
}

function Remove-VnaFixtures {
    param([Parameter(Mandatory)]$Data, $Left=$null, $Right=$null)
    Assert-VnaData $Data
    foreach ($fixture in @($Left,$Right)) {
        if ($null -eq $fixture) { continue }
        Assert-VnaData $fixture
        if ($fixture.Frequencies.Count -ne $Data.Frequencies.Count) { throw 'Сетка частот оснастки не совпадает с замером.' }
        for ($index=0; $index -lt $Data.Frequencies.Count; $index++) {
            if ($fixture.Frequencies[$index] -ne $Data.Frequencies[$index]) { throw 'Сетка частот оснастки не совпадает с замером; интерполяция запрещена.' }
        }
    }
    $result = @{ Frequencies=[double[]]$Data.Frequencies }
    foreach ($name in 'S11','S21','S12','S22') { $result[$name] = New-Object 'System.Numerics.Complex[]' $Data.Frequencies.Count }
    for ($index=0; $index -lt $Data.Frequencies.Count; $index++) {
        if ($null -eq $Left -and $null -eq $Right) {
            foreach ($name in 'S11','S21','S12','S22') { $result[$name][$index] = $Data.$name[$index] }
            continue
        }
        $matrix = ConvertTo-VnaTMatrix $Data.S11[$index] $Data.S21[$index] $Data.S12[$index] $Data.S22[$index]
        if ($null -ne $Left) {
            $fixtureMatrix = ConvertTo-VnaTMatrix $Left.S11[$index] $Left.S21[$index] $Left.S12[$index] $Left.S22[$index]
            $matrix = Multiply-VnaTMatrix (Invert-VnaTMatrix $fixtureMatrix) $matrix
        }
        if ($null -ne $Right) {
            $fixtureMatrix = ConvertTo-VnaTMatrix $Right.S11[$index] $Right.S21[$index] $Right.S12[$index] $Right.S22[$index]
            $matrix = Multiply-VnaTMatrix $matrix (Invert-VnaTMatrix $fixtureMatrix)
        }
        $parameters = ConvertFrom-VnaTMatrix $matrix
        $result.S11[$index]=$parameters[0]; $result.S21[$index]=$parameters[1]
        $result.S12[$index]=$parameters[2]; $result.S22[$index]=$parameters[3]
    }
    return (New-VnaData @result)
}

function Select-VnaBand {
    param([Parameter(Mandatory)]$Data,[Parameter(Mandatory)][double]$FmaxHz)
    Assert-VnaData $Data
    if (-not (Test-VnaFinite $FmaxHz) -or $FmaxHz -le 0) { throw 'Верхняя частота должна быть конечной и положительной.' }
    $indexes = @()
    for ($index=0; $index -lt $Data.Frequencies.Count; $index++) { if ($Data.Frequencies[$index] -le $FmaxHz) { $indexes += $index } }
    if ($indexes.Count -eq 0) { throw 'В выбранном диапазоне нет частотных точек.' }
    $result = @{ Frequencies=[double[]]@($Data.Frequencies[$indexes]) }
    foreach ($name in 'S11','S21','S12','S22') { $result[$name] = [System.Numerics.Complex[]]@($Data.$name[$indexes]) }
    return (New-VnaData @result)
}

function Get-VnaVswr {
    param([System.Numerics.Complex]$Reflection)
    $magnitude=$Reflection.Magnitude
    if ($magnitude -ge 1) { return [double]::PositiveInfinity }
    return (1+$magnitude)/(1-$magnitude)
}

function Get-VnaSummary {
    param([Parameter(Mandatory)]$Data)
    Assert-VnaData $Data
    $vswr1=1.0; $vswr2=1.0; $loss=[double]::NegativeInfinity
    for ($index=0; $index -lt $Data.Frequencies.Count; $index++) {
        $vswr1=[math]::Max($vswr1,(Get-VnaVswr $Data.S11[$index]))
        $vswr2=[math]::Max($vswr2,(Get-VnaVswr $Data.S22[$index]))
        # Loss is judged on both directions: the worse of S21 and S12 (СТО ФРАК-002-2026 п. 13.1).
        $magnitude=[math]::Min(([System.Numerics.Complex]$Data.S21[$index]).Magnitude, ([System.Numerics.Complex]$Data.S12[$index]).Magnitude)
        $pointLoss=[double]::PositiveInfinity
        if ($magnitude -gt 0) { $pointLoss=-20*[math]::Log10($magnitude) }
        $loss=[math]::Max($loss,$pointLoss)
    }
    return [pscustomobject]@{ Vswr1=$vswr1; Vswr2=$vswr2; LossDb=$loss; FmaxHz=$Data.Frequencies[-1] }
}

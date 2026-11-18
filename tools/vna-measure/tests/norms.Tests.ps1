# Pester 3.x. Passport norms for the screen: local data only, no network.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here '..\erp.ps1')
. (Join-Path $here '..\norms.ps1')
. (Join-Path $here '..\s2vna.ps1')

function New-TestNorms {
    # The shape of backend/data/rf-norms.json, cut to the entries these tests need.
    $json = @'
{
  "makers": [ { "series": "ADP1A", "match": "ADP1A", "title": "Планар ADP1A", "source": "Планар, РЭ ADP1A",
      "groups": [ { "title": "3,5 мм", "ports": ["3.5mm", "3.5mm"], "from_ghz": 0, "id": "adp1a-35",
                    "segments": [ { "to_ghz": 20, "a": 1.06, "b": 0 }, { "to_ghz": 26.5, "a": 1.1, "b": 0 } ],
                    "loss": { "from_ghz": 0, "segments": [ { "to_ghz": 32, "a": 0.2, "k": 0 } ], "source": "РЭ" } } ] } ],
  "mil_adapters": [
    { "id": "mil-55339-55", "sheet": "MIL-PRF-55339/55", "title": "SMA–SMA", "ports": ["SMA", "SMA"], "from_ghz": 0.5, "segments": [ { "to_ghz": 27, "a": 1.05, "b": 0.005 } ],
      "loss": { "from_ghz": 0.5, "segments": [ { "to_ghz": 18, "a": 0, "k": 0.03 }, { "to_ghz": 27, "a": 0, "k": 0.05 } ], "source": "лист" } },
    { "id": "mil-55339-54b", "sheet": "MIL-PRF-55339/54B", "title": "SMA–N", "ports": ["SMA", "N"], "from_ghz": 0,
      "segments": [ { "to_ghz": 12.4, "a": 1.06, "b": 0.005 }, { "to_ghz": 18, "a": 0.83, "b": 0.023 } ],
      "loss": { "points": [ { "ghz": 9, "max_db": 0.18 } ], "source": "лист" } },
    { "id": "mil-55339-40b", "sheet": "MIL-PRF-55339/40B, /41B, /42A", "title": "SMA–TNC", "ports": ["SMA", "TNC"], "from_ghz": 0.5,
      "segments": [ { "to_ghz": 11, "a": 1.15, "b": 0.015 } ], "loss": { "points": [ { "ghz": 6, "k": 0.06 } ], "source": "лист" } } ],
  "type_norms": [
    { "id": "escc-3402-003-sma", "level": "escc", "reference": "ESCC 3402/003", "title": "SMA–SMA", "ports": ["SMA", "SMA"], "from_ghz": 0,
      "segments": [ { "to_ghz": 18, "a": 1.1, "b": 0.008 } ], "loss": { "from_ghz": 0, "segments": [ { "to_ghz": 18, "a": 0.02, "k": 0.06 } ], "source": "лист" } },
    { "id": "iec-61169-8-bnc", "level": "iec", "reference": "IEC 61169-8", "title": "BNC", "ports": ["BNC", "BNC"], "from_ghz": 0,
      "segments": [ { "to_ghz": 4, "a": 1.3, "b": 0 } ] } ],
  "vna": []
}
'@
    return $json | ConvertFrom-Json
}

function New-TestRf {
    param([string]$NormId, [object[]]$Interfaces = @())
    return [pscustomobject]@{ interfaces = $Interfaces; norm_id = $NormId }
}

Describe 'Screen limit lines from the assigned norm' {
    BeforeEach { $script:norms = New-TestNorms }
    It 'draws the assigned MIL sheet from its lower edge, linear in F' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.035'; name = 'Переходник SMA вилка – SMA розетка'; doc_designation = '' }
        $limits = Get-VnaScreenLimits $script:norms $item (New-TestRf 'mil-55339-55') 18e9
        $limits.Swr.Count | Should Be 1
        $limits.Swr[0].StartHz | Should Be 5e8
        $limits.Swr[0].StopHz | Should Be 18e9
        [math]::Round($limits.Swr[0].From, 6) | Should Be 1.0525
        [math]::Round($limits.Swr[0].To, 6) | Should Be 1.14
        $limits.Title | Should Match '^СИ\.035 · КСВН ≤ 1,05 \+ 0,005·F \(0,5–18 ГГц\) — MIL-PRF-55339/55'
        $limits.Title | Should Not Match '1,30|требование цеха'
        $limits.Loss.Count | Should Be 12
        $limits.Loss[0].Type | Should Be 2
        $limits.Loss[0].StartHz | Should Be 5e8
        [math]::Round($limits.Loss[0].From, 5) | Should Be ([math]::Round(-0.03 * [math]::Sqrt(0.5), 5))
        [math]::Round($limits.Loss[11].To, 5) | Should Be ([math]::Round(-0.03 * [math]::Sqrt(18), 5))
        $limits.Loss[11].StopHz | Should Be 18e9
        $limits.Title | Should Match 'потери ≤ 0,03·√F дБ \(0,5–18 ГГц\) — MIL-PRF-55339/55'
    }
    It 'draws a short line at the test frequency of a sheet without a loss formula' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.040'; name = 'Переходник SMA розетка – N вилка'; doc_designation = '' }
        $limits = Get-VnaScreenLimits $script:norms $item (New-TestRf 'mil-55339-54b') 18e9
        $limits.Loss.Count | Should Be 1
        $limits.Loss[0].StartHz | Should Be 8.95e9
        $limits.Loss[0].From | Should Be -0.18
        $limits.Title | Should Match 'потери ≤ 0,18 дБ на частоте испытания 9 ГГц'
        (Get-VnaScreenLimits $script:norms $item (New-TestRf 'mil-55339-54b') 4e9).Title | Should Match 'норма MIL-PRF-55339/54B не покрывает полосу'
    }
    It 'computes a test-frequency limit given as k·√F' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.041'; name = 'Переходник SMA розетка – TNC вилка'; doc_designation = '' }
        $limits = Get-VnaScreenLimits $script:norms $item (New-TestRf 'mil-55339-40b') 11e9
        $limits.Loss.Count | Should Be 1
        [math]::Round($limits.Loss[0].From, 4) | Should Be ([math]::Round(-0.06 * [math]::Sqrt(6), 4))
        $limits.Title | Should Match 'потери ≤ 0,147 дБ на частоте испытания 6 ГГц'
    }
    It 'reads norms without loss keys tolerantly' {
        $old = New-TestNorms; $old.mil_adapters[0].PSObject.Properties.Remove('loss')
        $item = [pscustomobject]@{ inventory_number = 'СИ.035'; name = 'Переходник SMA вилка – SMA розетка'; doc_designation = '' }
        $limits = Get-VnaScreenLimits $old $item (New-TestRf 'mil-55339-55') 18e9
        $limits.Loss.Count | Should Be 0
        $limits.Title | Should Match 'потери: нормы нет'
    }
    It 'expands a shared-series pair the way the ERP parser does' {
        $ports = Get-VnaItemPorts 'Переход SMA вилка–розетка' @()
        ($ports.Series -join '|') | Should Be 'SMA|SMA'
    }
    It 'draws the assigned maker norm' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.009'; name = 'Переход'; doc_designation = 'ADP1A-35F-35F' }
        $interfaces = @([pscustomobject]@{ series = '3.5mm'; impedance_ohm = 50 }, [pscustomobject]@{ series = '3.5mm'; impedance_ohm = $null })
        $limits = Get-VnaScreenLimits $script:norms $item (New-TestRf 'adp1a-35' $interfaces) 18e9
        $limits.Swr[0].From | Should Be 1.06
        $limits.Swr[0].StartHz | Should Be 10e6
        $limits.Swr[0].StopHz | Should Be 18e9
        $limits.Title | Should Match 'КСВН ≤ 1,06 \(0,01–18 ГГц\) — Планар ADP1A'
        $limits.Loss.Count | Should Be 1
        $limits.Loss[0].From | Should Be -0.2
        $limits.Title | Should Match 'потери ≤ 0,2 дБ \(0,01–18 ГГц\) — Планар ADP1A'
        $limits.Title | Should Not Match '26,5'
    }
    It 'cuts the norm at the chosen upper frequency' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.040'; name = 'Переходник SMA розетка – N вилка'; doc_designation = '' }
        $limits = Get-VnaScreenLimits $script:norms $item (New-TestRf 'mil-55339-54b') 11e9
        $limits.Swr.Count | Should Be 1
        $limits.Swr[0].StopHz | Should Be 11e9
        [math]::Round($limits.Swr[0].To, 6) | Should Be 1.115
    }
    It 'keeps both pieces of a two-segment sheet across 12.4 GHz' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.040'; name = 'Переходник SMA розетка – N вилка'; doc_designation = '' }
        $limits = Get-VnaScreenLimits $script:norms $item (New-TestRf 'mil-55339-54b') 18e9
        $limits.Swr.Count | Should Be 2
        $limits.Swr[1].StartHz | Should Be 12.4e9
        [math]::Round($limits.Swr[1].From, 4) | Should Be 1.1152
        [math]::Round($limits.Swr[1].To, 4) | Should Be 1.244
    }
    It 'draws no limits for a 75-ohm item' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.050'; name = 'Переходник F вилка – BNC розетка 75 Ом'; doc_designation = '' }
        $limits = Get-VnaScreenLimits $script:norms $item (New-TestRf '') 4e9
        $limits.Swr.Count | Should Be 0
        $limits.Title | Should Match '75 Ом — не для 50 Ом тракта'
    }
    It 'draws no line without an assigned norm, even where a sheet or a maker would match, and none before an item' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.035'; name = 'Переходник SMA вилка – SMA розетка ADP1A'; doc_designation = 'ADP1A-35F-35F' }
        foreach ($rf in @($null, (New-TestRf ''))) {
            $limits = Get-VnaScreenLimits $script:norms $item $rf 18e9
            $limits.Swr.Count | Should Be 0
            $limits.Loss.Count | Should Be 0
            $limits.Title | Should Match 'норма не назначена'
        }
        $missing = Get-VnaScreenLimits $script:norms $item (New-TestRf 'escc-3402-010-tnc-plug-jack') 18e9
        $missing.Swr.Count | Should Be 0
        $missing.Title | Should Match 'норма escc-3402-010-tnc-plug-jack отсутствует в rf-norms.json'
        $generic = Get-VnaScreenLimits $script:norms $null
        $generic.Swr.Count | Should Be 0
        $generic.Title | Should Be ''
    }
    It 'warns instead of drawing anything without the norms file' {
        $limits = Get-VnaScreenLimits $null $null
        $limits.Swr.Count | Should Be 0
        $limits.Title | Should Match 'нет rf-norms.json'
        Find-VnaNormsFile (Join-Path $TestDrive 'nowhere') | Should BeNullOrEmpty
    }
    It 'packs segments into the S2VNA limit table' {
        $data = ConvertTo-VnaLimitData @([pscustomobject]@{ StartHz = 1e7; StopHz = 1.8e10; From = 1.3; To = 1.3 })
        ($data -join ',') | Should Be '1,1,10000000,18000000000,1.3,1.3'
        (ConvertTo-VnaLimitData @()) -join ',' | Should Be '0'
    }
    It 'reads the repository norms file when it is present' {
        $path = Find-VnaNormsFile (Join-Path $here '..')
        if (-not $path) { return }
        $norms = Read-VnaNorms $path
        $item = [pscustomobject]@{ inventory_number = 'СИ.009'; name = 'Переход ADP1A 35F-35F'; doc_designation = 'ADP1A-35F-35F' }
        $interfaces = @([pscustomobject]@{ series = '3.5mm'; impedance_ohm = 50 }, [pscustomobject]@{ series = '3.5mm'; impedance_ohm = 50 })
        (Get-VnaScreenLimits $norms $item (New-TestRf 'adp1a-35' $interfaces) 18e9).Title | Should Match 'ADP1A'
        $escc = [pscustomobject]@{ inventory_number = 'СИ.034'; name = 'Переходник SMA female–SMA female S-322'; doc_designation = '' }
        (Get-VnaScreenLimits $norms $escc (New-TestRf 'escc-3402-003-sma') 18e9).Title | Should Match 'КСВН ≤ 1,1 \+ 0,008·F \(0,01–18 ГГц\) — ESCC 3402/003'
    }
    It 'reads a type norm and an IEC norm without a loss limit' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.014'; name = 'Переходник BNC female–BNC female B-322'; doc_designation = '' }
        $limits = Get-VnaScreenLimits $script:norms $item (New-TestRf 'iec-61169-8-bnc') 4e9
        $limits.Swr.Count | Should Be 1
        $limits.Swr[0].From | Should Be 1.3
        $limits.Title | Should Match 'КСВН ≤ 1,3 \(0,01–4 ГГц\) — IEC 61169-8'
        $limits.Title | Should Match 'потери: нормы нет'
        $sma = [pscustomobject]@{ inventory_number = 'СИ.034'; name = 'Переходник SMA female–SMA female S-322'; doc_designation = '' }
        $esa = Get-VnaScreenLimits $script:norms $sma (New-TestRf 'escc-3402-003-sma') 18e9
        [math]::Round($esa.Swr[0].To, 4) | Should Be 1.244
        $esa.Loss.Count | Should Be 12
    }
}

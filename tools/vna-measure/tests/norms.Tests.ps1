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
      "groups": [ { "title": "3,5 мм", "ports": ["3.5mm", "3.5mm"], "from_ghz": 0,
                    "segments": [ { "to_ghz": 20, "a": 1.06, "b": 0 }, { "to_ghz": 26.5, "a": 1.1, "b": 0 } ] } ] } ],
  "mil_adapters": [
    { "sheet": "MIL-PRF-55339/55", "title": "SMA–SMA", "ports": ["SMA", "SMA"], "from_ghz": 0.5, "segments": [ { "to_ghz": 27, "a": 1.05, "b": 0.005 } ] },
    { "sheet": "MIL-PRF-55339/54B", "title": "SMA–N", "ports": ["SMA", "N"], "from_ghz": 0,
      "segments": [ { "to_ghz": 12.4, "a": 1.06, "b": 0.005 }, { "to_ghz": 18, "a": 0.83, "b": 0.023 } ] } ],
  "shop_requirement": { "vswr_max": 1.3, "label": "требование цеха" }
}
'@
    return $json | ConvertFrom-Json
}

Describe 'Screen limit lines from passport norms' {
    BeforeEach { $script:norms = New-TestNorms }
    It 'takes the MIL sheet of an SMA pair parsed from the card name, from its lower edge, linear in F' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.035'; name = 'Переходник SMA вилка – SMA розетка'; doc_designation = '' }
        $limits = Get-VnaScreenLimits $script:norms $item @() 18e9
        $limits.Swr.Count | Should Be 2
        $limits.Swr[0].StartHz | Should Be 5e8
        $limits.Swr[0].StopHz | Should Be 18e9
        [math]::Round($limits.Swr[0].From, 6) | Should Be 1.0525
        [math]::Round($limits.Swr[0].To, 6) | Should Be 1.14
        $limits.Swr[1].From | Should Be 1.3
        $limits.Swr[1].StartHz | Should Be 10e6
        $limits.Title | Should Match '^СИ\.035 · КСВН ≤ 1,05 \+ 0,005·F \(0,5–18 ГГц\) — MIL-PRF-55339/55'
        $limits.Title | Should Match 'КСВН ≤ 1,30 — требование цеха до 18 ГГц'
        $limits.Title | Should Match 'потери: нормы нет'
    }
    It 'expands a shared-series pair the way the ERP parser does' {
        $ports = Get-VnaItemPorts 'Переход SMA вилка–розетка' @()
        ($ports.Series -join '|') | Should Be 'SMA|SMA'
    }
    It 'prefers the maker spec matched by model and the card interfaces over the name' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.009'; name = 'Переход'; doc_designation = 'ADP1A-35F-35F' }
        $interfaces = @([pscustomobject]@{ series = '3.5mm'; impedance_ohm = 50 }, [pscustomobject]@{ series = '3.5mm'; impedance_ohm = $null })
        $limits = Get-VnaScreenLimits $script:norms $item $interfaces 18e9
        $limits.Swr[0].From | Should Be 1.06
        $limits.Swr[0].StartHz | Should Be 10e6
        $limits.Swr[0].StopHz | Should Be 18e9
        $limits.Title | Should Match 'КСВН ≤ 1,06 \(0,01–18 ГГц\) — Планар ADP1A'
        $limits.Title | Should Not Match '26,5'
    }
    It 'cuts the norm at the chosen upper frequency' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.040'; name = 'Переходник SMA розетка – N вилка'; doc_designation = '' }
        $limits = Get-VnaScreenLimits $script:norms $item @() 11e9
        $limits.Swr.Count | Should Be 2
        $limits.Swr[0].StopHz | Should Be 11e9
        [math]::Round($limits.Swr[0].To, 6) | Should Be 1.115
        $limits.Swr[1].StopHz | Should Be 11e9
    }
    It 'keeps both pieces of a two-segment sheet across 12.4 GHz' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.040'; name = 'Переходник SMA розетка – N вилка'; doc_designation = '' }
        $limits = Get-VnaScreenLimits $script:norms $item @() 18e9
        $limits.Swr.Count | Should Be 3
        $limits.Swr[1].StartHz | Should Be 12.4e9
        [math]::Round($limits.Swr[1].From, 4) | Should Be 1.1152
        [math]::Round($limits.Swr[1].To, 4) | Should Be 1.244
    }
    It 'draws no limits for a 75-ohm item' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.050'; name = 'Переходник F вилка – BNC розетка 75 Ом'; doc_designation = '' }
        $limits = Get-VnaScreenLimits $script:norms $item @() 4e9
        $limits.Swr.Count | Should Be 0
        $limits.Title | Should Match '75 Ом — не для 50 Ом тракта'
    }
    It 'shows only the shop requirement when the type has no norm' {
        $item = [pscustomobject]@{ inventory_number = 'СИ.060'; name = 'Переходник BNC вилка – BNC вилка'; doc_designation = '' }
        $limits = Get-VnaScreenLimits $script:norms $item @() 4e9
        $limits.Swr.Count | Should Be 1
        $limits.Title | Should Match 'норма на тип не найдена'
        (Get-VnaScreenLimits $script:norms $null).Swr.Count | Should Be 1
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
        [double]$norms.shop_requirement.vswr_max | Should BeGreaterThan 1
        $item = [pscustomobject]@{ inventory_number = 'СИ.009'; name = 'Переход ADP1A 35F-35F'; doc_designation = 'ADP1A-35F-35F' }
        $interfaces = @([pscustomobject]@{ series = '3.5mm'; impedance_ohm = 50 }, [pscustomobject]@{ series = '3.5mm'; impedance_ohm = 50 })
        (Get-VnaScreenLimits $norms $item $interfaces 18e9).Title | Should Match 'ADP1A'
    }
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here '..\vna-measure.ps1')

function New-TestMatingOffline {
    param([string]$Root)
    $null = New-Item -ItemType Directory -Path $Root -Force
    $items = @(
        [pscustomobject]@{ id = 'dut'; inventory_number = 'СИ.035'; name = 'Переходник'; serial_number = 'D'; files = @(); mating_ends = @('A', 'B'); mating_resource = 10 },
        [pscustomobject]@{ id = 'fixture'; inventory_number = 'СИ.009'; name = 'ADP1A'; serial_number = 'F'; files = @(); mating_ends = @('A', 'B'); mating_resource = 3000 },
        [pscustomobject]@{ id = 'c1'; inventory_number = 'СИ.100'; name = 'Кабель 1'; serial_number = 'C1'; files = @(); mating_ends = @('A', 'B') },
        [pscustomobject]@{ id = 'c2'; inventory_number = 'СИ.101'; name = 'Кабель 2'; serial_number = 'C2'; files = @(); mating_ends = @('A', 'B') },
        [pscustomobject]@{ id = 'acm'; inventory_number = 'СИ.104'; name = 'АКМ'; serial_number = 'A'; files = @(); mating_ends = @('A', 'B') },
        [pscustomobject]@{ id = 'load'; inventory_number = 'СИ.020'; name = 'Нагрузка'; serial_number = 'L'; files = @(); mating_ends = @('A') })
    @{ instruments = $items } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $Root 'instruments.json') -Encoding UTF8
    return New-VnaErpSession -OfflineDirectory $Root
}

function Format-TestEntries { param([object[]]$Entries) @($Entries | ForEach-Object { "$($_.instrument_id)$($_.inventory_number):$($_.end)" }) -join ',' }

Describe 'Mating setup' {
    It 'defaults to the owner kit and rejects duplicate or malformed items' {
        $setup = Get-VnaMatingSetup $null
        $setup.Acm | Should Be 'СИ.104'
        (Format-TestEntries (New-VnaCalibrationMatings $setup)) | Should Be 'СИ.104:A,СИ.104:B,СИ.100:B,СИ.101:B'
        { Get-VnaMatingSetup ([pscustomobject]@{ mating_acm = 'СИ.100' }) } | Should Throw
        { Get-VnaMatingSetup ([pscustomobject]@{ mating_cables = @([pscustomobject]@{ inventory_number = 'СИ.100'; end = 'C' }, [pscustomobject]@{ inventory_number = 'СИ.101'; end = 'B' }) }) } | Should Throw
    }
}

Describe 'Measurement chain matings' {
    It 'counts DUT and cable ends directly, and a kept fixture only on its DUT side' {
        $session = New-TestMatingOffline (Join-Path $TestDrive 'chain')
        $setup = Get-VnaMatingSetup $null
        $dut = Get-VnaInstrument $session 'СИ.035'
        $direct = Get-VnaMeasurementMatings $session $setup $dut @($null, $null) $null
        (Format-TestEntries $direct.Entries) | Should Be 'dut:A,dut:B,c1:B,c2:B'
        $fixture = [pscustomobject]@{ Instrument = (Get-VnaInstrument $session 'СИ.009'); Reverse = $false }
        $first = Get-VnaMeasurementMatings $session $setup $dut @($fixture, $null) $null
        (Format-TestEntries $first.Entries) | Should Be 'dut:A,dut:B,c1:B,fixture:B,fixture:A,c2:B'
        $again = Get-VnaMeasurementMatings $session $setup $dut @($fixture, $null) $first.Mounts
        (Format-TestEntries $again.Entries) | Should Be 'dut:A,dut:B,fixture:B,c2:B'
        $right = Get-VnaMeasurementMatings $session $setup $dut @($null, $fixture) $null
        (Format-TestEntries $right.Entries) | Should Be 'dut:A,dut:B,c1:B,c2:B,fixture:A,fixture:B'
    }
    It 'drops a side the card does not have and reports an exhausted resource' {
        $session = New-TestMatingOffline (Join-Path $TestDrive 'known')
        $entries = @((New-VnaMatingEntry -Instrument ([pscustomobject]@{ id = 'load' }) -End A -Kind measurement),
                     (New-VnaMatingEntry -Instrument ([pscustomobject]@{ id = 'load' }) -End B -Kind measurement))
        $known = Select-VnaKnownMatings $session $entries
        (Format-TestEntries $known.Entries) | Should Be 'load:A'
        $known.Exhausted | Should Be $false
        $null = Send-VnaMatingBatch $session @{ source = 'vna-measurement-00000000-0000-0000-0000-000000000001'; entries = @(@{ instrument_id = 'dut'; end = 'A'; delta = 10; kind = 'measurement' }) }
        (Select-VnaKnownMatings $session @(New-VnaMatingEntry -Instrument ([pscustomobject]@{ id = 'dut' }) -End A -Kind measurement)).Exhausted | Should Be $true
    }
}

Describe 'Pending mating batches' {
    It 'resolves inventory numbers, replays idempotently and refuses a changed batch' {
        $session = New-TestMatingOffline (Join-Path $TestDrive 'batch')
        $batch = @{ source = 'vna-calibration-00000000-0000-0000-0000-000000000002'; entries = @(New-VnaCalibrationMatings (Get-VnaMatingSetup $null)) }
        (Send-VnaMatingBatch $session $batch).replayed | Should Be $false
        (Send-VnaMatingBatch $session $batch).replayed | Should Be $true
        { Send-VnaMatingBatch $session @{ source = $batch.source; entries = @($batch.entries[0]) } } | Should Throw
        $summary = Get-VnaMatingSummary $session (Get-VnaInstrument $session 'СИ.104')
        (@($summary.ends | ForEach-Object { "$($_.end)=$($_.total)" }) -join ',') | Should Be 'A=1,B=1'
    }
    It 'keeps a batch on disk until sent, isolates refused batches and other targets' {
        $session = New-TestMatingOffline (Join-Path $TestDrive 'sync')
        $pending = Join-Path $TestDrive 'pending'
        $target = Get-VnaMatingTarget -Session $session
        $good = Save-VnaPendingMatings $pending $target 'vna-calibration-00000000-0000-0000-0000-000000000003' @(New-VnaCalibrationMatings (Get-VnaMatingSetup $null))
        $bad = Save-VnaPendingMatings $pending $target 'vna-measurement-00000000-0000-0000-0000-000000000004' @(New-VnaMatingEntry -Number 'СИ.020' -End B -Kind measurement)
        $foreign = Save-VnaPendingMatings $pending 'erp:https://other.test' 'vna-measurement-00000000-0000-0000-0000-000000000005' @(New-VnaMatingEntry -Number 'СИ.035' -End A -Kind measurement)
        { Save-VnaPendingMatings $pending $target 'vna-calibration-00000000-0000-0000-0000-000000000003' @(New-VnaCalibrationMatings (Get-VnaMatingSetup $null)) } | Should Throw
        $result = Sync-VnaPendingMatings $session $pending
        $result.Sent | Should Be 1
        $result.Rejected | Should Be 1
        $result.Left | Should Be 1
        (Test-Path -LiteralPath $good) | Should Be $false
        (Test-Path -LiteralPath (Join-Path (Join-Path $pending 'rejected') (Split-Path -Leaf $bad))) | Should Be $true
        (Test-Path -LiteralPath $foreign) | Should Be $true
    }
}

Describe 'Offline session records matings' {
    It 'records calibration and measurement matings without COM or HTTP' {
        $script:answers = New-Object 'System.Collections.Generic.Queue[string]'
        foreach ($answer in @('2','д','3','','','4','СИ.035','напрямую','','','н','0','д')) { $script:answers.Enqueue($answer) }
        Mock Read-Host { if ($script:answers.Count -eq 0) { throw 'Unexpected extra prompt' }; return $script:answers.Dequeue() }
        Mock New-VnaApplication { throw 'REAL COM FORBIDDEN' }
        Mock Invoke-RestMethod { throw 'NETWORK FORBIDDEN' }
        Mock Invoke-WebRequest { throw 'NETWORK FORBIDDEN' }
        $folder = Join-Path $TestDrive 'console'
        Start-VnaMeasure -Offline -DataDirectory $folder
        $script:answers.Count | Should Be 0
        $batches = @((Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $folder 'mock\matings.json') | ConvertFrom-Json) | ForEach-Object { $_ })
        $batches.Count | Should Be 2
        $batches[0].source | Should Match '^vna-calibration-'
        (Format-TestEntries $batches[0].entries) | Should Be 'offline-104:A,offline-104:B,offline-100:B,offline-101:B'
        $batches[1].source | Should Match '^vna-measurement-'
        (Format-TestEntries $batches[1].entries) | Should Be 'offline-035:A,offline-035:B,offline-100:B,offline-101:B'
        @(Get-ChildItem -LiteralPath (Join-Path $folder 'pending-matings') -Filter '*.json' -File).Count | Should Be 0
    }
}

Describe 'ERP refusals versus retryable conflicts' {
    It 'keeps a batch queued on 409 and moves it to rejected on 422' {
        $session = [pscustomobject]@{ Offline = $false; OfflineDirectory = ''; Login = 'x'; ServerUrl = 'https://erp.test'; WebSession = $null }
        $pending = Join-Path $TestDrive 'http'
        $target = Get-VnaMatingTarget -Session $session
        $path = Save-VnaPendingMatings $pending $target 'vna-calibration-00000000-0000-0000-0000-000000000009' @(New-VnaCalibrationMatings (Get-VnaMatingSetup $null))
        Mock Send-VnaMatingBatch { throw 'HTTP error' }
        $script:status = 409
        Mock Get-VnaHttpStatus { $script:status }
        $result = Sync-VnaPendingMatings $session $pending
        $result.Left | Should Be 1
        $result.Rejected | Should Be 0
        (Test-Path -LiteralPath $path) | Should Be $true
        $script:status = 422
        $result = Sync-VnaPendingMatings $session $pending
        $result.Rejected | Should Be 1
        (Test-Path -LiteralPath $path) | Should Be $false
        (Test-Path -LiteralPath (Join-Path (Join-Path $pending 'rejected') (Split-Path -Leaf $path))) | Should Be $true
    }
}

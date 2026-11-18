# Pester 3.x. Calibration checks and the VNA check journal; no COM, no network.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here '..\dsp.ps1')
. (Join-Path $here '..\s2vna.ps1')
. (Join-Path $here '..\erp.ps1')
. (Join-Path $here '..\checks.ps1')

function New-CheckData {
    param([double]$Reflection = 0.01, [double]$Transmission = 0.98, [int]$Points = 5)
    $frequencies = New-Object double[] $Points
    $r = New-Object 'System.Numerics.Complex[]' $Points; $t = New-Object 'System.Numerics.Complex[]' $Points
    for ($i = 0; $i -lt $Points; $i++) {
        $frequencies[$i] = 10e6 + (18e9 - 10e6) * $i / ($Points - 1)
        $r[$i] = [System.Numerics.Complex]::new($Reflection, 0); $t[$i] = [System.Numerics.Complex]::new($Transmission, 0)
    }
    return [pscustomobject]@{ Frequencies = $frequencies; S11 = $r; S21 = $t; S12 = $t.Clone(); S22 = $r.Clone() }
}
function Get-DbFactor { param([double]$Db) return [math]::Pow(10, $Db / 20) }

Describe 'ACM confidence test (Данные/Память)' {
    It 'passes within ±0,1 dB transmission and refuses beyond it' {
        $memory = New-CheckData -Reflection 0.5 -Transmission 0.5
        $close = New-CheckData -Reflection 0.5 -Transmission (0.5 * (Get-DbFactor 0.09))
        $far = New-CheckData -Reflection 0.5 -Transmission (0.5 * (Get-DbFactor 0.11))
        (Measure-VnaConfidence -Data $close -Memory $memory).Passed | Should Be $true
        $result = Measure-VnaConfidence -Data $far -Memory $memory
        $result.Passed | Should Be $false
        [math]::Round($result.TxDevDb, 2) | Should Be 0.11
    }
    It 'judges reflection only from −15 dB up, with 0,4 dB to 10 GHz and 0,5 dB above' {
        $memory = New-CheckData -Reflection 0.01 -Transmission 0.5
        $data = New-CheckData -Reflection 0.03 -Transmission 0.5
        $low = Measure-VnaConfidence -Data $data -Memory $memory
        $low.Passed | Should Be $true
        ($null -eq $low.RxDevDb) | Should Be $true
        $memory = New-CheckData -Reflection 0.5 -Transmission 0.5
        $data = New-CheckData -Reflection (0.5 * (Get-DbFactor 0.45)) -Transmission 0.5
        (Measure-VnaConfidence -Data $data -Memory $memory).Passed | Should Be $false
    }
    It 'refuses another frequency grid' {
        { Measure-VnaConfidence -Data (New-CheckData -Points 4) -Memory (New-CheckData) } | Should Throw 'сетка'
    }
}

Describe 'Flex, control standard and kit comparisons' {
    It 'flex fails when S21 moves more than 0,05 dB' {
        $before = New-CheckData
        (Compare-VnaFlex $before (New-CheckData -Transmission (0.98 * (Get-DbFactor 0.04)))).Passed | Should Be $true
        (Compare-VnaFlex $before (New-CheckData -Transmission (0.98 * (Get-DbFactor 0.06)))).Passed | Should Be $false
    }
    It 'control standard uses U = 1,5·КСВН % to 10 GHz and 2,0·КСВН % above at the worst point' {
        $reference = New-CheckData -Reflection 0.02
        $result = Compare-VnaControlStandard (New-CheckData -Reflection 0.025) $reference
        $result.Passed | Should Be $true
        $result.VswrDev | Should BeLessThan $result.VswrLimit
        $bad = Compare-VnaControlStandard (New-CheckData -Reflection 0.04) $reference
        $bad.Passed | Should Be $false
        [math]::Round($bad.VswrLimit, 4) | Should Be ([math]::Round(0.015 * (1.02 / 0.98), 4))
    }
    It 'models an ideal open, short and load and a lossless thru' {
        $open = [pscustomobject]@{ Type = 'OPEN'; C0 = 0; C1 = 0; C2 = 0; C3 = 0; Delay = 0; Loss = 0; Z0 = 50 }
        $short = [pscustomobject]@{ Type = 'SHORT'; L0 = 0; L1 = 0; L2 = 0; L3 = 0; Delay = 0; Loss = 0; Z0 = 50 }
        $load = [pscustomobject]@{ Type = 'LOAD'; Delay = 0; Loss = 0; Z0 = 50 }
        $thru = [pscustomobject]@{ Type = 'THRU'; Delay = 1e-10; Loss = 0; Z0 = 50 }
        [math]::Round((Get-VnaKitStandardResponse $open 1e9).Real, 6) | Should Be 1
        [math]::Round((Get-VnaKitStandardResponse $short 1e9).Real, 6) | Should Be -1
        [math]::Round((Get-VnaKitStandardResponse $load 1e9).Magnitude, 6) | Should Be 0
        [math]::Round((Get-VnaKitStandardResponse $thru 5e9).Magnitude, 6) | Should Be 1
        $delayed = [pscustomobject]@{ Type = 'OPEN'; C0 = 0; C1 = 0; C2 = 0; C3 = 0; Delay = 5e-11; Loss = 0; Z0 = 50 }
        [math]::Round((Get-VnaKitStandardResponse $delayed 5e9).Real, 6) | Should Be -1  # half-wave round trip: 2·ω·τ = π
    }
    It 'kit load passes within √(U²+0,05²) and the worst of three connections decides' {
        $load = [pscustomobject]@{ Type = 'LOAD'; Delay = 0; Loss = 0; Z0 = 50 }
        $tolerance = Get-VnaKitTolerance $null
        $good = Compare-VnaKitStandard -Standard $load -Port 1 -Sweeps @((New-CheckData -Reflection 0.01), (New-CheckData -Reflection 0.02), (New-CheckData -Reflection 0.03)) -Tolerance $tolerance
        $good.Ratio | Should BeLessThan 1
        [math]::Round($good.Deviation, 3) | Should Be 0.03
        $bad = Compare-VnaKitStandard -Standard $load -Port 2 -Sweeps @((New-CheckData -Reflection 0.01), (New-CheckData -Reflection 0.06)) -Tolerance $tolerance
        $bad.Ratio | Should BeGreaterThan 1
    }
}

Describe 'Check records and due dates' {
    $calibration = [pscustomobject]@{ Id = 'cal-1'; VnaSerial = '26030095'; AcmSerial = '26140008'; VnaTemperature = 31.2; AcmTemperature = 29.0 }
    It 'computes the session verdict from rounded numbers the ERP rechecks' {
        $record = New-VnaCheckRecord session $calibration -Values @{ confidence_passed = $true; confidence_tx_dev_db = 0.02; load_p1_vswr = 1.0600001; load_p2_vswr = 1.02 }
        $record.load_p1_vswr | Should Be 1.06
        $record.passed | Should Be $true
        (New-VnaCheckRecord session $calibration -Values @{ confidence_passed = $true; confidence_tx_dev_db = 0.02; load_p1_vswr = 1.07; load_p2_vswr = 1.02 }).passed | Should Be $false
        (New-VnaCheckRecord session $calibration -Values @{ confidence_passed = $false; confidence_tx_dev_db = 0.3 }).passed | Should Be $false
        $record.source | Should Match '^vna-check-[a-f0-9-]{36}$'
    }
    It 'saves each record once and replays it into the training ERP without doubling' {
        $folder = Join-Path $TestDrive 'erp'; [void][IO.Directory]::CreateDirectory($folder)
        $session = [pscustomobject]@{ Offline = $true; OfflineDirectory = $folder }
        $pending = Join-Path $TestDrive 'pending'
        $record = New-VnaCheckRecord flex $calibration -Values @{ s21_dev_db = 0.01; vswr_dev = 0.002 }
        $path = Save-VnaPendingCheck $pending (Get-VnaMatingTarget -Session $session) $record
        { Save-VnaPendingCheck $pending (Get-VnaMatingTarget -Session $session) $record } | Should Throw
        (Sync-VnaPendingChecks $session $pending).Sent | Should Be 1
        (Test-Path -LiteralPath $path) | Should Be $false
        (Send-VnaCheck $session $record).replayed | Should Be $true
        @(Get-VnaOfflineChecks $session).Count | Should Be 1
    }
    It 'reports weekly and monthly control as overdue, failed or in time' {
        $now = [datetimeoffset]::Parse('2026-11-18T12:00:00Z')
        $records = @(
            [pscustomobject]@{ kind = 'control_standard'; passed = $true; performed_at = '2026-11-10T12:00:00Z' },
            [pscustomobject]@{ kind = 'kit_standards'; passed = $true; performed_at = '2026-10-20T12:00:00Z' })
        $due = @(Get-VnaCheckDue $records $now)
        $due[0].status | Should Be 'overdue'
        $due[1].status | Should Be 'ok'
        (@(Format-VnaCheckReminders $due) -join "`n") | Should Match 'контроль просрочен'
        $records += [pscustomobject]@{ kind = 'kit_standards'; passed = $false; performed_at = '2026-11-17T12:00:00Z' }
        $due = @(Get-VnaCheckDue $records $now)
        $due[1].status | Should Be 'failed'
        { Assert-VnaControlNotFailed $due } | Should Throw 'замеры остановлены'
        (@(Get-VnaCheckDue @() $now) | ForEach-Object { $_.status }) -join ',' | Should Be 'never,never'
    }
}

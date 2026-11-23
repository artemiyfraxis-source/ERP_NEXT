# The writer talks to the real mbd request/pulse implementation in an isolated module.
# Only transport and PLC registers are simulated; no listener or serial port is opened.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $root 'ob019-writer.ps1') -BridgeScript (Join-Path $root 'ob019-bridge.ps1')

Describe 'correction through the real pulse primitive' {
    BeforeEach {
        $script:HoldMs = 100
        $script:integrationFault = ''
        $script:integrationCalls = @()
        $script:mbdModule = New-Module -ArgumentList $root, $TestDrive -ScriptBlock {
            param($Root, $TestDirectory)
            . (Join-Path $Root 'mbd.ps1') -Mock -Dir $TestDirectory
            $script:mockR = @{ 32 = 32500; 33 = 0; 54 = 100; 55 = 0; 600 = 2; 766 = 102; 782 = 0 }
            $script:originalXfer = (Get-Command Invoke-Xfer).ScriptBlock
            $script:failClear = $false
            function Invoke-Xfer([byte[]]$body, [int]$exp) {
                # Fail at the serial boundary, retaining the actual pulse/clear/readback logic.
                $address = [int]$body[2] * 256 + [int]$body[3]
                if ($script:failClear -and $body[1] -eq 16 -and $address -eq 600 -and ($body[8] -band 64) -eq 0) { throw 'simulated clear failure' }
                & $script:originalXfer $body $exp
            }
        }
        Mock Test-HmiOpen { $false }
        Mock Invoke-MbdLine {
            $request = $script:Json.DeserializeObject($Line)
            $script:integrationCalls += ,$request
            try {
                $value = & $script:mbdModule { param($Request) Invoke-Request $Request } $request
                if ($request['cmd'] -eq 'pulse' -and $script:integrationFault -ne 'unchanged') {
                    & $script:mbdModule { $script:mockR[54] = 65386; $script:mockR[55] = 65535 }
                }
                if ($request['cmd'] -eq 'write' -and $script:integrationFault -eq 'alarm_after_write') {
                    & $script:mbdModule { $script:mockR[782] = 19 }
                }
                ConvertTo-JsonLite @{ ok = $true; result = $value }
            } catch { ConvertTo-JsonLite @{ ok = $false; error = $_.Exception.Message } }
        }
        $script:integrationCommand = $script:Json.DeserializeObject((ConvertTo-JsonLite @{
            kind = 'length_correction'; blocks = @(@{ addr = 658; values = @(32000, 0) }); bits = @()
            seen = @{ '32' = 32500; '33' = 0; '54' = 100; '55' = 0 }; deadline_ms = 10000
        }))
    }
    It 'recovers a pre-existing high before D658 and then verifies a fresh pulse' {
        & $script:mbdModule { $script:mockR[600] = 66 }
        $result = Invoke-LengthCorrection $script:integrationCommand ([Diagnostics.Stopwatch]::StartNew())
        $result['error'] | Should Be ''
        $result['ok'] | Should Be $true
        $mutations = @($script:integrationCalls | Where-Object { $_['cmd'] -in @('recover_correction', 'write', 'pulse') })
        ($mutations | ForEach-Object { $_['cmd'] }) -join ',' | Should Be 'recover_correction,write,pulse'
        (& $script:mbdModule { $script:mockR[600] }) | Should Be 2
    }
    It 'refuses before D658 if recovery clearing fails' {
        & $script:mbdModule { $script:mockR[600] = 66; $script:failClear = $true }
        $result = Invoke-LengthCorrection $script:integrationCommand ([Diagnostics.Stopwatch]::StartNew())
        $result['ok'] | Should Be $false
        $result['outcome'] | Should Be 'failed'
        @($script:integrationCalls | Where-Object { $_['cmd'] -in @('write', 'pulse') }).Count | Should Be 0
    }
    It 'reports unknown if the real pulse cannot clear and never retries the pulse' {
        & $script:mbdModule { $script:failClear = $true }
        $result = Invoke-LengthCorrection $script:integrationCommand ([Diagnostics.Stopwatch]::StartNew())
        $result['ok'] | Should Be $false
        $result['correction_pulsed'] | Should Be $false
        $result['outcome'] | Should Be 'unknown'
        @($script:integrationCalls | Where-Object { $_['cmd'] -eq 'pulse' }).Count | Should Be 1
    }
    It 'does not report success for unchanged D54 after a verified real pulse' {
        $script:integrationFault = 'unchanged'
        $result = Invoke-LengthCorrection $script:integrationCommand ([Diagnostics.Stopwatch]::StartNew())
        $result['ok'] | Should Be $false
        $result['correction_pulsed'] | Should Be $true
        $result['outcome'] | Should Be 'unknown'
        $result['error'] | Should Be $script:CorrectionUnconfirmed
    }
    It 'rejects an initial alarm without correction writes' {
        & $script:mbdModule { $script:mockR[782] = 19 }
        $result = Invoke-LengthCorrection $script:integrationCommand ([Diagnostics.Stopwatch]::StartNew())
        $result['ok'] | Should Be $false
        $result['error'] | Should Be ($script:CorrectionAlarm -f 19)
        @($script:integrationCalls | Where-Object { $_['cmd'] -in @('write', 'pulse') }).Count | Should Be 0
    }
    It 'rejects an alarm appearing after D658 without pulsing' {
        $script:integrationFault = 'alarm_after_write'
        $result = Invoke-LengthCorrection $script:integrationCommand ([Diagnostics.Stopwatch]::StartNew())
        $result['ok'] | Should Be $false
        $result['error'] | Should Be ($script:CorrectionAlarm -f 19)
        @($script:integrationCalls | Where-Object { $_['cmd'] -eq 'pulse' }).Count | Should Be 0
    }
}

# All bus functions mocked. No serial port, listener or physical machine is used.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $root 'ob019-writer.ps1') -BridgeScript (Join-Path $root 'ob019-bridge.ps1')

function New-LengthCorrection {
    return $script:Json.DeserializeObject((ConvertTo-JsonLite @{
        kind = 'length_correction'; blocks = @(@{ addr = 658; values = @(32000, 0) }); bits = @()
        seen = @{ '32' = 32500; '33' = 0; '54' = 100; '55' = 0 }; deadline_ms = 10000
    }))
}

Describe 'PLC length correction' {
    BeforeEach {
        $script:corrCalls = @(); $script:corrFault = ''; $script:corrPulsed = $false; $script:corrStateReads = 0
        Mock Test-HmiOpen { $script:corrFault -eq 'hmi' }
        Mock Get-MbdVersion { if ($script:corrFault -eq 'old') { '1.5.0' } else { '1.5.1' } }
        Mock Read-MachineState {
            $script:corrStateReads++
            if ($script:corrFault -eq 'alarm' -or ($script:corrFault -eq 'alarm_after_write' -and $script:corrStateReads -eq 2)) { return @{ '766' = 102; '782' = 19 } }
            if ($script:corrFault -eq 'alarm_unreadable') { return @{ '766' = 102; '782' = $null } }
            if ($script:corrFault -eq 'running' -or ($script:corrFault -eq 'running_after_write' -and $script:corrStateReads -eq 2)) { @{ '766' = 103; '782' = 0 } }
            else { @{ '766' = 102; '782' = 0 } }
        }
        Mock Read-Range {
            if ($Address -eq 600) { return @(0) }
            if ($Address -eq 32) {
                if ($script:corrFault -eq 'length_changed') { return @(32600, 0) }
                return @(32500, 0)
            }
            if ($Address -eq 54) {
                if ($script:corrPulsed) {
                    if ($script:corrFault -eq 'unchanged') { return @(100, 0) }
                    if ($script:corrFault -eq 'read_failed') { throw 'read timeout' }
                    return @(65386, 65535)
                }
                if ($script:corrFault -eq 'correction_changed') { return @(200, 0) }
                return @(100, 0)
            }
            throw 'unexpected read'
        }
        Mock Invoke-MbdLine {
            $request = $script:Json.DeserializeObject($Line)
            $script:corrCalls += ,$request
            if ($request['cmd'] -eq 'write') {
                $read = @(32000, 0)
                if ($script:corrFault -eq 'write_mismatch') { $read = @(31999, 0) }
                return ConvertTo-JsonLite @{ ok = $true; result = @{ readback = $read } }
            }
            if ($request['cmd'] -eq 'pulse') {
                $script:corrPulsed = $true
                if ($script:corrFault -eq 'pulse_timeout') { throw 'pulse timeout' }
                $pulse = @{ addr = 600; bit = 6; set = $true; cleared = $true; before = 0; high = 64; after = 0 }
                if ($script:corrFault -eq 'preexisting_high') { $pulse['before'] = 64 }
                if ($script:corrFault -eq 'pulse_malformed') { $pulse.Remove('after') }
                if ($script:corrFault -eq 'pulse_not_cleared') { $pulse['cleared'] = $false }
                return ConvertTo-JsonLite @{ ok = $true; result = $pulse }
            }
            throw 'unexpected command'
        }
    }

    It 'writes only measured D658 then holds D600.6 and returns signed D54 words' {
        $result = Invoke-LengthCorrection (New-LengthCorrection) ([Diagnostics.Stopwatch]::StartNew())
        $result['error'] | Should Be ''
        $result['ok'] | Should Be $true
        $result['correction_pulsed'] | Should Be $true
        $result['readback']['54'] | Should Be 65386
        $result['readback']['55'] | Should Be 65535
        $result['before']['54'] | Should Be 100
        $script:corrCalls.Count | Should Be 2
        $script:corrCalls[0]['cmd'] | Should Be 'write'
        $script:corrCalls[0]['addr'] | Should Be 658
        $script:corrCalls[1]['cmd'] | Should Be 'pulse'
        $script:corrCalls[1]['addr'] | Should Be 600
        $script:corrCalls[1]['bit'] | Should Be 6
        $script:corrCalls[1]['hold_ms'] | Should Be $script:HoldMs
    }

    foreach ($fault in @('hmi', 'old', 'running', 'alarm', 'alarm_unreadable', 'length_changed', 'correction_changed')) {
        It ('refuses before any write: ' + $fault) {
            $script:corrFault = $fault
            $result = Invoke-LengthCorrection (New-LengthCorrection) ([Diagnostics.Stopwatch]::StartNew())
            $result['ok'] | Should Be $false
            $result['outcome'] | Should Be 'failed'
            $script:corrCalls.Count | Should Be 0
        }
    }
    foreach ($fault in @('write_mismatch', 'running_after_write', 'alarm_after_write')) {
        It ('does not pulse when measured write or final guards fail: ' + $fault) {
            $script:corrFault = $fault
            $result = Invoke-LengthCorrection (New-LengthCorrection) ([Diagnostics.Stopwatch]::StartNew())
            $result['ok'] | Should Be $false
            $script:corrPulsed | Should Be $false
        }
    }
    foreach ($fault in @('pulse_timeout', 'pulse_malformed', 'pulse_not_cleared', 'preexisting_high', 'read_failed', 'unchanged')) {
        It ('reports unknown without retry after ambiguous pulse: ' + $fault) {
            $script:corrFault = $fault
            $result = Invoke-LengthCorrection (New-LengthCorrection) ([Diagnostics.Stopwatch]::StartNew())
            $result['ok'] | Should Be $false
            $result['outcome'] | Should Be 'unknown'
            @($script:corrCalls | Where-Object { $_['cmd'] -eq 'pulse' }).Count | Should Be 1
        }
    }
    It 'refuses expired and malformed commands without bus writes' {
        $command = New-LengthCorrection
        $command['deadline_ms'] = 0
        (Invoke-LengthCorrection $command ([Diagnostics.Stopwatch]::StartNew()))['ok'] | Should Be $false
        $command = New-LengthCorrection
        $command['blocks'][0]['addr'] = 32
        (Invoke-LengthCorrection $command ([Diagnostics.Stopwatch]::StartNew()))['ok'] | Should Be $false
        $script:corrCalls.Count | Should Be 0
    }
}

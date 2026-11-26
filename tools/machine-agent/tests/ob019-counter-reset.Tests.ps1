# Pester 3.4+: kind reset_counters of the command runner (1.5.2). All bus functions mocked; no serial port, listener
# or physical machine is used.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $root 'ob019-writer.ps1') -BridgeScript (Join-Path $root 'ob019-bridge.ps1')

function New-CounterReset([switch]$WithJob) {
    $command = @{
        kind = 'reset_counters'; reset = @(@{ addr = 645; values = @(0, 0) }, @{ addr = 649; values = @(0, 0) })
        blocks = @(); bits = @(); states = @(100, 102, 106, 107); deadline_ms = 10000
        seen = @{ '645' = 6; '646' = 0; '649' = 2056; '650' = 0 }
    }
    if ($WithJob) {
        $command['blocks'] = @(@{ addr = 22; values = @(0, 0) }); $command['states'] = @(102, 107)
        $command['bits'] = @(@{ addr = 400; bit = 0; value = 0 })
    }
    return $script:Json.DeserializeObject((ConvertTo-JsonLite $command))
}

Describe 'counter reset' {
    BeforeEach {
        $script:resetCalls = @(); $script:resetFault = ''
        Mock Test-HmiOpen { $script:resetFault -eq 'hmi' }
        Mock Get-MbdVersion { if ($script:resetFault -eq 'old') { '1.5.1' } else { '1.5.2' } }
        # The same shape as the real Read-Range (ob019-bridge.ps1): `return ,@(values)`.
        Mock Read-Range {
            if ($Address -eq 766) { if ($script:resetFault -eq 'running') { return ,@(103) } else { return ,@(107) } }
            if ($Address -eq 645) { if ($script:resetFault -eq 'counted') { return ,@(7, 0, 1234, 1, 2057, 0) } else { return ,@(6, 0, 1234, 1, 2056, 0) } }
            throw 'unexpected read'
        }
        Mock Invoke-MbdLine {
            $request = $script:Json.DeserializeObject($Line)
            $script:resetCalls += ,$request
            if ($request['cmd'] -eq 'reset_counters') {
                if ($script:resetFault -eq 'timeout') { throw 'mbd does not answer' }
                if ($script:resetFault -eq 'mbd_state') { return ConvertTo-JsonLite @{ ok = $false; error = 'reset_counters refused: state 103' } }
                $after = @(0, 0, 1234, 1, 0, 0); $stateAfter = 107
                if ($script:resetFault -eq 'not_zero') { $after = @(0, 0, 1234, 1, 2056, 0) }
                if ($script:resetFault -eq 'started') { $stateAfter = 103 }
                return ConvertTo-JsonLite @{ ok = $true; result = @{ before = @(6, 0, 1234, 1, 2056, 0); readback = $after; state = 107; state_after = $stateAfter } }
            }
            if ($request['cmd'] -eq 'write') {
                if ($script:resetFault -eq 'write_failed') { return ConvertTo-JsonLite @{ ok = $false; error = 'no valid echo writing D22' } }
                return ConvertTo-JsonLite @{ ok = $true; result = @{ readback = $request['values'] } }
            }
            if ($request['cmd'] -eq 'setbit') { return ConvertTo-JsonLite @{ ok = $true; result = @{ readback = @(0) } } }
            throw 'unexpected command'
        }
    }

    It 'sends exactly zeros to D645 and D649 with the seen counters and reports D645..650 before and after' {
        $result = Invoke-CounterReset (New-CounterReset) ([Diagnostics.Stopwatch]::StartNew())
        $result['error'] | Should Be ''
        $result['ok'] | Should Be $true
        $script:resetCalls.Count | Should Be 1
        $script:resetCalls[0]['cmd'] | Should Be 'reset_counters'
        (ConvertTo-JsonLite $script:resetCalls[0]['blocks']) | Should Be '[{"addr":645,"values":[0,0]},{"addr":649,"values":[0,0]}]'
        (ConvertTo-JsonLite $script:resetCalls[0]['seen']) | Should Be '{"645":6,"646":0,"649":2056,"650":0}'
        $result['before']['649'] | Should Be 2056
        $result['before']['647'] | Should Be 1234
        $result['readback']['649'] | Should Be 0
        $result['readback']['647'] | Should Be 1234
    }
    It 'writes the new batch words and bits FIRST and zeroes the counters last' {
        $result = Invoke-CounterReset (New-CounterReset -WithJob) ([Diagnostics.Stopwatch]::StartNew())
        $result['ok'] | Should Be $true
        ($script:resetCalls | ForEach-Object { $_['cmd'] }) -join ',' | Should Be 'write,setbit,reset_counters'
        $result['readback']['22'] | Should Be 0
        $result['readback']['400'] | Should Be 0
    }
    It 'a failed batch write leaves the counters untouched and fails plainly' {
        $script:resetFault = 'write_failed'
        $result = Invoke-CounterReset (New-CounterReset -WithJob) ([Diagnostics.Stopwatch]::StartNew())
        $result['ok'] | Should Be $false
        $result['outcome'] | Should Be 'failed'
        @($script:resetCalls | Where-Object { $_['cmd'] -eq 'reset_counters' }).Count | Should Be 0
    }
    It 'refuses a switch bit outside the writable list without touching the bus' {
        $command = New-CounterReset -WithJob; $command['bits'][0]['addr'] = 600
        (Invoke-CounterReset $command ([Diagnostics.Stopwatch]::StartNew()))['error'] | Should Match 'not writable'
        $script:resetCalls.Count | Should Be 0
    }
    foreach ($fault in @('hmi', 'old', 'running', 'counted')) {
        It ('refuses before any bus write: ' + $fault) {
            $script:resetFault = $fault
            $result = Invoke-CounterReset (New-CounterReset -WithJob) ([Diagnostics.Stopwatch]::StartNew())
            $result['ok'] | Should Be $false
            $result['outcome'] | Should Be 'failed'
            $script:resetCalls.Count | Should Be 0
        }
    }
    It 'refuses a non-zero value or another register in the payload without touching the bus' {
        $command = New-CounterReset; $command['reset'][1]['values'][0] = 5
        (Invoke-CounterReset $command ([Diagnostics.Stopwatch]::StartNew()))['error'] | Should Be 'counter reset writes only 0'
        $command = New-CounterReset; $command['reset'][0]['addr'] = 647
        (Invoke-CounterReset $command ([Diagnostics.Stopwatch]::StartNew()))['ok'] | Should Be $false
        $command = New-CounterReset; $command['blocks'] = @(@{ addr = 649; values = @(0, 0) })
        (Invoke-CounterReset $command ([Diagnostics.Stopwatch]::StartNew()))['error'] | Should Match 'not writable'
        $command = New-CounterReset; $command['deadline_ms'] = 0
        (Invoke-CounterReset $command ([Diagnostics.Stopwatch]::StartNew()))['error'] | Should Be 'counter reset expired'
        $script:resetCalls.Count | Should Be 0
    }
    It 'a refusal by mbd is a plain failure, no answer is unknown, a non-zero read-back fails' {
        $script:resetFault = 'mbd_state'
        (Invoke-CounterReset (New-CounterReset) ([Diagnostics.Stopwatch]::StartNew()))['outcome'] | Should Be 'failed'
        $script:resetFault = 'timeout'
        (Invoke-CounterReset (New-CounterReset -WithJob) ([Diagnostics.Stopwatch]::StartNew()))['outcome'] | Should Be 'unknown'
        $script:resetFault = 'not_zero'
        $result = Invoke-CounterReset (New-CounterReset) ([Diagnostics.Stopwatch]::StartNew())
        $result['ok'] | Should Be $false
        $result['outcome'] | Should Be 'failed'
        $result['readback']['649'] | Should Be 2056
    }
    It 'reports unknown when the machine no longer stands after the zeroing' {
        $script:resetFault = 'started'
        $result = Invoke-CounterReset (New-CounterReset) ([Diagnostics.Stopwatch]::StartNew())
        $result['ok'] | Should Be $false
        $result['outcome'] | Should Be 'unknown'
        $result['error'] | Should Match 'not standing.*103'
        $result['readback']['649'] | Should Be 0
    }
}

Describe 'counter reset through the real Read-Range (only the mbd socket mocked)' {
    It 'reads six counter words and zeroes them' {
        $script:realCalls = @()
        Mock Test-HmiOpen { $false }
        Mock Invoke-MbdLine {
            $request = $script:Json.DeserializeObject($Line); $script:realCalls += [string]$request['cmd']
            switch ([string]$request['cmd']) {
                'version' { return '{"ok":true,"result":{"version":"1.5.2"}}' }
                'read' { if ($request['addr'] -eq 766) { return '{"ok":true,"result":{"values":[107]}}' } else { return '{"ok":true,"result":{"values":[6,0,1234,1,2056,0]}}' } }
                'reset_counters' { return '{"ok":true,"result":{"readback":[0,0,1234,1,0,0],"state":107,"state_after":107}}' }
            }
            throw 'unexpected command'
        }
        $result = Invoke-CounterReset (New-CounterReset) ([Diagnostics.Stopwatch]::StartNew())
        $result['error'] | Should Be ''
        $result['ok'] | Should Be $true
        $result['before']['649'] | Should Be 2056
        ($script:realCalls -join ',') | Should Be 'version,read,read,reset_counters'
    }
}

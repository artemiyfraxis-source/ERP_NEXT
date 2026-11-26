# Pester 3.4+: the Modbus service mbd.ps1 (dot-sourced with -Mock, no listener, no COM8): a switch bit is changed by
# read-modify-write without touching the other bits of its word, only allowed bits and words are written, the
# commands D599/D600, the HMI page D730, the date, passwords and SHA-256 never on request; "version" needs no bus.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('mbd-' + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
. (Join-Path $root 'mbd.ps1') -Mock -Dir $tmp

Describe 'setbit' {
    It 'changes only the asked bit of the word and reads it back' {
        $script:mockR[400] = 0x0A0A
        $result = Invoke-Request @{ cmd = 'setbit'; addr = 400; bit = 0; value = 1 }
        $result['before'] | Should Be 0x0A0A
        $result['written'] | Should Be 0x0A0B
        $result['readback'][0] | Should Be 0x0A0B
        $script:mockR[400] | Should Be 0x0A0B
        (Invoke-Request @{ cmd = 'setbit'; addr = 400; bit = 0; value = 0 })['readback'][0] | Should Be 0x0A0A
    }
    It 'refuses bits outside its allow-list and values other than 0/1' {
        foreach ($pair in @(@(599, 0), @(599, 2), @(600, 1), @(600, 4), @(730, 0), @(421, 0), @(390, 0), @(133, 0), @(400, 1), @(1682, 0), @(1203, 0))) {
            { Invoke-Request @{ cmd = 'setbit'; addr = $pair[0]; bit = $pair[1]; value = 1 } } | Should Throw 'setbit not allowed'
        }
        { Invoke-Request @{ cmd = 'setbit'; addr = 400; bit = 0; value = 2 } } | Should Throw '0 or 1'
    }
}

Describe 'write allow-list' {
    It 'refuses command bits, the HMI page, the date, passwords, SHA and whole switch words' {
        foreach ($a in @(599, 600, 730, 1087, 1088, 1089, 1203, 1211, 1682, 1684, 1686, 132, 400, 421, 422, 434, 60)) {
            { Invoke-Request @{ cmd = 'write'; addr = $a; values = @(1) } } | Should Throw 'write not allowed'
        }
    }
    It 'writes an allowed setting' {
        (Invoke-Request @{ cmd = 'write'; addr = 101; values = @(7) })['readback'][0] | Should Be 7
    }
}

Describe 'version' {
    It 'answers its version' {
        (Invoke-Request @{ cmd = 'version' })['version'] | Should Be '1.5.2'
    }
}

$script:Seen = @{ '645' = 6; '646' = 0; '649' = 2056; '650' = 0 }
Describe 'reset_counters (1.5.2)' {
    BeforeEach {
        $script:mockR = @{ 766 = 107; 645 = 6; 646 = 0; 647 = 1234; 648 = 1; 649 = 2056; 650 = 0 }
    }
    It 'writes exactly 0 to D645..646 and D649..650 and keeps the history D647..648' {
        $result = Invoke-Request @{ cmd = 'reset_counters'; seen = $script:Seen; blocks = @(@{ addr = 645; values = @(0, 0) }, @{ addr = 649; values = @(0, 0) }) }
        ($result['before'] -join ',') | Should Be '6,0,1234,1,2056,0'
        ($result['readback'] -join ',') | Should Be '0,0,1234,1,0,0'
        $result['state'] | Should Be 107
        $result['state_after'] | Should Be 107
        $script:mockR[647] | Should Be 1234
        $script:mockR[648] | Should Be 1
    }
    It 'refuses any non-zero value, other registers, odd word counts and repeats before touching the bus' {
        $bad = @(
            @(@{ addr = 649; values = @(1, 0) }), @(@{ addr = 649; values = @(0, 1) }), @(@{ addr = 645; values = @(0, 5) }),
            @(@{ addr = 647; values = @(0, 0) }), @(@{ addr = 22; values = @(0, 0) }), @(@{ addr = 650; values = @(0, 0) }),
            @(@{ addr = 649; values = @(0) }), @(@{ addr = 649; values = @(0, 0, 0) }), @(@{ addr = 649; values = @('0', 0) }),
            @(@{ addr = 649; values = @(0, 0) }, @{ addr = 649; values = @(0, 0) }), @()
        )
        foreach ($blocks in $bad) {
            { Invoke-Request @{ cmd = 'reset_counters'; seen = $script:Seen; blocks = $blocks } } | Should Throw 'reset_counters refused'
        }
        $script:mockR[649] | Should Be 2056
        $script:mockR[645] | Should Be 6
        $script:mockR.ContainsKey(730) | Should Be $false
    }
    It 'refuses while the machine runs or pauses' {
        foreach ($state in @(101, 103, 104, 105)) {
            $script:mockR[766] = $state
            { Invoke-Request @{ cmd = 'reset_counters'; seen = $script:Seen; blocks = @(@{ addr = 649; values = @(0, 0) }) } } | Should Throw ('state ' + $state)
            $script:mockR[649] | Should Be 2056
        }
    }
    It 'allows each stand-still state' {
        foreach ($state in @(100, 102, 106, 107)) {
            $script:mockR[766] = $state; $script:mockR[649] = 2056
            (Invoke-Request @{ cmd = 'reset_counters'; seen = $script:Seen; blocks = @(@{ addr = 649; values = @(0, 0) }) })['readback'][4] | Should Be 0
        }
    }
    It 'refuses inside the session when the counters moved since the confirmation, and without seen' {
        $script:mockR[649] = 2057
        { Invoke-Request @{ cmd = 'reset_counters'; seen = $script:Seen; blocks = @(@{ addr = 649; values = @(0, 0) }) } } | Should Throw 'seen value changed D649'
        $script:mockR[649] | Should Be 2057
        { Invoke-Request @{ cmd = 'reset_counters'; blocks = @(@{ addr = 649; values = @(0, 0) }) } } | Should Throw 'reset_counters refused: seen'
        $partial = @{ '645' = 6; '646' = 0; '649' = 2057 }
        { Invoke-Request @{ cmd = 'reset_counters'; seen = $partial; blocks = @(@{ addr = 649; values = @(0, 0) }) } } | Should Throw 'seen D650 missing'
        $script:mockR[649] | Should Be 2057
    }
    It 'keeps the counters out of the general write list' {
        foreach ($a in 645..650) {
            { Invoke-Request @{ cmd = 'write'; addr = $a; values = @(0) } } | Should Throw 'write not allowed'
        }
    }
}

$script:realWriteRegs = (Get-Command Write-Regs).ScriptBlock
Describe 'real correction pulse and restart recovery' {
    BeforeEach {
        $script:mockR = @{ 600 = 2 }
        $script:clearAttempts = 0
    }
    It 'observes low high low and preserves the other command bits' {
        $pulse = Invoke-Request @{ cmd = 'pulse'; addr = 600; bit = 6; hold_ms = 100 }
        $pulse['before'] | Should Be 2
        $pulse['high'] | Should Be 66
        $pulse['after'] | Should Be 2
        $pulse['set'] | Should Be $true
        $pulse['cleared'] | Should Be $true
    }
    It 'refuses a pre-existing high bit without treating it as a fresh pulse' {
        $script:mockR[600] = 66
        { Invoke-Pulse 600 6 100 } | Should Throw 'already high'
        $script:mockR[600] | Should Be 66
    }
    It 'fails if all three clear writes fail' {
        Mock Write-Regs {
            if ($a -eq 600 -and ($vals[0] -band 64) -eq 0) { $script:clearAttempts++; throw 'clear failed' }
            & $script:realWriteRegs $a $vals
        }
        { Invoke-Pulse 600 6 100 } | Should Throw 'clear readback not confirmed'
        $script:clearAttempts | Should Be 3
        $script:mockR[600] | Should Be 66
    }
    It 'does not accept successful echoes when the clear never reached the PLC' {
        Mock Write-Regs {
            if ($a -eq 600 -and ($vals[0] -band 64) -eq 0) { $script:clearAttempts++; return }
            & $script:realWriteRegs $a $vals
        }
        { Invoke-Pulse 600 6 100 } | Should Throw 'clear readback not confirmed'
        $script:clearAttempts | Should Be 3
    }
    It 'does not accept an unobserved high transition' {
        Mock Write-Regs {
            if ($a -eq 600 -and ($vals[0] -band 64) -ne 0) { return }
            & $script:realWriteRegs $a $vals
        }
        { Invoke-Pulse 600 6 100 } | Should Throw 'high readback not confirmed'
        $script:mockR[600] | Should Be 2
    }
    It 'clears a leftover high on service restart before opening the listener and logs recovery' {
        $script:mockR[600] = 66
        Mock New-Object { throw 'listener boundary' } -ParameterFilter { $TypeName -eq 'System.Net.Sockets.TcpListener' }
        { Start-Service2 } | Should Throw 'listener boundary'
        $script:mockR[600] | Should Be 2
        [IO.File]::ReadAllText($script:log) | Should Match 'RECOVERY D600.6 confirmed low'
    }
    It 'refuses startup when recovery cannot confirm low' {
        $script:mockR[600] = 66
        Mock Write-Regs { }
        Mock New-Object { throw 'listener must not open' } -ParameterFilter { $TypeName -eq 'System.Net.Sockets.TcpListener' }
        { Start-Service2 } | Should Throw 'recovery clear not confirmed'
        Assert-MockCalled New-Object -Times 0 -Exactly -Scope It -ParameterFilter { $TypeName -eq 'System.Net.Sockets.TcpListener' }
    }
    It 'does not open setbit access to the correction pulse bit' {
        { Invoke-Request @{ cmd = 'setbit'; addr = 600; bit = 6; value = 0 } } | Should Throw 'not allowed'
    }
}

$resolvedTmp = (Resolve-Path -LiteralPath $tmp).Path
if ((Split-Path -Parent $resolvedTmp) -eq ([IO.Path]::GetTempPath()).TrimEnd('\') -and (Split-Path -Leaf $resolvedTmp) -like 'mbd-*') {
    Remove-Item -LiteralPath $resolvedTmp -Recurse -Force
}

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
        (Invoke-Request @{ cmd = 'version' })['version'] | Should Be '1.5.1'
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

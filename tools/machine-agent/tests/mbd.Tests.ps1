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
        foreach ($a in @(599, 600, 730, 1087, 1088, 1089, 1203, 1211, 1682, 1684, 1686, 132, 400, 421, 422, 434, 44)) {
            { Invoke-Request @{ cmd = 'write'; addr = $a; values = @(1) } } | Should Throw 'write not allowed'
        }
    }
    It 'writes an allowed setting' {
        (Invoke-Request @{ cmd = 'write'; addr = 101; values = @(7) })['readback'][0] | Should Be 7
    }
}

Describe 'version' {
    It 'answers its version' {
        (Invoke-Request @{ cmd = 'version' })['version'] | Should Be '1.3.0'
    }
}

Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue

# Pester 3.4+: every agent log is capped (5 MB by default) and keeps 3 rotated files. mbd.ps1 starts its listener when
# loaded, so the function is taken from each script's source; all copies must be the same text.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here

function Get-RotationSource([string]$File) {
    $text = [IO.File]::ReadAllText((Join-Path $root $File)).Replace("`r`n", "`n")
    $start = $text.IndexOf('function Invoke-LogRotation(')
    if ($start -lt 0) { return '' }
    $end = $text.IndexOf("`n}`n", $start)
    return $text.Substring($start, $end - $start + 2)
}

Describe 'log rotation' {
    $sources = @{}
    foreach ($file in @('mbd.ps1', 'ob019-bridge.ps1', 'ob099-agent.ps1')) { $sources[$file] = Get-RotationSource $file }
    . ([scriptblock]::Create($sources['mbd.ps1']))
    $dir = Join-Path ([IO.Path]::GetTempPath()) ('rot-' + [guid]::NewGuid())
    New-Item -ItemType Directory $dir | Out-Null
    $log = Join-Path $dir 'mbd.log'

    It 'is the same function in mbd, bridge and OB.099 agent' {
        $sources['mbd.ps1'] | Should Not BeNullOrEmpty
        $sources['ob019-bridge.ps1'] | Should Be $sources['mbd.ps1']
        $sources['ob099-agent.ps1'] | Should Be $sources['mbd.ps1']
    }
    It 'defaults to 5 MB and 3 kept files' {
        $sources['mbd.ps1'] | Should Match '\[long\]\$MaxBytes = 5242880, \[int\]\$Keep = 3'
    }
    It 'leaves a small log alone' {
        [IO.File]::WriteAllText($log, 'abc')
        Invoke-LogRotation $log 10 3 | Should Be $false
        Test-Path ($log + '.1') | Should Be $false
    }
    It 'shifts .1 -> .2 -> .3 and drops the oldest' {
        foreach ($round in 1..5) {
            [IO.File]::WriteAllText($log, ('round' + $round + ' ' + ('x' * 20)))
            Invoke-LogRotation $log 10 3 | Should Be $true
        }
        Test-Path $log | Should Be $false
        [IO.File]::ReadAllText($log + '.1') | Should Match '^round5 '
        [IO.File]::ReadAllText($log + '.2') | Should Match '^round4 '
        [IO.File]::ReadAllText($log + '.3') | Should Match '^round3 '
        Test-Path ($log + '.4') | Should Be $false
    }
    It 'is used by every log writer' {
        [IO.File]::ReadAllText((Join-Path $root 'mbd.ps1')) | Should Match 'Invoke-LogRotation \$script:log'
        [IO.File]::ReadAllText((Join-Path $root 'ob019-bridge.ps1')) | Should Match 'Invoke-LogRotation \$LogPath'
        [IO.File]::ReadAllText((Join-Path $root 'ob099-agent.ps1')) | Should Match 'Invoke-LogRotation \$LogPath'
    }
    Remove-Item -Recurse -Force $dir
}

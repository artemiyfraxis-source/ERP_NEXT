# Pester 3.4+: the OB.099 agent task is registered from the OB.019 installer's XML: SYSTEM, hidden, at boot, every
# 5 minutes as a watchdog, no time limit; the token and the agent folder are closed to other users; BZW2.5 is not touched.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
. (Join-Path $root 'install-ob099.ps1') -Unit 1 -Dir 'D:\fraxis-ob099'

Describe 'OB.099 agent task' {
    It 'keeps its own parameters after loading the OB.019 installer' {
        $Dir | Should Be 'D:\fraxis-ob099'
        $Server | Should Be 'http://192.168.1.85'
        $TaskName | Should Be 'Fraxis OB099.1 agent'
    }
    $arguments = Get-Ob099Arguments 'D:\fraxis-ob099\ob099-agent.ps1' '1' 'http://192.168.1.85' 'D:\fraxis-ob099'
    It 'runs the agent hidden for its press with the token, state and log of its folder' {
        $arguments | Should Match '-WindowStyle Hidden'
        $arguments | Should Match '-NonInteractive'
        $arguments | Should Match '-File "D:\\fraxis-ob099\\ob099-agent\.ps1" -Unit 1 -Server http://192\.168\.1\.85 '
        $arguments | Should Match '-TokenPath "D:\\fraxis-ob099\\agent\.token"'
        $arguments | Should Match '-StatePath "D:\\fraxis-ob099\\state\.json"'
        $arguments | Should Match '-LogPath "D:\\fraxis-ob099\\agent\.log"$'
        $arguments -match '-Once' | Should Be $false
    }
    $xml = [xml](New-BridgeTaskXml 'powershell.exe' $arguments 'Fraxis ERP agent of OB.099.1')
    It 'is the SYSTEM boot task with a watchdog and no time limit' {
        $xml.Task.Principals.Principal.UserId | Should Be 'S-1-5-18'
        $xml.Task.Settings.ExecutionTimeLimit | Should Be 'PT0S'
        $xml.Task.Settings.MultipleInstancesPolicy | Should Be 'IgnoreNew'
        $xml.Task.Triggers.TimeTrigger.Repetition.Interval | Should Be 'PT5M'
        ($null -ne $xml.Task.Triggers.BootTrigger) | Should Be $true
        $xml.Task.Actions.Exec.Arguments | Should Be $arguments
    }
}

Describe 'installer source' {
    $source = Get-Content (Join-Path $root 'install-ob099.ps1') -Raw
    It 'stays ASCII' {
        [Text.Encoding]::ASCII.GetString([Text.Encoding]::ASCII.GetBytes($source)) | Should Be $source
    }
    It 'closes the token to SYSTEM (read) and Administrators, keeps an existing token without -TokenFile' {
        $source | Should Match "'\*S-1-5-18:\(R\)' '\*S-1-5-32-544:\(F\)'"
        $source | Should Match "elseif \(-not \(Test-Path \`$tokenPath\)\)"
    }
    It 'never stops or blocks on BZW2.5' {
        $source -match 'Get-BusyMachineSoftware|Stop-Process|Get-Process' | Should Be $false
    }
}

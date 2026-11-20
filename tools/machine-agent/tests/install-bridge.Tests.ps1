# Pester 3.4+: the OB.019 bridge task is registered from XML without the 72-hour execution limit of
# "schtasks /SC ONSTART", as SYSTEM, hidden, at boot, with restart on failure and a 5-minute watchdog.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
. (Join-Path $root 'install-bridge.ps1')

Describe 'bridge task XML' {
    $arguments = Get-BridgeArguments 'D:\fraxis-mb\ob019-bridge.ps1' 'http://192.168.1.85'
    $text = New-BridgeTaskXml 'powershell.exe' $arguments 'Fraxis ERP bridge of OB.019 <test> & co'
    $xml = [xml]$text
    $ns = New-Object System.Xml.XmlNamespaceManager($xml.NameTable)
    $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
    function Get-Node([string]$Path) { return $xml.SelectSingleNode($Path, $ns) }

    It 'has no execution time limit' {
        (Get-Node '/t:Task/t:Settings/t:ExecutionTimeLimit').InnerText | Should Be 'PT0S'
    }
    It 'runs as SYSTEM' {
        (Get-Node '/t:Task/t:Principals/t:Principal/t:UserId').InnerText | Should Be 'S-1-5-18'
        ($null -eq (Get-Node '/t:Task/t:Principals/t:Principal/t:LogonType')) | Should Be $true
    }
    It 'starts at boot and every 5 minutes as a watchdog, never twice' {
        ($null -ne (Get-Node '/t:Task/t:Triggers/t:BootTrigger')) | Should Be $true
        (Get-Node '/t:Task/t:Triggers/t:TimeTrigger/t:Repetition/t:Interval').InnerText | Should Be 'PT5M'
        (Get-Node '/t:Task/t:Settings/t:MultipleInstancesPolicy').InnerText | Should Be 'IgnoreNew'
    }
    It 'restarts after a failure' {
        (Get-Node '/t:Task/t:Settings/t:RestartOnFailure/t:Interval').InnerText | Should Be 'PT1M'
        (Get-Node '/t:Task/t:Settings/t:RestartOnFailure/t:Count').InnerText | Should Be '999'
    }
    It 'runs the bridge hidden against the given ERP server' {
        (Get-Node '/t:Task/t:Actions/t:Exec/t:Command').InnerText | Should Be 'powershell.exe'
        $actionArguments = (Get-Node '/t:Task/t:Actions/t:Exec/t:Arguments').InnerText
        $actionArguments | Should Match '-WindowStyle Hidden'
        $actionArguments | Should Match '-NonInteractive'
        $actionArguments | Should Match '-File "D:\\fraxis-mb\\ob019-bridge.ps1" -Server http://192.168.1.85$'
    }
    It 'escapes text for XML' {
        (Get-Node '/t:Task/t:RegistrationInfo/t:Description').InnerText | Should Be 'Fraxis ERP bridge of OB.019 <test> & co'
    }
}

Describe 'installer source' {
    $source = Get-Content (Join-Path $root 'install-bridge.ps1') -Raw
    It 'no longer registers with schtasks /SC (72-hour default limit)' {
        $source -match '/SC\s+ONSTART' | Should Be $false
        $source -match '/Create /F /TN \$Name /XML' | Should Be $true
    }
    It 'stays ASCII for PowerShell 2' {
        [Text.Encoding]::ASCII.GetString([Text.Encoding]::ASCII.GetBytes($source)) | Should Be $source
    }
}

Describe 'one installer for the three tasks' {
    It 'runs the writer like the bridge, hidden, against the ERP server' {
        $writer = Get-WriterArguments 'D:\fraxis-mb\ob019-writer.ps1' 'http://192.168.1.85'
        $writer | Should Match '-WindowStyle Hidden'
        $writer | Should Match '-File "D:\\fraxis-mb\\ob019-writer.ps1" -Server http://192.168.1.85$'
    }
    It 'runs mbd hidden with the allow list as one argument' {
        $mbd = Get-MbdArguments 'D:\fraxis-mb\mbd.ps1' '192.168.1.85,127.0.0.1'
        $mbd | Should Match '-WindowStyle Hidden'
        $mbd | Should Match '-File "D:\\fraxis-mb\\mbd.ps1" -Allow "192\.168\.1\.85,127\.0\.0\.1"$'
        $xml = [xml](New-BridgeTaskXml 'powershell.exe' $mbd 'mbd')
        $xml.Task.Actions.Exec.Arguments | Should Be $mbd
        $xml.Task.Settings.ExecutionTimeLimit | Should Be 'PT0S'
    }
    It 'refuses while the vendor HMI or the laser software runs' {
        Mock Get-Process { @([pscustomobject]@{ ProcessName = 'explorer' }, [pscustomobject]@{ ProcessName = 'PXS_HMI' }) }
        $busy = Get-BusyMachineSoftware
        $busy.Count | Should Be 1
        $busy[0] | Should Be 'PXS_HMI'
        Mock Get-Process { @([pscustomobject]@{ ProcessName = 'explorer' }) }
        (Get-BusyMachineSoftware).Count | Should Be 0
    }
    $source = Get-Content (Join-Path $root 'install-bridge.ps1') -Raw
    It 'registers the Modbus service, the bridge and the writer, and keeps an existing token without -Token' {
        $source | Should Match 'Register-BridgeTask \$MbdTaskName'
        $source | Should Match 'Register-BridgeTask \$TaskName'
        $source | Should Match 'Register-BridgeTask \$WriterTaskName'
        $source | Should Match "elseif \(-not \(Test-Path \`$tokenPath\)\)"
        $source | Should Match 'Get-BusyMachineSoftware'
    }
}

Describe 'operator panel helper tasks' {
    $arguments = Get-PultArguments 'D:\fraxis-mb\ob019-pult.ps1' 'close' 'D:\fraxis-mb'
    $xml = [xml](New-PultTaskXml 'powershell.exe' $arguments 'pult <close>')
    $ns = New-Object System.Xml.XmlNamespaceManager($xml.NameTable)
    $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
    function Get-Node([string]$Path) { return $xml.SelectSingleNode($Path, $ns) }
    It 'runs in the session of the interactive user, not as SYSTEM, only on demand' {
        (Get-Node '/t:Task/t:Principals/t:Principal/t:GroupId').InnerText | Should Be 'S-1-5-4'
        ($null -eq (Get-Node '/t:Task/t:Principals/t:Principal/t:UserId')) | Should Be $true
        (Get-Node '/t:Task/t:Principals/t:Principal/t:RunLevel').InnerText | Should Be 'LeastPrivilege'
        ($null -eq (Get-Node '/t:Task/t:Triggers')) | Should Be $true
        (Get-Node '/t:Task/t:Settings/t:AllowStartOnDemand').InnerText | Should Be 'true'
        (Get-Node '/t:Task/t:Settings/t:ExecutionTimeLimit').InnerText | Should Be 'PT2M'
    }
    It 'runs the helper hidden with its action and folder' {
        (Get-Node '/t:Task/t:Actions/t:Exec/t:Arguments').InnerText | Should Match '-WindowStyle Hidden -File "D:\\fraxis-mb\\ob019-pult\.ps1" -Action close -Dir "D:\\fraxis-mb"$'
    }
    $source = Get-Content (Join-Path $root 'install-bridge.ps1') -Raw
    It 'registers both helper tasks with the writer and copies the helper' {
        $source | Should Match 'Register-BridgeTask \$PultCloseTaskName'
        $source | Should Match 'Register-BridgeTask \$PultOpenTaskName'
        $source | Should Match 'Copy-Item -Force \(Join-Path \$here ''ob019-pult\.ps1''\)'
    }
}

Describe 'operator panel helper' {
    . (Join-Path $root 'ob019-pult.ps1') -Dir ([IO.Path]::GetTempPath())
    It 'stays ASCII for PowerShell 2 and knows the vendor path' {
        $source = Get-Content (Join-Path $root 'ob019-pult.ps1') -Raw
        [Text.Encoding]::ASCII.GetString([Text.Encoding]::ASCII.GetBytes($source)) | Should Be $source
        $script:DefaultHmiPath | Should Match '^E:\\DY\\.+_V1\.00_new\\binexe\\PXS_HMI\.exe$'
        $script:DefaultHmiPath.Substring(6, 6) | Should Be ([string][char]0x53EF + [char]0x7F16 + [char]0x7A0B + [char]0x4E0A + [char]0x4F4D + [char]0x673A)
    }
    It 'sends WM_CLOSE to every panel window and starts the panel in its own folder' {
        $script:closed = 0
        Mock Get-Process { @(New-Object psobject -Property @{ ProcessName = 'PXS_HMI' } | Add-Member -PassThru ScriptMethod CloseMainWindow { $script:closed++; $true }) }
        Close-Hmi | Should Be 1
        Mock Get-Process { @() }
        Mock Test-Path { $true }
        Mock Start-Process { $script:started = $FilePath + '|' + $WorkingDirectory }
        $HmiPath = 'E:\DY\x\binexe\PXS_HMI.exe'
        Open-Hmi | Should Be 'started'
        $script:started | Should Be 'E:\DY\x\binexe\PXS_HMI.exe|E:\DY\x\binexe'
    }
}

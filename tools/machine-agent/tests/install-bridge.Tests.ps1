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

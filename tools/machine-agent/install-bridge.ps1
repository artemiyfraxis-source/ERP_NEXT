param(
    [string]$Token = '',
    [string]$Server = 'http://192.168.1.85',
    [string]$Dir = 'D:\fraxis-mb',
    [string]$TaskName = 'Fraxis OB019 bridge'
)
# Installs the read-only ERP bridge of OB.019 on the machine PC (Windows 7, PowerShell 2.0); run as Administrator.
# Copies ob019-bridge.ps1 next to mbd.ps1, stores the API token (scope machine:report) readable by SYSTEM and
# Administrators only, and registers a task that starts the bridge at boot (E-stop powers this PC off) and keeps it
# running. mbd.ps1 must accept the bridge on 127.0.0.1: run it with -Allow '192.168.1.85,127.0.0.1' (keeps the ERP
# server's client) or -LocalOnly (the bridge only). The machine PC of OB.019 is 192.168.1.71 since 18.11.2026.
#
# The task is registered from XML, not with a schtasks ONSTART schedule (/SC): that form keeps the default 72-hour execution
# limit, so Windows would stop the bridge three days after boot (found on OB.019, 19.11.2026). The XML task runs as
# SYSTEM (session 0, nothing on the operator's screen), hidden, without a time limit, restarts after a failure and is
# re-run every 5 minutes as a watchdog (a second copy never starts: MultipleInstancesPolicy IgnoreNew).
# Pass the token through a file the caller deletes afterwards (-Token (Get-Content file)): never type it on a command
# line that is logged.
$ErrorActionPreference = 'Stop'

function ConvertTo-XmlText([string]$Text) { return [System.Security.SecurityElement]::Escape($Text) }

# Task Scheduler 1.2 XML (Windows 7+): boot trigger + 5-minute watchdog, SYSTEM, no time limit, restart on failure.
function New-BridgeTaskXml([string]$Command, [string]$Arguments, [string]$Description, [string]$BootDelay = 'PT45S') {
    $start = (Get-Date).Date.ToString('yyyy-MM-ddTHH:mm:ss')
    return @(
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
        ('  <RegistrationInfo><Description>' + (ConvertTo-XmlText $Description) + '</Description></RegistrationInfo>')
        '  <Triggers>'
        ('    <BootTrigger><Enabled>true</Enabled><Delay>' + $BootDelay + '</Delay></BootTrigger>')
        ('    <TimeTrigger><Enabled>true</Enabled><StartBoundary>' + $start + '</StartBoundary>' +
            '<Repetition><Interval>PT5M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition></TimeTrigger>')
        '  </Triggers>'
        '  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>'
        '  <Settings>'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>'
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>'
        '    <AllowHardTerminate>true</AllowHardTerminate>'
        '    <StartWhenAvailable>true</StartWhenAvailable>'
        '    <AllowStartOnDemand>true</AllowStartOnDemand>'
        '    <Enabled>true</Enabled>'
        '    <Hidden>false</Hidden>'
        '    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>'
        '    <Priority>7</Priority>'
        '    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>'
        '  </Settings>'
        ('  <Actions Context="Author"><Exec><Command>' + (ConvertTo-XmlText $Command) + '</Command><Arguments>' +
            (ConvertTo-XmlText $Arguments) + '</Arguments></Exec></Actions>')
        '</Task>'
    ) -join "`r`n"
}

function Get-BridgeArguments([string]$BridgePath, [string]$ServerUrl) {
    return '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $BridgePath + '" -Server ' + $ServerUrl
}

function Register-BridgeTask([string]$Name, [string]$Xml) {
    $xmlPath = Join-Path ([IO.Path]::GetTempPath()) ('fraxis-task-' + [guid]::NewGuid().ToString() + '.xml')
    try {
        [IO.File]::WriteAllText($xmlPath, $Xml, [Text.Encoding]::Unicode)
        & schtasks.exe /Create /F /TN $Name /XML $xmlPath | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'schtasks /Create failed' }
    } finally {
        Remove-Item -Force $xmlPath -ErrorAction SilentlyContinue
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    if (-not $Token.Trim()) { throw 'pass -Token (API token with scope machine:report)' }
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir | Out-Null }
    $bridgePath = Join-Path $Dir 'ob019-bridge.ps1'
    Copy-Item -Force (Join-Path $here 'ob019-bridge.ps1') $bridgePath
    $tokenPath = Join-Path $Dir 'bridge.token'
    [IO.File]::WriteAllText($tokenPath, $Token.Trim())
    # SIDs, not names: the Windows language of this PC may be zh-CN (SYSTEM = S-1-5-18, Administrators = S-1-5-32-544).
    & icacls.exe $tokenPath /inheritance:r /grant:r '*S-1-5-18:(R)' '*S-1-5-32-544:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'icacls failed on the token file' }
    $xml = New-BridgeTaskXml 'powershell.exe' (Get-BridgeArguments $bridgePath $Server) `
        ('Fraxis ERP bridge of OB.019: read-only, ' + $bridgePath + ' to ERP ' + $Server)
    # A running bridge keeps the old token: stop it before the new task starts it again.
    & cmd.exe /c ('schtasks /End /TN "' + $TaskName + '" >nul 2>&1') | Out-Null
    Register-BridgeTask $TaskName $xml
    & schtasks.exe /Run /TN $TaskName | Out-Null
    Write-Output ('installed: ' + $TaskName + ' -> ' + $Server)
}

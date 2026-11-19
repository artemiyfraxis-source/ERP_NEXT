param(
    [string]$Token = '',
    [string]$Server = 'http://192.168.1.85',
    [string]$Dir = 'D:\fraxis-mb',
    [string]$TaskName = 'Fraxis OB019 bridge',
    [string]$WriterTaskName = 'Fraxis OB019 writer',
    [string]$MbdTaskName = 'Fraxis Modbus',
    [string]$MbdAllow = '192.168.1.85,127.0.0.1',
    [switch]$NoWriter,
    [switch]$NoMbd
)
# Installs or updates the OB.019 agent on the machine PC (Windows 7, PowerShell 2.0); run as Administrator. One run
# puts the scripts of this folder into $Dir and registers three tasks:
#   - $MbdTaskName: the Modbus service mbd.ps1, the only owner of COM8, with -Allow $MbdAllow (the ERP server's
#     client and the local bridge/writer on 127.0.0.1); skipped with -NoMbd;
#   - $TaskName: the read-only ERP bridge ob019-bridge.ps1;
#   - $WriterTaskName: the ERP command runner ob019-writer.ps1 (writes and machine commands ERP asks for, same token
#     and PC binding as the bridge); skipped with -NoWriter.
# The API token (scope machine:report) is stored in $Dir\bridge.token, readable by SYSTEM and Administrators only.
# Without -Token an existing token file is kept as it is (update without touching the token). Refuses to run while
# the vendor HMI PXS_HMI, the laser bridge or EZCAD runs (people are working on the machine).
# The machine PC of OB.019 is 192.168.1.71 since 18.11.2026.
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

# The writer takes the same arguments as the bridge (it dot-sources the bridge next to it).
function Get-WriterArguments([string]$WriterPath, [string]$ServerUrl) { return (Get-BridgeArguments $WriterPath $ServerUrl) }

function Get-MbdArguments([string]$MbdPath, [string]$Allow) {
    return '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $MbdPath + '" -Allow "' + $Allow + '"'
}

# Programs that hold COM8 or mean an operator works on the machine right now.
function Get-BusyMachineSoftware {
    $busy = @(Get-Process | Where-Object { $_.ProcessName -like 'PXS_HMI*' -or $_.ProcessName -like '*modbus*' -or
        $_.ProcessName -like '*auto mark*' -or $_.ProcessName -like 'EzCad*' } | ForEach-Object { $_.ProcessName })
    return ,$busy
}

function Stop-AgentTask([string]$Name) { & cmd.exe /c ('schtasks /End /TN "' + $Name + '" >nul 2>&1') | Out-Null }

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
    $busy = Get-BusyMachineSoftware
    if ($busy.Count -gt 0) { throw ('refused: ' + ($busy -join ', ') + ' is running (people work on the machine)') }
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir | Out-Null }
    $tokenPath = Join-Path $Dir 'bridge.token'
    if ($Token.Trim()) {
        [IO.File]::WriteAllText($tokenPath, $Token.Trim())
        # SIDs, not names: the Windows language of this PC may be zh-CN (SYSTEM = S-1-5-18, Administrators = S-1-5-32-544).
        & icacls.exe $tokenPath /inheritance:r /grant:r '*S-1-5-18:(R)' '*S-1-5-32-544:(F)' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'icacls failed on the token file' }
    } elseif (-not (Test-Path $tokenPath)) {
        throw 'pass -Token (API token with scope machine:report): there is no token file yet'
    }
    # Running copies keep the old scripts and token: stop them before the new tasks start them again.
    Stop-AgentTask $WriterTaskName
    Stop-AgentTask $TaskName
    if (-not $NoMbd) { Stop-AgentTask $MbdTaskName }
    Start-Sleep -Seconds 2
    $bridgePath = Join-Path $Dir 'ob019-bridge.ps1'
    Copy-Item -Force (Join-Path $here 'ob019-bridge.ps1') $bridgePath
    if (-not $NoMbd) {
        $mbdPath = Join-Path $Dir 'mbd.ps1'
        Copy-Item -Force (Join-Path $here 'mbd.ps1') $mbdPath
        Register-BridgeTask $MbdTaskName (New-BridgeTaskXml 'powershell.exe' (Get-MbdArguments $mbdPath $MbdAllow) `
            ('Fraxis Modbus service of OB.019: ' + $mbdPath + ', COM8, TCP 5020 from ' + $MbdAllow) 'PT30S')
        & schtasks.exe /Run /TN $MbdTaskName | Out-Null
        Start-Sleep -Seconds 3
    }
    Register-BridgeTask $TaskName (New-BridgeTaskXml 'powershell.exe' (Get-BridgeArguments $bridgePath $Server) `
        ('Fraxis ERP bridge of OB.019: read-only, ' + $bridgePath + ' to ERP ' + $Server) 'PT45S')
    & schtasks.exe /Run /TN $TaskName | Out-Null
    if (-not $NoWriter) {
        $writerPath = Join-Path $Dir 'ob019-writer.ps1'
        Copy-Item -Force (Join-Path $here 'ob019-writer.ps1') $writerPath
        Register-BridgeTask $WriterTaskName (New-BridgeTaskXml 'powershell.exe' (Get-WriterArguments $writerPath $Server) `
            ('Fraxis ERP command runner of OB.019: ' + $writerPath + ' from ERP ' + $Server) 'PT60S')
        & schtasks.exe /Run /TN $WriterTaskName | Out-Null
    }
    $names = @()
    if (-not $NoMbd) { $names += $MbdTaskName }
    $names += $TaskName
    if (-not $NoWriter) { $names += $WriterTaskName }
    Write-Output ('installed: ' + ($names -join ', ') + ' -> ' + $Server)
}

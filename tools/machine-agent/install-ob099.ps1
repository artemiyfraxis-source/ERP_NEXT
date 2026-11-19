param(
    [string]$Unit = '',
    [string]$TokenFile = '',
    [string]$Server = 'http://192.168.1.85',
    [string]$Dir = 'D:\fraxis-ob099',
    [string]$TaskName = ''
)
# Installs or updates the read-only ERP agent of an OB.099 crimp press (ob099-agent.ps1) on the press PC (Windows 10,
# PowerShell 5.1); run as Administrator. OB.099.1 is the PC DESKTOP-K6M7O0U, 192.168.1.82 (19.11.2026).
#   powershell -ExecutionPolicy Bypass -File install-ob099.ps1 -Unit 1 -TokenFile <file with the API token>
# One run puts ob099-agent.ps1 into $Dir and registers one task, "Fraxis OB099.<unit> agent", from the same Task
# Scheduler XML as the OB.019 bridge (install-bridge.ps1): SYSTEM (session 0, no window on the operator's screen),
# at boot, re-run every 5 minutes as a watchdog (a second copy never starts), no execution time limit, restart after
# a failure. The agent writes its own rotated log $Dir\agent.log.
# The API token (scope machine:report) goes into $Dir\agent.token, readable by SYSTEM and Administrators only; $Dir
# itself is closed to other users (the state and the log stay there). Without -TokenFile an existing token file is kept
# (update without touching the token). The caller deletes the -TokenFile source; never put the token on a command line.
# BZW2.5 keeps running: the agent only reads the PLC (FINS), the BZW2.5 log and MySQL (SELECT), so nothing is refused
# while people work on the press.
$ErrorActionPreference = 'Stop'

if (-not $TaskName) { $TaskName = 'Fraxis OB099.' + $Unit + ' agent' }
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
# Read before the dot-source below: it runs in this scope and may replace $MyInvocation.
$invokedAs = $MyInvocation.InvocationName
# The task XML and registration of the OB.019 installer; the same values passed so its parameters do not replace ours.
. (Join-Path $here 'install-bridge.ps1') -Server $Server -Dir $Dir -TaskName $TaskName

function Get-Ob099Arguments([string]$AgentPath, [string]$UnitNo, [string]$ServerUrl, [string]$Folder) {
    $base = $Folder.TrimEnd('\') + '\'
    return '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $AgentPath + '" -Unit ' +
        $UnitNo + ' -Server ' + $ServerUrl + ' -TokenPath "' + $base + 'agent.token" -StatePath "' + $base +
        'state.json" -LogPath "' + $base + 'agent.log"'
}

# SIDs, not names: SYSTEM = S-1-5-18, Administrators = S-1-5-32-544 whatever the Windows language.
function Set-AgentFolderAcl([string]$Folder) {
    & icacls.exe $Folder /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw ('icacls failed on ' + $Folder) }
}

function Set-TokenFile([string]$Source, [string]$Target) {
    $value = ([IO.File]::ReadAllText($Source)).Trim()
    if (-not $value) { throw ('empty token file ' + $Source) }
    [IO.File]::WriteAllText($Target, $value)
    & icacls.exe $Target /inheritance:r /grant:r '*S-1-5-18:(R)' '*S-1-5-32-544:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'icacls failed on the token file' }
}

if ($invokedAs -ne '.') {
    if ($Unit -ne '1' -and $Unit -ne '2') { throw 'set -Unit 1 or 2 (OB.099.1 / OB.099.2 in the ERP equipment register)' }
    if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir | Out-Null }
    Set-AgentFolderAcl $Dir
    $tokenPath = Join-Path $Dir 'agent.token'
    if ($TokenFile) { Set-TokenFile $TokenFile $tokenPath }
    elseif (-not (Test-Path $tokenPath)) { throw 'pass -TokenFile (API token with scope machine:report): there is no token file yet' }
    # A running copy keeps the old script: stop it before the new task starts it again.
    Stop-AgentTask $TaskName
    Start-Sleep -Seconds 2
    $agentPath = Join-Path $Dir 'ob099-agent.ps1'
    Copy-Item -Force (Join-Path $here 'ob099-agent.ps1') $agentPath
    Register-BridgeTask $TaskName (New-BridgeTaskXml 'powershell.exe' (Get-Ob099Arguments $agentPath $Unit $Server $Dir) `
        ('Fraxis ERP agent of OB.099.' + $Unit + ': read-only, ' + $agentPath + ' to ERP ' + $Server) 'PT60S')
    & schtasks.exe /Run /TN $TaskName | Out-Null
    Write-Output ('installed: ' + $TaskName + ' -> ' + $Server)
}

param(
    [Parameter(Mandatory = $true)][string]$Token,
    [string]$Server = 'http://192.168.1.85',
    [string]$Dir = 'D:\fraxis-mb',
    [string]$TaskName = 'Fraxis OB019 bridge'
)
# Installs the read-only ERP bridge of OB.019 on the machine PC (Windows 7, PowerShell 2.0); run as Administrator.
# Copies ob019-bridge.ps1 next to mbd.ps1, stores the API token (scope machine:report) readable by SYSTEM and
# Administrators only, and registers a task that starts the bridge at boot (E-stop powers this PC off) and keeps it
# running. mbd.ps1 must accept the bridge on 127.0.0.1: run it with -Allow '192.168.1.85,127.0.0.1' (keeps the ERP
# server's client) or -LocalOnly (the bridge only). The machine PC of OB.019 is 192.168.1.71 since 18.11.2026.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir | Out-Null }
Copy-Item -Force (Join-Path $here 'ob019-bridge.ps1') (Join-Path $Dir 'ob019-bridge.ps1')
$tokenPath = Join-Path $Dir 'bridge.token'
[IO.File]::WriteAllText($tokenPath, $Token.Trim())
# SIDs, not names: the Windows language of this PC may be zh-CN (SYSTEM = S-1-5-18, Administrators = S-1-5-32-544).
& icacls.exe $tokenPath /inheritance:r /grant:r '*S-1-5-18:(R)' '*S-1-5-32-544:(F)' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'icacls failed on the token file' }
$command = 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
    (Join-Path $Dir 'ob019-bridge.ps1') + '" -Server ' + $Server
& schtasks.exe /Create /F /TN $TaskName /SC ONSTART /RU SYSTEM /RL HIGHEST /TR $command | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'schtasks /Create failed' }
& schtasks.exe /Run /TN $TaskName | Out-Null
Write-Output ('installed: ' + $TaskName + ' -> ' + $Server)

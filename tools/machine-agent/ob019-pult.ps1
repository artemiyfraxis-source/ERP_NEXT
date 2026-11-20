param(
    [string]$Action = 'close',
    [string]$Dir = 'D:\fraxis-mb',
    [string]$HmiPath = ''
)
# Helper of the OB.019 command runner for the vendor operator panel PXS_HMI (ERP_NEXT, runner 1.4.0). PXS_HMI runs on
# the operator's desktop; the runner is SYSTEM in session 0 and cannot close it gracefully nor start it there. This
# script runs IN the operator's session: install-bridge.ps1 registers two on-demand tasks with the INTERACTIVE group as
# principal ("Fraxis OB019 pult close" -Action close, "Fraxis OB019 pult open" -Action open), the runner starts them
# with schtasks /Run.
#   close: WM_CLOSE to every PXS_HMI window (CloseMainWindow) - the panel closes as if the operator pressed its X. The
#          runner checks the result and forces the close itself only when the machine stands (ob019-writer.ps1).
#   open:  starts PXS_HMI.exe in its own folder (the panel reads its ini files from there). The path the runner saw
#          last ($Dir\pult.path) wins over the default install path.
# Windows 7, PowerShell 2.0, ASCII file: the default path is built from code points.
$ErrorActionPreference = 'Stop'
$script:Self = $MyInvocation.InvocationName
# E:\DY\<kebiancheng shangweiji>_V1.00_new\binexe\PXS_HMI.exe (programmable host computer, the vendor's folder name).
$script:DefaultHmiPath = 'E:\DY\' + [string][char]0x53EF + [char]0x7F16 + [char]0x7A0B + [char]0x4E0A + [char]0x4F4D +
    [char]0x673A + '_V1.00_new\binexe\PXS_HMI.exe'

function Write-PultLog([string]$Text) {
    try { Add-Content -Path (Join-Path $Dir 'pult.log') -Value ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $Text) -Encoding UTF8 } catch { }
}

function Get-HmiPath {
    if ($HmiPath) { return $HmiPath }
    $saved = Join-Path $Dir 'pult.path'
    try {
        if (Test-Path -LiteralPath $saved) {
            $path = ([IO.File]::ReadAllText($saved, [Text.Encoding]::UTF8)).Trim()
            if ($path -and (Test-Path -LiteralPath $path)) { return $path }
        }
    } catch { }
    return $script:DefaultHmiPath
}

function Close-Hmi {
    $count = 0
    foreach ($process in @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'PXS_HMI*' })) {
        if ($process.CloseMainWindow()) { $count++ }
    }
    return $count
}

function Open-Hmi {
    if (@(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'PXS_HMI*' }).Count -gt 0) { return 'running' }
    $path = Get-HmiPath
    if (-not (Test-Path -LiteralPath $path)) { throw ('PXS_HMI.exe not found: ' + $path) }
    Start-Process -FilePath $path -WorkingDirectory (Split-Path -Parent $path) | Out-Null
    return 'started'
}

if ($script:Self -ne '.') {
    try {
        if ($Action -eq 'open') { Write-PultLog ('open: ' + (Open-Hmi)) }
        elseif ($Action -eq 'close') { Write-PultLog ('close: WM_CLOSE sent to ' + (Close-Hmi) + ' window(s)') }
        else { Write-PultLog ('unknown action ' + $Action) }
    } catch {
        Write-PultLog ('ERROR ' + $Action + ': ' + $_.Exception.Message)
    }
}

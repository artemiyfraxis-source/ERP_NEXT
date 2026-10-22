<#
 Установка агента печати этикеток. Запускать в PowerShell на ПК склада (под тем пользователем, который работает за ПК).
   .\install.ps1 -Token <токен ERP со scope labels:print>
 Удаление: .\install.ps1 -Uninstall   (или uninstall.ps1)
#>
[CmdletBinding()]
param(
    [string]$Server = 'https://erp-server.tail811f20.ts.net',
    [string]$Token,
    [string]$Printer,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$TaskName = 'Fraxis — печать этикеток'
$Dir = Join-Path $env:ProgramData 'FraxisPrintAgent'

function Remove-Agent {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    # на случай, если процесс агента ещё жив
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like '*FraxisPrintAgent*agent.ps1*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    if (Test-Path $Dir) { Remove-Item $Dir -Recurse -Force }
    Write-Host 'Агент печати удалён: задача и папка убраны.'
}

if ($Uninstall) { Remove-Agent; return }

if (-not $Token) { throw 'Нужен -Token (токен ERP со scope labels:print, создаёт администратор в ERP).' }

$printers = @(Get-Printer | Select-Object -ExpandProperty Name)
if (-not $Printer) {
    $Printer = $printers | Where-Object { $_ -like '*TSC*' } | Select-Object -First 1
    if (-not $Printer) {
        throw ("Принтер с 'TSC' в названии не найден. Установленные: " + ($printers -join '; ') + ". Укажите -Printer '<имя очереди>'.")
    }
} elseif ($printers -notcontains $Printer) {
    throw ("Очереди '$Printer' нет. Установленные: " + ($printers -join '; '))
}

New-Item -ItemType Directory -Path $Dir -Force | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'agent.ps1') (Join-Path $Dir 'agent.ps1') -Force

# Токен защищён DPAPI: читается только этим пользователем Windows на этом ПК.
$secure = ConvertTo-SecureString -String $Token -AsPlainText -Force
$cfg = [ordered]@{
    server         = $Server.TrimEnd('/')
    printer        = $Printer
    tokenProtected = (ConvertFrom-SecureString $secure)
}
$cfgPath = Join-Path $Dir 'config.json'
[IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))

# Доступ к папке: только SYSTEM, администраторы и текущий пользователь.
$me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $Dir /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "BUILTIN\Administrators:(OI)(CI)F" "${me}:(OI)(CI)F" | Out-Null

$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskArgs = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $Dir 'agent.ps1') + '"'
$action = New-ScheduledTaskAction -Execute $ps -Argument $taskArgs -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host ''
Write-Host 'Агент печати этикеток установлен.'
Write-Host "  Сервер:   $($cfg.server)"
Write-Host "  Принтер:  $Printer"
Write-Host "  Задача:   $TaskName (запуск при входе в Windows, уже запущена)"
Write-Host "  Журнал:   $Dir\agent.log"
Write-Host 'Токен сохранён в зашифрованном виде и в журнал не пишется.'

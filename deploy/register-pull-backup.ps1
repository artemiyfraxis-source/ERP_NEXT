<#
Регистрирует в Планировщике Windows задачу «ERP — копия базы с сервера» для текущего пользователя:
каждый день в 10:00 запускает pull-backup.ps1 скрытым окном; если ПК в это время был выключен — запустит при включении.
Повторный запуск заменяет задачу. Запуск: powershell -NoProfile -ExecutionPolicy Bypass -File deploy\register-pull-backup.ps1
Параметры -Server/-User/-Key/-Dest/-KeepDaily передаются в pull-backup.ps1, если заданы.
#>
param(
    [string]$Server,
    [string]$User,
    [string]$Key,
    [string]$Dest,
    [int]$KeepDaily = 0,
    [string]$At = '10:00'
)

$ErrorActionPreference = 'Stop'
$taskName = 'ERP ' + [char]0x2014 + ' копия базы с сервера'
$script = Join-Path $PSScriptRoot 'pull-backup.ps1'
if (-not (Test-Path $script)) { throw "не найден $script" }

$argList = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $script + '"'
foreach ($p in 'Server', 'User', 'Key', 'Dest') {
    $v = Get-Variable -Name $p -ValueOnly
    if ($v) { $argList += " -$p `"$v`"" }
}
if ($KeepDaily -gt 0) { $argList += " -KeepDaily $KeepDaily" }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argList
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description 'Забирает с сервера ERP свежую копию базы (deploy/pull-backup.ps1)' | Out-Null
Write-Host "Задача «$taskName» зарегистрирована: каждый день в $At."

<#
 Установка агента печати этикеток. Запускать в PowerShell на ПК склада (под тем пользователем, который работает за ПК).
   .\install.ps1 -Token <токен ERP со scopes labels:print и scan:upload> [-Scanner <IP сканера>]
 Удаление: .\install.ps1 -Uninstall   (или uninstall.ps1)
#>
# No positional binding: a stray word after the token (a pasted «14:20») is an error, not the server address.
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Server = 'https://erp-server.tail811f20.ts.net',
    [string]$Token,
    [string]$Printer,
    [string]$Scanner,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$TaskName = 'Fraxis — печать этикеток'
$Dir = Join-Path $env:ProgramData 'FraxisPrintAgent'

function Get-AgentProcess {
    @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like '*FraxisPrintAgent*agent.ps1*' })
}

# Останавливает уже запущенный агент: задачу и сам процесс (Register-ScheduledTask -Force не трогает живой процесс,
# он остался бы со старым токеном). Ждёт, пока процессы выйдут, не дольше $WaitSeconds. Возвращает $true, если что-то остановили.
function Stop-Agent {
    param([int]$WaitSeconds = 10)
    $stopped = $false
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        if ($task.State -eq 'Running') { $stopped = $true }
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    # на случай, если процесс агента ещё жив
    $alive = Get-AgentProcess
    foreach ($process in $alive) {
        $stopped = $true
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-AgentProcess).Count -gt 0 -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
    return $stopped
}

function Remove-Agent {
    Stop-Agent | Out-Null
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    if (Test-Path $Dir) { Remove-Item $Dir -Recurse -Force }
    Write-Host 'Агент печати удалён: задача и папка убраны.'
}

if ($Uninstall) { Remove-Agent; return }

if (-not $Token) { throw 'Нужен -Token (токен ERP со scopes labels:print и scan:upload, создаёт администратор в ERP).' }
# A chat client turns a pasted link into «https://host (https://host/)»: keep the first word only.
$Server = ($Server.Trim() -split '\s+')[0]
if ($Server -notmatch '^https?://[^\s/]+') { throw "Адрес сервера должен начинаться с https:// — получено «$Server»." }

$printers = @(Get-Printer | Select-Object -ExpandProperty Name)
if (-not $Printer) {
    $Printer = $printers | Where-Object { $_ -like '*TSC*' } | Select-Object -First 1
    if (-not $Printer) {
        throw ("Принтер с 'TSC' в названии не найден. Установленные: " + ($printers -join '; ') + ". Укажите -Printer '<имя очереди>'.")
    }
} elseif ($printers -notcontains $Printer) {
    throw ("Очереди '$Printer' нет. Установленные: " + ($printers -join '; '))
}

# Переустановка: прежний агент (со старым токеном) останавливается до записи нового, иначе он продолжит работать.
if (Stop-Agent) { Write-Host 'Прежний агент остановлен' }

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
if ($Scanner) { $cfg['scanner'] = $Scanner.Trim() }
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
Write-Host "  Сканер:   $(if ($Scanner) { $Scanner + ' (eSCL)' } else { 'USB (WIA), адрес не задан' })"
Write-Host "  Задача:   $TaskName (запуск при входе в Windows, уже запущена)"
Write-Host "  Журнал:   $Dir\agent.log"
Write-Host 'Токен сохранён в зашифрованном виде и в журнал не пишется.'

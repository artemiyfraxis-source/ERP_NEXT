<#
 Установка агента Fraxis (сканер УПД и печать этикеток) на ЛЮБОЙ ПК с Windows - под тем пользователем, который работает за ПК.
 Права администратора Windows не нужны. Один раз, дальше агент стартует при входе в Windows, сам находит сканер и сам обновляется.
 Обычный путь - одна команда из окна «Установить сканер» в ERP (администратор ERP выпускает одноразовый токен установки):
   powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((irm '<сервер>/api/stock/scan/agent/install.ps1'))) -Server '<сервер>' -InstallToken '<токен>'"
 Из папки с файлами (старый способ, токен API со scopes labels:print и scan:upload):
   .\install.ps1 -Token <токен ERP> [-Scanner <IP сканера>] [-Printer <очередь>]
 Принтер этикеток необязателен: без него (и если очереди с 'TSC' нет) ПК только сканирует.
 Удаление: .\install.ps1 -Uninstall   (или uninstall.ps1)
#>
# No positional binding: a stray word after the token (a pasted «14:20») is an error, not the server address.
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Server = 'https://erp-server.tail811f20.ts.net',
    [string]$Token,
    [string]$InstallToken,
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

function Assert-AgentVnaIdle {
    # Never erase the only original calibration backup or replace credentials under a live lease.
    foreach ($name in @('owner.json','quarantine.json')) {
        if (Test-Path -LiteralPath (Join-Path $Dir ('vna-control\' + $name))) {
            throw 'На ПК сохранена незавершённая сессия ВАЦ. Завершите или восстановите её в ERP перед переустановкой или удалением агента.'
        }
    }
    $pendingDir = Join-Path $Dir 'vna-control'
    if (Test-Path -LiteralPath $pendingDir) {
        foreach ($file in @(Get-ChildItem -LiteralPath $pendingDir -Filter '*.json')) {
            if ($file.BaseName -notmatch '^[a-f0-9-]{36}$') { continue }
            $journal = [IO.File]::ReadAllText($file.FullName,[Text.Encoding]::UTF8) | ConvertFrom-Json
            if (-not $journal.acknowledged) { throw 'На ПК есть команда ВАЦ без подтверждения ERP. Сначала восстановите сессию и дождитесь сверки журнала.' }
        }
        $requestFile = Join-Path $pendingDir 'worker-request.json'
        if (Test-Path -LiteralPath $requestFile) {
            $request = [IO.File]::ReadAllText($requestFile,[Text.Encoding]::UTF8) | ConvertFrom-Json
            if ($request.command) {
                $commandName = ([guid]::Parse($request.command.command_id)).ToString() + '.json'
                $receiptPath = Join-Path (Join-Path $pendingDir 'acknowledged') $commandName
                if (-not (Test-Path -LiteralPath $receiptPath)) { throw 'На ПК осталась команда исполнителя ВАЦ. Сначала восстановите сессию в ERP.' }
            }
        }
    }
}

function Exit-AgentLifecycle {
    if ($script:InstallLifecycleMutex) {
        $script:InstallLifecycleMutex.ReleaseMutex()
        $script:InstallLifecycleMutex.Dispose()
        $script:InstallLifecycleMutex = $null
    }
}

function Confirm-AgentStopped {
    if (@(Get-AgentProcess).Count -gt 0) { throw 'Не удалось остановить агент и исполнителей ВАЦ. Файлы и настройки сохранены.' }
    # The same lifecycle mutex is taken by the agent before starting any worker.
    if (-not $script:InstallLifecycleMutex) {
        $script:InstallLifecycleMutex = New-Object Threading.Mutex($false, 'Global\FraxisVnaParent')
        $locked = $false
        try { $locked = $script:InstallLifecycleMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) {
            $script:InstallLifecycleMutex.Dispose(); $script:InstallLifecycleMutex = $null
            throw 'Агент снова запущен. Файлы и настройки сохранены; повторите установку после завершения сессии.'
        }
    }
    try { Assert-AgentVnaIdle } catch { Exit-AgentLifecycle; throw }
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
    while (@(Get-AgentProcess).Count -gt 0 -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
    return $stopped
}

function Remove-Agent {
    Assert-AgentVnaIdle
    Stop-Agent | Out-Null
    Confirm-AgentStopped
    try {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        if (Test-Path -LiteralPath $Dir) {
            $target = [IO.Path]::GetFullPath($Dir)
            $expected = [IO.Path]::GetFullPath((Join-Path $env:ProgramData 'FraxisPrintAgent'))
            if (-not $target.Equals($expected,[StringComparison]::OrdinalIgnoreCase) -or
                ((Get-Item -LiteralPath $target).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw 'Каталог агента перенаправлен или путь изменён; автоматическое удаление остановлено.'
            }
            Remove-Item -LiteralPath $target -Recurse -Force
        }
    } finally {
        Exit-AgentLifecycle
    }
    Write-Host 'Агент печати удалён: задача и папка убраны.'
}

if ($Uninstall) { Remove-Agent; return }
Assert-AgentVnaIdle

if (-not $Token -and -not $InstallToken) {
    throw 'Нужен -InstallToken (одноразовая команда из окна «Установить сканер» в ERP) или -Token (токен ERP со scopes labels:print и scan:upload).'
}
# A chat client turns a pasted link into «https://host (https://host/)»: keep the first word only.
$Server = ($Server.Trim() -split '\s+')[0]
if ($Server -notmatch '^https?://[^\s/]+') { throw "Адрес сервера должен начинаться с https:// — получено «$Server»." }
$Server = $Server.TrimEnd('/')
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
$ProgressPreference = 'SilentlyContinue'

# Постоянный номер этого ПК: MachineGuid Windows (тот же, что у агента).
function Get-MachineId {
    try {
        $guid = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid
        if ($guid) { return ([string]$guid).ToLower() }
    } catch {}
    return ([string]$env:COMPUTERNAME).ToLower()
}

# Одноразовый токен установки меняется на токен API агента (сервер гасит токен установки при первом обмене).
if ($InstallToken) {
    $body = @{ install_token = $InstallToken.Trim(); machine_id = (Get-MachineId); pc_name = [string]$env:COMPUTERNAME } | ConvertTo-Json -Compress
    try {
        $reply = Invoke-RestMethod -Method Post -Uri "$Server/api/stock/scan/agent/enroll" -ContentType 'application/json; charset=utf-8' `
            -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 30
    } catch {
        throw "Сервер не принял токен установки: $($_.Exception.Message). Он одноразовый и живёт час - выпустите новую команду в ERP."
    }
    $Token = [string]$reply.token
}

# Принтер этикеток необязателен: указанный проверяется, иначе берётся первая очередь с 'TSC'; нет такой - ПК только сканирует.
$printers = @()
try { $printers = @(Get-Printer | Select-Object -ExpandProperty Name) } catch {}
if ($Printer) {
    if ($printers -notcontains $Printer) { throw ("Очереди '$Printer' нет. Установленные: " + ($printers -join '; ')) }
} else {
    $Printer = [string]($printers | Where-Object { $_ -like '*TSC*' } | Select-Object -First 1)
}

# Переустановка: прежний агент (со старым токеном) останавливается до записи нового, иначе он продолжит работать.
Assert-AgentVnaIdle
if (Stop-Agent) { Write-Host 'Прежний агент остановлен' }
Confirm-AgentStopped

try {
New-Item -ItemType Directory -Path $Dir -Force | Out-Null
$agentTarget = Join-Path $Dir 'agent.ps1'
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'agent.ps1'))) {
    Copy-Item (Join-Path $PSScriptRoot 'agent.ps1') $agentTarget -Force
} else {
    # Запуск из памяти (одна команда): файл агента берётся с сервера и сверяется с хэшем из манифеста.
    $manifest = Invoke-RestMethod -Uri "$Server/api/stock/scan/agent/manifest" -TimeoutSec 30
    Invoke-WebRequest -Uri ($Server + [string]$manifest.path) -OutFile $agentTarget -UseBasicParsing -TimeoutSec 60
    $actual = (Get-FileHash -Path $agentTarget -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne ([string]$manifest.sha256).ToLower()) {
        Remove-Item $agentTarget -Force
        throw 'Файл агента скачался с ошибкой (хэш не совпал). Повторите команду.'
    }
}

# Токен защищён DPAPI: читается только этим пользователем Windows на этом ПК.
$secure = ConvertTo-SecureString -String $Token -AsPlainText -Force
$cfg = [ordered]@{
    server         = $Server
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
# Сторож: раз в 5 минут задача запускается снова; агент уже работает — второй не стартует (MultipleInstances IgnoreNew),
# упал — поднимается сам (перезапуск задачи «при сбое» не ловит убитый процесс, 23.10.2026).
$watch = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($trigger, $watch) -Settings $settings -Principal $principal -Force | Out-Null
} finally { Exit-AgentLifecycle }
Start-ScheduledTask -TaskName $TaskName

Write-Host ''
Write-Host 'Агент Fraxis (сканер УПД и печать этикеток) установлен.'
Write-Host "  Сервер:   $($cfg.server)"
Write-Host "  ПК:       $($env:COMPUTERNAME)"
Write-Host "  Принтер:  $(if ($Printer) { $Printer } else { 'нет (этот ПК только сканирует)' })"
Write-Host "  Сканер:   $(if ($Scanner) { $Scanner + ' (eSCL)' } else { 'USB (WIA): ищется сам, подключённый позже подхватится' })"
Write-Host "  Задача:   $TaskName (запуск при входе в Windows, уже запущена)"
Write-Host "  Журнал:   $Dir\agent.log"
Write-Host 'Токен сохранён в зашифрованном виде и в журнал не пишется.'

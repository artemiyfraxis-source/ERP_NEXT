# Фоновое автообновление локальной ERP (владелец 29.09: «разрешаю автообновление ERP через планировщик Windows»).
# Раз в $IntervalSec секунд смотрит origin/main; если там новые коммиты или ERP не отвечает — запускает
# scripts\update-local.ps1 (база и PIN сохраняются, копия базы, откат при ошибке сборки/миграций, outbox_worker).
# После перезагрузки ПК поднимает ERP сам. Журнал — backend\auto-update.log.
# Включить один раз (задача планировщика Windows при входе в систему, без окон; сразу и запускается):
#   powershell -ExecutionPolicy Bypass -File scripts\auto-update-local.ps1 -Install
# Выключить: ... -Uninstall
param([switch]$Install, [switch]$Uninstall, [int]$IntervalSec = 150, [string]$DbFile = 'real.db', [int]$Port = 8022)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = (Resolve-Path "$PSScriptRoot\..").Path
$task = 'ERP local auto-update'
$self = Join-Path $root 'scripts\auto-update-local.ps1'

if ($Uninstall) {
  Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host 'Автообновление выключено. ERP работает до перезагрузки ПК.'
  exit 0
}
if ($Install) {
  $arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$self`" -DbFile $DbFile -Port $Port -IntervalSec $IntervalSec"
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $root
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
  Start-ScheduledTask -TaskName $task
  Write-Host "Автообновление включено: проверка main каждые $IntervalSec с, журнал backend\auto-update.log."
  Write-Host "ERP: http://localhost:$Port. Выключить: powershell -ExecutionPolicy Bypass -File scripts\auto-update-local.ps1 -Uninstall"
  exit 0
}

# Фоновый цикл не должен падать от stderr git/powershell (в PowerShell 5.1 это ошибка при 'Stop').
$ErrorActionPreference = 'Continue'
$log = Join-Path $root 'backend\auto-update.log'
function Log($text) { Add-Content -Path $log -Value "$(Get-Date -Format 'dd.MM.yyyy HH:mm:ss') $text" -Encoding UTF8 }
$update = Join-Path $root 'scripts\update-local.ps1'
function Test-Up { [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) }
function Invoke-Update {
  $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $update -DbFile $DbFile -Port $Port 2>&1
  $code = $LASTEXITCODE
  $output | ForEach-Object { Log "  $_" }
  if ($code -eq 0) { Log 'обновление: готово' } else { Log "обновление: ошибка (код $code), ERP на прежней версии" }
  return $code
}
# Коммит, который не собрался: повторно его не пробуем, ждём следующий.
$failed = ''

Log "старт автообновления (каждые $IntervalSec с)"
while ($true) {
  try {
    git -C $root fetch -q origin main 2>&1 | Out-Null
    $here = (git -C $root rev-parse HEAD).Trim()
    $there = (git -C $root rev-parse origin/main).Trim()
    if ($here -ne $there -and $there -ne $failed) {
      Log "новая версия $($there.Substring(0, 7))"
      if ((Invoke-Update) -ne 0) { $failed = $there }
    }
    elseif (-not (Test-Up)) { Log 'ERP не отвечает — запуск'; Invoke-Update | Out-Null }
  } catch { Log "ошибка проверки: $_" }
  Start-Sleep -Seconds $IntervalSec
}

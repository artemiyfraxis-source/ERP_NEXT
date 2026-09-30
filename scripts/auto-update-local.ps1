# Фоновое автообновление локальной ERP (владелец 29.09: «разрешаю автообновление ERP через планировщик Windows»).
# Раз в $IntervalSec секунд — одна проверка (-Tick) НОВЫМ процессом по файлу с диска: логика проверки обновляется
# вместе с кодом, а сам цикл в задаче планировщика — только таймер, его переустанавливать больше не нужно.
# Проверка: новые коммиты в origin/main → scripts\update-local.ps1 (база и PIN сохраняются, копия базы, откат при
# ошибке сборки/миграций, outbox_worker); ERP не отвечает → тот же скрипт с -StartOnly. После перезагрузки ПК
# поднимает ERP сам. Сбой не тихий: «СБОЙ: <причина>» и хвост вывода — в backend\auto-update.log и в
# frontend\dist\update-status.json, его ERP показывает плашкой «Обновление не установилось». Полный вывод
# последнего запуска — backend\update-last.log. Сбойная версия повторяется ещё дважды через 10 мин (сеть,
# занятый файл), дальше ждём следующую; нет связи с GitHub дольше 30 мин — тоже плашка.
# Включить (или починить) одной строкой: останавливает прежний цикл, обновляет ERP на глазах, ставит задачу
# планировщика (при входе в систему, без окон) и печатает хвост журнала:
#   powershell -ExecutionPolicy Bypass -File scripts\auto-update-local.ps1 -Install
# Выключить: ... -Uninstall
# Вывод update-local.ps1 идёт в файл, ждём только выхода процесса, а не конца трубы. Раньше цикл читал вывод из
# трубы, а outbox_worker, запущенный с -RedirectStandard*, наследовал её (CreateProcess с наследованием
# дескрипторов) и держал открытой, пока жив, — цикл молча вставал после первого же обновления.
param([switch]$Install, [switch]$Uninstall, [switch]$Tick, [switch]$Show, [int]$IntervalSec = 150,
  [string]$DbFile = 'real.db', [int]$Port = 8022)
# Фоновый цикл не должен падать от stderr git/powershell (в PowerShell 5.1 это ошибка при 'Stop').
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = (Resolve-Path "$PSScriptRoot\..").Path
$task = 'ERP local auto-update'
$self = Join-Path $root 'scripts\auto-update-local.ps1'
$update = Join-Path $root 'scripts\update-local.ps1'
$backend = Join-Path $root 'backend'
$log = Join-Path $backend 'auto-update.log'
$stateFile = Join-Path $backend 'auto-update.state.json'
$statusFile = Join-Path $root 'frontend\dist\update-status.json'
$RetryMin = 10
$MaxTries = 3
$FetchAlarmMin = 30
$UpdateTimeoutMin = 45
# Git без окон и вопросов: в скрытой задаче запрос пароля ждал бы вечно.
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'never'

function Log($text) {
  Add-Content -Path $log -Value "$(Get-Date -Format 'dd.MM.yyyy HH:mm:ss') $text" -Encoding UTF8
  if ($Show) { Write-Host $text }
}
function Write-Utf8($path, $text) { [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding $false)) }
function Test-Up { [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) }

# Состояние между проверками (каждая — свой процесс): сбойная версия, число попыток, когда повтор, с каких пор
# нет связи с GitHub. Время — в тиках.
function Read-State {
  $state = @{ failed = ''; tries = 0; next = 0; fetchFailSince = 0 }
  if (Test-Path $stateFile) {
    try {
      $saved = Get-Content $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
      foreach ($name in @($state.Keys)) { if ($null -ne $saved.$name) { $state[$name] = $saved.$name } }
    } catch { }
  }
  $state
}
function Save-State($state) { Write-Utf8 $stateFile ($state | ConvertTo-Json) }

# Плашка в ERP: файл рядом с сайтом, его отдаёт сам API. kind: update — версия не встала, start — ERP не
# поднялась, fetch — нет связи с GitHub.
function Set-Status($kind, $reason, $lines, $retry) {
  if (-not (Test-Path (Split-Path $statusFile))) { return }
  $tail = if ($lines) { @($lines | ForEach-Object { "$_" }) } else { @() }
  $body = [ordered]@{ ok = $false; kind = $kind; at = (Get-Date -Format 'dd.MM.yyyy HH:mm'); reason = "$reason";
    retry = $retry; log = $tail }
  Write-Utf8 $statusFile ($body | ConvertTo-Json)
}
function Clear-Status($kind) {
  if (-not (Test-Path $statusFile)) { return }
  $current = ''
  try { $current = (Get-Content $statusFile -Raw -Encoding UTF8 | ConvertFrom-Json).kind } catch { }
  if (-not $kind -or $current -eq $kind) { Remove-Item $statusFile -Force -ErrorAction SilentlyContinue }
}

# Запуск update-local.ps1: вывод — в update-last.log (при -Show — ещё и на экран по мере записи), ждём выхода
# процесса не дольше $UpdateTimeoutMin мин. Причина — последняя строка «СБОЙ: …» (иначе последнее предупреждение
# или ошибка), хвост — 15 строк для журнала и плашки.
function Invoke-Update([switch]$StartOnly) {
  $out = Join-Path $backend 'update-last.log'
  $err = Join-Path $backend 'update-last.err.log'
  $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$update`" -DbFile $DbFile -Port $Port"
  if ($StartOnly) { $arguments += ' -StartOnly' }
  $process = Start-Process powershell.exe -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $out -RedirectStandardError $err
  $null = $process.Handle  # PowerShell 5.1: без открытого дескриптора ExitCode после выхода пустой
  $deadline = (Get-Date).AddMinutes($UpdateTimeoutMin)
  $reader = $null
  $timedOut = $false
  while ($true) {
    $done = $process.WaitForExit(1000)
    if ($Show) {
      if (-not $reader -and (Test-Path $out)) {
        try { $reader = New-Object IO.StreamReader((New-Object IO.FileStream($out, 'Open', 'Read', 'ReadWrite')), [Text.Encoding]::UTF8) } catch { }
      }
      if ($reader) { while ($null -ne ($line = $reader.ReadLine())) { Write-Host $line } }
    }
    if ($done) { break }
    if ((Get-Date) -gt $deadline) {
      & taskkill.exe /T /F /PID $process.Id 2>&1 | Out-Null
      $timedOut = $true
      break
    }
  }
  if ($reader) { $reader.Dispose() }
  $lines = @()
  foreach ($file in @($out, $err)) {
    if (Test-Path $file) { $lines += @(Get-Content $file -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object { "$_" -replace "\x1b\[[0-9;]*m", '' }) }
  }
  if ($Show -and (Test-Path $err)) { Get-Content $err -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ } }
  $code = if ($timedOut) { 1 } elseif ($null -ne $process.ExitCode) { $process.ExitCode } else { 1 }
  $reason = $lines | Where-Object { $_ -match '^СБОЙ: ' } | Select-Object -Last 1
  if ($timedOut) { $reason = "update-local.ps1 не закончил за $UpdateTimeoutMin мин и остановлен" }
  elseif ($reason) { $reason = $reason.Substring(6) }
  elseif ($code -ne 0) {
    $reason = $lines | Where-Object { $_ -match 'WARNING|Ошибка|ошибка|Error|error' } | Select-Object -Last 1
    if (-not $reason) { $reason = "update-local.ps1 завершился с кодом $code" }
  }
  $warnings = @($lines | Where-Object { $_ -match '^WARNING' } | Select-Object -First 5)
  $tail = @($lines | Where-Object { $_.Trim() } | Select-Object -Last 15)
  @{ code = $code; reason = "$reason".Trim(); tail = $tail; warnings = $warnings }
}

function Start-Only {
  Log 'ERP не отвечает — запуск'
  $result = Invoke-Update -StartOnly
  if ($result.code -eq 0) { Log 'запуск: готово'; Clear-Status 'start'; return }
  Log "запуск: СБОЙ — $($result.reason)"
  $result.tail | ForEach-Object { Log "  | $_" }
  Set-Status 'start' $result.reason $result.tail 'повтор через 2,5 мин'
}

function Invoke-Tick {
  $state = Read-State
  $fetch = @(& git -C $root fetch -q origin main 2>&1 | ForEach-Object { "$_" })
  if ($LASTEXITCODE -ne 0) {
    # «fatal: …» — суть; последняя строка у git бывает хвостом совета («and the repository exists.»).
    $why = $fetch | Where-Object { $_ -match '^(fatal|error): ' } | Select-Object -First 1
    if (-not $why) { $why = $fetch | Where-Object { $_.Trim() } | Select-Object -Last 1 }
    if (-not $state.fetchFailSince) { $state.fetchFailSince = (Get-Date).Ticks; Log "нет связи с GitHub: $why" }
    elseif (((Get-Date) - [datetime][long]$state.fetchFailSince).TotalMinutes -ge $FetchAlarmMin) {
      Set-Status 'fetch' "обновления не скачиваются с GitHub больше $FetchAlarmMin мин: $why" $fetch $null
    }
    Save-State $state
    if (-not (Test-Up)) { Start-Only }
    return
  }
  if ($state.fetchFailSince) { $state.fetchFailSince = 0; Clear-Status 'fetch'; Log 'связь с GitHub восстановлена' }

  # Сравниваем с последней СОБРАННОЙ версией (update-local.ps1 пишет её в backend\<база>.version), а не с HEAD:
  # checkout без сборки иначе выглядел бы как «уже обновлено».
  $stamp = Join-Path $backend "$DbFile.version"
  $here = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { '' }
  $there = (git -C $root rev-parse origin/main).Trim()
  if ($here -eq $there) {
    if ($state.failed) { $state.failed = ''; $state.tries = 0; $state.next = 0; Clear-Status 'update' }
    if ($Show) { Log "ERP уже на свежей версии $($there.Substring(0, 7))" }
    Save-State $state
    if (-not (Test-Up)) { Start-Only }
    return
  }
  if ($state.failed -eq $there) {
    if ($state.tries -ge $MaxTries -or (Get-Date).Ticks -lt [long]$state.next) {
      Save-State $state
      if (-not (Test-Up)) { Start-Only }
      return
    }
  } else { $state.failed = ''; $state.tries = 0; $state.next = 0 }

  $short = $there.Substring(0, 7)
  Log "новая версия $short$(if ($state.tries) { ", попытка $($state.tries + 1) из $MaxTries" })"
  # Попытка засчитывается до запуска: оборвавшаяся проверка не должна повторять сбойную версию каждые 150 с.
  $state.failed = $there
  $state.tries = [int]$state.tries + 1
  $state.next = (Get-Date).AddMinutes($RetryMin).Ticks
  Save-State $state
  $result = Invoke-Update
  if ($result.code -eq 0) {
    Log "обновление: готово ($short)"
    $result.warnings | ForEach-Object { Log "  ! $_" }
    $state.failed = ''; $state.tries = 0; $state.next = 0
    Clear-Status ''
  } else {
    if ($state.tries -lt $MaxTries) {
      $at = (Get-Date).AddMinutes($RetryMin)
      $state.next = $at.Ticks
      $retry = "повтор в $($at.ToString('HH:mm'))"
    } else {
      $state.next = 0
      $retry = 'повторов больше не будет, ждём следующую версию'
    }
    Log "обновление: СБОЙ — $($result.reason) ($retry)"
    $result.tail | ForEach-Object { Log "  | $_" }
    Set-Status 'update' $result.reason $result.tail $retry
  }
  Save-State $state
}

# Процессы прежнего цикла и его update-local.ps1 этой копии (по пути в командной строке), кроме себя.
function Get-Runner($script) {
  Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $_.ProcessId -ne $PID -and $_.CommandLine -and
    $_.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
    $_.CommandLine -match 'update-local\.ps1' -and (-not $script -or $_.CommandLine -like "*\$script*")
  }
}
function Stop-Loop {
  Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
  # Прежний цикл мог остаться процессом: задача перерегистрировалась поверх работающей, а IgnoreNew не пускал новую.
  Get-Runner '' | ForEach-Object { & taskkill.exe /T /F /PID $_.ProcessId 2>&1 | Out-Null }
}

if ($Uninstall) {
  Stop-Loop
  Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host 'Автообновление выключено. ERP работает до перезагрузки ПК.'
  exit 0
}
if ($Install) {
  Write-Host '== Останавливаю прежнее автообновление'
  # Идущее обновление не рвём: ждём его окончания (до 20 мин).
  for ($i = 0; $i -lt 240 -and (Get-Runner 'update-local.ps1'); $i++) {
    if ($i -eq 0) { Write-Host 'Идёт обновление — жду его окончания' }
    Start-Sleep 5
  }
  Stop-Loop
  Remove-Item $stateFile -ErrorAction SilentlyContinue
  $Show = $true
  Log 'установка автообновления: проверка сейчас, затем задача планировщика'
  try { Invoke-Tick } catch { Log "ошибка проверки: $_" }

  $arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$self`" -DbFile $DbFile -Port $Port -IntervalSec $IntervalSec"
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $root
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
  try {
    Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings -Force -ErrorAction Stop | Out-Null
    Start-ScheduledTask -TaskName $task -ErrorAction Stop
    Write-Host ''
    Write-Host "Автообновление включено: проверка main каждые $IntervalSec с. ERP: http://localhost:$Port"
  } catch {
    Write-Host ''
    Write-Host "СБОЙ: задача планировщика не создана: $_"
    Log "установка: задача планировщика не создана: $_"
  }
  Write-Host '== Журнал автообновления (backend\auto-update.log), последние 30 строк:'
  Get-Content $log -Tail 30 -Encoding UTF8 | ForEach-Object { Write-Host $_ }
  exit 0
}
if ($Tick) {
  try { Invoke-Tick } catch { Log "ошибка проверки: $_" }
  exit 0
}

# Цикл задачи планировщика: только таймер, каждая проверка — свежий процесс по файлу с диска. Без трубы: ждём
# выхода процесса (не дольше часа), ошибки PowerShell самой проверки — из файла в журнал.
Log "старт автообновления (каждые $IntervalSec с)"
$tickErr = Join-Path $backend 'auto-update-tick.err.log'
while ($true) {
  try {
    $check = Start-Process powershell.exe -WindowStyle Hidden -PassThru -RedirectStandardError $tickErr `
      -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$self`" -Tick -DbFile $DbFile -Port $Port" -WorkingDirectory $root
    if (-not $check.WaitForExit(3600 * 1000)) {
      & taskkill.exe /T /F /PID $check.Id 2>&1 | Out-Null
      Log 'проверка шла больше часа — остановлена'
    }
    Get-Content $tickErr -Encoding UTF8 -ErrorAction SilentlyContinue | Where-Object { "$_".Trim() } |
      Select-Object -First 20 | ForEach-Object { Log "  ! $_" }
  } catch { Log "ошибка запуска проверки: $_" }
  Start-Sleep -Seconds $IntervalSec
}

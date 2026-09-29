# Обновить локальную ERP (копия ERP_NEXT на ПК, база real.db) до свежего main, не трогая данные.
# Запуск из корня копии: powershell -ExecutionPolicy Bypass -File scripts\update-local.ps1
# Порядок безопасный: сначала новый код, зависимости и сборка сайта в frontend\dist-next — старая ERP в это
# время работает. Ошибка на этом шаге → код возвращается на прежнюю версию, ERP не останавливалась.
# Потом остановка API, копия базы в backend\<база>.bak, миграции, сотрудники (без --reset-pins), отгруженные
# проекты по снимку data\import\projects.json, подмена сайта, запуск API и фонового outbox_worker. Ошибка миграций → база из копии,
# прежний код, прежний сайт, API снова запущен. В конце — версия и «Что нового» (влитые PR с прошлого
# обновления; коммит прошлого обновления в backend\<база>.version), то же уходит в version.json для плашки в ERP.
param([string]$DbFile = 'real.db', [int]$Port = 8022)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = (Resolve-Path "$PSScriptRoot\..").Path
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$py = Join-Path $backend '.venv\Scripts\python.exe'
$db = Join-Path $backend $DbFile
if (-not (Test-Path $db)) { throw "Нет базы $db — сначала scripts\load-real-data.ps1" }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "Ошибка: $what" } }
$env:ERP_DATABASE_URL = "sqlite+pysqlite:///$DbFile"
$env:ERP_COOKIE_SECURE = '0'

# Фоновый outbox_worker (как worker в deploy/docker-compose.yml): уведомления, наименования по ссылкам и прочие
# фоновые события. Живёт вместе с API, скрыто; PID в backend\outbox-worker.pid, журнал backend\outbox-worker.log.
$workerPid = Join-Path $backend 'outbox-worker.pid'
function Stop-Api {
  if (Test-Path $workerPid) {
    Stop-Process -Id ([int](Get-Content $workerPid -Raw).Trim()) -Force -ErrorAction SilentlyContinue
    Remove-Item $workerPid -ErrorAction SilentlyContinue
  }
  Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
  Start-Sleep 1
}
function Start-Api {
  Start-Process $py -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$Port" `
    -WorkingDirectory $backend -WindowStyle Hidden | Out-Null
  for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:$Port/api/health" | Out-Null; return } catch { Start-Sleep 1 }
  }
  throw "API не ответил на порту $Port"
}
function Start-Worker {
  $worker = Start-Process $py -ArgumentList '-X','utf8','-m','app.outbox_worker' -WorkingDirectory $backend `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $backend 'outbox-worker.log') `
    -RedirectStandardError (Join-Path $backend 'outbox-worker.err.log')
  Set-Content $workerPid $worker.Id
}

# Влитые PR по первой линии main: «Merge pull request #N» (название — первая строка описания) или «… (#N)».
function Get-News($prev) {
  $range = if ($prev) { @("$prev..HEAD") } else { @('-15', 'HEAD') }
  $log = (git -C $root log --first-parent --format='%s%x1f%b%x1e' @range) -join "`n"
  foreach ($entry in ($log -split [char]0x1e)) {
    $parts = $entry.Trim() -split [char]0x1f, 2
    if (-not $parts[0]) { continue }
    if ($parts[0] -match '^Merge pull request #(\d+)') {
      $title = (($parts[1] -split "`n") | Where-Object { $_.Trim() } | Select-Object -First 1)
      "#$($Matches[1]) $title"
    } elseif ($parts[0] -match '^(.*) \(#(\d+)\)$') { "#$($Matches[2]) $($Matches[1])" }
    else { $parts[0] }
  }
}

$old = (git -C $root rev-parse HEAD).Trim()
Write-Host '== Свежий main (ERP пока работает на прежней версии)'
Set-Location $root
git fetch origin main; Check 'git fetch'
# База и сборки в .gitignore, их checkout не трогает; правки в файлах копии сбрасываются.
git checkout --force --detach origin/main; Check 'git checkout'
$next = Join-Path $frontend 'dist-next'
try {
  Write-Host '== Зависимости и сборка сайта'
  & $py -m pip install -q -r (Join-Path $backend 'requirements.lock.txt'); Check 'pip install'
  Set-Location $frontend
  npm ci --no-audit --no-fund; Check 'npm ci'
  npm run build -- --outDir dist-next --emptyOutDir; Check 'npm run build'
} catch {
  Write-Warning "Обновление не собралось, ERP остаётся на прежней версии: $_"
  Set-Location $root
  git checkout --force --detach $old | Out-Null
  exit 1
}

Write-Host "== Останавливаю API на порту $Port"
Stop-Api
Copy-Item $db "$db.bak" -Force
Write-Host "== Копия базы: $db.bak"
Set-Location $backend
& $py -m alembic upgrade head
if ($LASTEXITCODE -ne 0) {
  Write-Warning 'Миграции не прошли: база возвращена из копии, ERP остаётся на прежней версии.'
  Copy-Item "$db.bak" $db -Force
  git -C $root checkout --force --detach $old | Out-Null
  Start-Api
  Start-Worker
  exit 1
}

Write-Host '== Сотрудники из SUPPLYMAP (выданные PIN не меняются, PIN получают только новые)'
$staff = & $py -X utf8 -m app.modules.identity.import_staff --apply
if ($LASTEXITCODE -ne 0) { Write-Warning "Сотрудники не обновлены (ошибка выше)" }
else {
  $new = @($staff | Where-Object { "$_" -match ' создан\s*$' })
  if ($new.Count) { Write-Host "Новые сотрудники, раздайте им PIN:"; $new | ForEach-Object { Write-Host "  $_" } }
  else { Write-Host 'Новых сотрудников нет.' }
}

Write-Host '== Отгруженные по листу цеха — из базы (владелец 29.09: отгруженное не нужно)'
& $py -X utf8 ..\scripts\projects_import.py drop-shipped (Join-Path $root 'data\import\projects.json')
if ($LASTEXITCODE -ne 0) { Write-Warning "Отгруженные не убраны (ошибка выше)" }

$head = (git -C $root rev-parse HEAD).Trim()
$stamp = "$db.version"
$prev = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { '' }
$date = git -C $root log -1 --format='%cd' --date=format:'%d.%m.%Y %H:%M'
$news = @(if ($prev -ne $head) { Get-News $prev })
$info = [ordered]@{ version = $head; date = $date; news = $news } | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $next 'version.json'), $info, (New-Object Text.UTF8Encoding $false))

Write-Host '== Новый сайт и запуск API'
$dist = Join-Path $frontend 'dist'
if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
Move-Item $next $dist
Start-Api
Start-Worker
# Наименования комплектующих с сайтов поставщиков (tcKitFetchNames): разовый добор наборов с пустыми
# наименованиями — фоном, сайт уже работает. Дальше новые наборы проходит outbox_worker. Журнал: backend\names-fill.log.
Start-Process $py -ArgumentList '-X','utf8','-m','app.workflows.kitset_names' -WorkingDirectory $backend `
  -WindowStyle Hidden -RedirectStandardOutput (Join-Path $backend 'names-fill.log') `
  -RedirectStandardError (Join-Path $backend 'names-fill.err.log') | Out-Null
Write-Host 'Фоном: outbox_worker (backend\outbox-worker.log) и добор наименований (backend\names-fill.log).'

Write-Host ''
Write-Host "Версия: $($head.Substring(0, 7)) от $date"
if ($prev -eq $head) { Write-Host 'Что нового: ничего, версия та же, что и в прошлый раз.' }
else {
  Write-Host $(if ($prev) { 'Что нового с прошлого обновления:' } else { 'Последние правки (прошлое обновление не записано):' })
  $news | ForEach-Object { Write-Host "  $_" }
}
Set-Content $stamp $head
Write-Host "Готово: ERP на http://localhost:$Port"

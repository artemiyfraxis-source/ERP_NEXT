# Обновить локальную ERP (копия ERP_NEXT на ПК, база real.db) до свежего main, не трогая данные.
# Запуск из корня копии: powershell -ExecutionPolicy Bypass -File scripts\update-local.ps1
# Порядок безопасный: сначала новый код, зависимости и сборка сайта в frontend\dist-next — старая ERP в это
# время работает. Ошибка на этом шаге → код возвращается на прежнюю версию, ERP не останавливалась.
# Потом остановка API, копия базы в backend\<база>.bak, миграции, сотрудники (без --reset-pins), отгруженное по
# листу цеха «ЗАГРУЗ» (полностью отгруженные проекты прочь, частичные отгрузки — в готовую продукцию; с ключом лист
# перечитывается из Google, иначе снимок data\import\projects.json), подмена сайта, запуск API и фонового outbox_worker. Ошибка миграций → база из копии,
# прежний код, прежний сайт, API снова запущен. В конце — версия и «Что нового» (влитые PR с прошлого
# обновления; коммит прошлого обновления в backend\<база>.version), то же уходит в version.json для плашки в ERP.
# Проекты (владелец 30.09: «все проекты снесем и буду по одному тебе давать»): разовый снос всех проектов с ТК
# (app.workflows.projects_wipe — по метке в базе, перед ним копия базы рядом; склад, комплектация и выдачи не
# трогаются), дальше из Google загружаются только новые номера списка data\projects.txt вместе с их ТК
# (scripts\projects_import.py load, итог в backend\projects-load.txt). Массовой загрузки проектов и пакетной
# генерации и пересборки ТК больше нет.
# Ключ Google erp-reader (только чтение): -Key путь (запоминается), иначе ERP_GOOGLE_SERVICE_ACCOUNT_JSON, иначе
# запомненный путь (backend\google-key.path, его пишет и load-real-data.ps1), иначе поиск в Загрузках/Документах/Рабочем столе.
param([string]$DbFile = 'real.db', [int]$Port = 8022, [string]$Key = '')
# Из планировщика (auto-update-local.ps1) stderr перенаправлен, и PowerShell 5.1 превращает каждую строку stderr
# у git/npm/pip («From github.com…», предупреждения) в ошибку — при 'Stop' скрипт падал на первом git fetch.
# Поэтому: командлеты — Stop (через значения по умолчанию), внешние программы — только по коду выхода (Check).
$ErrorActionPreference = 'Continue'
$PSDefaultParameterValues['*:ErrorAction'] = 'Stop'
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
  npm run build:tcgen; Check 'npm run build:tcgen'
} catch {
  Write-Warning "Обновление не собралось, ERP остаётся на прежней версии: $_"
  Set-Location $root
  git checkout --force --detach $old | Out-Null
  # ERP могла быть уже остановлена (сбой, перезагрузка) — поднять прежнюю версию, если сайт от неё есть.
  if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
    try { Start-Api; Start-Worker } catch { Write-Warning "Прежняя версия тоже не поднялась: $_" }
  }
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

# Ключ Google: только чтение таблиц; запоминается путь, не сам ключ.
$keyPath = Join-Path $backend 'google-key.path'
if ($Key) { $Key = (Resolve-Path $Key).Path }
elseif ($env:ERP_GOOGLE_SERVICE_ACCOUNT_JSON) { $Key = $env:ERP_GOOGLE_SERVICE_ACCOUNT_JSON }
elseif ((Test-Path $keyPath) -and (Test-Path (Get-Content $keyPath -Raw).Trim())) { $Key = (Get-Content $keyPath -Raw).Trim() }
else {
  $found = @("$HOME\Downloads", "$HOME\Documents", "$HOME\Desktop", 'C:\secrets') | Where-Object { Test-Path $_ } |
    ForEach-Object { Get-ChildItem $_ -Filter *.json -File -Depth 1 -ErrorAction SilentlyContinue } |
    Where-Object { $_.Length -lt 20KB -and (Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue) -match '"client_email"\s*:\s*"erp-reader@' } |
    Select-Object -First 1
  if ($found) { $Key = $found.FullName; Write-Host "Ключ Google найден: $Key" }
}
if ($Key) { Set-Content $keyPath $Key; $env:ERP_GOOGLE_SERVICE_ACCOUNT_JSON = $Key }

Write-Host '== Проекты: разовый снос (владелец 30.09: «все проекты снесем и буду по одному давать»)'
# Один раз, по метке в самой базе; перед сносом — копия базы рядом (<база>.before-projects-wipe.bak).
& $py -X utf8 -m app.workflows.projects_wipe
if ($LASTEXITCODE -ne 0) { Write-Warning 'Проекты не снесены (ошибка выше), база не изменена' }

Write-Host '== Проекты по одному: новые номера из data\projects.txt с их ТК (Google только читается)'
$report = Join-Path $backend 'projects-load.txt'
$load = @('-X', 'utf8', '..\scripts\projects_import.py', 'load')
if ($Key -and (Test-Path $Key)) { $load += @('--key', $Key) }
& $py @load | Tee-Object -FilePath $report -Append
if ($LASTEXITCODE -gt 1) { Write-Warning "Проекты из списка не загружены (причина выше, итог: $report)" }
elseif ($LASTEXITCODE -eq 1) { Write-Warning "Часть проектов из списка не загружена — повтор при следующем обновлении (итог: $report)" }

Write-Host '== Отгруженное по листу цеха «ЗАГРУЗ» (владелец 29.09: отгруженное не нужно, отгруженное из Google — «отгружено»)'
# С ключом лист перечитывается из Google (только чтение) в снимок data\import\projects.json, без ключа — берётся снимок.
$shipped = @('-X', 'utf8', '..\scripts\projects_import.py', 'drop-shipped', (Join-Path $root 'data\import\projects.json'))
if ($Key -and (Test-Path $Key)) { $shipped += @('--key', $Key) }
& $py @shipped
if ($LASTEXITCODE -ne 0) { Write-Warning "Отгруженное не перенесено (причина выше)" }

$head = (git -C $root rev-parse HEAD).Trim()
$stamp = "$db.version"
$prev = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { '' }
$date = git -C $root log -1 --format='%cd' --date=format:'%d.%m.%Y %H:%M'
$news = @(if ($prev -ne $head) { Get-News $prev })
$info = [ordered]@{ version = $head; date = $date; news = $news } | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $next 'version.json'), $info, (New-Object Text.UTF8Encoding $false))

Write-Host '== Новый сайт и запуск API'
$dist = Join-Path $frontend 'dist'
$prevDist = Join-Path $frontend 'dist-prev'
if (Test-Path $prevDist) { Remove-Item $prevDist -Recurse -Force }
if (Test-Path $dist) { Move-Item $dist $prevDist }
Move-Item $next $dist
try { Start-Api } catch {
  # Новая версия не поднялась: база из копии, прежние код и сайт, прежняя ERP снова запущена.
  Write-Warning "Новая версия не запустилась ($_), возвращаю прежнюю."
  Stop-Api
  Copy-Item "$db.bak" $db -Force
  git -C $root checkout --force --detach $old | Out-Null
  Remove-Item $dist -Recurse -Force
  if (Test-Path $prevDist) { Move-Item $prevDist $dist }
  Start-Api; Start-Worker
  exit 1
}
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

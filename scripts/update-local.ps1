# Обновить локальную ERP (копия ERP_NEXT на ПК, база real.db) до свежего main, не трогая данные.
# Запуск из корня копии: powershell -ExecutionPolicy Bypass -File scripts\update-local.ps1
# Порядок безопасный: сначала новый код, зависимости и сборка сайта в frontend\dist-next — старая ERP в это
# время работает. Ошибка на этом шаге → код возвращается на прежнюю версию, ERP не останавливалась.
# Потом остановка API и фоновых процессов, копия базы в backend\<база>.bak, миграции, сотрудники (без
# --reset-pins), подмена сайта, запуск API и фонового outbox_worker. Ошибка миграций → база из копии,
# прежний код, прежний сайт, API снова запущен. В конце — версия и «Что нового» (влитые PR с прошлого
# обновления; коммит прошлого обновления в backend\<база>.version), то же уходит в version.json для плашки в ERP.
# Любой сбой заканчивается строкой «СБОЙ: <причина>» и кодом 1: её берёт автообновление (auto-update-local.ps1)
# в журнал и в плашку «Обновление не установилось» в ERP. Уже обновлено (версия = origin/main, ERP отвечает) —
# выход без пересборки, -Force пересобирает всё равно; -StartOnly — только поднять ERP той же версии.
# Проекты (владелец 30.09: «все проекты снесем», «я сам буду заводить, либо через тебя по одному»): обновление их
# не грузит, не генерирует им ТК и не трогает по листу цеха. Разовый снос — миграция 0078_wipe_projects_once (копия
# базы рядом: <база>.before-projects-wipe.bak). Один проект из Google по запросу — scripts\load-project.ps1 <№>.
# Ключ Google erp-reader (только чтение): -Key путь (запоминается), иначе ERP_GOOGLE_SERVICE_ACCOUNT_JSON, иначе
# запомненный путь (backend\google-key.path, его пишет и load-real-data.ps1), иначе поиск в Загрузках/Документах/Рабочем столе.
param([string]$DbFile = 'real.db', [int]$Port = 8022, [string]$Key = '', [switch]$StartOnly, [switch]$Force)
# Из планировщика (auto-update-local.ps1) stderr перенаправлен, и PowerShell 5.1 превращает каждую строку stderr
# у git/npm/pip («From github.com…», предупреждения) в ошибку — при 'Stop' скрипт падал на первом git fetch.
# Поэтому: командлеты — Stop (через значения по умолчанию), внешние программы — только по коду выхода (Run).
$ErrorActionPreference = 'Continue'
$PSDefaultParameterValues['*:ErrorAction'] = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
# Python в UTF-8 при любой кодировке Windows: вывод в трубу (планировщик) иначе идёт в ANSI, и символ вне неё
# роняет шаг (UnicodeEncodeError), хотя руками в консоли тот же шаг проходит.
$env:PYTHONUTF8 = '1'
# Наследование stdin/stdout/stderr НЕ снимать: python.exe из venv — запускатель, он передаёт их настоящему Python и
# без наследуемого stdin падает «stdin duplication failed» (30.09: API и прежняя версия не поднялись).
# Непойманная ошибка — тоже «СБОЙ: …», а не молчаливый код 1; остановленную ERP сначала поднимаем (версия не
# записана, автообновление повторит).
$apiStopped = $false
trap {
  Write-Host "СБОЙ: $_"
  if ($apiStopped -and -not (Test-Up)) { try { Start-Api; Start-Background } catch { Write-Host "ERP не поднялась: $_" } }
  exit 1
}
function Fail($reason) { Write-Host "СБОЙ: $reason"; exit 1 }

$root = (Resolve-Path "$PSScriptRoot\..").Path
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$py = Join-Path $backend '.venv\Scripts\python.exe'
$db = Join-Path $backend $DbFile
if (-not (Test-Path $db)) { Fail "нет базы $db — сначала scripts\load-real-data.ps1" }
$env:ERP_DATABASE_URL = "sqlite+pysqlite:///$DbFile"
$env:ERP_COOKIE_SECURE = '0'
# Файл-стоп: API, outbox_worker и добор наименований этой копии завершаются сами, когда он обновляется
# (app/shared/stop_file.py). ERP, запущенную из окна администратора, процесс планировщика иначе не остановит:
# taskkill — «Отказано в доступе», порт занят (30.09 15:20).
$stopFile = Join-Path $backend 'erp.stop'
$env:ERP_STOP_FILE = $stopFile

# Внешняя программа: вывод на экран (и в журнал автообновления), код ≠ 0 — ошибка с её последней строкой.
function Run($what, [scriptblock]$command) {
  $last = ''
  & $command 2>&1 | ForEach-Object { $line = "$_"; Write-Host $line; if ($line.Trim()) { $last = $line.Trim() } }
  if ($LASTEXITCODE -ne 0) { throw "${what}: $last" }
}
# Сеть, антивирус, занятый файл в node_modules — разовые сбои: повтор, а не отказ от версии.
function Retry($what, [scriptblock]$command, [int]$times = 2) {
  for ($i = 1; ; $i++) {
    try { Run $what $command; return }
    catch { if ($i -ge $times) { throw }; Write-Host "$_ — повтор через 15 с"; Start-Sleep 15 }
  }
}

# Два обновления разом (планировщик и ручной запуск) ломали бы друг другу сборку: второе ждёт первое.
$lockPath = Join-Path $backend 'update-local.lock'
$lock = $null
for ($i = 0; $i -lt 360 -and -not $lock; $i++) {
  try { $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
  catch { if ($i -eq 0) { Write-Host 'Идёт другое обновление — жду его окончания' }; Start-Sleep 5 }
}
if (-not $lock) { Fail 'другое обновление идёт больше 30 минут' }

# Фоновые процессы (как worker в deploy/docker-compose.yml): outbox_worker — уведомления, наименования по ссылкам
# и прочие события; разовый добор наименований. Живут вместе с API, скрыто; PID в backend\*.pid, журналы рядом.
# Останавливаются оба до миграций: живой процесс держал бы базу.
$workerPid = Join-Path $backend 'outbox-worker.pid'
$namesPid = Join-Path $backend 'names-fill.pid'
function Test-Up { [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) }
$stopProblem = ''
function Stop-Tree($id) {
  # Только python: PID из файла мог достаться чужому процессу. /T — вместе с дочерним (venv-запускатель).
  if ($id -and (Get-Process -Id $id -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'python*' })) {
    $said = @(& taskkill.exe /T /F /PID $id 2>&1 | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
    if ($LASTEXITCODE -ne 0 -and $said) { $script:stopProblem = "taskkill: $($said[-1])" }
  }
}
function Get-PortHolder {
  $owner = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)[0].OwningProcess
  if (-not $owner) { return 'неизвестный процесс' }
  $process = Get-Process -Id $owner -ErrorAction SilentlyContinue
  if ($process) { "$($process.ProcessName), PID $owner" } else { "PID $owner" }
}
function Stop-Api {
  # Сначала просим процессы ERP завершиться самим (файл-стоп), потом добиваем по PID и порту — те, что
  # запущены до появления файла-стопа.
  try { Set-Content $stopFile (Get-Date -Format 'dd.MM.yyyy HH:mm:ss') } catch { Write-Warning "Файл-стоп не записан: $_" }
  for ($i = 0; $i -lt 8 -and (Test-Up); $i++) { Start-Sleep 1 }
  foreach ($file in @($workerPid, $namesPid)) {
    if (Test-Path $file) {
      $id = 0
      if ([int]::TryParse("$(Get-Content $file -Raw -ErrorAction SilentlyContinue)".Trim(), [ref]$id)) { Stop-Tree $id }
      Remove-Item $file -ErrorAction SilentlyContinue
    }
  }
  Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Tree $_.OwningProcess }
  Start-Sleep 2
}
function Start-Api {
  $err = Join-Path $backend 'api.err.log'
  Start-Process $py -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$Port",'--no-access-log' `
    -WorkingDirectory $backend -WindowStyle Hidden -RedirectStandardOutput (Join-Path $backend 'api.log') `
    -RedirectStandardError $err | Out-Null
  for ($i = 0; $i -lt 90; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:$Port/api/health" | Out-Null; return } catch { Start-Sleep 1 }
  }
  $why = if (Test-Path $err) { (Get-Content $err -Tail 8 -ErrorAction SilentlyContinue) -join ' | ' } else { '' }
  throw "API не ответил на порту $Port за 90 с. $why"
}
function Start-Background {
  # Уже работающий сайт важнее фоновых процессов: их сбой — предупреждение, не откат.
  try {
    $worker = Start-Process $py -ArgumentList '-m','app.outbox_worker' -WorkingDirectory $backend `
      -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $backend 'outbox-worker.log') `
      -RedirectStandardError (Join-Path $backend 'outbox-worker.err.log')
    Set-Content $workerPid $worker.Id
    # Наименования комплектующих с сайтов поставщиков (tcKitFetchNames): разовый добор наборов с пустыми
    # наименованиями — фоном, сайт уже работает. Дальше новые наборы проходит outbox_worker.
    $names = Start-Process $py -ArgumentList '-m','app.workflows.kitset_names' -WorkingDirectory $backend `
      -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $backend 'names-fill.log') `
      -RedirectStandardError (Join-Path $backend 'names-fill.err.log')
    Set-Content $namesPid $names.Id
    Write-Host 'Фоном: outbox_worker (backend\outbox-worker.log) и добор наименований (backend\names-fill.log).'
  } catch { Write-Warning "Фоновые процессы не запущены: $_" }
}
# Зависший node этой копии (сборка, оборванная на полпути) держит файлы node_modules, и npm ci не может их стереть
# (EPERM/EBUSY) — на каждом обновлении, пока процесс жив. Генератор ТК (dist-tools) API не трогаем.
function Stop-StrayNode {
  $modules = Join-Path $frontend 'node_modules'
  Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($modules, [StringComparison]::OrdinalIgnoreCase) -ge 0 } |
    ForEach-Object { Write-Host "Останавливаю зависший node: $($_.CommandLine)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
# Откат к прежней версии и её запуск; сбой запуска дописывается к причине, а не заменяет её.
function Restore-Old($reason) {
  git -C $root checkout --force --detach $old 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { $reason = "$reason; прежний код не вернулся (git checkout $($old.Substring(0, 7)) не прошёл)" }
  if (-not (Test-Up)) {
    try { Start-Api; Start-Background } catch { $reason = "$reason; прежняя версия тоже не поднялась: $_" }
  }
  Fail $reason
}

$stamp = "$db.version"
$prev = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { '' }

if ($StartOnly) {
  if (Test-Up) { Write-Host "ERP уже работает: http://localhost:$Port"; exit 0 }
  Write-Host "== Запуск ERP на порту $Port (версия та же)"
  Stop-Api
  Start-Api
  Start-Background
  Write-Host "Готово: ERP на http://localhost:$Port"
  exit 0
}

# Влитые PR по первой линии main: «Merge pull request #N» (название — первая строка описания) или «… (#N)».
function Get-News($prev) {
  # @(...) снаружи: из if одна строка выходит строкой, и @range раздавал git её по буквам («ambiguous argument 'c'»).
  $range = @(if ($prev) { "$prev..HEAD" } else { '-15'; 'HEAD' })
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
Retry 'git fetch' { git fetch origin main } 3
$target = (git rev-parse origin/main).Trim()
if (-not $Force -and $prev -eq $target -and (Test-Up)) {
  Write-Host "Уже обновлено: $($target.Substring(0, 7)), ERP работает."
  exit 0
}
$next = Join-Path $frontend 'dist-next'
try {
  # База и сборки в .gitignore, их checkout не трогает; правки в файлах копии сбрасываются.
  Retry 'git checkout' { git checkout --force --detach origin/main }
  Write-Host '== Зависимости и сборка сайта'
  Retry 'pip install' { & $py -m pip install -q -r (Join-Path $backend 'requirements.lock.txt') }
  Set-Location $frontend
  # npm ci стирает node_modules и качает всё заново — на каждом обновлении лишний шанс упасть (сеть, занятый
  # антивирусом файл). Только когда сменился package-lock.json или сборка не прошла на прежних зависимостях.
  $lockHash = (Get-FileHash (Join-Path $frontend 'package-lock.json') -Algorithm SHA256).Hash
  $depsStamp = Join-Path $frontend 'node_modules\.erp-package-lock.sha256'
  $deps = if (Test-Path $depsStamp) { (Get-Content $depsStamp -Raw).Trim() } else { '' }
  $fresh = $false
  if ($deps -ne $lockHash) {
    Stop-StrayNode
    Retry 'npm ci' { npm ci --no-audit --no-fund }
    Set-Content $depsStamp $lockHash
    $fresh = $true
  } else { Write-Host 'Зависимости сайта те же — npm ci не нужен.' }
  try {
    Run 'сборка сайта' { npm run build -- --outDir dist-next --emptyOutDir }
    Run 'сборка генератора ТК' { npm run build:tcgen }
  } catch {
    if ($fresh) { throw }
    Write-Host "$_ — ставлю зависимости сайта заново и собираю ещё раз"
    Stop-StrayNode
    Retry 'npm ci' { npm ci --no-audit --no-fund }
    Set-Content $depsStamp $lockHash
    Run 'сборка сайта' { npm run build -- --outDir dist-next --emptyOutDir }
    Run 'сборка генератора ТК' { npm run build:tcgen }
  }
} catch {
  Set-Location $root
  Restore-Old "обновление не собралось ($_), ERP осталась на прежней версии"
}

Write-Host "== Останавливаю API на порту $Port"
$apiStopped = $true
Stop-Api
# ERP, запущенная не этим скриптом (другой пользователь, не python), держит порт и базу: миграции поверх неё не
# запускаем — прежняя версия остаётся работать.
for ($i = 0; $i -lt 10 -and (Test-Up); $i++) { Start-Sleep 1 }
if (Test-Up) {
  $holder = Get-PortHolder
  $why = if ($stopProblem) { "; $stopProblem" } else { '' }
  Write-Host "Порт $Port держит $holder$why"
  Restore-Old "ERP на порту $Port не остановилась ($holder$why) — новая версия не ставилась, ERP работает на прежней. Если ERP запускали из окна администратора, это снимет перезагрузка ПК"
}
Set-Location $backend
try {
  Copy-Item $db "$db.bak" -Force
  Write-Host "== Копия базы: $db.bak"
} catch {
  Restore-Old "копия базы не сделана ($_), миграции не запускались, ERP на прежней версии"
}
try {
  Run 'миграции базы' { & $py -m alembic upgrade head }
} catch {
  $reason = "$_"
  Write-Warning 'Миграции не прошли: возвращаю базу из копии, ERP остаётся на прежней версии.'
  $back = 'база возвращена из копии, ERP на прежней версии'
  try { Copy-Item "$db.bak" $db -Force } catch { $back = "база из копии не вернулась ($_), копия: $db.bak" }
  Restore-Old "$reason — $back"
}

Write-Host '== Сотрудники из SUPPLYMAP (выданные PIN не меняются, PIN получают только новые)'
$staff = & $py -m app.modules.identity.import_staff --apply 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -ne 0) { $staff | ForEach-Object { Write-Host $_ }; Write-Warning 'Сотрудники не обновлены (ошибка выше)' }
else {
  $new = @($staff | Where-Object { $_ -match ' создан\s*$' })
  if ($new.Count) { Write-Host 'Новые сотрудники, раздайте им PIN:'; $new | ForEach-Object { Write-Host "  $_" } }
  else { Write-Host 'Новых сотрудников нет.' }
}

# Ключ Google: только чтение таблиц; запоминается путь, не сам ключ.
$keyPath = Join-Path $backend 'google-key.path'
try {
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
} catch { Write-Warning "Ключ Google не найден: $_" }

$head = (git -C $root rev-parse HEAD).Trim()
$date = git -C $root log -1 --format='%cd' --date=format:'%d.%m.%Y %H:%M'
$news = @(if ($prev -ne $head) { Get-News $prev })
$info = [ordered]@{ version = $head; date = $date; news = $news } | ConvertTo-Json
try { [IO.File]::WriteAllText((Join-Path $next 'version.json'), $info, (New-Object Text.UTF8Encoding $false)) }
catch { Write-Warning "version.json не записан: $_" }

Write-Host '== Новый сайт и запуск API'
$dist = Join-Path $frontend 'dist'
$prevDist = Join-Path $frontend 'dist-prev'
# Флаги — чтобы откат трогал только то, что успели подменить: иначе сбой на первом же шаге стирал живой сайт.
$moved = $false; $swapped = $false
try {
  if (Test-Path $prevDist) { Remove-Item $prevDist -Recurse -Force }
  if (Test-Path $dist) { Move-Item $dist $prevDist; $moved = $true }
  Move-Item $next $dist; $swapped = $true
  Start-Api
} catch {
  # Новая версия не поднялась: база из копии, прежние код и сайт, прежняя ERP снова запущена.
  $reason = "новая версия не запустилась ($_)"
  Write-Warning "$reason, возвращаю прежнюю."
  $back = 'база, код и сайт прежние'
  try {
    Stop-Api
    Copy-Item "$db.bak" $db -Force
    if ($swapped) { Remove-Item $dist -Recurse -Force }
    if ($moved) { Move-Item $prevDist $dist }
  } catch { $back = "откат не доделан ($_), копия базы: $db.bak" }
  Restore-Old "$reason — $back"
}
# Версия записывается сразу после запуска: дальше только фон, его сбой не делает обновление несостоявшимся.
Set-Content $stamp $head
Start-Background

Write-Host ''
Write-Host "Версия: $($head.Substring(0, 7)) от $date"
if ($prev -eq $head) { Write-Host 'Что нового: ничего, версия та же, что и в прошлый раз.' }
else {
  Write-Host $(if ($prev) { 'Что нового с прошлого обновления:' } else { 'Последние правки (прошлое обновление не записано):' })
  $news | ForEach-Object { Write-Host "  $_" }
}
Write-Host "Готово: ERP на http://localhost:$Port"

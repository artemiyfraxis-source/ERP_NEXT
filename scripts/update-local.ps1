# Обновить локальную ERP (копия ERP_NEXT на ПК, база real.db) до свежего main, не трогая данные.
# Запуск из корня копии: powershell -ExecutionPolicy Bypass -File scripts\update-local.ps1
# Шаги: копия базы в backend\<база>.bak, main, зависимости, миграции, сотрудники (без --reset-pins),
# сборка сайта, перезапуск API на порту. В конце — версия и «Что нового» (влитые PR с прошлого обновления;
# коммит прошлого обновления хранится в backend\<база>.version).
param([string]$DbFile = 'real.db', [int]$Port = 8022)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = Resolve-Path "$PSScriptRoot\.."
$backend = Join-Path $root 'backend'
$py = Join-Path $backend '.venv\Scripts\python.exe'
$db = Join-Path $backend $DbFile
if (-not (Test-Path $db)) { throw "Нет базы $db — сначала scripts\load-real-data.ps1" }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "Ошибка: $what" } }

Write-Host "== Останавливаю API на порту $Port"
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
Start-Sleep 1
Copy-Item $db "$db.bak" -Force
Write-Host "== Копия базы: $db.bak"

Set-Location $root
Write-Host '== Свежий main'
git fetch origin main; Check 'git fetch'
# База и сборки в .gitignore, их checkout не трогает; правки в файлах копии сбрасываются.
git checkout --force --detach origin/main; Check 'git checkout'

Write-Host '== Зависимости и миграции'
& $py -m pip install -q -r (Join-Path $backend 'requirements.lock.txt'); Check 'pip install'
Set-Location $backend
$env:ERP_DATABASE_URL = "sqlite+pysqlite:///$DbFile"
$env:ERP_COOKIE_SECURE = '0'
& $py -m alembic upgrade head; Check 'alembic upgrade'

Write-Host '== Сотрудники из SUPPLYMAP (выданные PIN не меняются, PIN получают только новые)'
$staff = & $py -X utf8 -m app.modules.identity.import_staff --apply
if ($LASTEXITCODE -ne 0) { Write-Warning "Сотрудники не обновлены (ошибка выше)" }
else {
  $new = @($staff | Where-Object { "$_" -match ' создан\s*$' })
  if ($new.Count) { Write-Host "Новые сотрудники, раздайте им PIN:"; $new | ForEach-Object { Write-Host "  $_" } }
  else { Write-Host 'Новых сотрудников нет.' }
}

Write-Host '== Сборка сайта'
Set-Location (Join-Path $root 'frontend')
npm ci --no-audit --no-fund; Check 'npm ci'
npm run build; Check 'npm run build'

Write-Host "== Запускаю API на порту $Port"
Start-Process $py -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$Port" `
  -WorkingDirectory $backend -WindowStyle Hidden | Out-Null
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
  try { Invoke-RestMethod "http://127.0.0.1:$Port/api/health" | Out-Null; $ok = $true; break } catch { Start-Sleep 1 }
}
if (-not $ok) { throw "API не ответил на порту $Port" }
$head = git -C $root rev-parse HEAD
$stamp = "$db.version"
$prev = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { '' }
Write-Host ''
Write-Host "Версия: $(git -C $root log -1 --format='%h от %cd' --date=format:'%d.%m.%Y %H:%M')"
if ($prev -eq $head) { Write-Host 'Что нового: ничего, версия та же, что и в прошлый раз.' }
else {
  # Влитые PR по первой линии main: «Merge pull request #N» (название — первая строка описания) или «… (#N)».
  $range = if ($prev) { @("$prev..HEAD") } else { @('-15', 'HEAD') }
  $log = (git -C $root log --first-parent --format='%s%x1f%b%x1e' @range) -join "`n"
  $news = foreach ($entry in ($log -split [char]0x1e)) {
    $parts = $entry.Trim() -split [char]0x1f, 2
    if (-not $parts[0]) { continue }
    if ($parts[0] -match '^Merge pull request #(\d+)') {
      $title = (($parts[1] -split "`n") | Where-Object { $_.Trim() } | Select-Object -First 1)
      "  #$($Matches[1]) $title"
    } elseif ($parts[0] -match '^(.*) \(#(\d+)\)$') { "  #$($Matches[2]) $($Matches[1])" }
    else { "  $($parts[0])" }
  }
  Write-Host $(if ($prev) { 'Что нового с прошлого обновления:' } else { 'Последние правки (прошлое обновление не записано):' })
  $news | ForEach-Object { Write-Host $_ }
}
Set-Content $stamp $head
Write-Host "Готово: ERP на http://localhost:$Port"

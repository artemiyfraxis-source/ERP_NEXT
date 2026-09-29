# Обновить локальную ERP (копия ERP_NEXT на ПК, база real.db) до свежего main, не трогая данные.
# Запуск из корня копии: powershell -ExecutionPolicy Bypass -File scripts\update-local.ps1
# Шаги: копия базы в backend\<база>.bak, main, зависимости, миграции, сборка сайта, перезапуск API на порту.
param([string]$DbFile = 'real.db', [int]$Port = 8022)
$ErrorActionPreference = 'Stop'
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
Write-Host "Готово: ERP на http://localhost:$Port, версия" (git -C $root rev-parse --short HEAD)

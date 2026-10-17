# Local ERP_NEXT demo: fresh SQLite base with demo data, API on 8021, web on 5176.
# Run from the repo root: powershell -ExecutionPolicy Bypass -File scripts\demo\run-demo.ps1
param([int]$ApiPort = 8021, [int]$WebPort = 5176)
$ErrorActionPreference = 'Stop'
$env:ERP_API_PORT = "$ApiPort"; $env:ERP_WEB_PORT = "$WebPort"; $env:DEMO_BASE = "http://127.0.0.1:$ApiPort"
$root = Resolve-Path "$PSScriptRoot\..\.."
$backend = Join-Path $root 'backend'
$py = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { py -3.12 -m venv (Join-Path $backend '.venv') }
& $py -m pip install -q -r (Join-Path $backend 'requirements.lock.txt') httpx
Set-Location $backend
Remove-Item -ErrorAction SilentlyContinue demo.db
$env:ERP_DATABASE_URL = 'sqlite+pysqlite:///demo.db'
$env:ERP_COOKIE_SECURE = '0'
& $py -m alembic upgrade head
$env:ERP_NEW_USER_PASSWORD = 'Preview-2026!'; & $py -m app.identity_admin create-user --login preview --email preview@example.com --name 'Артемий' --department 'Руководящий состав' --role admin
& $py -m app.modules.references.load_mirror  # справочники из снимка backend\data\reference-mirror
& $py -m app.modules.techcards.seed_library
& $py -m app.modules.references.erp_corrections
& $py -m app.modules.references.load_cables  # монтажные провода по таблице сечений + карточки коаксиалов
$api = Start-Process $py -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$ApiPort" -WorkingDirectory $backend -PassThru
for ($i = 0; $i -lt 60; $i++) { try { Invoke-RestMethod http://127.0.0.1:$ApiPort/api/health | Out-Null; break } catch { Start-Sleep 1 } }
& $py (Join-Path $root 'scripts\demo\seed_demo.py')
Set-Location (Join-Path $root 'frontend')
if (-not (Test-Path node_modules)) { npm ci }
Start-Process powershell -ArgumentList '-NoExit','-Command','npm run dev' -WorkingDirectory (Get-Location)
Start-Sleep 5
Start-Process "http://localhost:$WebPort"
Write-Host "ERP: http://localhost:$WebPort  вход preview / Preview-2026!  (API PID $($api.Id))"

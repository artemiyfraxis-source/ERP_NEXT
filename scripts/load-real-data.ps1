# Чистая база ERP с реальными данными из Google (только чтение Google): справочники из зеркала SUPPLYMAP,
# библиотека техкарт, склад ЦАР/ЖУК/СПИСАНИЕ, проекты SUPPLYMAP (наборы, реестр, журнал, лист цеха).
# Запуск из корня копии ERP_NEXT, API этой базы должен быть остановлен:
#   powershell -ExecutionPolicy Bypass -File scripts\load-real-data.ps1 -Key C:\путь\ключ-erp-reader.json -Db demo.db
# База -Db пересоздаётся с нуля. Живые выгрузки кладутся в data\import (в .gitignore).
param([Parameter(Mandatory = $true)][string]$Key, [string]$Db = 'real.db',
      [string]$Login = 'preview', [string]$Password = 'Preview-2026!')
$ErrorActionPreference = 'Stop'
$root = Resolve-Path "$PSScriptRoot\.."
$backend = Join-Path $root 'backend'
$py = Join-Path $backend '.venv\Scripts\python.exe'
$Key = (Resolve-Path $Key).Path
$data = Join-Path $root 'data\import'
New-Item -ItemType Directory -Force $data | Out-Null
Set-Location $backend
$env:ERP_DATABASE_URL = "sqlite+pysqlite:///$Db"
$env:ERP_COOKIE_SECURE = '0'
function Run { & $py @args; if ($LASTEXITCODE -gt 1) { throw "Ошибка: $args" } }

Write-Host '== Снимки из Google (только чтение)'
Run ..\scripts\stock_export_google.py --key $Key --out $data
Run ..\scripts\projects_import.py snapshot --key $Key --out (Join-Path $data 'projects.json')

Write-Host "== Пересоздаю базу $Db"
Remove-Item -ErrorAction SilentlyContinue $Db
Run -m alembic upgrade head
"$Password`n$Password`n" | & $py -m app.identity_admin create-user --login $Login --email "$Login@example.com" --name 'Артемий' --department 'Руководство' --role admin
$supplymap = if ($env:SUPPLYMAP_SOURCE_REPOSITORY) { $env:SUPPLYMAP_SOURCE_REPOSITORY } else { Join-Path $root '..\SUPPLYMAP' }
Run -m app.modules.references.load_mirror --source $supplymap
Run -m app.modules.techcards.seed_library

Write-Host '== Склад'
Run -m app.modules.warehouse.import_stock --dir $data
Write-Host '== Проекты'
Run ..\scripts\projects_import.py apply (Join-Path $data 'projects.json') --login $Login --password $Password
Run -m app.outbox_worker --once
Write-Host "Готово: база $Db. Запустите API над ней (ERP_DATABASE_URL=$env:ERP_DATABASE_URL)."

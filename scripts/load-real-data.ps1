# Чистая база ERP с реальными данными из Google (только чтение Google): справочники из зеркала SUPPLYMAP,
# библиотека техкарт, склад ЦАР/ЖУК/СПИСАНИЕ и только те проекты SUPPLYMAP, что в списке data\projects.txt
# (владелец 30.09: проекты — по одному; каждый со своими ТК).
# Запуск из корня копии ERP_NEXT, API этой базы должен быть остановлен:
#   powershell -ExecutionPolicy Bypass -File scripts\load-real-data.ps1 -Key C:\путь\ключ-erp-reader.json -DbFile demo.db
# База -DbFile (не -Db: в PowerShell 5.1 это алиас -Debug) (SQLite) пересоздаётся с нуля. Боевая PostgreSQL: -DatabaseUrl postgresql+psycopg://... — пустая база,
# скрипт её не удаляет, только накатывает миграции. Живые выгрузки кладутся в data\import (в .gitignore).
# Догрузка склада в уже загруженную базу (новые партии, нетронутые в ERP партии — к Google, остальное в отчёт):
#   ... -Key ключ.json -DbFile demo.db -StockOnly [-DryRun]
param([Parameter(Mandatory = $true)][string]$Key, [string]$DbFile = 'real.db', [string]$DatabaseUrl = '',
      [string]$Login = 'preview', [string]$Password = 'Preview-2026!', [switch]$StockOnly, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
$root = Resolve-Path "$PSScriptRoot\.."
$backend = Join-Path $root 'backend'
$py = Join-Path $backend '.venv\Scripts\python.exe'
$Key = (Resolve-Path $Key).Path
$data = Join-Path $root 'data\import'
New-Item -ItemType Directory -Force $data | Out-Null
Set-Location $backend
$env:ERP_DATABASE_URL = if ($DatabaseUrl) { $DatabaseUrl } else { "sqlite+pysqlite:///$DbFile" }
$env:ERP_COOKIE_SECURE = '0'
function Run { & $py @args; if ($LASTEXITCODE -gt 1) { throw "Ошибка: $args" } }

if ($StockOnly) {
    Write-Host '== Догрузка склада из Google (только чтение Google)'
    Run ..\scripts\stock_export_google.py --key $Key --out $data
    Run -m alembic upgrade head
    if ($DryRun) { Run -m app.modules.warehouse.import_stock --dir $data --dry-run }
    else { Run -m app.modules.warehouse.import_stock --dir $data }
    exit 0
}

Write-Host '== Снимки из Google (только чтение)'
Run ..\scripts\stock_export_google.py --key $Key --out $data

if (-not $DatabaseUrl) { Write-Host "== Пересоздаю базу $DbFile"; Remove-Item -ErrorAction SilentlyContinue $DbFile }
Run -m alembic upgrade head
$env:ERP_NEW_USER_PASSWORD = $Password
& $py -m app.identity_admin create-user --login $Login --email "$Login@example.com" --name 'Артемий' --department 'Руководство' --role admin
Remove-Item Env:ERP_NEW_USER_PASSWORD
$supplymap = if ($env:SUPPLYMAP_SOURCE_REPOSITORY) { $env:SUPPLYMAP_SOURCE_REPOSITORY } else { Join-Path $root '..\SUPPLYMAP' }
Run -m app.modules.references.load_mirror --source $supplymap
Run -m app.modules.techcards.seed_library

Write-Host '== Склад'
Run -m app.modules.warehouse.import_stock --dir $data
Set-Content (Join-Path $backend 'google-key.path') $Key  # update-local.ps1 берёт ключ отсюда
$env:ERP_GOOGLE_SERVICE_ACCOUNT_JSON = $Key
Write-Host '== Проекты: только список data\projects.txt с их ТК (генератору нужен npm run build:tcgen в frontend)'
# Метка сноса ставится и в новой базе, иначе update-local.ps1 снёс бы загруженные по списку проекты.
Run -m app.workflows.projects_wipe
Run ..\scripts\projects_import.py load --key $Key
Run -m app.outbox_worker --once
Write-Host '== Наименования комплектующих с сайтов поставщиков'
Run -m app.workflows.kitset_names
Write-Host "Готово: база $env:ERP_DATABASE_URL. Запустите API над ней (ERP_DATABASE_URL=$env:ERP_DATABASE_URL)."

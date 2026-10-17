# Один проект из Google в локальную ERP — по запросу (владелец 30.09: проекты заводит он сам в ERP или по одному
# через чат; само обновление проекты не грузит). Карточка из расчёта, строка реестра, журнал, сверка с листом цеха,
# ТК этого проекта (выданные в SUPPLYMAP переносятся, остальным сборкам — генератор). Проект, который уже есть в ERP,
# не трогается. Google только читается, ключ — путь, запомненный update-local.ps1. ERP останавливать не нужно.
#   powershell -ExecutionPolicy Bypass -File scripts\load-project.ps1 708К.4 [ещё № …]
param([Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)][string[]]$Numbers, [string]$DbFile = 'real.db')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$backend = Join-Path (Resolve-Path "$PSScriptRoot\..").Path 'backend'
$py = Join-Path $backend '.venv\Scripts\python.exe'
$keyPath = Join-Path $backend 'google-key.path'
if (-not (Test-Path $keyPath)) { throw 'Нет ключа Google: один раз scripts\update-local.ps1 -Key путь\к\ключу.json' }
Set-Location $backend
$env:ERP_DATABASE_URL = "sqlite+pysqlite:///$DbFile"
& $py -X utf8 ..\scripts\projects_import.py load @Numbers --key (Get-Content $keyPath -Raw).Trim()
exit $LASTEXITCODE

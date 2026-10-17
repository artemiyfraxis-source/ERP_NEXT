# Move the owner's local ERP (ERP_NEXT-demo, backend\real.db) to the shop-floor server. Owner 01.10.2026: «давай всё
# что сейчас наработано и в гит и на серв, чтобы могли работать» — the local base holds what Google does not
# (project 719К.2 with its ТК, hand-fixed lots, requests), so the server continues from it.
#   .\deploy\move-local-db.ps1        # stops the local ERP for good, copies real.db, replaces the server data
# 1. auto-update off and the local ERP stopped (backend\erp.stop), so nothing is typed in after the snapshot;
# 2. consistent SQLite snapshot, kept on the PC as backend\real.db.moved-<time>;
# 3. server: backup first, api/worker stopped, the copy upgraded to the server's schema,
#    scripts/sqlite_to_postgres.py --yes (one transaction, row counts compared before COMMIT), api/worker started.
# A failure leaves the server data as it was. Logins afterwards are the local ones (same accounts and PINs).
param(
  [string]$Server = '192.168.1.85',
  [string]$Key = "$HOME\.ssh\erp_server",
  [string]$User = 'erp',
  [string]$Demo = "$HOME\Documents\AI\Projects\ERP_NEXT-demo",
  [string]$DbFile = 'real.db',
  [int]$Port = 8022
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ssh = @('-i', $Key, '-o', 'StrictHostKeyChecking=accept-new')
$target = "$User@$Server"
$sudo = if ($User -eq 'root') { '' } else { 'sudo -n ' }
$backend = Join-Path $Demo 'backend'
$db = Join-Path $backend $DbFile
$py = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $db)) { throw "нет локальной базы $db" }

Write-Host '== Выключаю автообновление и локальную ERP'
& powershell -ExecutionPolicy Bypass -File (Join-Path $Demo 'scripts\auto-update-local.ps1') -Uninstall
Set-Content (Join-Path $backend 'erp.stop') (Get-Date -Format 'dd.MM.yyyy HH:mm:ss')
for ($i = 0; $i -lt 45 -and (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue); $i++) {
  Start-Sleep 2
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
  Write-Warning "Порт $Port ещё занят: локальная ERP не остановилась. Снимок согласованный, но правки после него не перенесутся."
}

$snap = Join-Path $backend "$DbFile.moved-$(Get-Date -Format yyyyMMdd-HHmm)"
& $py -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" $db $snap
if ($LASTEXITCODE) { throw 'снимок базы не получился' }
Write-Host "Снимок: $snap ($([math]::Round((Get-Item $snap).Length / 1MB, 1)) МБ)"

& scp @ssh $snap "${target}:/tmp/real.db"
if ($LASTEXITCODE) { throw 'scp failed' }
$remote = @'
set -e
cp /tmp/real.db /opt/erp/import/real.db && chown 10001:10001 /opt/erp/import/real.db && chmod 600 /opt/erp/import/real.db
rm -f /tmp/real.db
cd /opt/erp/app/deploy
./erp.sh backup-now
trap './erp.sh up -d api worker >/dev/null' EXIT
./erp.sh stop api worker
./erp.sh run --rm -e ERP_DATABASE_URL=sqlite+pysqlite:////app/data/import/real.db api alembic upgrade head
./erp.sh run --rm api python ../scripts/sqlite_to_postgres.py --sqlite /app/data/import/real.db --yes
'@ -replace "`r", ''
$remote | & ssh @ssh $target "${sudo}bash -s"
if ($LASTEXITCODE) { throw "перенос не прошёл: данные сервера не тронуты, локальная база цела ($db)" }
Write-Host "Готово: ERP на http://$Server с данными локальной базы. Локальная ERP выключена, автообновление снято."
Write-Host 'Вход как на локальной: те же логины и PIN.'

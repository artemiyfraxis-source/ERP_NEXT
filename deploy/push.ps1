# Deploy from the owner's Windows PC: packs origin/main of ERP_NEXT (and SUPPLYMAP for the imports) with
# `git archive` — the working tree, branches and stash are not touched — copies it to the server and runs
# bootstrap.sh (first time) or update.sh (every next time, with a backup first).
#   .\deploy\push.ps1 -Server 203.0.113.10               # ERP from origin/main
#   .\deploy\push.ps1 -Server 203.0.113.10 -Ref <sha>    # a specific commit (rollback)
#   .\deploy\push.ps1 -Server 203.0.113.10 -GoogleKey $HOME\Downloads\techmap-498119-….json   # first time
param(
  [Parameter(Mandatory = $true)][string]$Server,
  [string]$Ref = 'origin/main',
  [string]$Key = "$HOME\.ssh\erp_server",
  [string]$Domain = '',
  [string]$GoogleKey = '',   # JSON-ключ erp-reader: кладётся в /opt/erp/config/secrets до установки

  [string]$ErpRepo = (Split-Path $PSScriptRoot -Parent),
  [string]$SupplymapRepo = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'SUPPLYMAP')
)
$ErrorActionPreference = 'Stop'
$ssh = @('-i', $Key, '-o', 'StrictHostKeyChecking=accept-new')
$target = "root@$Server"
$tmp = Join-Path $env:TEMP "erp-push-$(Get-Date -Format yyyyMMddHHmmss)"
New-Item -ItemType Directory $tmp | Out-Null

git -C $ErpRepo fetch origin
$rev = (git -C $ErpRepo rev-parse --short $Ref).Trim()
git -C $ErpRepo archive --format=tar.gz -o "$tmp\app.tar.gz" $Ref
if (Test-Path $SupplymapRepo) {
  git -C $SupplymapRepo fetch origin
  git -C $SupplymapRepo archive --format=tar.gz -o "$tmp\supplymap.tar.gz" origin/main
}

& scp @ssh "$tmp\*.tar.gz" "${target}:/tmp/"
if ($LASTEXITCODE) { throw 'scp failed' }
if ($GoogleKey) {
  & ssh @ssh $target 'mkdir -p /opt/erp/config/secrets && chmod 700 /opt/erp/config && chmod 755 /opt/erp/config/secrets'
  & scp @ssh $GoogleKey "${target}:/opt/erp/config/secrets/google-service-account.json"
  if ($LASTEXITCODE) { throw 'scp key failed' }
  & ssh @ssh $target 'chown 10001 /opt/erp/config/secrets/*.json && chmod 600 /opt/erp/config/secrets/*.json'
}
$remote = @"
set -e
mkdir -p /opt/erp/supplymap
if [ -f /tmp/supplymap.tar.gz ]; then rm -rf /opt/erp/supplymap/*; tar -xzf /tmp/supplymap.tar.gz -C /opt/erp/supplymap; fi
if [ -d /opt/erp/app ]; then
  rm -rf /opt/erp/app.new; mkdir -p /opt/erp/app.new; tar -xzf /tmp/app.tar.gz -C /opt/erp/app.new
  echo $rev > /opt/erp/app.new/REVISION; bash /opt/erp/app.new/deploy/update.sh
else
  mkdir -p /opt/erp/app; tar -xzf /tmp/app.tar.gz -C /opt/erp/app
  echo $rev > /opt/erp/app/REVISION; bash /opt/erp/app/deploy/bootstrap.sh $Domain
fi
rm -f /tmp/app.tar.gz /tmp/supplymap.tar.gz
"@
$remote = $remote -replace "`r", ''
$remote | & ssh @ssh $target 'bash -s'
if ($LASTEXITCODE) { throw "deploy failed on $Server" }
Remove-Item -Recurse -Force $tmp
Write-Host "Выложено: $rev"

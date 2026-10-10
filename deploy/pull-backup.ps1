<#
Копия базы вне сервера (владелец, 10.10.2026): этот ПК по Tailscale забирает с сервера свежие копии базы.
Берёт самую новую копию из backups/daily (и, если ещё нет, из weekly и monthly): .dump + .sha256 -> <Dest>\<вид>\.
Файл сначала качается под именем .part, сверяется SHA-256 с .sha256 с сервера и только потом получает своё имя;
при расхождении .part удаляется, скрипт завершается с ненулевым кодом. Ключи и пароли в репозитории не хранятся:
ключ ssh лежит у владельца в ~\.ssh\erp_server.
Запуск: powershell -NoProfile -ExecutionPolicy Bypass -File deploy\pull-backup.ps1
Расписание: deploy\register-pull-backup.ps1. Журнал: <Dest>\pull-backup.log (одна строка за запуск).
Восстановление из такой копии: deploy/README.md, раздел «Копия вне сервера».
#>
param(
    [string]$Server = $(if ($env:ERP_BACKUP_SERVER) { $env:ERP_BACKUP_SERVER } else { '100.96.49.68' }),
    [string]$User = 'erp',
    [string]$Key = (Join-Path $HOME '.ssh\erp_server'),
    [string]$Dest = (Join-Path $HOME 'Documents\ERP-backups'),
    [int]$KeepDaily = 14
)

$ErrorActionPreference = 'Stop'
$RemoteRoot = '/opt/erp/backups'
$SshOpts = @('-i', $Key, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15')
$Target = "$User@$Server"
$Results = New-Object System.Collections.ArrayList
$Failed = $false

function Write-RunLog([string]$Text) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Text
    try {
        New-Item -ItemType Directory -Force -Path $Dest | Out-Null
        Add-Content -Path (Join-Path $Dest 'pull-backup.log') -Value $line -Encoding UTF8
    } catch { }
    Write-Host $line
}

function Get-NewestRemote([string]$Kind) {
    # Имена erp_<дата_время>.dump сортируются по времени; `$` в конце — для grep на сервере.
    $cmd = 'ls -1 ' + $RemoteRoot + '/' + $Kind + '/ | grep dump$ | sort | tail -n 1'
    $out = & ssh @SshOpts $Target $cmd
    if ($LASTEXITCODE -ne 0) { throw "ssh $Kind -> код $LASTEXITCODE" }
    $name = ("$out").Trim()
    if (-not $name) { return $null }
    if ($name -notmatch '^erp_[0-9A-Za-z_.-]+\.dump$') { throw "неожиданное имя копии на сервере: $name" }
    return $name
}

function Get-Hash256([string]$ShaFile) {
    $text = (Get-Content -Raw -Path $ShaFile).Trim()
    $token = ($text -split '\s+')[0]
    if ($token -notmatch '^[0-9A-Fa-f]{64}$') { throw "файл $ShaFile не похож на sha256" }
    return $token.ToLower()
}

function Pull-One([string]$Kind) {
    $name = Get-NewestRemote $Kind
    if (-not $name) { [void]$Results.Add("$Kind=нет копий на сервере"); return }
    $dir = Join-Path $Dest $Kind
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    if ((Test-Path (Join-Path $dir $name)) -and (Test-Path (Join-Path $dir ($name + '.sha256')))) {
        [void]$Results.Add("$Kind=уже есть $name"); return
    }
    Push-Location $dir
    try {
        $parts = @("$name.part", "$name.sha256.part")
        Remove-Item -Force -ErrorAction SilentlyContinue $parts
        & scp @SshOpts "${Target}:$RemoteRoot/$Kind/$name" "$name.part"
        if ($LASTEXITCODE -ne 0) { throw "scp $name -> код $LASTEXITCODE" }
        & scp @SshOpts "${Target}:$RemoteRoot/$Kind/$name.sha256" "$name.sha256.part"
        if ($LASTEXITCODE -ne 0) { throw "scp $name.sha256 -> код $LASTEXITCODE" }
        $expected = Get-Hash256 "$name.sha256.part"
        $actual = (Get-FileHash -Algorithm SHA256 -Path "$name.part").Hash.ToLower()
        if ($actual -ne $expected) {
            throw "SHA-256 $name не совпал (сервер $expected, здесь $actual), файл удалён"
        }
        Move-Item -Force "$name.part" $name
        Move-Item -Force "$name.sha256.part" "$name.sha256"
        $mb = [math]::Round((Get-Item $name).Length / 1MB, 1)
        [void]$Results.Add("$Kind=скачано $name ($mb МБ, sha256 сошёлся)")
    } finally {
        # Остатки .part (сбой скачивания или несовпадение суммы) не оставляем.
        Remove-Item -Force -ErrorAction SilentlyContinue ("$name.part", "$name.sha256.part")
        Pop-Location
    }
}

foreach ($kind in @('daily', 'weekly', 'monthly')) {
    try { Pull-One $kind } catch { $Failed = $true; [void]$Results.Add("$kind=ОШИБКА $($_.Exception.Message)") }
}

# Ротация: в daily остаются KeepDaily самых новых копий (weekly и monthly не трогаем). Только после успешной загрузки.
if (-not $Failed -and $KeepDaily -gt 0) {
    try {
        $dumps = @(Get-ChildItem -Path (Join-Path $Dest 'daily') -Filter 'erp_*.dump' -File | Sort-Object Name -Descending)
        $old = @($dumps | Select-Object -Skip $KeepDaily)
        foreach ($f in $old) {
            Remove-Item -Force $f.FullName, ($f.FullName + '.sha256') -ErrorAction SilentlyContinue
        }
        if ($old.Count -gt 0) { [void]$Results.Add("ротация daily: удалено $($old.Count)") }
    } catch { $Failed = $true; [void]$Results.Add("ротация=ОШИБКА $($_.Exception.Message)") }
}

$status = if ($Failed) { 'СБОЙ' } else { 'ОК' }
Write-RunLog ("$status; " + ($Results -join '; '))
if ($Failed) { exit 1 }
exit 0

# Ручной тест без принтера: мок-сервер на localhost + агент в режиме -DryRun -Once.
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\print-agent\tests\mock-server.ps1
# Ожидается: claim отдал задание, агент записал "DRY RUN" в журнал и отправил result ok=false "dry run".
$ErrorActionPreference = 'Stop'
$agent = Join-Path $PSScriptRoot '..\agent.ps1'
$log = Join-Path ([IO.Path]::GetTempPath()) 'fraxis-agent-mock.log'
Remove-Item $log -ErrorAction SilentlyContinue
$port = 18765

$job = Start-Job -ArgumentList $port -ScriptBlock {
    param($port)
    $l = New-Object Net.HttpListener
    $l.Prefixes.Add("http://localhost:$port/")
    $l.Start()
    $seen = @()
    $claimed = $false
    while ($seen.Count -lt 2) {
        $c = $l.GetContext()
        $body = (New-Object IO.StreamReader($c.Request.InputStream, [Text.Encoding]::UTF8)).ReadToEnd()
        $seen += ('{0} {1} auth={2} body={3}' -f $c.Request.HttpMethod, $c.Request.Url.AbsolutePath, $c.Request.Headers['Authorization'], $body)
        if ($c.Request.Url.AbsolutePath -like '*/claim') {
            $b64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("SIZE 40 mm,30 mm`r`nPRINT 1`r`n"))
            $resp = '{"job":{"id":"job-1","copies":1,"tspl_b64":"' + $b64 + '"}}'
        } else { $resp = '{"ok":true}' }
        $bytes = [Text.Encoding]::UTF8.GetBytes($resp)
        $c.Response.ContentType = 'application/json'
        $c.Response.OutputStream.Write($bytes, 0, $bytes.Length)
        $c.Response.Close()
    }
    $l.Stop()
    $seen
}
Start-Sleep -Seconds 1
& $agent -Server "http://localhost:$port" -Token 'TEST-TOKEN' -Printer 'TSC TEST' -LogPath $log -ConfigPath 'none' -DryRun -Once
$seen = Receive-Job $job -Wait
$seen
'--- log ---'
Get-Content $log
if (($seen -join "`n") -match '"ok":false' -and ($seen -join "`n") -match 'dry run') { 'OK' } else { throw 'FAIL' }

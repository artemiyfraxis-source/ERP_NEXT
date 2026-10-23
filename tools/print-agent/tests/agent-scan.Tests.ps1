# Pester (3.x/5.x): multipart-тело скана и опрос заданий сканирования (сканер и сеть замоканы).
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('pa-' + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
. (Join-Path $here '..\agent.ps1') -LogPath (Join-Path $tmp 'a.log') -ConfigPath (Join-Path $tmp 'none.json')

Describe 'New-MultipartBody' {
    It 'бинарные байты сохраняются, границы и CRLF на месте' {
        $bytes = [byte[]](0, 255, 13, 10, 37, 80, 68, 70)
        $body = New-MultipartBody -Boundary 'B1' -FieldName 'file' -FileName 'scan.pdf' -ContentType 'application/pdf' -Bytes $bytes
        $text = [Text.Encoding]::GetEncoding(28591).GetString($body)
        $text.StartsWith("--B1`r`nContent-Disposition: form-data; name=`"file`"; filename=`"scan.pdf`"`r`nContent-Type: application/pdf`r`n`r`n") | Should Be $true
        $text.EndsWith("`r`n--B1--`r`n") | Should Be $true
        $head = "--B1`r`nContent-Disposition: form-data; name=`"file`"; filename=`"scan.pdf`"`r`nContent-Type: application/pdf`r`n`r`n".Length
        ($body[$head..($head + 7)] -join ',') | Should Be ($bytes -join ',')
    }
}

Describe 'Invoke-ScanPollOnce' {
    It 'нет задания -> idle, сканера не трогаем' {
        Mock Invoke-ErpApi { [pscustomobject]@{ job = $null } }
        Mock Invoke-ScannerScan {}
        Invoke-ScanPollOnce -Base 'http://x' -AuthToken 't' -ScannerAddress '' | Should Be 'idle'
        Assert-MockCalled Invoke-ScannerScan -Times 0 -Exactly -Scope It
    }
    It 'задание -> скан и загрузка файла' {
        Mock Invoke-ErpApi { [pscustomobject]@{ job = [pscustomobject]@{ id = 's1' } } }
        Mock Invoke-ScannerScan { @{ Bytes = [byte[]](1, 2, 3); Ext = 'pdf'; Mime = 'application/pdf' } }
        Mock Send-ScanFile {}
        Invoke-ScanPollOnce -Base 'http://x' -AuthToken 't' -ScannerAddress '10.0.0.5' | Should Be 'done'
        Assert-MockCalled Send-ScanFile -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/scan/agent/jobs/s1/result' -and $ContentType -eq 'application/pdf' }
    }
    It 'ошибка сканера -> result {error}, цикл не падает' {
        Mock Invoke-ErpApi -ParameterFilter { $Url -like '*/claim' } { [pscustomobject]@{ job = [pscustomobject]@{ id = 's2' } } }
        Mock Invoke-ErpApi -ParameterFilter { $Url -like '*/result' } {}
        Mock Invoke-ScannerScan { throw 'Сканер: нет связи' }
        Invoke-ScanPollOnce -Base 'http://x' -AuthToken 't' -ScannerAddress '10.0.0.5' | Should Be 'error'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/jobs/s2/result' -and $Body.error -like '*нет связи*' }
    }
}

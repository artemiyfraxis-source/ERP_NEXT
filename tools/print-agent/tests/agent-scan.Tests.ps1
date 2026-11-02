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

# Отдельный Describe: в Pester 3 мок с ParameterFilter из предыдущего It перекрывает мок без фильтра.
Describe 'Invoke-ScanPollOnce: выбранный сканер' {
    It 'задание с выбранным сканером -> сканирует им' {
        Mock Invoke-ErpApi { [pscustomobject]@{ job = [pscustomobject]@{ id = 's3'; target_scanner = '{wia-2}' } } }
        Mock Invoke-ScannerScan { @{ Bytes = [byte[]](1); Ext = 'jpg'; Mime = 'image/jpeg' } }
        Mock Send-ScanFile {}
        Invoke-ScanPollOnce -Base 'http://x' -AuthToken 't' -ScannerAddress '' | Should Be 'done'
        Assert-MockCalled Invoke-ScannerScan -Times 1 -Exactly -Scope It -ParameterFilter { $Device -eq '{wia-2}' -and -not $Address }
    }
    It 'задание без выбора (старый сервер) передаёт отсутствие выбора адаптеру' {
        Mock Invoke-ErpApi { [pscustomobject]@{ job = [pscustomobject]@{ id = 's4' } } }
        Mock Invoke-ScannerScan { @{ Bytes = [byte[]](1); Ext = 'jpg'; Mime = 'image/jpeg' } }
        Mock Send-ScanFile {}
        Invoke-ScanPollOnce -Base 'http://x' -AuthToken 't' -ScannerAddress '' | Should Be 'done'
        Assert-MockCalled Invoke-ScannerScan -Times 1 -Exactly -Scope It -ParameterFilter { -not $Device }
    }
}

Describe 'Select-WiaDevice' {
    $printer = [pscustomobject]@{ Type = 2; DeviceID = '{p}'; Name = 'Принтер' }
    $first = [pscustomobject]@{ Type = 1; DeviceID = '{wia-1}'; Name = 'Pantum BM2300W' }
    $second = [pscustomobject]@{ Type = 1; DeviceID = '{wia-2}'; Name = 'Canon LiDE' }
    It 'выбранный по DeviceID' {
        (Select-WiaDevice -DeviceInfos @($printer, $first, $second) -Device '{wia-2}').DeviceID | Should Be '{wia-2}'
    }
    It 'выбранный по имени (агент прислал только имя)' {
        (Select-WiaDevice -DeviceInfos @($printer, $first, $second) -Device 'Canon LiDE').DeviceID | Should Be '{wia-2}'
    }
    It 'исчезнувший выбор не подменяется первым устройством' {
        Select-WiaDevice -DeviceInfos @($printer, $first, $second) -Device '{gone}' | Should Be $null
    }
    It 'без выбора разрешён только единственный сканер' {
        { Select-WiaDevice -DeviceInfos @($printer, $first, $second) -Device '' } | Should Throw
        (Select-WiaDevice -DeviceInfos @($printer, $first) -Device '').DeviceID | Should Be '{wia-1}'
    }
    It 'три сканера доступны по id; прочие типы исключены' {
        $third = [pscustomobject]@{ Type = 1; DeviceID = '{wia-3}'; Name = 'Epson' }
        foreach ($id in '{wia-1}', '{wia-2}', '{wia-3}') {
            (Select-WiaDevice -DeviceInfos @($printer, $first, $second, $third) -Device $id).DeviceID | Should Be $id
        }
        Select-WiaDevice -DeviceInfos @($printer, $first) -Device '{p}' | Should Be $null
    }
    It 'сканеров нет -> $null' {
        Select-WiaDevice -DeviceInfos @($printer) -Device '{wia-1}' | Should Be $null
    }
    It 'одинаковые имена требуют id устройства' {
        $same = [pscustomobject]@{ Type = 1; DeviceID = '{wia-3}'; Name = 'Canon LiDE' }
        { Select-WiaDevice -DeviceInfos @($second, $same) -Device 'Canon LiDE' } | Should Throw
        (Select-WiaDevice -DeviceInfos @($second, $same) -Device '{wia-3}').DeviceID | Should Be '{wia-3}'
    }
}

Describe 'Invoke-WiaScan: устройство исчезло' {
    It 'не вызывает Connect или Transfer другого устройства' {
        $script:connected = $false
        $other = [pscustomobject]@{ Type = 1; DeviceID = '{other}'; Name = 'Другой' }
        $other | Add-Member -MemberType ScriptMethod -Name Connect -Value { $script:connected = $true; throw 'Не должен подключаться' }
        Mock New-Object -ParameterFilter { $ComObject -eq 'WIA.DeviceManager' } { [pscustomobject]@{ DeviceInfos = @($other) } }
        { Invoke-WiaScan -Device '{gone}' } | Should Throw 'Выбранный сканер WIA не найден'
        $script:connected | Should Be $false
    }
}

Describe 'Invoke-ScannerScan: строгий выбор' {
    It 'выбранный WIA сканер не заменяется настроенным eSCL' {
        Mock Invoke-EsclScan { throw 'Не должен вызываться' }
        Mock Invoke-WiaScan { @{ Ext = 'jpg' } }
        (Invoke-ScannerScan -Address '10.0.0.5' -Device '{wia-2}').Ext | Should Be 'jpg'
        Assert-MockCalled Invoke-EsclScan -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-WiaScan -Times 1 -Exactly -Scope It -ParameterFilter { $Device -eq '{wia-2}' }
    }
    It 'выбранный адрес eSCL совпадает с настройкой' {
        Mock Invoke-EsclScan { @{ Ext = 'pdf' } }
        (Invoke-ScannerScan -Address '10.0.0.5' -Device '10.0.0.5').Ext | Should Be 'pdf'
        Assert-MockCalled Invoke-EsclScan -Times 1 -Exactly -Scope It -ParameterFilter { $Address -eq '10.0.0.5' }
    }
}

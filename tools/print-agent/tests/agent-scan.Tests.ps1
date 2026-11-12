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
        Assert-MockCalled Send-ScanFile -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/scan/agent/jobs/s1/result' -and $ContentType -eq 'application/pdf' -and $FileName -eq 'capture.pdf' }
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

Describe 'PDF assembly' {
    It 'embeds RGB JPEG pages with physical dimensions and valid byte offsets' {
        Add-Type -AssemblyName System.Drawing
        $bitmap = New-Object Drawing.Bitmap(300, 600)
        $ms = New-Object IO.MemoryStream
        try {
            $bitmap.Save($ms, [Drawing.Imaging.ImageFormat]::Bmp)
            $page = ConvertTo-ScanPage -Bytes $ms.ToArray()
        } finally { $bitmap.Dispose(); $ms.Dispose() }
        $page.Width | Should Be 300
        $page.Height | Should Be 600
        $page.Bytes[0] | Should Be 255
        $page.Bytes[1] | Should Be 216
        foreach ($count in @(1, 2)) {
            $pages = @($page)
            if ($count -eq 2) { $pages += $page }
            $pdf = New-ScanPdf -Pages $pages
            ($pdf -is [byte[]]) | Should Be $true
            $text = [Text.Encoding]::GetEncoding(28591).GetString($pdf)
            $text.StartsWith('%PDF-1.4') | Should Be $true
            $text.Contains("/Count $count ") | Should Be $true
            $text.Contains('/MediaBox [0 0 72 144]') | Should Be $true
            ([regex]::Matches($text, '/Filter /DCTDecode')).Count | Should Be $count
            $jpeg = [Text.Encoding]::GetEncoding(28591).GetString($page.Bytes)
            $text.Contains($jpeg) | Should Be $true
            $start = [int][regex]::Match($text, 'startxref\n(\d+)').Groups[1].Value
            $text.Substring($start, 4) | Should Be 'xref'
            $entries = [regex]::Matches($text, '(?m)^(\d{10}) 00000 n ')
            $entries.Count | Should Be (2 + 3 * $count)
            for ($i = 0; $i -lt $entries.Count; $i++) {
                $offset = [int]$entries[$i].Groups[1].Value
                $text.Substring($offset).StartsWith("$($i + 1) 0 obj`n") | Should Be $true
            }
        }
    }
    It 'uses invariant PDF numbers under Russian locale' {
        $old = [Threading.Thread]::CurrentThread.CurrentCulture
        try {
            [Threading.Thread]::CurrentThread.CurrentCulture = 'ru-RU'
            $pdf = New-ScanPdf -Pages @(@{ Bytes = [byte[]](1); Width = 301; Height = 601 })
            [Text.Encoding]::ASCII.GetString($pdf).Contains('/MediaBox [0 0 72.24 144.24]') | Should Be $true
        } finally { [Threading.Thread]::CurrentThread.CurrentCulture = $old }
    }
}

Describe 'PDF fallback' {
    It 'logs the cause and returns the scanned JPEG without rescanning' {
        Mock New-ScanPdf { throw 'pdf failure' }
        Mock Write-Log {}
        $result = ConvertTo-ScanDocument -Pages @(@{ Bytes = [byte[]](1, 2) })
        $result.Ext | Should Be 'jpg'
        $result.Mime | Should Be 'image/jpeg'
        ($result.Bytes -join ',') | Should Be '1,2'
        Assert-MockCalled Write-Log -Times 1 -Scope It -ParameterFilter { $Message -like '*pdf failure*' }
    }
    It 'does not silently lose additional pages if PDF assembly fails' {
        Mock New-ScanPdf { throw 'pdf failure' }
        Mock Write-Log {}
        { ConvertTo-ScanDocument -Pages @(@{ Bytes = [byte[]](1) }, @{ Bytes = [byte[]](2) }) } | Should Throw
    }
}

Describe 'WIA feeder acquisition' {
    # Fake the COM property collections and Transfer; exercise the real acquisition loop.
    function New-FakeWiaScanner {
        param([bool]$Feeder, [int]$ErrorCode = -2145320957)
        $script:transfers = 0
        $script:scanError = $ErrorCode
        $props = @{ '3087' = @{ Value = [int]$Feeder }; '3088' = @{ Value = 0 } }
        $props | Add-Member ScriptMethod Item { param($key) $this[$key] } -Force
        $itemProps = @{ '6146' = @{ Value = 2 }; '6147' = @{ Value = 300 }; '6148' = @{ Value = 300 } }
        $itemProps | Add-Member ScriptMethod Item { param($key) $this[$key] } -Force
        $item = [pscustomobject]@{ Properties = $itemProps }
        $item | Add-Member ScriptMethod Transfer {
            param($format)
            $script:transfers++
            if ($script:transfers -gt 2) { throw (New-Object Runtime.InteropServices.COMException('wia error', $script:scanError)) }
            [pscustomobject]@{ FileData = @{ BinaryData = [byte[]]@($script:transfers) } }
        }
        $items = [pscustomobject]@{ ScanItem = $item }
        $items | Add-Member ScriptMethod Item { param($index) $this.ScanItem }
        $scanner = [pscustomobject]@{ Properties = $props; Items = $items }
        $info = [pscustomobject]@{ Scanner = $scanner }
        $info | Add-Member ScriptMethod Connect { $this.Scanner }
        return $info
    }
    It 'reads a flatbed only once' {
        $script:fakeInfo = New-FakeWiaScanner -Feeder $false
        Mock New-Object -ParameterFilter { $ComObject -eq 'WIA.DeviceManager' } { @{ DeviceInfos = @() } }
        Mock Select-WiaDevice { $script:fakeInfo }
        Mock ConvertTo-ScanPage { @{ Bytes = $Bytes; Width = 300; Height = 600 } }
        $result = Invoke-WiaScan
        $script:transfers | Should Be 1
        $result.Mime | Should Be 'application/pdf'
    }
    It 'reads all feeder sheets and ends only on paper empty' {
        $script:fakeInfo = New-FakeWiaScanner -Feeder $true
        Mock New-Object -ParameterFilter { $ComObject -eq 'WIA.DeviceManager' } { @{ DeviceInfos = @() } }
        Mock Select-WiaDevice { $script:fakeInfo }
        Mock ConvertTo-ScanPage { @{ Bytes = $Bytes; Width = 300; Height = 600 } }
        $result = Invoke-WiaScan
        $script:transfers | Should Be 3
        [Text.Encoding]::ASCII.GetString($result.Bytes).Contains('/Count 2 ') | Should Be $true
        $script:fakeInfo.Scanner.Properties.Item('3088').Value | Should Be 0
        $script:fakeInfo.Scanner.Properties.Item('3087').Value = 0
        $script:transfers = 0
        $next = Invoke-WiaScan
        $script:transfers | Should Be 1
        [Text.Encoding]::ASCII.GetString($next.Bytes).Contains('/Count 1 ') | Should Be $true
    }
    It 'propagates a jam after pages instead of uploading a partial document' {
        $script:fakeInfo = New-FakeWiaScanner -Feeder $true -ErrorCode -2145320958
        Mock New-Object -ParameterFilter { $ComObject -eq 'WIA.DeviceManager' } { @{ DeviceInfos = @() } }
        Mock Select-WiaDevice { $script:fakeInfo }
        Mock ConvertTo-ScanPage { @{ Bytes = $Bytes; Width = 300; Height = 600 } }
        { Invoke-WiaScan } | Should Throw
        $script:fakeInfo.Scanner.Properties.Item('3088').Value | Should Be 0
    }
}

Describe 'JPEG-only eSCL scanner' {
    It 'wraps the JPEG as PDF' {
        Mock Invoke-EsclScan { @{ Bytes = [byte[]](1); Mime = 'image/jpeg'; Ext = 'jpg' } }
        Mock ConvertTo-ScanPage { @{ Bytes = [byte[]](1); Width = 300; Height = 600 } }
        $result = Invoke-ScannerScan -Address '10.0.0.5'
        $result.Mime | Should Be 'application/pdf'
        $result.Ext | Should Be 'pdf'
    }
}

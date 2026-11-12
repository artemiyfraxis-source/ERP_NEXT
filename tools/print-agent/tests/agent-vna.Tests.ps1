# Pester 3+: every COM boundary is mocked. Never activate or attach to a real VNA.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('pa-vna-' + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
. (Join-Path $here '..\agent.ps1') -LogPath (Join-Path $tmp 'a.log') -ConfigPath (Join-Path $tmp 'none.json')

function New-TestVna {
    $activeTrace = [pscustomobject]@{ ACTive = 2 }
    $channel = [pscustomobject]@{ ActiveTrace = $activeTrace }
    $channel | Add-Member ScriptMethod TRACe { param($index) $this.ActiveTrace }
    $service = [pscustomobject]@{ ChannelValue = $channel }
    $service | Add-Member ScriptMethod CHANnel { param($index) $this.ChannelValue }
    $calc = [pscustomobject]@{
        Parameters = @{ 1 = [pscustomobject]@{ DEFine = 'S21' }; 2 = [pscustomobject]@{ DEFine = 'S11' } }
        SELected = [pscustomobject]@{
            DATA = [pscustomobject]@{ SDATa = [double[]](0.1, -0.2, 0.25, 0.5) }
            SMOothing = [pscustomobject]@{ STATe = $true; APERture = 1.5 }
            CORRection = [pscustomobject]@{ STATus = 'C?' }
        }
    }
    $calc | Add-Member ScriptMethod PARameter { param($index) $this.Parameters[$index] }
    $scpi = [pscustomobject]@{
        SERVice = $service; Calculation = $calc
        SenseValue = [pscustomobject]@{
            FREQuency = [pscustomobject]@{ DATA = [double[]](100000000, 200000000); STARt = 100000000; STOP = 200000000 }
            SWEep = [pscustomobject]@{ POINts = 2 }
            BANDwidth = [pscustomobject]@{ RESolution = 1000 }
            AVERage = [pscustomobject]@{ STATe = $true; COUNt = 4 }
            CORRection = [pscustomobject]@{ STATe = $true }
        }
    }
    $scpi | Add-Member ScriptMethod CALCulate { param($index) if ($index -ne 1) { throw 'wrong channel' }; $this.Calculation }
    $scpi | Add-Member ScriptMethod SENSe { param($index) $this.SenseValue }
    return [pscustomobject]@{ Ready = $true; Name = 'Planar, S50180, 26030095, 26.2.2/2'; SCPI = $scpi }
}

Describe 'ВАЦ: чтение COM и Touchstone' {
    BeforeEach {
        $script:vna = New-TestVna
        Mock Get-VnaApplication { $script:vna }
        Mock Save-VnaName {}
    }
    It 'выбранная трасса 2, RI, герцы и десятичная точка даже в русской локали' {
        $savedCulture = [Threading.Thread]::CurrentThread.CurrentCulture
        try {
            [Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo('ru-RU')
            $result = Invoke-VnaCapture
        } finally { [Threading.Thread]::CurrentThread.CurrentCulture = $savedCulture }
        $result.Ext | Should Be 's1p'
        $result.Name | Should Be $script:vna.Name
        Assert-MockCalled Save-VnaName -Times 1 -Exactly -Scope It -ParameterFilter { $Name -eq $script:vna.Name }
        $text = [Text.Encoding]::UTF8.GetString($result.Bytes)
        $lines = $text.Trim() -split '\r?\n'
        $lines.Count | Should Be 4
        $lines[0] | Should Match '^! S2VNA Planar, S50180, 26030095, 26.2.2/2 \d{4}-.* S11$'
        $lines[1] | Should Be '# Hz S RI R 50'
        $lines[2] | Should Match '^100000000 0\.1\d* -0\.2\d*$'
        $lines[3] | Should Be '200000000 0.25 0.5'
        $script:vna.SCPI.Calculation.Parameters[1].DEFine | Should Be 'S21'
        $script:vna.SCPI.SERVice.ChannelValue.ActiveTrace.ACTive | Should Be 2
    }
    It 'S22 тоже даёт однопортовый Touchstone' {
        $script:vna.SCPI.Calculation.Parameters[2].DEFine = 'S22'
        [Text.Encoding]::UTF8.GetString((Invoke-VnaCapture).Bytes) | Should Match ' S22\r?\n# Hz S RI R 50'
    }
    It 'читает все подтверждённые настройки, не изменяя состояние COM' {
        $before = [Management.Automation.PSSerializer]::Serialize($script:vna, 10)
        $settings = (Invoke-VnaCapture).CaptureSettings
        $settings.points | Should Be 2
        $settings.start_hz | Should Be 100000000
        $settings.stop_hz | Should Be 200000000
        $settings.if_bandwidth_hz | Should Be 1000
        $settings.averaging_enabled | Should Be $true
        $settings.averaging_factor | Should Be 4
        $settings.smoothing_enabled | Should Be $true
        $settings.smoothing_aperture_percent | Should Be 1.5
        $settings.correction_enabled | Should Be $true
        $settings.correction_status | Should Be 'C?'
        $settings.s_parameter | Should Be 'S11'
        $settings.frequency_step_hz | Should Be 100000000
        $settings.unavailable | Should Be $null
        ([Management.Automation.PSSerializer]::Serialize($script:vna, 10)) | Should Be $before
    }
    It 'неизвестный статус и ошибка необязательного getter не мешают снять трассу' {
        $script:vna.SCPI.Calculation.SELected.CORRection.STATus = 'undocumented'
        $script:vna.SCPI.SenseValue.BANDwidth | Add-Member ScriptProperty RESolution { throw 'COM getter unavailable' } -Force
        $capture = Invoke-VnaCapture
        $capture.Bytes.Length | Should BeGreaterThan 0
        $capture.CaptureSettings.Contains('if_bandwidth_hz') | Should Be $false
        $capture.CaptureSettings.Contains('correction_status') | Should Be $false
        ($capture.CaptureSettings.unavailable -join ',') | Should Match 'if_bandwidth_hz'
        ($capture.CaptureSettings.unavailable -join ',') | Should Match 'correction_status'
        $capture.CaptureSettings.smoothing_enabled | Should Be $true
    }
    It 'отсутствующая настройка не превращается в выключенное усреднение' {
        $script:vna.SCPI.SenseValue.AVERage = $null
        $settings = (Invoke-VnaCapture).CaptureSettings
        $settings.Contains('averaging_enabled') | Should Be $false
        ($settings.unavailable -join ',') | Should Match 'averaging_enabled'
    }
    It 'передаёт выключенные настройки и экстраполяцию без отказа' {
        $script:vna.SCPI.SenseValue.AVERage.STATe = $false
        $script:vna.SCPI.SenseValue.CORRection.STATe = $false
        $script:vna.SCPI.Calculation.SELected.CORRection.STATus = 'C!'
        $settings = (Invoke-VnaCapture).CaptureSettings
        $settings.averaging_enabled | Should Be $false
        $settings.correction_enabled | Should Be $false
        $settings.correction_status | Should Be 'C!'
    }
    It 'максимальный шаг берётся по сетке частот, включая нелинейную' {
        $script:vna.SCPI.SenseValue.FREQuency.DATA = [double[]](100000000, 110000000, 200000000)
        $script:vna.SCPI.Calculation.SELected.DATA.SDATa = [double[]](0.1, 0, 0.2, 0, 0.3, 0)
        (Invoke-VnaCapture).CaptureSettings.frequency_step_hz | Should Be 90000000
    }
    It 'S21 в выбранной трассе запрещён даже если первая трасса S11' {
        $script:vna.SCPI.Calculation.Parameters[1].DEFine = 'S11'
        $script:vna.SCPI.Calculation.Parameters[2].DEFine = 'S21'
        { Invoke-VnaCapture } | Should Throw 'Активная трасса — S21: выберите S11 или S22 в S2VNA'
    }
    It 'неготовый прибор не читается' {
        $script:vna.Ready = $false
        $script:vna.SCPI = $null
        { Invoke-VnaCapture } | Should Throw 'ВАЦ не готов'
    }
    It 'неполная трасса отклоняется' {
        $script:vna.SCPI.Calculation.SELected.DATA.SDATa = [double[]](0.1, 0.2)
        { Invoke-VnaCapture } | Should Throw 'число частот и точек трассы не совпадает'
    }
    It 'NaN отклоняется' {
        $script:vna.SCPI.Calculation.SELected.DATA.SDATa[1] = [double]::NaN
        { Invoke-VnaCapture } | Should Throw 'недопустимые числа'
        Assert-MockCalled Save-VnaName -Times 0 -Exactly -Scope It
    }
    It 'COM ошибка подключения объясняется по-русски' {
        Mock Get-VnaApplication { throw 'REGDB_E_CLASSNOTREG' }
        { Invoke-VnaCapture } | Should Throw 'не удалось подключиться к S2VNA'
    }
}

Describe 'ВАЦ: отчёт без запуска S2VNA' {
    BeforeEach {
        Mock Test-VnaRegistered { $true }
        Mock Test-ArinstInstalled { $false }
        Mock Get-VnaApplication { throw 'heartbeat must not use COM' }
        Mock Get-Process { [pscustomobject]@{ ProcessName = 'S2VNA' } } -ParameterFilter { $Name -eq 'S2VNA' }
        Mock Get-PnpDevice { [pscustomobject]@{ InstanceId = 'USB\VID_2226&PID_0001\123'; Status = 'OK' } }
        Mock Get-CachedVnaName { 'Planar, S50180, 26030095, 26.2.2/2' }
    }
    It 'только зарегистрированный COM объявляет возможность ВАЦ' {
        Mock Test-VnaRegistered { $false }
        Get-VnaCapability | Should Be $null
        Assert-MockCalled Get-VnaApplication -Times 0 -Exactly -Scope It
    }
    It 'процесс и подключённый Planar готовы даже без Running Object Table' {
        $capability = Get-VnaCapability
        $capability.name | Should Match '26030095'
        $capability.ready | Should Be $true
        $capability.reason | Should Be ''
        Assert-MockCalled Get-VnaApplication -Times 0 -Exactly -Scope It
        Assert-MockCalled Get-Process -Times 1 -Exactly -Scope It -ParameterFilter { $Name -eq 'S2VNA' }
        Assert-MockCalled Get-PnpDevice -Times 1 -Exactly -Scope It -ParameterFilter { $PresentOnly }
    }
    It 'зарегистрированный но закрытый S2VNA не запускается' {
        Mock Get-Process { $null } -ParameterFilter { $Name -eq 'S2VNA' }
        $capability = Get-VnaCapability
        $capability.ready | Should Be $false
        $capability.reason | Should Be 's2vna_not_running'
        Assert-MockCalled Get-VnaApplication -Times 0 -Exactly -Scope It
    }
    It 'без USB-прибора процесс не означает готовность' {
        Mock Get-PnpDevice { @() }
        $capability = Get-VnaCapability
        $capability.ready | Should Be $false
        $capability.reason | Should Be 'device_not_connected'
    }
    It 'чужой VID, не-USB и ошибочный статус Planar не означают готовность' {
        Mock Get-PnpDevice { @(
            [pscustomobject]@{ InstanceId = 'USB\VID_1234&PID_0001\123'; Status = 'OK' },
            [pscustomobject]@{ InstanceId = 'PCI\VID_2226&PID_0001\123'; Status = 'OK' },
            [pscustomobject]@{ InstanceId = 'USB\VID_2226&PID_0001\123'; Status = 'Error' }
        ) }
        (Get-VnaCapability).ready | Should Be $false
    }
    It 'ошибка PnP не останавливает heartbeat и не подтверждает готовность' {
        Mock Get-PnpDevice { throw 'PnP unavailable' }
        $capability = Get-VnaCapability
        $capability.ready | Should Be $false
        $capability.reason | Should Be 'probe_failed'
    }
    It 'до первого замера имя S2VNA не мешает готовности' {
        Mock Get-CachedVnaName { 'S2VNA' }
        $capability = Get-VnaCapability
        $capability.name | Should Be 'S2VNA'
        $capability.ready | Should Be $true
    }
    It 'heartbeat содержит имя и готовность ВАЦ' {
        Mock Get-MachineId { 'pc-1' }
        Mock Get-LanIp { '' }
        Mock Get-AgentScanners { @() }
        Mock Invoke-ErpApi { [pscustomobject]@{ ok = $true } }
        Send-Heartbeat -Base 'http://test' -AuthToken 'mock-token'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Body.vna.name -match '26030095' -and $Body.vna.ready -and $Body.vna.reason -eq '' -and $Body.version -eq '2.5.1' -and ($Body.vna_sources -join ',') -eq 's2vna'
        }
    }
    It 'heartbeat передаёт причину неготовности' {
        Mock Get-Process { $null } -ParameterFilter { $Name -eq 'S2VNA' }
        Mock Get-MachineId { 'pc-1' }
        Mock Get-LanIp { '' }
        Mock Get-AgentScanners { @() }
        Mock Invoke-ErpApi { [pscustomobject]@{ ok = $true } }
        Send-Heartbeat -Base 'http://test' -AuthToken 'mock-token'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            -not $Body.vna.ready -and $Body.vna.reason -eq 's2vna_not_running'
        }
    }
}

Describe 'ВАЦ: постоянное имя прибора' {
    BeforeEach {
        $script:ConfigPath = Join-Path $tmp (([guid]::NewGuid().ToString()) + '\config.json')
    }
    It 'отсутствующий кэш даёт S2VNA' {
        Get-CachedVnaName | Should Be 'S2VNA'
    }
    It 'успешный замер сохраняет IDN в каталоге настроек для следующего запуска' {
        Mock Get-VnaApplication { New-TestVna }
        $capture = Invoke-VnaCapture
        $path = Join-Path (Split-Path -Parent $script:ConfigPath) 'vna-idn.txt'
        [IO.File]::ReadAllText($path) | Should Be $capture.Name
        Get-CachedVnaName | Should Be $capture.Name
    }
    It 'неудачный замер сохраняет предыдущий IDN' {
        Save-VnaName 'previous, model, serial, version'
        Mock Get-VnaApplication { throw 'not connected' }
        { Invoke-VnaCapture } | Should Throw
        Get-CachedVnaName | Should Be 'previous, model, serial, version'
    }
    It 'повреждённый кэш не выдаётся за IDN' {
        Save-VnaName "bad`nname"
        Get-CachedVnaName | Should Be 'S2VNA'
    }
    It 'ошибка чтения кэша даёт S2VNA' {
        Mock Test-Path { throw 'access denied' }
        Mock Write-Log {}
        Get-CachedVnaName | Should Be 'S2VNA'
        Assert-MockCalled Write-Log -Times 1 -Exactly -Scope It
    }
    It 'ошибка записи кэша не теряет успешную трассу и попадает в журнал' {
        $script:ConfigPath = Join-Path $tmp 'blocked\config.json'
        [IO.File]::WriteAllText((Join-Path $tmp 'blocked'), 'not a directory')
        Mock Get-VnaApplication { New-TestVna }
        Mock Write-Log {}
        (Invoke-VnaCapture).Ext | Should Be 's1p'
        Assert-MockCalled Write-Log -Times 1 -Exactly -Scope It -ParameterFilter { $Message -like '*не удалось сохранить*' }
    }
}

Describe 'ВАЦ: общая очередь' {
    BeforeEach {
        Mock Get-MachineId { 'pc-1' }
        Mock Invoke-ErpApi { [pscustomobject]@{ job = [pscustomobject]@{ id = 'v1'; kind = 'vna' } } }
        Mock Invoke-VnaCapture { @{ Bytes = [byte[]](1, 2); Ext = 's1p'; Mime = 'application/octet-stream' } }
        Mock Invoke-ScannerScan { throw 'must not scan' }
        Mock Send-ScanFile {}
    }
    It 'задание ВАЦ отправляет s1p с номером ПК и не включает сканер' {
        Invoke-ScanPollOnce -Base 'http://test' -AuthToken 'mock-token' | Should Be 'done'
        Assert-MockCalled Send-ScanFile -Times 1 -Exactly -Scope It -ParameterFilter { $FileName -like '*.s1p' -and $MachineId -eq 'pc-1' }
        Assert-MockCalled Invoke-ScannerScan -Times 0 -Exactly -Scope It
    }
    It 'настройки передаются вместе с файлом в том же задании' {
        Mock Invoke-VnaCapture { @{ Bytes = [byte[]](1, 2); Ext = 's1p'; Mime = 'application/octet-stream'; CaptureSettings = @{ smoothing_enabled = $true; correction_status = 'C?' } } }
        Invoke-ScanPollOnce -Base 'http://test' -AuthToken 'mock-token' | Should Be 'done'
        Assert-MockCalled Send-ScanFile -Times 1 -Exactly -Scope It -ParameterFilter {
            $CaptureSettings.smoothing_enabled -eq $true -and $CaptureSettings.correction_status -eq 'C?'
        }
    }
    It 'ошибка ВАЦ возвращается в то же задание' {
        Mock Invoke-VnaCapture { throw 'ВАЦ не готов' }
        Invoke-ScanPollOnce -Base 'http://test' -AuthToken 'mock-token' | Should Be 'error'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/result' -and $Body.error -eq 'ВАЦ не готов' -and $MachineId -eq 'pc-1' }
    }
    It 'Arinst отправляет исходное имя и не обращается к COM или сканеру' {
        Mock Invoke-ErpApi { [pscustomobject]@{ job = [pscustomobject]@{ id = 'v1'; kind = 'vna'; source = 'arinst' } } }
        Mock Invoke-ArinstCapture { @{ Bytes = [byte[]](1, 2); FileName = 'замер.s2p'; Ext = 's2p'; Mime = 'application/octet-stream' } }
        Invoke-ScanPollOnce -Base 'http://test' -AuthToken 'mock-token' | Should Be 'done'
        Assert-MockCalled Send-ScanFile -Times 1 -Exactly -Scope It -ParameterFilter { $FileName -eq 'замер.s2p' -and $MachineId -eq 'pc-1' }
        Assert-MockCalled Invoke-VnaCapture -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-ScannerScan -Times 0 -Exactly -Scope It
    }
}

Describe 'ВАЦ: JSON настроек рядом с бинарным файлом' {
    It 'multipart сохраняет байты и содержит отдельное поле capture_settings' {
        [byte[]]$body = New-MultipartBody -Boundary 'test-boundary' -FieldName 'file' -FileName 'trace.s1p' -ContentType 'application/octet-stream' -Bytes ([byte[]](0, 255, 13, 10)) -CaptureSettings @{ points = 201; smoothing_enabled = $true }
        $text = [Text.Encoding]::UTF8.GetString($body)
        $text | Should Match 'name="file"; filename="trace.s1p"'
        $text | Should Match 'name="capture_settings"'
        $text | Should Match '"points":201'
        $text | Should Match '"smoothing_enabled":true'
        $text | Should Match 'test-boundary--\r\n$'
        $fileStart = $text.IndexOf("`r`n`r`n") + 4
        # Headers are ASCII, so this is also the byte offset.
        ($body[$fileStart..($fileStart + 3)] -join ',') | Should Be '0,255,13,10'
    }
    It 'скан и Arinst без настроек не получают JSON поля' {
        $body = New-MultipartBody -Boundary 'test-boundary' -FieldName 'file' -FileName 'scan.jpg' -ContentType 'image/jpeg' -Bytes ([byte[]](1, 2))
        [Text.Encoding]::UTF8.GetString($body) | Should Not Match 'capture_settings'
    }
}

Describe 'Arinst: последний сохранённый Touchstone' {
    BeforeEach {
        $script:arinstDir = Join-Path $tmp ([guid]::NewGuid().ToString())
        [void][IO.Directory]::CreateDirectory($script:arinstDir)
        Mock Get-ArinstFolders { @($script:arinstDir) }
        Mock Get-VnaApplication { throw 'must not touch COM' }
    }
    It 'выбирает самый новый s1p или s2p и сохраняет исходный файл' {
        $old = Join-Path $script:arinstDir 'old.s1p'
        $new = Join-Path $script:arinstDir 'замер.s2p'
        [IO.File]::WriteAllText($old, 'old')
        [IO.File]::SetLastWriteTimeUtc($old, [DateTime]::UtcNow.AddMinutes(-2))
        [IO.File]::WriteAllBytes($new, [byte[]](1, 2, 3))
        [IO.File]::WriteAllText((Join-Path $script:arinstDir 'ignore.txt'), 'newest but not Touchstone')
        $capture = Invoke-ArinstCapture
        $capture.FileName | Should Be 'замер.s2p'
        ($capture.Bytes -join ',') | Should Be '1,2,3'
        ([IO.File]::ReadAllBytes($new) -join ',') | Should Be '1,2,3'
        Test-Path -LiteralPath $old | Should Be $true
        Assert-MockCalled Get-VnaApplication -Times 0 -Exactly -Scope It
    }
    It 'принимает файл внутри 15 минут и игнорирует более старый и будущий' {
        foreach ($pair in @(@('fresh.s1p', -899), @('old.s1p', -901), @('future.s1p', 60))) {
            $path = Join-Path $script:arinstDir $pair[0]
            [IO.File]::WriteAllText($path, 'trace')
            [IO.File]::SetLastWriteTimeUtc($path, [DateTime]::UtcNow.AddSeconds($pair[1]))
        }
        (Invoke-ArinstCapture).FileName | Should Be 'fresh.s1p'
    }
    It 'смотрит один уровень вложенности, но не глубже' {
        $child = Join-Path $script:arinstDir 'child'
        $deep = Join-Path $child 'deep'
        [void][IO.Directory]::CreateDirectory($deep)
        [IO.File]::WriteAllText((Join-Path $child 'child.s1p'), 'child')
        [IO.File]::WriteAllText((Join-Path $deep 'deep.s1p'), 'deep')
        (Invoke-ArinstCapture).FileName | Should Be 'child.s1p'
    }
    It 'без свежих файлов возвращает инструкцию сохранения' {
        { Invoke-ArinstCapture } | Should Throw 'Сохраните трассу в Arinst Virtual Lab (.s1p) и повторите — свежих файлов за 15 минут нет'
    }
    It 'не берёт файл старше 15 минут' {
        $path = Join-Path $script:arinstDir 'old.s1p'
        [IO.File]::WriteAllText($path, 'old')
        [IO.File]::SetLastWriteTimeUtc($path, [DateTime]::UtcNow.AddMinutes(-16))
        { Invoke-ArinstCapture } | Should Throw 'свежих файлов за 15 минут нет'
    }
    It 'самый новый файл больше 2 МиБ даёт ошибку без подмены старым' {
        [IO.File]::WriteAllText((Join-Path $script:arinstDir 'small.s1p'), 'small')
        [IO.File]::SetLastWriteTimeUtc((Join-Path $script:arinstDir 'small.s1p'), [DateTime]::UtcNow.AddMinutes(-1))
        [IO.File]::WriteAllBytes((Join-Path $script:arinstDir 'big.s1p'), (New-Object byte[] (2MB + 1)))
        { Invoke-ArinstCapture } | Should Throw 'Файл ВАЦ больше 2 МиБ'
    }
    It 'ровно 2 МиБ допустимы' {
        [IO.File]::WriteAllBytes((Join-Path $script:arinstDir 'limit.S1P'), (New-Object byte[] 2MB))
        (Invoke-ArinstCapture).Bytes.Length | Should Be 2MB
    }
    It 'несуществующий каталог не мешает чтению остальных' {
        Mock Get-ArinstFolders { @((Join-Path $script:arinstDir 'missing'), $script:arinstDir) }
        [IO.File]::WriteAllText((Join-Path $script:arinstDir 'trace.s1p'), 'trace')
        (Invoke-ArinstCapture).FileName | Should Be 'trace.s1p'
    }
    It 'ошибка доступа к папке объясняется по-русски и журналируется' {
        Mock Get-ChildItem { throw 'Access denied' }
        Mock Write-Log {}
        { Invoke-ArinstCapture } | Should Throw 'Не удалось найти файл Arinst — проверьте arinstFolders и доступ к папкам в настройках агента'
        Assert-MockCalled Write-Log -Times 1 -Exactly -Scope It -ParameterFilter { $Message -like '*Access denied*' }
    }
}

Describe 'Arinst: настройки и heartbeat' {
    It 'читает список каталогов из config.json и ищет по нему' {
        $script:ConfigPath = Join-Path $tmp 'arinst-config.json'
        $folder1 = Join-Path $tmp 'watch1'
        $folder2 = Join-Path $tmp 'watch2'
        [void][IO.Directory]::CreateDirectory($folder1)
        [void][IO.Directory]::CreateDirectory($folder2)
        [IO.File]::WriteAllText($script:ConfigPath, (@{ arinstFolders = @($folder1, $folder2) } | ConvertTo-Json))
        ((Get-ArinstFolders) -join ',') | Should Be "$folder1,$folder2"
        [IO.File]::WriteAllText((Join-Path $folder2 'configured.s1p'), 'trace')
        (Invoke-ArinstCapture).FileName | Should Be 'configured.s1p'
    }
    It 'без настройки использует Документы, Рабочий стол и Загрузки' {
        $script:ConfigPath = Join-Path $tmp 'absent-config.json'
        $folders = @(Get-ArinstFolders)
        $folders.Count | Should Be 3
        ($folders[0] -in @([Environment]::GetFolderPath('MyDocuments'), (Join-Path $env:USERPROFILE 'Documents'))) | Should Be $true
        ($folders[1] -in @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:USERPROFILE 'Desktop'))) | Should Be $true
        $folders[2] | Should Be (Join-Path $env:USERPROFILE 'Downloads')
    }
    It 'пустой список настроек не включает каталоги по умолчанию' {
        $script:ConfigPath = Join-Path $tmp 'empty-config.json'
        [IO.File]::WriteAllText($script:ConfigPath, '{"arinstFolders":[]}')
        @(Get-ArinstFolders).Count | Should Be 0
    }
    It 'обнаруживает установленный ArinstVL по файлу exe в APPDATA' {
        Mock Get-ChildItem { [pscustomobject]@{ Name = 'ArinstVL.exe' } } -ParameterFilter { $Path -eq (Join-Path $env:APPDATA 'ArinstVL*\ArinstVL.exe') -and $File }
        Test-ArinstInstalled | Should Be $true
        Assert-MockCalled Get-ChildItem -Times 1 -Exactly -Scope It
    }
    It 'не объявляет Arinst без установленного exe' {
        Mock Get-ChildItem { @() } -ParameterFilter { $Path -eq (Join-Path $env:APPDATA 'ArinstVL*\ArinstVL.exe') -and $File }
        Test-ArinstInstalled | Should Be $false
    }
    It 'heartbeat с одним Arinst не добавляет S2VNA' {
        Mock Test-ArinstInstalled { $true }
        Mock Get-VnaCapability { $null }
        Mock Get-MachineId { 'pc-1' }
        Mock Get-LanIp { '' }
        Mock Get-AgentScanners { @() }
        Mock Invoke-ErpApi { [pscustomobject]@{ ok = $true } }
        Send-Heartbeat -Base 'http://test' -AuthToken 'mock-token'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { ($Body.vna_sources -join ',') -eq 'arinst' -and -not $Body.vna }
    }
    It 'heartbeat может объявить оба источника' {
        Mock Test-ArinstInstalled { $true }
        Mock Get-VnaCapability { @{ name = 'S2VNA'; ready = $false } }
        Mock Get-MachineId { 'pc-1' }
        Mock Get-LanIp { '' }
        Mock Get-AgentScanners { @() }
        Mock Invoke-ErpApi { [pscustomobject]@{ ok = $true } }
        Send-Heartbeat -Base 'http://test' -AuthToken 'mock-token'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { ($Body.vna_sources -join ',') -eq 's2vna,arinst' -and $Body.vna.name -eq 'S2VNA' }
    }
}


Describe 'VNA: first -Once run with a legacy token' {
    It 'binds the token by heartbeat before claiming and uploading a VNA job' {
        $script:legacyMachine = ''
        Mock Get-MachineId { 'pc-legacy' }
        Mock Get-AgentScanners { @() }
        Mock Get-LanIp { '127.0.0.1' }
        Mock Get-VnaCapability { @{ name = 'S2VNA'; ready = $true } }
        Mock Test-ArinstInstalled { $false }
        Mock Invoke-AgentUpdate {}
        Mock Invoke-ErpApi {
            if ($AuthToken -ne 'legacy-token') { throw 'wrong token' }
            if ($Url -like '*/heartbeat') {
                $script:legacyMachine = $Body.machine_id
                return [pscustomobject]@{}
            }
            if ($Url -like '*/claim') {
                if (-not $script:legacyMachine -or $Body.machine_id -ne $script:legacyMachine) { throw '403: legacy token is not bound' }
                return [pscustomobject]@{ job = [pscustomobject]@{ id = 'legacy-vna'; kind = 'vna'; source = 's2vna' } }
            }
            throw 'unexpected API call'
        }
        Mock Invoke-VnaCapture { @{ Bytes = [byte[]](1, 2); Ext = 's1p'; Mime = 'application/octet-stream' } }
        Mock Send-ScanFile {}
        Mock Invoke-ScannerScan { throw 'must not scan' }
        Mock Start-Sleep { throw 'Once must not sleep' }
        Start-AgentLoop -Base 'http://test' -AuthToken 'legacy-token' -PrinterName '' -Once
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/heartbeat' -and $Body.machine_id -eq 'pc-legacy' }
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/claim' -and $Body.kinds -contains 'vna' }
        Assert-MockCalled Send-ScanFile -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/jobs/legacy-vna/result' -and $MachineId -eq 'pc-legacy' -and $AuthToken -eq 'legacy-token' }
        Assert-MockCalled Invoke-ScannerScan -Times 0 -Exactly -Scope It
    }
}

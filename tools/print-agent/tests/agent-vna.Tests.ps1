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
        SELected = [pscustomobject]@{ DATA = [pscustomobject]@{ SDATa = [double[]](0.1, -0.2, 0.25, 0.5) } }
    }
    $calc | Add-Member ScriptMethod PARameter { param($index) $this.Parameters[$index] }
    $scpi = [pscustomobject]@{
        SERVice = $service; Calculation = $calc
        SenseValue = [pscustomobject]@{ FREQuency = [pscustomobject]@{ DATA = [double[]](100000000, 200000000) } }
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
            $Body.vna.name -match '26030095' -and $Body.vna.ready -and $Body.vna.reason -eq '' -and $Body.version -eq '2.3.1'
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
    It 'ошибка ВАЦ возвращается в то же задание' {
        Mock Invoke-VnaCapture { throw 'ВАЦ не готов' }
        Invoke-ScanPollOnce -Base 'http://test' -AuthToken 'mock-token' | Should Be 'error'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/result' -and $Body.error -eq 'ВАЦ не готов' -and $MachineId -eq 'pc-1' }
    }
}

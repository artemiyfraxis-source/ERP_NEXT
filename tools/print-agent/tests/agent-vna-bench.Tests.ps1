# Pester 3.x compatible. VNA bench protocol v2 (docs/vna/bench-protocol-v2.md). COM is a stateful fake; never opens S2VNA.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$benchTmp = Join-Path ([IO.Path]::GetTempPath()) ('pa-bench-' + [guid]::NewGuid())
[void][IO.Directory]::CreateDirectory($benchTmp)
. (Join-Path $here '..\agent.ps1') -ConfigPath (Join-Path $benchTmp 'bench-config.json') -LogPath (Join-Path $benchTmp 'agent.log')

$script:benchHash = ('b' * 64)
function New-BenchProfile {
    return [pscustomobject]@{ start_hz = 1e6; stop_hz = 2e6; points = 2; if_bandwidth_hz = 1000; power_dbm = 0
        impedance_ohm = 50; sweep = 'LIN'; smoothing = $false; averaging = $false; processing = 'off'
        traces = @('S11','S21','S12','S22'); vswr_limit = 1.5 }
}
function New-BenchScreen {
    # Shape of the ERP JSON: numbers as they arrive after ConvertFrom-Json (Decimal / Int32).
    $json = @'
{"split": 8, "title": "КБ1 № 1 — ОТК\"; :SYST:PRES",
 "markers_hz": [1000000.0, 1500000.0],
 "traces": [
  {"param": "S21", "format": "MLOG", "scale": {"pdiv": 0.05, "rlev": 0.0, "rpos": 10}, "hidden": false, "memory": true,
   "limits": [{"kind": "lower", "start_hz": 1000000.0, "stop_hz": 2000000.0, "start_value": -0.3, "stop_value": -0.5}]},
  {"param": "S11", "format": "SWR", "scale": {"pdiv": 0.0333333333, "rlev": 1.0, "rpos": 0}, "hidden": false, "memory": false,
   "limits": [{"kind": "upper", "start_hz": 1000000.0, "stop_hz": 2000000.0, "start_value": 1.3, "stop_value": 1.3}]},
  {"param": "S11", "format": "SMIT", "scale": {"pdiv": 1.0, "rlev": null, "rpos": null}, "hidden": false, "memory": false, "limits": []},
  {"param": "S12", "format": "MLOG", "scale": {"pdiv": 0.05, "rlev": 0.0, "rpos": 10}, "hidden": true, "memory": false, "limits": []},
  {"param": "S22", "format": "SWR", "scale": {"pdiv": 0.02, "rlev": 1.0, "rpos": 0}, "hidden": false, "memory": false, "limits": []},
  {"param": "S21", "format": "GDEL", "scale": "auto", "hidden": false, "memory": false, "limits": []}
 ]}
'@
    return ($json | ConvertFrom-Json)
}
function New-BenchEnvelope {
    param([string]$Op, $ArgsValue = @{}, [int]$Protocol = 2)
    $id = [guid]::NewGuid().ToString()
    return [pscustomobject]@{ id = $id; command_id = $id; kind = 'vna_control'; protocol_version = $Protocol
        session_id = '11111111-1111-1111-1111-111111111111'; expected_version = 4; expected_step = 0
        instrument_id = '22222222-2222-2222-2222-222222222222'; instrument_serial = '26030095'; op = $Op; args = $ArgsValue }
}
function New-BenchStatus {
    param($Command)
    return [pscustomobject]@{ session_id = $Command.session_id; state = 'preparing'; version = $Command.expected_version
        step = $Command.expected_step; lease_held = $true; instrument_id = $Command.instrument_id; instrument_serial = $Command.instrument_serial
        kit_definitions = @{ '33333333-3333-3333-3333-333333333333' = @{ serial_number = '26140008' } }
        commands = @([pscustomobject]@{ command_id = $Command.command_id; status = 'claimed'; expected_version = $Command.expected_version; expected_step = $Command.expected_step }) }
}
function Invoke-BenchOp {
    param([string]$Op, $ArgsValue = @{}, [int]$Protocol = 2)
    # Commands travel as JSON (claim -> worker request): reproduce the same types.
    $command = (ConvertTo-Json -InputObject (New-BenchEnvelope $Op $ArgsValue $Protocol) -Depth 30 -Compress) | ConvertFrom-Json
    $result = Invoke-S2Command $script:benchApp $command (New-BenchStatus $command) (Join-Path (Get-VnaControlRoot) 'owner.json')
    return (ConvertTo-Json -InputObject $result -Depth 30 -Compress) | ConvertFrom-Json
}
function Get-BenchTraceParam { param([int]$Index) return [string]$script:ioValues["CALCulate(1).PARameter($Index).DEFine"] }
function Invoke-BenchFakeIo {
    param([string]$Path, [string]$Mode, $Value)
    [void]$script:ioLog.Add("$Mode $Path")
    if ($Mode -eq 'set') { $script:setValues[$Path] = $Value }
    if ($Mode -eq 'call' -and $Path -match '^CALCulate\(1\)\.PARameter\((\d+)\)\.SELect$') { $script:selected = [int]$Matches[1]; return }
    if ($Path -eq 'SERVice.CHANnel(1).TRACe(1).ACTive') { return $script:selected }
    if ($Path -like 'CALCulate(1).SELected.*') { $Path = $Path.Replace('SELected.', ('trace' + $script:selected + '.')) }
    if ($Mode -eq 'call') {
        if ($Path -match '^CALCulate\(1\)\.trace(\d+)\.MATH\.MEMorize$') { [void]$script:memorized.Add([int]$Matches[1]) }
        if ($Path -eq 'TRIGger.SEQuence.SINGle' -and $script:onSweep) { & $script:onSweep }
        return
    }
    if ($Mode -eq 'set') {
        if ($Path -eq $script:ignorePath) { return }
        if ($Path -match '^CALCulate\(1\)\.trace(\d+)\.MARKer\((\d+)\)\.STATe$') {
            $trace = $Matches[1]; $number = [int]$Matches[2]
            if ($Value) { for ($m = 1; $m -le $number; $m++) { $script:ioValues["CALCulate(1).trace$trace.MARKer($m).STATe"] = $true } }
            else { for ($m = $number; $m -le 15; $m++) { $script:ioValues["CALCulate(1).trace$trace.MARKer($m).STATe"] = $false } }
            return
        }
        if ($Path -eq 'MMEMory.STORe.STATe') { [IO.File]::WriteAllText($Value, 'original state'); return }
        $script:ioValues[$Path] = $Value; return
    }
    if ($Path -match '^CALCulate\(1\)\.trace(\d+)\.CORRection\.STATus$') { return $script:correction[(Get-BenchTraceParam ([int]$Matches[1]))] }
    if ($Path -match '^CALCulate\(1\)\.TRACe\((\d+)\)\.DATA\.(SDATa|SMEMory)$') {
        $param = Get-BenchTraceParam ([int]$Matches[1]); $base = @{ S11 = 1; S21 = 2; S12 = 3; S22 = 4 }[$param]
        if ($Matches[2] -eq 'SMEMory') { $base += 10 }
        return @($base, 0.5, $base, -0.5)
    }
    if ($Path -match '^CALCulate\(1\)\.trace\d+\.MARKer\(\d+\)\.STATe$' -and -not $script:ioValues.ContainsKey($Path)) { return $false }
    if ($Path -eq 'SENSe(1).CORRection.COLLect.ECAL.INFormation' -and $script:acmInfo -eq 'absent') { throw 'module not connected' }
    if ($Path -eq 'SENSe(1).CORRection.COLLect.ECAL.INFormation') { return $script:acmInfo }
    if (-not $script:ioValues.ContainsKey($Path)) { throw "Missing fake COM getter: $Path" }
    return $script:ioValues[$Path]
}
function Initialize-BenchDevice {
    $script:ioLog = New-Object 'Collections.Generic.List[string]'
    $script:memorized = New-Object 'Collections.Generic.List[int]'
    $script:setValues = @{}
    $script:ioValues = @{}; $script:selected = 1; $script:ignorePath = ''; $script:onSweep = $null
    $script:correction = @{ S11 = 'COR'; S21 = 'COR'; S12 = 'COR'; S22 = 'COR' }
    $script:acmInfo = 'ACM2520.2,26140008,30,Factory,2026-01-01,100 kHz,20 GHz,1601,3.5mm -F-,3.5mm -F-'
    $writes = Get-S2ProfileWrites $script:profile
    foreach ($path in $writes.Keys) { $script:ioValues[$path] = $writes[$path] }
    $extra = @{
        'SENSe(1).AVERage.COUNt' = 3; 'SENSe(1).CORRection.STATe' = $true
        'SENSe(1).CORRection.COLLect.CKIT.SELect' = 28; 'SENSe(1).CORRection.COLLect.CKIT.LABel' = '6650F27'
        'SENSe(1).CORRection.TRIGger.FREE.STATe' = $false; 'SOURce(1).POWer.PORT(2).COUPle' = $false
        'INITiate(1).CONTinuous' = $true; 'TRIGger.SEQuence.SOURce' = 'INT'; 'MMEMory.STORe.STYPe' = 'STAN'
        'SERVice.SWEep.POINts' = 20001; 'SERVice.SWEep.FREQuency.MINimum' = 1e5; 'SERVice.SWEep.FREQuency.MAXimum' = 18e9
        'IEEE4882.OPC' = 1; 'SENSe(1).FREQuency.DATA' = @(1e6,2e6)
        'SENSe(1).CORRection.COLLect.ECAL.ORIentation.STATe' = $false
        'SYSTem.TEMPerature.SENSor(1)' = 31.2; 'SYSTem.COMMunicate.ECAL.TEMPerature.SENSor' = 30.9
        'DISPlay.WINDow(1).SPLit' = 4; 'DISPlay.WINDow(1).TITLe.STATe' = $false; 'DISPlay.WINDow(1).TITLe.DATA' = ''
    }
    foreach ($path in $extra.Keys) { $script:ioValues[$path] = $extra[$path] }
    for ($index = 1; $index -le 16; $index++) {
        $parameter = if ($index -le 4) { $script:profile.traces[$index - 1] } else { 'S11' }
        $script:ioValues["CALCulate(1).PARameter($index).DEFine"] = $parameter
        $settings = @{ FORMat = $(if ($parameter -in @('S11','S22')) { 'SWR' } else { 'MLOG' })
            'SMOothing.STATe' = $false; 'MATH.FUNCtion' = 'OFF'; 'CONVersion.STATe' = $false
            'FILTer.GATE.TIME.STATe' = $false; 'TRANsform.TIME.STATe' = $false
            'LIMit.DATA' = @(1,1,1e6,2e6,1.5,1.5); 'LIMit.DISPlay.STATe' = $true; 'LIMit.STATe' = $false }
        foreach ($tail in $settings.Keys) { $script:ioValues["CALCulate(1).trace$index.$tail"] = $settings[$tail] }
        $window = "DISPlay.WINDow(1).TRACe($index)"
        $script:ioValues["$window.STATe"] = $true; $script:ioValues["$window.MEMory.STATe"] = $false
        $script:ioValues["$window.Y.SCALe.PDIVision"] = 0.1; $script:ioValues["$window.Y.SCALe.RLEVel"] = 1.0; $script:ioValues["$window.Y.SCALe.RPOSition"] = 0
    }
}
function Initialize-BenchCalibrated {
    # prepare -> apply_profile -> begin_calibration (ACM) -> acm_calibrate -> save_calibration, all under protocol 2.
    [void](Invoke-BenchOp prepare)
    [void](Invoke-BenchOp apply_profile @{ profile = $script:profile })
    [void](Invoke-BenchOp begin_calibration $script:beginArgs)
    [void](Invoke-BenchOp acm_calibrate @{ ports = @(1,2); kit_ports = @(2,1); module_serial = '26140008'; start_hz = 1e6; stop_hz = 2e6 })
    [void](Invoke-BenchOp save_calibration @{ method = 'acm'; traces = $script:profile.traces })
}
function Get-BenchSets { param([string]$Like) return @($script:ioLog | Where-Object { $_ -like "set $Like" }) }

Describe 'ВАЦ v2: рабочее место' {
    BeforeEach {
        $script:ConfigPath = Join-Path $TestDrive ([guid]::NewGuid().ToString() + '\config.json')
        $script:benchApp = [pscustomobject]@{ Ready = $true; Name = 'Planar, S50180, 26030095, 26.2.2/2'; SCPI = [pscustomobject]@{} }
        $script:profile = New-BenchProfile
        Initialize-BenchDevice
        Mock Invoke-S2Io { $modeValue = if ($Mode) { $Mode } else { 'get' }; Invoke-BenchFakeIo $Path $modeValue $Value }
        Mock Invoke-S2KitFile { if ($Direction -eq 'STORe') { [IO.File]::WriteAllText($Path, 'original kit') } }
        Mock Get-VnaWarmupMinutes { 42.5 }
        Mock Get-VnaApplication { throw 'Tests must not open real COM' }
        $acmStandard = [pscustomobject]@{ kind = 'ACM'; ports = @(1,2); label = 'Подключите АКМ'; kit_ports = @(2,1) }
        $kit = [pscustomobject]@{ kit_id = '33333333-3333-3333-3333-333333333333'; method = 'acm'; registry_fingerprint = ('a' * 64)
            standards = @($acmStandard); definition = [pscustomobject]@{ model = 'ACM2520.2'; serial = '26140008'; min_hz = 1e5; max_hz = 20e9
                identity_fields = [pscustomobject]@{ f3 = 'Factory'; f4 = '2026-01-01'; f7 = '1601'; f8 = '3.5mm -F-'; f9 = '3.5mm -F-' } } }
        $script:manifest = [pscustomobject]@{ instrument_serial = '26030095'; ordinary_state_verified = $true; kits = @($kit) }
        Mock Get-VnaManifest { $script:manifest }
        $script:beginArgs = @{ method = 'acm'; kit_id = $kit.kit_id; kit_fingerprint = $kit.registry_fingerprint; orientation = 'both'; standards = @($acmStandard) }
        $script:captureArgs = @{ profile = $script:profile; orientation = 'both'; port_to_end = @{ '1' = 'А'; '2' = 'Б' }; screen_hash = $script:benchHash }
    }

    It 'операция рабочего места в конверте протокола 1 отклоняется до обращения к прибору' {
        { Invoke-BenchOp device_status @{} 1 } | Should Throw 'протокол управления версии 2'
        $script:ioLog.Count | Should Be 0
    }
    It 'неизвестная версия протокола отклоняется' {
        { Invoke-BenchOp prepare @{} 3 } | Should Throw 'неподдерживаемая версия'
        $script:ioLog.Count | Should Be 0
    }
    It 'device_status до prepare только читает: имя, прогрев, температуры с null, АКМ, коррекция' {
        $script:correction.S21 = 'C?'
        $status = Invoke-BenchOp device_status
        $status.serial | Should Be '26030095'
        $status.name | Should Be 'Planar, S50180, 26030095, 26.2.2/2'
        $status.ready | Should Be $true
        $status.warmup_min | Should Be 42.5
        $status.temperatures.vna_c.Count | Should Be 2
        $status.temperatures.vna_c[0] | Should Be 31.2
        ($null -eq $status.temperatures.vna_c[1]) | Should Be $true
        $status.temperatures.acm_c | Should Be 30.9
        $status.acm.serial | Should Be '26140008'
        $status.acm.min_hz | Should Be 1e5
        $status.acm.max_hz | Should Be 2e10
        $status.correction.S11 | Should Be 'COR'
        $status.correction.S21 | Should Be 'C?'
        ($null -eq $status.screen_hash) | Should Be $true
        @(Get-BenchSets '*').Count | Should Be 0
        $script:selected | Should Be 1
    }
    It 'device_status без АКМ сообщает acm null, а не ошибку' {
        $script:acmInfo = 'absent'
        ($null -eq (Invoke-BenchOp device_status).acm) | Should Be $true
    }
    It 'apply_screen задаёт графики, шкалы, линии, маркеры, заголовок свойством и читает обратно' {
        Initialize-BenchCalibrated; $script:ioLog.Clear()
        $screen = New-BenchScreen
        $result = Invoke-BenchOp apply_screen @{ screen = $screen; screen_hash = $script:benchHash }
        $result.screen_hash | Should Be $script:benchHash
        $script:ioValues['CALCulate(1).PARameter(1).COUNt'] | Should Be 6
        $script:ioValues['DISPlay.WINDow(1).SPLit'] | Should Be 8
        $script:ioValues['CALCulate(1).PARameter(1).DEFine'] | Should Be 'S21'
        $script:ioValues['CALCulate(1).trace3.FORMat'] | Should Be 'SMIT'
        foreach ($index in 1..6) {
            $script:ioValues["CALCulate(1).trace$index.SMOothing.STATe"] | Should Be $false
            $script:ioValues["CALCulate(1).trace$index.TRANsform.TIME.STATe"] | Should Be $false
            $script:ioValues["CALCulate(1).trace$index.FILTer.GATE.TIME.STATe"] | Should Be $false
            $script:ioValues["CALCulate(1).trace$index.MARKer(2).STATe"] | Should Be $true
            $script:ioValues["CALCulate(1).trace$index.MARKer(3).STATe"] | Should Be $false
        }
        ($script:ioValues['CALCulate(1).trace1.LIMit.DATA'] -join ',') | Should Be '1,2,1000000,2000000,-0.3,-0.5'
        $script:ioValues['CALCulate(1).trace1.LIMit.STATe'] | Should Be $true
        $script:ioValues['CALCulate(1).trace3.LIMit.DISPlay.STATe'] | Should Be $false
        $script:ioValues['DISPlay.WINDow(1).TRACe(1).MEMory.STATe'] | Should Be $true
        $script:ioValues['DISPlay.WINDow(1).TRACe(4).STATe'] | Should Be $false
        $script:ioValues['DISPlay.WINDow(1).TRACe(1).Y.SCALe.RPOSition'].GetType().Name | Should Be 'Int32'
        @(Get-BenchSets 'DISPlay.WINDow(1).TRACe(3).Y.SCALe.RLEVel').Count | Should Be 0
        ($script:ioLog -contains 'call DISPlay.WINDow(1).TRACe(6).Y.SCALe.AUTO') | Should Be $true
        @($script:ioLog | Where-Object { $_ -like 'call *Y.SCALe.AUTO' }).Count | Should Be 1
        # The title is a COM property value, byte for byte, never a command.
        $script:setValues['DISPlay.WINDow(1).TITLe.DATA'] | Should BeExactly 'КБ1 № 1 — ОТК"; :SYST:PRES'
        $script:ioValues['DISPlay.WINDow(1).TITLe.STATe'] | Should Be $true
        $result.screen.traces.Count | Should Be 6
        $result.screen.traces[5].scale | Should Be 'auto'
        ($null -eq $result.screen.traces[2].scale.rlev) | Should Be $true
        $result.screen.title | Should BeExactly 'КБ1 № 1 — ОТК"; :SYST:PRES'
        (Read-VnaJson (Join-Path (Get-VnaControlRoot) 'owner.json')).screen_hash | Should Be $script:benchHash
        (Get-VnaScreenSpec $script:benchHash).traces.Count | Should Be 6
    }
    It 'apply_screen: прочтённое значение с другим хвостом после 9 знаков совпадает' {
        Initialize-BenchCalibrated
        $script:ignorePath = 'DISPlay.WINDow(1).TRACe(2).Y.SCALe.PDIVision'
        $script:ioValues[$script:ignorePath] = 0.03333333330002
        (Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash }).screen.traces[1].scale.pdiv | Should Be 0.03333333330002
    }
    It 'apply_screen: прибор не принял шкалу — ошибка, не квитанция' {
        Initialize-BenchCalibrated
        $script:ignorePath = 'DISPlay.WINDow(1).TRACe(2).Y.SCALe.RPOSition'
        $script:ioValues[$script:ignorePath] = 5
        { Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash } } | Should Throw 'не совпадает с заданным'
        ($null -eq (Read-VnaJson (Join-Path (Get-VnaControlRoot) 'owner.json')).screen_hash) | Should Be $true
    }
    It 'apply_screen отказывает при калибровке <Code>' -TestCases @(@{ Code = 'C?'; Text = 'интерполирована' }, @{ Code = 'C!'; Text = 'экстраполирована' }, @{ Code = 'NONE'; Text = 'нет полной калибровки' }) {
        param($Code, $Text)
        Initialize-BenchCalibrated
        $script:correction.S22 = $Code; $script:ioLog.Clear()
        { Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash } } | Should Throw $Text
        @(Get-BenchSets '*').Count | Should Be 0
    }
    It 'apply_screen отказывает при включённом усреднении и изменённой сетке' {
        Initialize-BenchCalibrated
        $script:ioValues['SENSe(1).AVERage.STATe'] = $true
        { Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash } } | Should Throw 'усреднение'
        $script:ioValues['SENSe(1).AVERage.STATe'] = $false
        $script:ioValues['SENSe(1).SWEep.POINts'] = 3
        { Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash } } | Should Throw 'сетка частот изменены'
    }
    It 'заголовок с управляющим символом отклоняется до записи' {
        Initialize-BenchCalibrated; $script:ioLog.Clear()
        $screen = New-BenchScreen; $screen.title = "КБ1`n:SYST:PRES"
        { Invoke-BenchOp apply_screen @{ screen = $screen; screen_hash = $script:benchHash } } | Should Throw 'заголовок'
        @(Get-BenchSets '*').Count | Should Be 0
    }
    It 'неверный отпечаток экрана не становится именем файла' {
        Initialize-BenchCalibrated
        { Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = ('..\' + ('b' * 61)) } } | Should Throw 'отпечаток'
        { Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = ($script:benchHash + "`n") } } | Should Throw 'отпечаток'
    }
    It 'device_status после экрана ERP возвращает его отпечаток, после правки оператором — null' {
        Initialize-BenchCalibrated
        [void](Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash })
        (Invoke-BenchOp device_status).screen_hash | Should Be $script:benchHash
        $script:ioValues['CALCulate(1).trace5.FORMat'] = 'MLOG'
        ($null -eq (Invoke-BenchOp device_status).screen_hash) | Should Be $true
    }
    It 'capture v2: свежая развёртка, данные по трассам экрана, температуры и эхо отпечатка' {
        Initialize-BenchCalibrated
        [void](Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash }); $script:ioLog.Clear()
        $result = Invoke-BenchOp capture $script:captureArgs
        $script:ioLog.IndexOf('set TRIGger.SEQuence.SOURce') | Should BeLessThan $script:ioLog.IndexOf('call TRIGger.SEQuence.SINGle')
        $script:ioLog.IndexOf('call TRIGger.SEQuence.SINGle') | Should BeLessThan $script:ioLog.IndexOf('get IEEE4882.OPC')
        $script:ioLog.IndexOf('call TRIGger.SEQuence.SINGle') | Should BeLessThan $script:ioLog.IndexOf('call DISPlay.WINDow(1).TRACe(6).Y.SCALe.AUTO')
        $result.screen_hash | Should Be $script:benchHash
        $result.temperatures.vna_c[0] | Should Be 31.2
        $result.correction.S12 | Should Be 'COR'
        $text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($result.artifacts[0].content_base64))
        # s2p order S11 S21 S12 S22 even though S21 is graph 1 and S12 is a hidden graph 4.
        $text | Should Match '1000000 1 0.5 2 0.5 3 0.5 4 0.5'
        $result.artifacts[0].file_name | Should Be 'measurement.s2p'
    }
    It 'capture v2 отказывает до развёртки: <Case>' -TestCases @(
        @{ Case = 'экран изменён'; Path = 'CALCulate(1).trace2.FORMat'; Value = 'MLOG'; Text = 'экран S2VNA изменён' },
        @{ Case = 'сглаживание'; Path = 'CALCulate(1).trace3.SMOothing.STATe'; Value = $true; Text = 'сглаживание' },
        @{ Case = 'стробирование'; Path = 'CALCulate(1).trace1.FILTer.GATE.TIME.STATe'; Value = $true; Text = 'стробирование' },
        @{ Case = 'усреднение'; Path = 'SENSe(1).AVERage.STATe'; Value = 'ON'; Text = 'усреднение' },
        @{ Case = 'полоса'; Path = 'SENSe(1).FREQuency.STARt'; Value = 1.5e6; Text = 'сетка частот изменены' },
        @{ Case = 'маркер'; Path = 'CALCulate(1).trace4.MARKer(2).X'; Value = 1.6e6; Text = 'экран S2VNA изменён' },
        @{ Case = 'заголовок'; Path = 'DISPlay.WINDow(1).TITLe.DATA'; Value = 'другое'; Text = 'экран S2VNA изменён' }) {
        param($Case, $Path, $Value, $Text)
        Initialize-BenchCalibrated
        [void](Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash })
        $script:ioValues[$Path] = $Value; $script:ioLog.Clear()
        { Invoke-BenchOp capture $script:captureArgs } | Should Throw $Text
        ($script:ioLog -contains 'call TRIGger.SEQuence.SINGle') | Should Be $false
    }
    It 'capture v2 отказывает при C? и при подключённом другом АКМ' {
        Initialize-BenchCalibrated
        [void](Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash })
        $script:correction.S21 = 'C?'
        { Invoke-BenchOp capture $script:captureArgs } | Should Throw 'интерполирована'
        $script:correction.S21 = 'COR'
        $script:acmInfo = 'ACM2520.2,99999999,30,Factory,2026-01-01,100 kHz,20 GHz,1601,3.5mm -F-,3.5mm -F-'
        { Invoke-BenchOp capture $script:captureArgs } | Should Throw 'другой АКМ'
        $script:acmInfo = 'absent'
        (Invoke-BenchOp capture $script:captureArgs).screen_hash | Should Be $script:benchHash
    }
    It 'capture v2: прибор заменён во время развёртки — ошибка' {
        Initialize-BenchCalibrated
        [void](Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash })
        $script:onSweep = { $script:benchApp.Name = 'Planar, S50180, 26030096, 26.2.2/2' }
        { Invoke-BenchOp capture $script:captureArgs } | Should Throw 'прибор заменён'
    }
    It 'capture v2 без применённого экрана или с чужим отпечатком отклоняется' {
        Initialize-BenchCalibrated
        { Invoke-BenchOp capture $script:captureArgs } | Should Throw 'не применялся'
        $arguments = $script:captureArgs.Clone(); $arguments.Remove('screen_hash')
        { Invoke-BenchOp capture $arguments } | Should Throw 'отпечаток'
    }
    It 'capture v2: трассы профиля нет на экране ERP' {
        Initialize-BenchCalibrated
        $screen = New-BenchScreen; $screen.traces = @($screen.traces | Where-Object { $_.param -ne 'S12' })
        [void](Invoke-BenchOp apply_screen @{ screen = $screen; screen_hash = $script:benchHash })
        { Invoke-BenchOp capture $script:captureArgs } | Should Throw 'трассы S12 нет на экране ERP'
    }
    It 'capture протокола 1 не меняется: без температур и отпечатка' {
        Initialize-BenchCalibrated
        $result = Invoke-BenchOp capture @{ profile = $script:profile; orientation = 'both'; port_to_end = @{ '1' = 'А'; '2' = 'Б' } } 1
        $names = @($result.PSObject.Properties.Name)
        ($names -contains 'temperatures') | Should Be $false
        ($names -contains 'screen_hash') | Should Be $false
        $result.artifacts[0].file_name | Should Be 'measurement.s2p'
    }
    It 'acm_confidence: удержание и внутренний запуск, данные и память АКМ парами' {
        Initialize-BenchCalibrated; $script:ioLog.Clear()
        $result = Invoke-BenchOp acm_confidence @{ profile = $script:profile; module_serial = '26140008' }
        $script:ioLog.IndexOf('set TRIGger.SEQuence.SOURce') | Should BeLessThan $script:ioLog.IndexOf('call SENSe(1).CORRection.COLLect.ECAL.CCHeck.ACQuire')
        $script:ioValues['TRIGger.SEQuence.SOURce'] | Should Be 'INT'
        $script:ioValues['INITiate(1).CONTinuous'] | Should Be $false
        $result.module_serial | Should Be '26140008'
        ($result.frequencies_hz -join ',') | Should Be '1000000,2000000'
        (@($result.data.PSObject.Properties.Name) -join ',') | Should Be 'S11,S21,S12,S22'
        ($result.data.S21[1] -join ',') | Should Be '2,-0.5'
        ($result.memory.S21[0] -join ',') | Should Be '12,0.5'
        $result.temperatures.acm_c | Should Be 30.9
    }
    It 'acm_confidence отказывает: чужой модуль, больше 4001 точки, без калибровки АКМ' {
        { Invoke-BenchOp acm_confidence @{ profile = $script:profile; module_serial = '26140008' } } | Should Throw 'исходное состояние'
        Initialize-BenchCalibrated
        { Invoke-BenchOp acm_confidence @{ profile = $script:profile; module_serial = '11111111' } } | Should Throw 'АКМ'
        $profile = New-BenchProfile; $profile.points = 4002
        { Invoke-BenchOp acm_confidence @{ profile = $profile; module_serial = '26140008' } } | Should Throw '4001'
        ($script:ioLog -contains 'call SENSe(1).CORRection.COLLect.ECAL.CCHeck.ACQuire') | Should Be $false
    }
    It 'store_memory запоминает только перечисленные графики, не меняя экран' {
        Initialize-BenchCalibrated
        [void](Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash }); $script:ioLog.Clear()
        $result = Invoke-BenchOp store_memory @{ screen_hash = $script:benchHash; traces = @(2,5) }
        ($script:memorized -join ',') | Should Be '2,5'
        ($result.memorized -join ',') | Should Be '2,5'
        $result.screen_hash | Should Be $script:benchHash
        @(Get-BenchSets '*').Count | Should Be 0
        (Invoke-BenchOp device_status).screen_hash | Should Be $script:benchHash
    }
    It 'store_memory: номер за пределом экрана или повтор отклоняются' {
        Initialize-BenchCalibrated
        [void](Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash })
        { Invoke-BenchOp store_memory @{ screen_hash = $script:benchHash; traces = @(7) } } | Should Throw 'номер графика'
        { Invoke-BenchOp store_memory @{ screen_hash = $script:benchHash; traces = @(1,1) } } | Should Throw 'повторяются'
        $script:memorized.Count | Should Be 0
    }
    It 'release: непрерывная развёртка с внутренним запуском, экран ERP остаётся, дальше только восстановление' {
        Initialize-BenchCalibrated
        [void](Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash })
        [void](Invoke-BenchOp capture $script:captureArgs); $script:ioLog.Clear()
        $result = Invoke-BenchOp release @{ screen_hash = $script:benchHash }
        $result.released | Should Be $true
        $result.continuous | Should Be $true
        $result.screen_hash | Should Be $script:benchHash
        $script:ioValues['TRIGger.SEQuence.SOURce'] | Should Be 'INT'
        $script:ioValues['INITiate(1).CONTinuous'] | Should Be $true
        @($script:ioLog | Where-Object { $_ -like '*MMEMory.LOAD*' }).Count | Should Be 0
        (Invoke-BenchOp device_status).screen_hash | Should Be $script:benchHash
        { Invoke-BenchOp capture $script:captureArgs } | Should Throw 'уже завершено'
    }
    It 'release без экрана ERP возвращает null, с чужим отпечатком отклоняется' {
        Initialize-BenchCalibrated
        { Invoke-BenchOp release @{ screen_hash = ('c' * 64) } } | Should Throw 'не применялся'
        ($null -eq (Invoke-BenchOp release @{ screen_hash = $null }).screen_hash) | Should Be $true
    }
    It 'после release и завершения сессии на сервере локальный маркер владения снимается' {
        Initialize-BenchCalibrated
        [void](Invoke-BenchOp release @{ screen_hash = $null })
        $ownerPath = Join-Path (Get-VnaControlRoot) 'owner.json'
        Mock Invoke-VnaSessionApi { [pscustomobject]@{ session_id = '11111111-1111-1111-1111-111111111111'; state = 'aborted'; lease_held = $false
            instrument_serial = '26030095'; commands = @() } }
        Get-ChildItem -LiteralPath (Get-VnaControlRoot) -Filter '*.json' | Where-Object { $_.BaseName -match '^[a-f0-9-]{36}$' } | Remove-Item
        $script:VnaWorker = $null
        Sync-VnaReceipts 'http://test' 'token'
        (Test-Path -LiteralPath $ownerPath) | Should Be $false
    }
    It 'приём команды: конверт протокола 2 запускает исполнителя, протокол 3 — нет' {
        $script:VnaControlInitialized = $true; $script:VnaWorker = $null
        Mock Get-MachineId { 'pc-1' }; Mock Get-VnaObservation { @{ ready = $true; serial = '26030095' } }
        Mock Start-VnaWorker {}; Mock Sync-VnaReceipts {}
        $script:claimJob = New-BenchEnvelope device_status @{} 2
        Mock Invoke-ErpApi { @{ job = $script:claimJob; poll_after_ms = 500 } }
        Invoke-ScanPollOnce 'http://test' 'token' '' | Should Be 'active'
        Assert-MockCalled Start-VnaWorker -Times 1 -Exactly -Scope It
        $script:claimJob = New-BenchEnvelope device_status @{} 3
        Invoke-ScanPollOnce 'http://test' 'token' '' | Should Be 'error'
        Assert-MockCalled Start-VnaWorker -Times 1 -Exactly -Scope It
        $script:VnaControlInitialized = $false
    }
}

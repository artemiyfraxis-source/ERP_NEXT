# Pester 3.x compatible. Every device/COM boundary is mocked; never opens S2VNA.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$controlTmp = Join-Path ([IO.Path]::GetTempPath()) ('pa-control-' + [guid]::NewGuid())
[void][IO.Directory]::CreateDirectory($controlTmp)
. (Join-Path $here '..\agent.ps1') -ConfigPath (Join-Path $controlTmp 'control-config.json') -LogPath (Join-Path $controlTmp 'agent.log')
$script:realS2ManualDefinition = (Get-Command Get-S2ManualDefinition).ScriptBlock
$script:realUpdateVnaWorker = (Get-Command Update-VnaWorker).ScriptBlock
$script:realScanPollOnce = (Get-Command Invoke-ScanPollOnce).ScriptBlock
$script:realSendHeartbeat = (Get-Command Send-Heartbeat).ScriptBlock
$script:realErpApi = (Get-Command Invoke-ErpApi).ScriptBlock

function New-ControlProfile {
    return [pscustomobject]@{ start_hz = 1e6; stop_hz = 2e6; points = 2; if_bandwidth_hz = 1000; power_dbm = 0
        impedance_ohm = 50; sweep = 'LIN'; smoothing = $false; averaging = $false; processing = 'off'
        traces = @('S11','S21','S12','S22'); vswr_limit = 1.5 }
}
function New-ControlEnvelope {
    param([string]$Op = 'prepare', $ArgsValue = @{})
    $id = [guid]::NewGuid().ToString()
    return [pscustomobject]@{ id = $id; command_id = $id; kind = 'vna_control'; protocol_version = 1
        session_id = '11111111-1111-1111-1111-111111111111'; expected_version = 4; expected_step = 0
        instrument_id = '22222222-2222-2222-2222-222222222222'; instrument_serial = '26030095'; op = $Op; args = $ArgsValue }
}
function New-ControlStatus {
    param($Command)
    return [pscustomobject]@{ session_id = $Command.session_id; state = 'preparing'; version = $Command.expected_version
        step = $Command.expected_step; lease_held = $true; instrument_id = $Command.instrument_id; instrument_serial = $Command.instrument_serial
        kit_definitions = @{ '33333333-3333-3333-3333-333333333333' = @{ serial_number = '26140008' } }
        commands = @([pscustomobject]@{ command_id = $Command.command_id; status = 'claimed'; expected_version = $Command.expected_version; expected_step = $Command.expected_step }) }
}
function Invoke-ControlTest {
    param([string]$Op, $ArgsValue = @{})
    $command = New-ControlEnvelope $Op $ArgsValue
    return Invoke-S2Command $script:controlApp $command (New-ControlStatus $command) (Join-Path (Get-VnaControlRoot) 'owner.json')
}
function Invoke-FakeS2Io {
    param($Scpi, [string]$Path, [string]$Mode = 'get', $Value)
    [void]$script:ioLog.Add("$Mode $Path")
    if ($Mode -eq 'call' -and $Path -match '^CALCulate\(1\).PARameter\((\d+)\).SELect$') { $script:selected = [int]$Matches[1]; return }
    if ($Path -eq 'SERVice.CHANnel(1).TRACe(1).ACTive') { return $script:selected }
    if ($Path -like 'CALCulate(1).SELected.*') { $Path = $Path.Replace('SELected.', ('trace' + $script:selected + '.')) }
    if ($Mode -eq 'set') {
        if ($Path -eq $script:rejectPath) { return }
        if ($Path -eq 'MMEMory.STORe.STATe') { [IO.File]::WriteAllText($Value,'original state'); return }
        if ($Path -eq 'MMEMory.LOAD.STATe') {
            $script:ioValues = $script:originalValues.Clone(); $script:selected = 2
            return
        }
        $script:ioValues[$Path] = $Value; return
    }
    if ($Mode -eq 'call') { return }
    if (-not $script:ioValues.ContainsKey($Path)) { throw "Missing fake COM getter: $Path" }
    return $script:ioValues[$Path]
}
function Set-ControlManualKit {
    $standards = @(); $definitions = @(); $assignments = @(); $index = 0
    foreach ($kind in @('OPEN','SHORT','LOAD')) {
        $index++
        $standards += [pscustomobject]@{ kind = $kind; ports = @(1); label = $kind; kit_ports = @() }
        $properties = @{ LABel = $kind; TYPE = $kind; FMINimum = 1e5; FMAXimum = 18e9; DELay = 0; LOSS = 0; Z0 = 50 }
        foreach ($coefficient in $(if ($kind -eq 'OPEN') { @('C0','C1','C2','C3') } elseif ($kind -eq 'SHORT') { @('L0','L1','L2','L3') } else { @() })) { $properties[$coefficient] = 0 }
        $definitions += [pscustomobject]@{ index = $index; properties = [pscustomobject]$properties }
        $assignments += [pscustomobject]@{ kind = $kind; ports = @(1); label = $kind; class_index = 1; standard_index = $index }
        foreach ($property in $properties.Keys) { $script:ioValues["SENSe(1).CORRection.COLLect.CKIT.STAN($index).$property"] = $properties[$property] }
    }
    $script:kit = [pscustomobject]@{ kit_id = '33333333-3333-3333-3333-333333333333'; method = 'solt1'; registry_fingerprint = ('a' * 64); slot = 28
        standards = $standards; assignments = $assignments; definition = [pscustomobject]@{ label = '6650F27'; standards = $definitions } }
    $script:manifest.kits = @($script:kit)
    $script:beginArgs = @{ method = 'solt1'; kit_id = $script:kit.kit_id; kit_fingerprint = $script:kit.registry_fingerprint; orientation = 'А'; standards = $standards }
}

Describe 'Управление ВАЦ: типовые команды и COM' {
    BeforeEach {
        $script:ConfigPath = Join-Path $TestDrive ([guid]::NewGuid().ToString() + '\config.json')
        $script:controlApp = [pscustomobject]@{ Ready = $true; Name = 'Planar, S50180, 26030095, 26.2.2/2'; SCPI = [pscustomobject]@{} }
        $script:profile = New-ControlProfile
        $script:ioLog = New-Object 'Collections.Generic.List[string]'
        $script:ioValues = @{}; $script:selected = 2; $script:rejectPath = ''
        $writes = Get-S2ProfileWrites $script:profile
        foreach ($path in $writes.Keys) { $script:ioValues[$path] = $writes[$path] }
        $extra = @{
            'SENSe(1).AVERage.COUNt' = 3; 'SENSe(1).CORRection.STATe' = $true
            'SENSe(1).CORRection.COLLect.CKIT.SELect' = 28; 'SENSe(1).CORRection.COLLect.CKIT.LABel' = '6650F27'
            'SENSe(1).CORRection.TRIGger.FREE.STATe' = $false; 'SOURce(1).POWer.PORT(2).COUPle' = $false
            'INITiate(1).CONTinuous' = $true; 'TRIGger.SEQuence.SOURce' = 'INT'; 'MMEMory.STORe.STYPe' = 'STAN'
            'SERVice.SWEep.POINts' = 20001; 'SERVice.SWEep.FREQuency.MINimum' = 1e5; 'SERVice.SWEep.FREQuency.MAXimum' = 18e9
            'IEEE4882.OPC' = 1; 'SENSe(1).FREQuency.DATA' = @(1e6,2e6)
            'SENSe(1).CORRection.COLLect.ECAL.INFormation' = 'ACM2520.2,26140008,30,Factory,2026-01-01,100 kHz,20 GHz,1601,3.5mm -F-,3.5mm -F-'
            'SENSe(1).CORRection.COLLect.ECAL.ORIentation.STATe' = $false
        }
        foreach ($path in $extra.Keys) { $script:ioValues[$path] = $extra[$path] }
        $traceIndex = 0
        foreach ($parameter in $script:profile.traces) {
            $traceIndex++
            $script:ioValues["CALCulate(1).PARameter($traceIndex).DEFine"] = $parameter
            $settings = @{ FORMat = $(if ($parameter -in @('S11','S22')) { 'SWR' } else { 'MLOG' })
                'SMOothing.STATe' = $false; 'MATH.FUNCtion' = 'OFF'; 'CONVersion.STATe' = $false
                'FILTer.GATE.TIME.STATe' = $false; 'CORRection.STATus' = 'COR'
                'LIMit.DATA' = @(1,1,1e6,2e6,1.5,1.5); 'LIMit.DISPlay.STATe' = $true }
            foreach ($tail in $settings.Keys) { $script:ioValues["CALCulate(1).trace$traceIndex.$tail"] = $settings[$tail] }
            $script:ioValues["CALCulate(1).TRACe($traceIndex).DATA.SDATa"] = @($traceIndex,0,$traceIndex,0)
        }
        $script:originalValues = $script:ioValues.Clone()
        Mock Invoke-S2Io { $modeValue = if ($Mode) { $Mode } else { 'get' }; Invoke-FakeS2Io $Scpi $Path $modeValue $Value }
        Mock Get-S2ManualDefinition { & $script:realS2ManualDefinition $Scpi $Kit $Profile }
        Mock Invoke-S2KitFile {
            if ($Direction -eq 'STORe') { [IO.File]::WriteAllText($Path,'original kit definitions and classes') }
        }
        $script:acmStandard = [pscustomobject]@{ kind = 'ACM'; ports = @(1,2); label = 'Подключите АКМ'; kit_ports = @(2,1) }
        $script:kit = [pscustomobject]@{ kit_id = '33333333-3333-3333-3333-333333333333'; method = 'acm'; registry_fingerprint = ('a' * 64)
            standards = @($script:acmStandard); definition = [pscustomobject]@{ model = 'ACM2520.2'; serial = '26140008'; min_hz = 1e5; max_hz = 20e9
                identity_fields = [pscustomobject]@{ f3 = 'Factory'; f4 = '2026-01-01'; f7 = '1601'; f8 = '3.5mm -F-'; f9 = '3.5mm -F-' } } }
        $script:manifest = [pscustomobject]@{ instrument_serial = '26030095'; ordinary_state_verified = $true; kits = @($script:kit) }
        Mock Get-VnaManifest { $script:manifest }
        $script:beginArgs = @{ method = 'acm'; kit_id = $script:kit.kit_id; kit_fingerprint = $script:kit.registry_fingerprint
            orientation = 'both'; standards = @($script:acmStandard) }
        Mock Get-VnaApplication { throw 'Tests must not open real COM' }
    }
    It 'неверный серийный номер запрещает даже резервирование' {
        $script:controlApp.Name = 'Planar, S50180, other, 26.2'
        { Invoke-ControlTest prepare } | Should Throw 'серийный номер'
        $script:ioLog.Count | Should Be 0
    }
    It 'утраченная аренда запрещает записи' {
        $command = New-ControlEnvelope; $status = New-ControlStatus $command; $status.lease_held = $false
        { Invoke-S2Command $script:controlApp $command $status (Join-Path (Get-VnaControlRoot) 'owner.json') } | Should Throw 'не владеет'
        $script:ioLog.Count | Should Be 0
    }
    It 'изменённый шаг запрещает записи' {
        $command = New-ControlEnvelope; $status = New-ControlStatus $command; $status.step++
        { Assert-VnaLease $command $status } | Should Throw 'не владеет'
    }
    It 'отменённая команда запрещает записи' {
        $command = New-ControlEnvelope; $status = New-ControlStatus $command; $status.commands[0].status = 'cancelled'
        { Assert-VnaLease $command $status } | Should Throw 'не владеет'
    }
    It 'резервирует CDST и набор до применения профиля' {
        $result = Invoke-ControlTest prepare
        $result.state_saved | Should Be $true
        $result.kit_assignments_saved | Should Be $true
        [guid]::Parse($result.backup_id) | Should Not Be ([guid]::Empty)
        $owner = Read-VnaJson (Join-Path (Get-VnaControlRoot) 'owner.json')
        (Test-Path $owner.state_path) | Should Be $true
        $owner.state_hash | Should Match '^[A-F0-9]{64}$'
        $script:selected | Should Be 2
        $script:ioLog.IndexOf('set MMEMory.STORe.STYPe') | Should BeLessThan $script:ioLog.IndexOf('set MMEMory.STORe.STATe')
        Assert-MockCalled Invoke-S2KitFile -Times 1 -Exactly -Scope It -ParameterFilter { $Direction -eq 'STORe' -and $Slot -eq 28 }
    }
    It 'не заменяет исходную копию повторным prepare' {
        [void](Invoke-ControlTest prepare)
        { Invoke-ControlTest prepare } | Should Throw 'уже сохранено'
    }
    It 'ошибка резервирования удерживает локальное владение' {
        Mock Invoke-S2KitFile { throw 'disk full' }
        { Invoke-ControlTest prepare } | Should Throw 'disk full'
        (Test-Path (Join-Path (Get-VnaControlRoot) 'owner.json')) | Should Be $true
    }
    It 'ошибка проверки возможностей до сохранения копии подтверждает отсутствие изменений' {
        $preparation = @{ backup_saved = $false }
        $command = New-ControlEnvelope; $script:ioValues['SERVice.SWEep.POINts'] = 0
        { Invoke-S2Command $script:controlApp $command (New-ControlStatus $command) (Join-Path (Get-VnaControlRoot) 'owner.json') -Preparation $preparation } | Should Throw 'возможности прибора'
        $preparation.backup_saved | Should Be $false
    }
    It 'ошибка после записи состояния подтверждает сохранённую копию' {
        $preparation = @{ backup_saved = $false }; $command = New-ControlEnvelope
        Mock Invoke-S2KitFile { throw 'kit backup failed' }
        { Invoke-S2Command $script:controlApp $command (New-ControlStatus $command) (Join-Path (Get-VnaControlRoot) 'owner.json') -Preparation $preparation } | Should Throw 'kit backup failed'
        $preparation.backup_saved | Should Be $true
    }
    It 'частичная запись состояния не подтверждает отсутствие изменений' {
        $preparation = @{ backup_saved = $false }; $command = New-ControlEnvelope
        Mock Invoke-S2Io {
            if ($Path -eq 'MMEMory.STORe.STATe' -and $Mode -eq 'set') {
                [IO.File]::WriteAllText($Value,'partial backup'); throw 'partial write'
            }
            $modeValue = if ($Mode) { $Mode } else { 'get' }; Invoke-FakeS2Io $Scpi $Path $modeValue $Value
        }
        { Invoke-S2Command $script:controlApp $command (New-ControlStatus $command) (Join-Path (Get-VnaControlRoot) 'owner.json') -Preparation $preparation } | Should Throw 'partial write'
        $preparation.backup_saved | Should Be $null
    }
    It 'неподтверждённый возврат выбранной трассы не считается неизменённым прибором' {
        $preparation = @{ backup_saved = $false }; $command = New-ControlEnvelope
        Mock Invoke-S2Io {
            if ($Path -eq 'CALCulate(1).PARameter(2).SELect' -and $Mode -eq 'call') { return }
            $modeValue = if ($Mode) { $Mode } else { 'get' }; Invoke-FakeS2Io $Scpi $Path $modeValue $Value
        }
        { Invoke-S2Command $script:controlApp $command (New-ControlStatus $command) (Join-Path (Get-VnaControlRoot) 'owner.json') -Preparation $preparation } | Should Throw 'выбранная трасса после чтения'
        $preparation.backup_saved | Should Be $null
    }
    It 'профиль: остановка, параметры, трассы, отключение обработки, чтение' {
        [void](Invoke-ControlTest prepare); $script:ioLog.Clear()
        $result = Invoke-ControlTest apply_profile @{ profile = $script:profile }
        $script:ioLog.IndexOf('call ABORt') | Should BeLessThan $script:ioLog.IndexOf('set INITiate(1).CONTinuous')
        $script:ioLog.IndexOf('set INITiate(1).CONTinuous') | Should BeLessThan $script:ioLog.IndexOf('set SENSe(1).SWEep.TYPE')
        $script:ioLog.IndexOf('set SOURce(1).POWer.PORT(1).COUPle') | Should BeLessThan $script:ioLog.IndexOf('set SOURce(1).POWer.PORT(1).LEVel.IMMediate.AMPLitude')
        $script:ioValues['CALCulate(1).trace1.FORMat'] | Should Be 'SWR'
        $script:ioValues['CALCulate(1).trace2.FORMat'] | Should Be 'MLOG'
        foreach ($index in 1..4) {
            $script:ioValues["CALCulate(1).trace$index.CONVersion.STATe"] | Should Be $false
            $script:ioValues["CALCulate(1).trace$index.FILTer.GATE.TIME.STATe"] | Should Be $false
        }
        Assert-VnaEqual $result.profile $script:profile 'profile'
    }
    It 'отказ чтения параметра не выдаёт успех профиля' {
        [void](Invoke-ControlTest prepare)
        $script:rejectPath = 'SENSe(1).SWEep.POINts'; $script:profile.points = 5
        { Invoke-ControlTest apply_profile @{ profile = $script:profile } } | Should Throw 'проверка чтением'
    }
    It 'профиль за пределом прибора запрещён до ABORt' {
        [void](Invoke-ControlTest prepare); $script:ioLog.Clear(); $script:profile.stop_hz = 19e9
        { Invoke-ControlTest apply_profile @{ profile = $script:profile } } | Should Throw 'возможности прибора'
        ($script:ioLog -contains 'call ABORt') | Should Be $false
    }
    It 'NaN не отправляется прибору' {
        $script:profile.start_hz = [double]::NaN
        { Assert-S2Profile $script:controlApp.SCPI $script:profile -Apply } | Should Throw 'недопустимое число'
        $script:ioLog.Count | Should Be 0
    }
    It 'манифест и АКМ проверяются до начала калибровки' {
        [void](Invoke-ControlTest prepare); [void](Invoke-ControlTest apply_profile @{ profile = $script:profile })
        $result = Invoke-ControlTest begin_calibration $script:beginArgs
        $result.definition_hash | Should Match '^[a-f0-9]{64}$'
        $result.kit_fingerprint | Should Be ('a' * 64)
        ($script:ioLog -contains 'set SENSe(1).CORRection.COLLect.ECAL.SOLT2') | Should Be $false
    }
    It 'несовпадение определения ERP запрещает калибровку' {
        [void](Invoke-ControlTest prepare); [void](Invoke-ControlTest apply_profile @{ profile = $script:profile })
        $script:beginArgs.kit_fingerprint = 'b' * 64
        { Invoke-ControlTest begin_calibration $script:beginArgs } | Should Throw 'манифесту'
    }
    It 'изменённые коэффициенты характеризации АКМ запрещают калибровку' {
        $script:ioValues['SENSe(1).CORRection.COLLect.ECAL.INFormation'] = 'ACM2520.2,26140008,31,User,2026-01-01,100 kHz,20 GHz,1601'
        { Assert-S2Acm $script:controlApp.SCPI '26140008' 1e6 2e6 $script:kit.definition } | Should Throw 'проверка чтением'
    }
    It 'температура АКМ может меняться' {
        $script:ioValues['SENSe(1).CORRection.COLLect.ECAL.INFormation'] = $script:ioValues['SENSe(1).CORRection.COLLect.ECAL.INFormation'].Replace(',30,',',33,')
        (Assert-S2Acm $script:controlApp.SCPI '26140008' 1e6 2e6 $script:kit.definition).serial | Should Be '26140008'
    }
    It 'неверный модуль АКМ запрещает получение' {
        { Assert-S2Acm $script:controlApp.SCPI 'other' 1e6 2e6 $script:kit.definition } | Should Throw 'серийный номер или полоса'
    }
    It 'недостаточная полоса АКМ запрещает получение' {
        { Assert-S2Acm $script:controlApp.SCPI '26140008' 1e6 21e9 $script:kit.definition } | Should Throw 'серийный номер или полоса'
    }
    It 'АКМ выполняется один раз после установки автоориентации' {
        [void](Invoke-ControlTest prepare); [void](Invoke-ControlTest apply_profile @{ profile = $script:profile })
        [void](Invoke-ControlTest begin_calibration $script:beginArgs); $script:ioLog.Clear()
        $result = Invoke-ControlTest acm_calibrate @{ ports = @(1,2); kit_ports = @(2,1); module_serial = '26140008'; start_hz = 1e6; stop_hz = 2e6 }
        $result.acquired | Should Be $true
        $script:ioLog.IndexOf('set SENSe(1).CORRection.COLLect.ECAL.ORIentation.STATe') | Should BeLessThan $script:ioLog.IndexOf('set SENSe(1).CORRection.COLLect.ECAL.SOLT2')
        @($script:ioLog | Where-Object { $_ -eq 'set SENSe(1).CORRection.COLLect.ECAL.SOLT2' }).Count | Should Be 1
        Assert-VnaEqual $script:ioValues['SENSe(1).CORRection.COLLect.ECAL.SOLT2'] @(1,2) 'ports'
    }
    It 'несоответствие перестановки портов АКМ запрещено' {
        [void](Invoke-ControlTest prepare); [void](Invoke-ControlTest apply_profile @{ profile = $script:profile })
        [void](Invoke-ControlTest begin_calibration $script:beginArgs)
        { Invoke-ControlTest acm_calibrate @{ ports = @(1,2); kit_ports = @(1,2); module_serial = '26140008'; start_hz = 1e6; stop_hz = 2e6 } } | Should Throw 'порты АКМ'
    }
    It 'сохранение АКМ проверяет COR всех трасс без SAVE и METHod.TYPE' {
        [void](Invoke-ControlTest prepare); [void](Invoke-ControlTest apply_profile @{ profile = $script:profile })
        [void](Invoke-ControlTest begin_calibration $script:beginArgs); $script:ioLog.Clear()
        $result = Invoke-ControlTest save_calibration @{ method = 'acm'; traces = $script:profile.traces }
        $result.correction.Count | Should Be 4
        $result.method | Should Be 'acm'
        ($script:ioLog -contains 'call SENSe(1).CORRection.COLLect.SAVE') | Should Be $false
        @($script:ioLog | Where-Object { $_ -like '*METHod.TYPE*' }).Count | Should Be 0
        foreach ($index in 1..4) { ($script:ioLog -contains "call CALCulate(1).PARameter($index).SELect") | Should Be $true }
    }
    It 'интерполяция C? не считается полной коррекцией' {
        $script:ioValues['CALCulate(1).trace3.CORRection.STATus'] = 'C?'
        { Get-S2Correction $script:controlApp.SCPI $script:profile.traces } | Should Throw 'нет полной коррекции'
    }
    It 'свежая развёртка: continuous BUS SINGLE OPC данные' {
        [void](Invoke-ControlTest prepare); [void](Invoke-ControlTest apply_profile @{ profile = $script:profile }); $script:ioLog.Clear()
        $result = Invoke-ControlTest capture @{ profile = $script:profile; orientation = 'both'; port_to_end = @{ '1' = 'А'; '2' = 'Б' } }
        $script:ioLog.IndexOf('set INITiate(1).CONTinuous') | Should BeLessThan $script:ioLog.IndexOf('set TRIGger.SEQuence.SOURce')
        $script:ioLog.IndexOf('set TRIGger.SEQuence.SOURce') | Should BeLessThan $script:ioLog.IndexOf('call TRIGger.SEQuence.SINGle')
        $script:ioLog.IndexOf('call TRIGger.SEQuence.SINGle') | Should BeLessThan $script:ioLog.IndexOf('get IEEE4882.OPC')
        $script:ioLog.IndexOf('get IEEE4882.OPC') | Should BeLessThan $script:ioLog.IndexOf('get SENSe(1).FREQuency.DATA')
        $text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($result.artifacts[0].content_base64))
        $text | Should Match '1000000 1 0 2 0 3 0 4 0'
        $result.artifacts[0].file_name | Should Be 'measurement.s2p'
        @($script:ioLog | Where-Object { $_ -like '*IMMediate' }).Count | Should Be 0
    }
    It 'неполная развёртка OPC запрещает экспорт' {
        [void](Invoke-ControlTest prepare); [void](Invoke-ControlTest apply_profile @{ profile = $script:profile })
        $script:ioValues['IEEE4882.OPC'] = 0
        { Invoke-ControlTest capture @{ profile = $script:profile } } | Should Throw 'развёртка не завершена'
    }
    It 'abort останавливает и очищает незавершённую калибровку' {
        [void](Invoke-ControlTest prepare); $script:ioLog.Clear()
        $result = Invoke-ControlTest abort
        $script:ioLog.IndexOf('call ABORt') | Should BeLessThan $script:ioLog.IndexOf('set INITiate(1).CONTinuous')
        $script:ioLog.IndexOf('set INITiate(1).CONTinuous') | Should BeLessThan $script:ioLog.IndexOf('call SENSe(1).CORRection.COLLect.CLEar')
        $result.stopped | Should Be $true
        $result.calibration_cleared | Should Be $true
    }
    It 'restore восстанавливает исходный CDST, набор и проверяет чтением' {
        [void](Invoke-ControlTest prepare); [void](Invoke-ControlTest apply_profile @{ profile = $script:profile })
        (Invoke-ControlTest restore).restored | Should Be $true
        (Read-VnaJson (Join-Path (Get-VnaControlRoot) 'owner.json')).restored | Should Be $true
        Assert-MockCalled Invoke-S2KitFile -Times 1 -Exactly -Scope It -ParameterFilter { $Direction -eq 'LOAD' }
    }
    It 'restore с потерянной копией запрещён' {
        [void](Invoke-ControlTest prepare)
        $owner = Read-VnaJson (Join-Path (Get-VnaControlRoot) 'owner.json')
        Remove-Item -LiteralPath $owner.state_path
        { Invoke-ControlTest restore } | Should Throw 'отсутствует или повреждена'
    }
    It 'restore другой сессии запрещён до записи' {
        [void](Invoke-ControlTest prepare); $script:ioLog.Clear()
        $command = New-ControlEnvelope restore; $command.session_id = [guid]::NewGuid().ToString()
        { Invoke-S2Command $script:controlApp $command (New-ControlStatus $command) (Join-Path (Get-VnaControlRoot) 'owner.json') } | Should Throw 'другой сессии'
        $script:ioLog.Count | Should Be 0
    }
    It 'restore при несовпадении настроек не выдаёт успех' {
        [void](Invoke-ControlTest prepare)
        $script:originalValues['SENSe(1).AVERage.COUNt'] = 8
        { Invoke-ControlTest restore } | Should Throw 'восстановленное состояние'
    }
    It 'после watchdog restore запрещён даже с завершившимся клиентом' {
        [void](Invoke-ControlTest prepare); $script:ioLog.Clear()
        Write-VnaJson (Join-Path (Get-VnaControlRoot) 'quarantine.json') @{
            session_id = '11111111-1111-1111-1111-111111111111'; serial = '26030095'; created_at = [DateTime]::UtcNow.ToString('o') }
        Mock Get-Process { [pscustomobject]@{ StartTime = [DateTime]::UtcNow.AddHours(-1) } }
        { Invoke-ControlTest restore } | Should Throw 'могла продолжиться'
        $script:ioLog.Count | Should Be 0
    }
    It 'после watchdog abort требует перезапуска S2VNA' {
        [void](Invoke-ControlTest prepare)
        Write-VnaJson (Join-Path (Get-VnaControlRoot) 'quarantine.json') @{
            session_id = '11111111-1111-1111-1111-111111111111'; serial = '26030095'; created_at = [DateTime]::UtcNow.ToString('o') }
        Mock Get-Process { [pscustomobject]@{ StartTime = [DateTime]::UtcNow.AddHours(-1) } }
        { Invoke-ControlTest abort } | Should Throw 'перезапустите S2VNA'
    }
    It 'новый abort после перезапуска S2VNA снимает запрет' {
        [void](Invoke-ControlTest prepare)
        Write-VnaJson (Join-Path (Get-VnaControlRoot) 'quarantine.json') @{
            session_id = '11111111-1111-1111-1111-111111111111'; serial = '26030095'; created_at = [DateTime]::UtcNow.AddMinutes(-1).ToString('o') }
        Mock Get-Process { [pscustomobject]@{ StartTime = [DateTime]::UtcNow } }
        (Invoke-ControlTest abort).stopped | Should Be $true
        (Test-Path (Join-Path (Get-VnaControlRoot) 'quarantine.json')) | Should Be $false
    }
    It 'ручной SOLT выполняет две отдельные команды THRU с внутренним триггером' {
        [void](Invoke-ControlTest prepare); [void](Invoke-ControlTest apply_profile @{ profile = $script:profile })
        Mock Get-S2ManualDefinition { @{ verified = $true } }
        $standards = @(
            [pscustomobject]@{ kind = 'THRU'; ports = @(1,2); label = 'THRU'; kit_ports = @() },
            [pscustomobject]@{ kind = 'THRU'; ports = @(2,1); label = 'THRU'; kit_ports = @() })
        $ownerPath = Join-Path (Get-VnaControlRoot) 'owner.json'; $owner = Read-VnaJson $ownerPath
        $owner.definition_hash = Get-VnaDigest (ConvertTo-VnaCanonical @{ verified = $true })
        $owner.calibration = @{ method = 'solt2'; standards = $standards }; Write-VnaJson $ownerPath $owner
        $script:ioValues['SENSe(1).CORRection.TRIGger.FREE.STATe'] = $true
        (Invoke-ControlTest measure_standard @{ standard = $standards[0] }).acquired | Should Be $true
        Assert-VnaEqual $script:ioValues['SENSe(1).CORRection.COLLect.ACQuire.THRU'] @(1,2) 'первое направление'
        (Invoke-ControlTest measure_standard @{ standard = $standards[1] }).acquired | Should Be $true
        Assert-VnaEqual $script:ioValues['SENSe(1).CORRection.COLLect.ACQuire.THRU'] @(2,1) 'второе направление'
        @($script:ioLog | Where-Object { $_ -eq 'set SENSe(1).CORRection.COLLect.ACQuire.THRU' }).Count | Should Be 2
    }
    It 'ручной SOLT с BUS запрещён до блокирующего вызова' {
        [void](Invoke-ControlTest prepare); [void](Invoke-ControlTest apply_profile @{ profile = $script:profile })
        Mock Get-S2ManualDefinition { @{ verified = $true } }
        $standard = [pscustomobject]@{ kind = 'OPEN'; ports = @(1); label = 'OPEN'; kit_ports = @() }
        $ownerPath = Join-Path (Get-VnaControlRoot) 'owner.json'; $owner = Read-VnaJson $ownerPath
        $owner.definition_hash = Get-VnaDigest (ConvertTo-VnaCanonical @{ verified = $true })
        $owner.calibration = @{ method = 'solt1'; standards = @($standard) }; Write-VnaJson $ownerPath $owner
        $script:ioValues['TRIGger.SEQuence.SOURce'] = 'BUS'
        { Invoke-ControlTest measure_standard @{ standard = $standard } } | Should Throw 'триггер SOLT'
        ($script:ioLog -contains 'set SENSe(1).CORRection.COLLect.ACQuire.OPEN') | Should Be $false
    }
    It 'ручной SAVE выполняется один раз и проверяет COR' {
        [void](Invoke-ControlTest prepare); [void](Invoke-ControlTest apply_profile @{ profile = $script:profile })
        $ownerPath = Join-Path (Get-VnaControlRoot) 'owner.json'; $owner = Read-VnaJson $ownerPath
        $owner.calibration = @{ method = 'solt2' }; Write-VnaJson $ownerPath $owner
        (Invoke-ControlTest save_calibration @{ method = 'solt2'; traces = $script:profile.traces }).correction.Count | Should Be 4
        @($script:ioLog | Where-Object { $_ -eq 'call SENSe(1).CORRection.COLLect.SAVE' }).Count | Should Be 1
    }
    It 'ручной begin сверяет коэффициенты и назначения до CLEar и выбора SOLT1' {
        [void](Invoke-ControlTest prepare); [void](Invoke-ControlTest apply_profile @{ profile = $script:profile }); Set-ControlManualKit
        $script:ioLog.Clear()
        $result = Invoke-ControlTest begin_calibration $script:beginArgs
        $result.definition_hash | Should Match '^[a-f0-9]{64}$'
        $script:ioLog.IndexOf('get SENSe(1).CORRection.COLLect.CKIT.STAN(1).C3') | Should BeLessThan $script:ioLog.IndexOf('call SENSe(1).CORRection.COLLect.CLEar')
        $script:ioLog.IndexOf('call SENSe(1).CORRection.COLLect.CLEar') | Should BeLessThan $script:ioLog.IndexOf('set SENSe(1).CORRection.TRIGger.FREE.STATe')
        $script:ioLog.IndexOf('set SENSe(1).CORRection.TRIGger.FREE.STATe') | Should BeLessThan $script:ioLog.IndexOf('set SENSe(1).CORRection.COLLect.METHod.SOLT1')
        $script:ioValues['SENSe(1).CORRection.COLLect.METHod.SOLT1'] | Should Be 1
    }
    It 'несовпадение коэффициента ручной меры запрещает начало' {
        [void](Invoke-ControlTest prepare); [void](Invoke-ControlTest apply_profile @{ profile = $script:profile }); Set-ControlManualKit
        $script:ioValues['SENSe(1).CORRection.COLLect.CKIT.STAN(1).C0'] = 1; $script:ioLog.Clear()
        { Invoke-ControlTest begin_calibration $script:beginArgs } | Should Throw 'мера SOLT C0'
        ($script:ioLog -contains 'call SENSe(1).CORRection.COLLect.CLEar') | Should Be $false
    }
    It 'реальный COM адаптер обходит методы с индексами 3 и 4' {
        # Bypass the mock only for this in-memory object; no COM class is instantiated.
        $ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $here '..\agent.ps1'),[ref]$null,[ref]$null)
        $definition = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-S2Io' },$true)
        $adapter = [scriptblock]::Create($definition.Body.Extent.Text.Trim('{','}'))
        $node = [pscustomobject]@{ Value = [pscustomobject]@{ DEFine = 'S22' } }
        $node | Add-Member ScriptMethod PARameter { param($index) if ($index -ne 4) { throw 'index' }; $this.Value }
        (& $adapter $node 'PARameter(4).DEFine') | Should Be 'S22'
    }
}

Describe 'Управление ВАЦ: Touchstone' {
    BeforeEach { $script:profile = New-ControlProfile; $script:data = @{ S11 = @(1,2,3,4); S21 = @(5,6,7,8); S12 = @(9,10,11,12); S22 = @(13,14,15,16) } }
    It 'перестановка требуемых трасс не меняет порядок матрицы s2p' {
        $script:profile.traces = @('S22','S12','S11','S21')
        $artifact = ConvertTo-S2Artifact @(1e6,2e6) $script:data $script:profile
        [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($artifact.content_base64)) | Should Match '1000000 1 2 5 6 9 10 13 14'
    }
    It 'одна трасса формирует s1p' {
        $script:profile.traces = @('S11')
        (ConvertTo-S2Artifact @(1e6,2e6) $script:data $script:profile).file_name | Should Be 'measurement.s1p'
    }
    It 'неполный массив отклоняется' {
        $script:data.S22 = @(1,2)
        { ConvertTo-S2Artifact @(1e6,2e6) $script:data $script:profile } | Should Throw 'неполные'
    }
    It 'частоты другого диапазона отклоняются' {
        { ConvertTo-S2Artifact @(1e6,3e6) $script:data $script:profile } | Should Throw 'сетка частот'
    }
    It 'NaN в данных отклоняется' {
        $script:data.S11[0] = [double]::NaN
        { ConvertTo-S2Artifact @(1e6,2e6) $script:data $script:profile } | Should Throw 'недопустимое число'
    }
}

Describe 'Управление ВАЦ: основной цикл и блокирующие устройства' {
    BeforeEach {
        $script:ConfigPath = Join-Path $TestDrive ([guid]::NewGuid().ToString() + '\config.json')
        $script:command = New-ControlEnvelope
        $script:VnaWorker = $null
        $script:HeartbeatDevices = $null; $script:VnaFirstObservationReported = $false
        $script:VnaNextFence = [DateTime]::MinValue
        Mock Initialize-VnaControl { $script:VnaControlInitialized = $true }
        Mock Update-VnaWorker { & $script:realUpdateVnaWorker $Base $AuthToken }
        Mock Get-VnaObservation { $null }
        Mock Write-Log {}
        Mock Send-Heartbeat {}
        Mock Invoke-PollOnce { 'idle' }
        Mock Invoke-ScanPollOnce { 'active' }
        Mock Sync-VnaReceipts {}
        Mock Start-VnaWorker {}
        Mock Invoke-VnaSessionApi { New-ControlStatus $script:command }
    }
    AfterEach { $script:VnaWorker = $null; $script:VnaControlInitialized = $false }

    It 'активная команда сохраняет лёгкий heartbeat и опрос управления без печати и поиска сканеров' {
        Mock Update-VnaWorker {}
        $script:VnaWorker = @{ command = $script:command }
        Start-AgentLoop 'http://test' 'token' -PrinterName 'TSC' -Once
        Assert-MockCalled Invoke-PollOnce -Times 0 -Exactly -Scope It
        Assert-MockCalled Send-Heartbeat -Times 0 -Exactly -Scope It -ParameterFilter { -not $Lightweight }
        Assert-MockCalled Send-Heartbeat -Times 1 -Exactly -Scope It -ParameterFilter { $Lightweight }
        Assert-MockCalled Invoke-ScanPollOnce -Times 1 -Exactly -Scope It
    }

    It 'ожидающая зависающая печать не мешает watchdog остановить worker и записать uncertain' {
        $entry = Get-VnaJournal $script:command; $entry.state = 'executing'
        Write-VnaJson (Get-VnaCommandPath $script:command) $entry
        $process = [pscustomobject]@{ HasExited = $false; Killed = $false; Waited = $false; Disposed = $false }
        $process | Add-Member ScriptMethod Kill { $this.Killed = $true; $this.HasExited = $true }
        $process | Add-Member ScriptMethod WaitForExit { param($timeout) $this.Waited = $true; return $true }
        $process | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
        $script:VnaWorker = @{ process = $process; command = $script:command; started = [DateTime]::UtcNow }
        # Fail fast at the blocking boundary instead of hanging the test runner inside Winspool.
        Mock Invoke-PollOnce { throw 'Simulated hung print' }
        Mock Send-Heartbeat { if (-not $Lightweight) { throw 'Simulated hung scanner discovery' } }
        Mock Invoke-VnaSessionApi { throw 'offline' }
        $script:sleepCalls = 0
        Mock Start-Sleep {
            $script:sleepCalls++
            if ($script:sleepCalls -gt 1) { throw 'End test loop' }
            $script:VnaWorker.started = [DateTime]::UtcNow.AddSeconds(-121)
        }
        { Start-AgentLoop 'http://test' 'token' -PrinterName 'TSC' } | Should Throw 'End test loop'
        Assert-MockCalled Invoke-PollOnce -Times 0 -Exactly -Scope It
        Assert-MockCalled Send-Heartbeat -Times 0 -Exactly -Scope It -ParameterFilter { -not $Lightweight }
        Assert-MockCalled Send-Heartbeat -Times 1 -Exactly -Scope It -ParameterFilter { $Lightweight }
        Assert-MockCalled Invoke-ScanPollOnce -Times 1 -Exactly -Scope It
        $process.Killed | Should Be $true
        $process.Waited | Should Be $true
        $process.Disposed | Should Be $true
        $script:VnaWorker | Should Be $null
        $entry = Read-VnaJson (Get-VnaCommandPath $script:command)
        $entry.state | Should Be 'uncertain'
        $entry.payload.error | Should Match 'неизвестен'
        (Test-Path (Join-Path (Get-VnaControlRoot) 'quarantine.json')) | Should Be $true
    }

    It 'без активной команды печать и отложенный heartbeat выполняются' {
        Mock Update-VnaWorker {}
        Start-AgentLoop 'http://test' 'token' -PrinterName 'TSC' -Once
        Assert-MockCalled Invoke-PollOnce -Times 1 -Exactly -Scope It
        Assert-MockCalled Send-Heartbeat -Times 1 -Exactly -Scope It
    }

    It 'между командами незавершённая сессия получает restore, минуя зависающую печать и поиск сканеров' {
        Mock Update-VnaWorker {}
        $script:command = New-ControlEnvelope restore
        Write-VnaJson (Join-Path (Get-VnaControlRoot) 'owner.json') @{
            session_id = $script:command.session_id; serial = $script:command.instrument_serial; restored = $false }
        # The loop must reach control claiming and dispatch restore without entering either blocking call.
        Mock Invoke-PollOnce { throw 'Simulated hung print' }
        Mock Send-Heartbeat { if (-not $Lightweight) { throw 'Simulated hung scanner discovery' } }
        Mock Get-MachineId { 'pc-1' }
        Mock Invoke-ErpApi { @{ job = $script:command; poll_after_ms = 500 } }
        Mock Invoke-ScanPollOnce { & $script:realScanPollOnce $Base $AuthToken $ScannerAddress }
        Start-AgentLoop 'http://test' 'token' -PrinterName 'TSC' -Once
        Assert-MockCalled Invoke-PollOnce -Times 0 -Exactly -Scope It
        Assert-MockCalled Send-Heartbeat -Times 0 -Exactly -Scope It -ParameterFilter { -not $Lightweight }
        Assert-MockCalled Send-Heartbeat -Times 1 -Exactly -Scope It -ParameterFilter { $Lightweight }
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Url -like '*/scan/agent/claim' -and ($Body.kinds -join ',') -ceq 'vna_control' }
        Assert-MockCalled Start-VnaWorker -Times 1 -Exactly -Scope It -ParameterFilter {
            $Command.op -ceq 'restore' -and $Command.command_id -ceq $script:command.command_id }
    }

    It 'после восстановления или освобождения сессии печать и heartbeat возобновляются: <Release>' -TestCases @(
        @{ Release = 'restored' }, @{ Release = 'released' }
    ) {
        param($Release)
        $script:ownerPath = Join-Path (Get-VnaControlRoot) 'owner.json'
        Write-VnaJson $script:ownerPath @{ session_id = $script:command.session_id; restored = $false }
        if ($Release -eq 'restored') {
            Mock Update-VnaWorker {
                $owner = Read-VnaJson $script:ownerPath; $owner.restored = $true
                Write-VnaJson $script:ownerPath $owner
            }
        } else {
            Mock Update-VnaWorker { Remove-Item -LiteralPath $script:ownerPath }
        }
        Start-AgentLoop 'http://test' 'token' -PrinterName 'TSC' -Once
        Assert-MockCalled Invoke-PollOnce -Times 1 -Exactly -Scope It
        Assert-MockCalled Send-Heartbeat -Times 1 -Exactly -Scope It
    }

    It 'лёгкий heartbeat сохраняет управление дольше трёх минут без обнаружения устройств: worker=<Worker>' -TestCases @(
        @{ Worker = $true }, @{ Worker = $false }
    ) {
        param($Worker)
        $script:loopNow = [datetime]'2026-11-16T12:00:00'; $script:loopStart = $script:loopNow
        $script:holding = $false; $script:sleepCalls = 0; $script:discoveryCount = 0; $script:acmEnabled = $true
        $script:beats = New-Object 'Collections.Generic.List[object]'
        Mock Get-Date { $script:loopNow }
        Mock Update-VnaWorker {}
        Mock Send-Heartbeat { & $script:realSendHeartbeat $Base $AuthToken $PrinterName $ScannerAddress -Lightweight:$Lightweight }
        Mock Get-MachineId { 'pc-1' }
        Mock Get-VnaObservation { @{ ready = $true; serial = '26030095'; name = 'Planar, S50180, 26030095, 26.2' } }
        Mock Get-VnaManifest {
            if ($script:acmEnabled) { @{ instrument_serial = '26030095'; ordinary_state_verified = $true; kits = @(
                @{ method = 'acm'; registry_fingerprint = ('a' * 64); definition = @{ serial = '26140008' } }) } }
        }
        Mock Get-AgentScanners {
            if ($script:holding) { throw 'Simulated hung scanner discovery' }
            $script:discoveryCount++
            @{ id = "wia-$($script:discoveryCount)"; name = "Scanner $($script:discoveryCount)" }
        }
        Mock Get-LanIp { if ($script:holding) { throw 'Blocking network discovery' }; '192.168.1.20' }
        Mock Test-ArinstInstalled { if ($script:holding) { throw 'Blocking file discovery' }; $true }
        Mock Get-VnaCapability { throw 'Must not enter COM' }
        Mock Get-VnaApplication { throw 'Must not enter COM' }
        Mock Get-WiaScanners { throw 'Must not enter WIA' }
        Mock Send-RawToPrinter { throw 'Must not enter Winspool' }
        Mock Invoke-AgentUpdate {}
        Mock Invoke-ErpApi {
            if ($Url -notlike '*/heartbeat') { throw 'Unexpected API call' }
            [void]$script:beats.Add(@{ at = $script:loopNow; body = $Body; light = $script:holding })
            @{ update = @{ version = '9.9.9'; sha256 = 'test'; path = '/test' } }
        }
        Mock Invoke-PollOnce { if ($script:holding) { throw 'Simulated hung print' }; 'idle' }
        Mock Invoke-ScanPollOnce { $script:VnaPollAfterMs = 500; 'active' }
        Mock Start-Sleep {
            $script:sleepCalls++
            if ($script:sleepCalls -gt 5) { throw 'End test loop' }
            if ($script:sleepCalls -eq 5) {
                # Release before the next heartbeat is due: full discovery must resume immediately.
                $script:loopNow = $script:loopNow.AddSeconds(10)
                $script:holding = $false; $script:VnaWorker = $null
                $path = Join-Path (Get-VnaControlRoot) 'owner.json'
                if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
            } else {
                $script:loopNow = $script:loopNow.AddSeconds(60)
                if ($script:sleepCalls -eq 2) { $script:acmEnabled = $false }
            }
        }
        $script:VnaControlInitialized = $true
        Send-Heartbeat 'http://test' 'token' -PrinterName 'Cached TSC'
        $script:holding = $true
        if ($Worker) { $script:VnaWorker = @{ command = $script:command; started = [DateTime]::UtcNow } }
        else { Write-VnaJson (Join-Path (Get-VnaControlRoot) 'owner.json') @{ restored = $false; session_id = $script:command.session_id } }
        { Start-AgentLoop 'http://test' 'token' -PrinterName 'TSC' } | Should Throw 'End test loop'
        $light = @($script:beats | Where-Object { $_.light })
        $light.Count | Should Be 5
        ($light[-1].at - $light[0].at).TotalSeconds | Should BeGreaterThan 180
        for ($i = 1; $i -lt $light.Count; $i++) { ($light[$i].at - $light[$i-1].at).TotalSeconds | Should Be 60 }
        foreach ($beat in $light) {
            $beat.body.scanners.Count | Should Be 1
            $beat.body.scanners[0].id | Should Be 'wia-1'
            $beat.body.printer | Should Be 'Cached TSC'
            $beat.body.lan_ip | Should Be '192.168.1.20'
            ($beat.body.vna_sources -join ',') | Should Be 's2vna,arinst'
            $beat.body.vna.ready | Should Be $true
            $beat.body.vna_control.version | Should Be 1
            if (($beat.at - $script:loopStart).TotalSeconds -lt 120) { ($beat.body.vna_control.methods -join ',') | Should Be 'acm' }
            else { @($beat.body.vna_control.methods).Count | Should Be 0 }
        }
        $script:beats.Count | Should Be 7
        $script:beats[-1].body.scanners[0].id | Should Be 'wia-2'
        $script:beats[-1].body.printer | Should Be 'TSC'
        ($script:beats[-1].at - $light[-1].at).TotalSeconds | Should Be 10
        Assert-MockCalled Get-AgentScanners -Times 2 -Exactly -Scope It
        Assert-MockCalled Get-LanIp -Times 2 -Exactly -Scope It
        Assert-MockCalled Test-ArinstInstalled -Times 2 -Exactly -Scope It
        Assert-MockCalled Invoke-AgentUpdate -Times 2 -Exactly -Scope It
        Assert-MockCalled Invoke-PollOnce -Times 1 -Exactly -Scope It
        Assert-MockCalled Get-WiaScanners -Times 0 -Exactly -Scope It
        Assert-MockCalled Send-RawToPrinter -Times 0 -Exactly -Scope It
        Assert-MockCalled Get-VnaCapability -Times 0 -Exactly -Scope It
        Assert-MockCalled Get-VnaApplication -Times 0 -Exactly -Scope It
    }

    It 'лёгкий heartbeat после перезапуска без кэша и наблюдения сохраняет протокол без объявления калибровки' {
        Mock Send-Heartbeat { & $script:realSendHeartbeat $Base $AuthToken $PrinterName $ScannerAddress -Lightweight:$Lightweight }
        Mock Get-AgentScanners { throw 'Must not discover scanners' }
        Mock Get-LanIp { throw 'Must not discover network' }
        Mock Test-ArinstInstalled { throw 'Must not discover files' }
        Mock Get-VnaCapability { throw 'Must not enter COM' }
        Mock Get-MachineId { 'pc-1' }
        Mock Invoke-AgentUpdate {}
        Mock Invoke-ErpApi { @{ update = @{ version = '9.9.9' } } }
        Send-Heartbeat 'http://test' 'token' -PrinterName 'TSC' -Lightweight
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Body.vna_control.version -eq 1 -and @($Body.vna_control.methods).Count -eq 0 -and
            @($Body.scanners).Count -eq 0 -and $Body.printer -eq 'TSC' -and -not $Body.vna
        }
        Assert-MockCalled Get-AgentScanners -Times 0 -Exactly -Scope It
        Assert-MockCalled Get-LanIp -Times 0 -Exactly -Scope It
        Assert-MockCalled Test-ArinstInstalled -Times 0 -Exactly -Scope It
        Assert-MockCalled Get-VnaCapability -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-AgentUpdate -Times 0 -Exactly -Scope It
    }

    It 'HTTP heartbeat ограничен таймаутом' {
        Mock Invoke-RestMethod { @{} }
        & $script:realErpApi -Url 'http://test/api/stock/scan/agent/heartbeat' -Body @{} -AuthToken 'token'
        Assert-MockCalled Invoke-RestMethod -Times 1 -Exactly -Scope It -ParameterFilter { $TimeoutSec -gt 0 -and $TimeoutSec -le 20 }
    }

    It 'после завершения команды печать и heartbeat возобновляются в том же цикле' {
        $process = [pscustomobject]@{ HasExited = $true }
        $process | Add-Member ScriptMethod Dispose {}
        $script:VnaWorker = @{ process = $process; command = $script:command; started = [DateTime]::UtcNow }
        Start-AgentLoop 'http://test' 'token' -PrinterName 'TSC' -Once
        Assert-MockCalled Invoke-PollOnce -Times 1 -Exactly -Scope It
        Assert-MockCalled Send-Heartbeat -Times 1 -Exactly -Scope It
    }
}

Describe 'Управление ВАЦ: журнал, опрос, watchdog' {
    BeforeEach {
        $script:ConfigPath = Join-Path $TestDrive ([guid]::NewGuid().ToString() + '\config.json')
        $script:VnaWorker = $null; $script:VnaControlInitialized = $false
        $script:command = New-ControlEnvelope
        Mock Invoke-VnaSessionApi { [pscustomobject]@{} }
        Mock Get-VnaApplication { throw 'Tests must not open real COM' }
    }
    It 'после lost ack повторяется идентичная квитанция, а не операция' {
        $entry = Get-VnaJournal $script:command
        $entry.state = 'completed'; $entry.payload = New-VnaReceipt $script:command @{ serial = '26030095'; ready = $true }
        Write-VnaJson (Get-VnaCommandPath $script:command) $entry
        $script:posted = @()
        Mock Invoke-VnaSessionApi { $script:posted += (ConvertTo-VnaCanonical $Body); if ($script:posted.Count -eq 1) { throw 'lost ack' } }
        { Sync-VnaReceipts 'http://test' 'token' } | Should Throw 'lost ack'
        Sync-VnaReceipts 'http://test' 'token'
        $script:posted.Count | Should Be 2
        $script:posted[0] | Should Be $script:posted[1]
        (Get-VnaJournal $script:command).acknowledged | Should Be $true
        (Split-Path -Leaf (Split-Path -Parent (Get-VnaCommandPath $script:command))) | Should Be 'acknowledged'
    }
    It 'executing после перезапуска становится uncertain' {
        $entry = Get-VnaJournal $script:command; $entry.state = 'executing'; Write-VnaJson (Get-VnaCommandPath $script:command) $entry
        $resumed = Get-VnaJournal $script:command
        $resumed.state | Should Be 'uncertain'
        $resumed.payload.error | Should Match 'неизвестен'
        @($resumed.payload.PSObject.Properties.Name) -contains 'backup_saved' | Should Be $false
        (Test-Path (Join-Path (Get-VnaControlRoot) 'quarantine.json')) | Should Be $true
    }
    It 'тот же id с другим аргументом отклоняется' {
        [void](Get-VnaJournal $script:command); $script:command.op = 'abort'
        { Get-VnaJournal $script:command } | Should Throw 'содержимое повторной команды'
    }
    It 'watchdog убивает и дожидается процесса до uncertain' {
        $entry = Get-VnaJournal $script:command; $entry.state = 'executing'; Write-VnaJson (Get-VnaCommandPath $script:command) $entry
        $process = [pscustomobject]@{ HasExited = $false; Killed = $false; Waited = $false; Disposed = $false }
        $process | Add-Member ScriptMethod Kill { $this.Killed = $true; $this.HasExited = $true }
        $process | Add-Member ScriptMethod WaitForExit { param($timeout) $this.Waited = $true; return $true }
        $process | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
        Stop-VnaWorker @{ process = $process; command = $script:command } 'ВАЦ: watchdog, результат неизвестен'
        $process.Killed | Should Be $true
        $process.Waited | Should Be $true
        (Get-VnaJournal $script:command).state | Should Be 'uncertain'
        @((Get-VnaJournal $script:command).payload.PSObject.Properties.Name) -contains 'backup_saved' | Should Be $false
    }
    It 'неостановленный worker запрещает дальнейшее восстановление' {
        $process = [pscustomobject]@{ HasExited = $false }
        $process | Add-Member ScriptMethod Kill {}
        $process | Add-Member ScriptMethod WaitForExit { param($timeout) return $false }
        $script:VnaWorker = @{ process = $process; command = $script:command }
        { Stop-VnaWorker $script:VnaWorker 'timeout' } | Should Throw 'восстановление запрещено'
        ($null -ne $script:VnaWorker) | Should Be $true
    }
    It 'готовая квитанция не затирается отменой в гонке завершения' {
        $entry = Get-VnaJournal $script:command; $entry.state = 'completed'; $entry.payload = New-VnaReceipt $script:command @{ serial = '26030095' }
        Write-VnaJson (Get-VnaCommandPath $script:command) $entry
        $process = [pscustomobject]@{ HasExited = $true }; $process | Add-Member ScriptMethod Dispose {}
        Stop-VnaWorker @{ process = $process; command = $script:command } 'cancelled'
        (Get-VnaJournal $script:command).state | Should Be 'completed'
    }
    It 'активная команда продолжает опрос без повторного запуска worker' {
        $script:VnaControlInitialized = $true
        $script:VnaWorker = @{ command = $script:command }
        Mock Get-VnaObservation { @{ ready = $true; serial = '26030095' } }
        Mock Get-MachineId { 'pc-1' }
        Mock Invoke-ErpApi { @{ job = $script:command; poll_after_ms = 500 } }
        Mock Start-VnaWorker { throw 'must not start twice' }
        (Invoke-ScanPollOnce 'http://test' 'token') | Should Be 'active'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Body.observed_vna_serial -eq '26030095' -and ($Body.kinds -join ',') -ceq 'vna_control' }
        Assert-MockCalled Start-VnaWorker -Times 0 -Exactly -Scope It
    }
    It 'маркер <Marker> без свежего наблюдения всё равно опрашивает только управление' -TestCases @(@{ Marker = 'owner.json' },@{ Marker = 'quarantine.json' }) {
        param($Marker)
        $script:VnaControlInitialized = $true
        Write-VnaJson (Join-Path (Get-VnaControlRoot) $Marker) @{ session_id = $script:command.session_id }
        Mock Get-VnaObservation { $null }; Mock Get-MachineId { 'pc-1' }; Mock Invoke-ErpApi { @{ job = $null } }
        (Invoke-ScanPollOnce 'http://test' 'token') | Should Be 'idle'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { ($Body.kinds -join ',') -ceq 'vna_control' }
    }
    It 'квитанция ошибки подготовки сохраняет JSON boolean false' {
        $receipt = New-VnaReceipt $script:command $null 'prepare failed' -BackupSaved $false
        $decoded = ($receipt | ConvertTo-Json -Compress) | ConvertFrom-Json
        ($decoded.backup_saved -is [bool]) | Should Be $true
        $decoded.backup_saved | Should Be $false
    }
    It 'успех и ошибка другой операции не получают backup_saved' {
        (New-VnaReceipt $script:command @{ serial = '26030095' } -BackupSaved $true).ContainsKey('backup_saved') | Should Be $false
        $script:command.op = 'capture'
        (New-VnaReceipt $script:command $null 'capture failed' -BackupSaved $true).ContainsKey('backup_saved') | Should Be $false
    }
    It 'потеря подтверждения ошибки prepare повторяет исходный backup_saved' {
        $entry = Get-VnaJournal $script:command; $entry.state = 'completed'
        $entry.payload = New-VnaReceipt $script:command $null 'prepare failed' -BackupSaved $false
        Write-VnaJson (Get-VnaCommandPath $script:command) $entry
        $script:posted = @()
        Mock Invoke-VnaSessionApi { $script:posted += (ConvertTo-VnaCanonical $Body); if ($script:posted.Count -eq 1) { throw 'lost ack' } }
        { Sync-VnaReceipts 'http://test' 'token' } | Should Throw 'lost ack'
        Sync-VnaReceipts 'http://test' 'token'
        $script:posted[0] | Should Be $script:posted[1]
        $script:posted[1] | Should Match '"backup_saved":false'
    }
    It 'подтверждённый failed до записи копии освобождает локальный маркер' {
        $entry = Get-VnaJournal $script:command; $entry.state = 'completed'
        $entry.payload = New-VnaReceipt $script:command $null 'prepare failed' -BackupSaved $false
        Write-VnaJson (Get-VnaCommandPath $script:command) $entry
        $ownerPath = Join-Path (Get-VnaControlRoot) 'owner.json'
        Write-VnaJson $ownerPath @{ session_id = $script:command.session_id; serial = $script:command.instrument_serial; prepare_command_id = $script:command.command_id; restored = $false }
        Mock Invoke-VnaSessionApi {
            if ($Method -eq 'Post') { return @{ ok = $true } }
            $status = New-ControlStatus $script:command; $status.lease_held = $false; $status.state = 'failed'; $status.commands[0].status = 'failed'
            return $status
        }
        Sync-VnaReceipts 'http://test' 'token'
        (Test-Path $ownerPath) | Should Be $false
        (Get-VnaJournal $script:command).payload.backup_saved | Should Be $false
    }
    It 'без подтверждения квитанции локальное владение сохраняется' {
        $entry = Get-VnaJournal $script:command; $entry.state = 'completed'
        $entry.payload = New-VnaReceipt $script:command $null 'prepare failed' -BackupSaved $false
        Write-VnaJson (Get-VnaCommandPath $script:command) $entry
        $ownerPath = Join-Path (Get-VnaControlRoot) 'owner.json'
        Write-VnaJson $ownerPath @{ session_id = $script:command.session_id; serial = $script:command.instrument_serial; prepare_command_id = $script:command.command_id; restored = $false }
        Mock Invoke-VnaSessionApi { throw 'lost ack' }
        { Sync-VnaReceipts 'http://test' 'token' } | Should Throw 'lost ack'
        (Test-Path $ownerPath) | Should Be $true
    }
    It 'prepare с сохранённой копией не освобождает владение по одному failed' {
        $entry = Get-VnaJournal $script:command; $entry.state = 'completed'
        $entry.payload = New-VnaReceipt $script:command $null 'prepare failed' -BackupSaved $true
        Write-VnaJson (Get-VnaCommandPath $script:command) $entry
        $ownerPath = Join-Path (Get-VnaControlRoot) 'owner.json'
        Write-VnaJson $ownerPath @{ session_id = $script:command.session_id; serial = $script:command.instrument_serial; prepare_command_id = $script:command.command_id; restored = $false }
        Mock Invoke-VnaSessionApi {
            if ($Method -eq 'Post') { return @{ ok = $true } }
            $status = New-ControlStatus $script:command; $status.lease_held = $false; $status.state = 'failed'; $status.commands[0].status = 'failed'
            return $status
        }
        Sync-VnaReceipts 'http://test' 'token'
        (Test-Path $ownerPath) | Should Be $true
    }
    It 'квитанция false сохраняет маркер при условии <Condition>' -TestCases @(@{ Condition = 'held' },@{ Condition = 'other_receipt' },@{ Condition = 'other_serial' }) {
        param($Condition)
        $script:reconcileCondition = $Condition
        $entry = Get-VnaJournal $script:command; $entry.state = 'completed'
        $entry.payload = New-VnaReceipt $script:command $null 'prepare failed' -BackupSaved $false
        Write-VnaJson (Get-VnaCommandPath $script:command) $entry
        $ownerPath = Join-Path (Get-VnaControlRoot) 'owner.json'
        Write-VnaJson $ownerPath @{ session_id = $script:command.session_id; serial = $script:command.instrument_serial; prepare_command_id = $script:command.command_id; restored = $false }
        Mock Invoke-VnaSessionApi {
            if ($Method -eq 'Post') { return @{ ok = $true } }
            $status = New-ControlStatus $script:command; $status.lease_held = $false; $status.state = 'failed'; $status.commands[0].status = 'failed'
            if ($script:reconcileCondition -eq 'held') { $status.lease_held = $true }
            if ($script:reconcileCondition -eq 'other_receipt') { $status.commands[0].command_id = [guid]::NewGuid().ToString() }
            if ($script:reconcileCondition -eq 'other_serial') { $status.instrument_serial = 'other' }
            return $status
        }
        Sync-VnaReceipts 'http://test' 'token'
        (Test-Path $ownerPath) | Should Be $true
    }
    It 'worker сообщает false при отказе до первой операции прибора' {
        Mock Test-VnaNormalToken { $false }
        $requestPath = Join-Path (Get-VnaControlRoot) 'worker-request.json'; Write-VnaJson $requestPath @{ command = $script:command }
        Invoke-VnaWorker $requestPath
        $entry = Get-VnaJournal $script:command
        $entry.state | Should Be 'completed'
        ($entry.payload.backup_saved -is [bool]) | Should Be $true
        $entry.payload.backup_saved | Should Be $false
        Assert-MockCalled Get-VnaApplication -Times 0 -Exactly -Scope It
    }
    It 'worker не выводит false из существующего маркера без копии' {
        Write-VnaJson (Join-Path (Get-VnaControlRoot) 'owner.json') @{ session_id = $script:command.session_id }
        Mock Test-VnaNormalToken { $false }
        $requestPath = Join-Path (Get-VnaControlRoot) 'worker-request.json'; Write-VnaJson $requestPath @{ command = $script:command }
        Invoke-VnaWorker $requestPath
        @((Get-VnaJournal $script:command).payload.PSObject.Properties.Name) -contains 'backup_saved' | Should Be $false
    }
    It 'обновление откладывается пока прибор принадлежит сессии' {
        $script:VnaWorker = @{ command = $script:command }
        Mock Invoke-WebRequest { throw 'must not download during session' }
        (Invoke-AgentUpdate 'http://test' @{ version = '99.0.0'; sha256 = ('a' * 64) } -AgentPath (Join-Path $TestDrive 'agent.ps1') -NoRestart) | Should Be $false
        Assert-MockCalled Invoke-WebRequest -Times 0 -Exactly -Scope It
    }
    It 'фоновая проверка прибора не мешает самообновлению' {
        $script:VnaWorker = @{ command = $null }
        Mock Stop-VnaWorker { $script:VnaWorker = $null }
        Mock Invoke-WebRequest { throw 'download reached' }
        [void](Invoke-AgentUpdate 'http://test' @{ version = '99.0.0'; sha256 = ('a' * 64); path = '/agent.ps1' } -AgentPath (Join-Path $TestDrive 'agent.ps1') -NoRestart)
        Assert-MockCalled Stop-VnaWorker -Times 1 -Exactly -Scope It
        Assert-MockCalled Invoke-WebRequest -Times 1 -Exactly -Scope It
    }
    It 'неожиданный скан получает ошибку занятости и не блокирует контроль' {
        $script:VnaControlInitialized = $true; $script:VnaWorker = @{ command = $script:command }
        Mock Get-VnaObservation { @{ ready = $true; serial = '26030095' } }
        Mock Get-MachineId { 'pc-1' }
        Mock Invoke-ErpApi {
            if ($Url -like '*/claim') { return @{ job = @{ id = 'scan-1'; kind = 'scan' } } }
        }
        Mock Invoke-ScannerScan { throw 'must never scan' }
        (Invoke-ScanPollOnce 'http://test' 'token') | Should Be 'error'
        Assert-MockCalled Invoke-ScannerScan -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/jobs/scan-1/result' -and $Body.error -like '*занят*' }
    }
    It 'worker повторной завершённой команды не открывает COM' {
        $entry = Get-VnaJournal $script:command; $entry.state = 'completed'; $entry.payload = New-VnaReceipt $script:command @{ serial = '26030095' }
        Write-VnaJson (Get-VnaCommandPath $script:command) $entry
        $requestPath = Join-Path (Get-VnaControlRoot) 'worker-request.json'; Write-VnaJson $requestPath @{ command = $script:command }
        Invoke-VnaWorker $requestPath
        Assert-MockCalled Get-VnaApplication -Times 0 -Exactly -Scope It
    }
    It 'worker сохраняет executing до COM и квитанцию <Outcome>' -TestCases @(@{ Outcome = 'success' },@{ Outcome = 'after_backup' },@{ Outcome = 'unknown' }) {
        param($Outcome)
        $script:workerOutcome = $Outcome
        $script:command.instrument_serial = 'TEST-ONLY'
        Mock Test-VnaNormalToken { $true }; Mock Test-VnaRegistered { $true }
        Mock Get-Process { [pscustomobject]@{ ProcessName = 'S2VNA' } }
        Mock Get-VnaApplication { [pscustomobject]@{ Ready = $true; Name = 'Planar, S50180, TEST-ONLY, 26.2' } }
        Mock Release-VnaApplication {}
        Mock New-VnaDeviceMutex {
            $mutex = [pscustomobject]@{}
            $mutex | Add-Member ScriptMethod WaitOne { param($timeout) return $true }
            $mutex | Add-Member ScriptMethod ReleaseMutex {}
            $mutex | Add-Member ScriptMethod Dispose {}
            return $mutex
        }
        Mock Invoke-VnaSessionApi { New-ControlStatus $script:command }
        Mock Invoke-S2Command {
            (Read-VnaJson (Get-VnaCommandPath $Command)).state | Should Be 'executing'
            if ($script:workerOutcome -eq 'after_backup') { $Preparation.backup_saved = $true; throw 'kit backup failed' }
            if ($script:workerOutcome -eq 'unknown') { $Preparation.backup_saved = $null; throw 'partial write' }
            return @{ serial = 'TEST-ONLY'; ready = $true }
        }
        $requestPath = Join-Path (Get-VnaControlRoot) 'worker-request.json'
        Mock ConvertTo-SecureString {
            $secret = New-Object Security.SecureString
            foreach ($character in 'test-token'.ToCharArray()) { $secret.AppendChar($character) }
            return $secret
        }
        $protected = 'mock-dpapi-test-token'
        Write-VnaJson $requestPath @{ command = $script:command; base = 'http://test'; token_protected = $protected }
        Invoke-VnaWorker $requestPath
        $entry = Read-VnaJson (Get-VnaCommandPath $script:command)
        $entry.state | Should Be 'completed'
        if ($Outcome -eq 'success') {
            $entry.payload.result.serial | Should Be 'TEST-ONLY'
            @($entry.payload.PSObject.Properties.Name) -contains 'backup_saved' | Should Be $false
        } elseif ($Outcome -eq 'after_backup') {
            $entry.payload.backup_saved | Should Be $true
            ($entry.payload.backup_saved -is [bool]) | Should Be $true
        } else {
            @($entry.payload.PSObject.Properties.Name) -contains 'backup_saved' | Should Be $false
        }
        Assert-MockCalled Invoke-S2Command -Times 1 -Exactly -Scope It
    }
    It 'манифест объявляет только ACM, даже если содержит SOLT' {
        Mock Get-VnaManifest { @{ instrument_serial = '26030095'; ordinary_state_verified = $true; kits = @(
            @{ method = 'acm'; registry_fingerprint = ('a' * 64); definition = @{ serial = '26140008' } }, @{ method = 'solt2' }) } }
        $capability = Get-VnaControlCapability @{ ready = $true; serial = '26030095' }
        $capability.version | Should Be 1
        ($capability.methods -join ',') | Should Be 'acm'
    }
    It 'без манифеста методы не объявляются' {
        Mock Get-VnaManifest { $null }
        @((Get-VnaControlCapability @{ ready = $true; serial = '26030095' }).methods).Count | Should Be 0
    }
    It 'чужой прибор не получает ACM capability' {
        Mock Get-VnaManifest { @{ instrument_serial = 'other'; ordinary_state_verified = $true } }
        @((Get-VnaControlCapability @{ ready = $true; serial = '26030095' }).methods).Count | Should Be 0
    }
    It 'неготовый прибор не получает control capability' {
        (Get-VnaControlCapability @{ ready = $false; serial = '26030095' }) | Should Be $null
    }
    It 'heartbeat объявляет протокол и методы без COM в основном цикле' {
        $script:VnaControlInitialized = $true
        Mock Get-VnaObservation { @{ ready = $true; serial = '26030095'; name = 'Planar, S50180, 26030095, 26.2' } }
        Mock Get-VnaManifest { @{ instrument_serial = '26030095'; ordinary_state_verified = $true; kits = @(
            @{ method = 'acm'; registry_fingerprint = ('a' * 64); definition = @{ serial = '26140008' } }) } }
        Mock Get-AgentScanners { @() }; Mock Get-LanIp { '' }; Mock Get-MachineId { 'pc-1' }; Mock Test-ArinstInstalled { $false }
        Mock Invoke-ErpApi { @{} }
        Send-Heartbeat 'http://test' 'token'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Body.version -eq $script:AgentVersion -and $Body.vna_control.version -eq 1 -and ($Body.vna_control.methods -join ',') -eq 'acm'
        }
        Assert-MockCalled Get-VnaApplication -Times 0 -Exactly -Scope It
    }
}

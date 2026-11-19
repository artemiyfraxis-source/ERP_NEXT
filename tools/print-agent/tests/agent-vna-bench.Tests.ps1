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
    if ($script:kitSlots -and $Path -like 'SENSe(1).CORRection.COLLect.CKIT.*') {
        # S2VNA kit table: SELect picks a slot (out of range throws), LABel and STAN(i).* read the selected slot.
        $select = 'SENSe(1).CORRection.COLLect.CKIT.SELect'
        if ($Path -eq $select) {
            if ($Mode -ne 'set') { return $script:ioValues[$select] }
            if ([int]$Value -lt 1 -or [int]$Value -gt @($script:kitSlots).Count) { throw 'Parameter out of range' }
            $script:ioValues[$select] = [int]$Value; return
        }
        if ($Mode -eq 'set') { throw "Kit definitions must not be written: $Path" }
        $kit = $script:kitSlots[[int]$script:ioValues[$select] - 1]
        if ($Path -eq 'SENSe(1).CORRection.COLLect.CKIT.LABel') { return $kit.label }
        if ($Path -match '^SENSe\(1\)\.CORRection\.COLLect\.CKIT\.STAN\((\d+)\)\.(\w+)$') {
            $index = [int]$Matches[1]; $property = $Matches[2]
            if ($index -gt @($kit.standards).Count) { throw 'Standard index out of range' }
            $standard = $kit.standards[$index - 1]
            if (-not $standard.Contains($property)) { throw "Missing fake kit property: $property" }
            return $standard[$property]
        }
    }
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
    $script:ioValues = @{}; $script:selected = 1; $script:ignorePath = ''; $script:onSweep = $null; $script:kitSlots = $null
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
# Python reference: struct bits of x and of float(f"{x:.9g}") (ties, signs, subnormal, 1e-12..1e12 random sweep).
$script:sig9Table = @'
3ff00000015798ee 3ff0000000000000
419d6f3442000000 419d6f3440000000
419d6f3446000000 419d6f3448000000
3ff000000406caca 3ff0000002af31dc
3ff0000006b5fca7 3ff00000080d9595
3ff000001172c418 3ff0000012ca5d06
3fa1111110c7c411 3fa1111110c7c411
3fa1111111111111 3fa1111110c7c411
3fa1111110c7c412 3fa1111110c7c411
3e90c6f7a0b5ed8d 3e90c6f7a0b5ed8d
426d1a94a2000000 426d1a94a2000000
4210c388d0000000 4210c388d0000000
416312d000000000 416312d000000000
bfd3333333333333 bfd3333333333333
bfe0000000000000 bfe0000000000000
c12e848000000000 c12e848000000000
412e848000000000 412e848000000000
41cdcd64ffc00000 41cdcd6500000000
4023ffffffd50ce2 4023ffffffaa19c4
3fd3333333333334 3fd3333333333333
0000000000000001 0000000000000001
01a56e1fc2f8f359 01a56e1fc2f8f359
3ff0000000000000 3ff0000000000000
3f947ae147ae147b 3f947ae147ae147b
408bf52362e90364 408bf52362a5e786
3fa49f44c1cd80ac 3fa49f44c15f8d2d
3ee1cbcc3eccf27d 3ee1cbcc3e9fe946
3ed865477b25c1a5 3ed865477acbaf38
3fa456aea27d3381 3fa456aea2eb2700
4032236797bece06 4032236796e80e72
3fb82cd505557f64 3fb82cd5058c7923
3f83f2db0fc2d80c 3f83f2db0f96dd0d
3f5084cef76b7c3b 3f5084cef8cb5439
3ed3780e56a72ad5 3ed3780e564d1867
400b64d95546587f 400b64d955f224f6
413d7e18e28f5c29 413d7e18e3d70a3d
405dae4b790fb657 405dae4b7b28954a
3f79bd5dc3508972 3f79bd5dc3a87f71
40147921d740433c 40147921d6ea5d01
3f820f6f63ae01de 3f820f6f63d9fcde
3ee3b9b23f31a0ea 3ee3b9b23f0497b4
3ed2c255723e566d 3ed2c255729868db
40354e3a6a5f196b 40354e3a698859d6
40e15ab7a161e4f8 40e15ab7a1cac083
4051accba40d90e2 4051accba3d7e0fd
40b3eacd797cc3a0 40b3eacd79d0a676
3f3f71f201dae190 3f3f71f2014e24c5
40213fc059210386 40213fc0594bf6a4
4046e33d7ccdb0f9 4046e33d7d3910c3
3ece9518f0ca765a 3ece9518f016517f
4122934ab810624e 4122934ab851eb85
4128eff472f1a9fd 4128eff473333333
3f4f7b7f8627b6d1 3f4f7b7f866e1536
3ed872dc2522894c 3ed872dc24c876de
40239a30c2c0846a 40239a30c295914c
40234ff2211a2bfb 40234ff220ef38dd
404c01cf143a49a8 404c01cf14a5a972
40c06ff1acc4ef89 40c06ff1aceee0f4
4019b3872f33ca32 4019b3872f89b06d
4161bf47ebae147b 4161bf47ebd70a3d
3fdcfda8e22e554a 3fdcfda8e1a4e4eb
3ee3c16f2ffe3708 3ee3c16f2fd12dd2
3f911c6219fd368a 3f911c621ad91d89
40b50be99a95421c 40b50be99a415f46
be726d848cf53fca be726d848d20877e
3e77b8441e274fce 3e77b8441e1685f3
bfe9811aeb4859f5 bfe9811aeb1b7504
c09593bd1167553d c09593bd1244a622
3fede8571d5e00bf 3fede8571d596862
bdba209b5e466002 bdba209b5f2b0765
4050f3a355f5dd50 4050f3a355dafde8
c0ad9d8740f2bce5 c0ad9d87414a4d2b
4027a8cb6058ad6c 4027a8cb5f0e8351
be4665884dec2ba2 be4665884fac1cdb
c230df6b130d910e c230df6b12ec0000
40adc9d8a14b29ca 40adc9d8a1dfb939
c1ccb5f15d5b4d16 c1ccb5f15d800000
c1616d7b242a463b c1616d7b2428f5c3
c22e90faadd4f726 c22e90faad800000
c194abd5ab20ace7 c194abd5ab333333
c08ef643278a3088 c08ef64327674d16
c11286afcf58ed85 c11286afced91687
bfb0680387efc911 bfb0680387d27a69
41c6b7e18afd4e09 41c6b7e18b000000
3fe89f18815d2798 3fe89f1881811ef9
3e4280530d6d19f0 3e4280530d7ca43a
c03858fa68458739 c03858fa68108a92
3d81c3b3c3e8a6f7 3d81c3b3c3e0566f
c0d8f9f6fd713a0d c0d8f9f6fd21ff2e
4260eeecf1a4a4b9 4260eeecf1a40000
c19140019a6a484d c19140019a666666
bea08339701cea6e bea08339700c7a3d
3f794feae7a510aa 3f794feae75099e9
40e267f685480284 40e267f6858793de
c1922c44ab541a9b c1922c44ab333333
c03798e3e47e4850 c03798e3e517466e
bf59e32bb9b7f8cd bf59e32bbac228f2
c1ac853fefadefde c1ac853ff0000000
bf4d93035c01f245 bf4d93035c439329
406a9f82c6a594d3 406a9f82c6ef3d3a
41f1e23051e5a9a5 41f1e23052200000
bda11237eadb143b bda11237eabd96b3
42326af24bbe2998 42326af24ba40000
41c9792f9d494b22 41c9792f9d800000
40b31b65f8a90f93 40b31b65f8f47304
c057a8359e52b348 c057a8359e477e44
3dd660bb9779eace 3dd660bb974a8a02
c15d726fa9ded669 c15d726fa999999a
bf72db43b2b28adf bf72db43b2b0349c
407794c1add9ea52 407794c1adea8976
c0b8d31d4afbae5f c0b8d31d4b48d3ae
bf7e99db4cc4718d bf7e99db4d091866
c088e0545dba2948 c088e0545d851655
402a8b5c8b22ad2b 402a8b5c8a449200
be5362309dd6e8de be5362309d69c382
c16142c85d1b8bd0 c16142c85d1eb852
3e99edd2461ee83c 3e99edd245db63e6
c148227bb40d7c71 c148227bb47ae148
3fadad9e78a2bb6a 3fadad9e78f0647e
3f985c12fcb6ace0 3f985c12fd1221dc
408517c769e3c456 408517c769ec2ce4
41572da5d9f57a2c 41572da5da3d70a4
bfc91a1611662158 bfc91a1611f65dd1
bf8073f45fbc468d bf8073f45fb60d8c
3ffddde34e86a786 3ffddde34d41d232
bfabc0a167400d6a bfabc0a166dfb673
3e7a571b2093a930 3e7a571b209e651b
41804518d617b382 41804518d6666666
415cf923d73a41b6 415cf923d70a3d71
40c1e50c5b2e6871 40c1e50c5b18548b
411e9f7bb51ef1e2 411e9f7bb53f7cee
c1ad2d8736c9376c c1ad2d8736000000
3f8c7ad033d03800 3f8c7ad0351f0c2e
bf182c59e85ec92e bf182c59e8708a8f
bf67927b4932e7b3 bf67927b491fcf40
42130561c6772903 42130561c6100000
40212fe24f54b726 40212fe24f400427
bda322ee6024e044 bda322ee6010d795
bf3993ba7fb38040 bf3993ba7fb6f356
40ccf4fd83079c29 40ccf4fd844d013b
4071a3854de4b164 4071a3854de7ea60
41f0caee1317dbdc 41f0caee13000000
3f9ddad849f809ba 3f9ddad84aa6b9b5
41f9ad3b18d32e02 41f9ad3b18e00000
3da749be639ed0f9 3da749be6342fdcb
be0a4971cf052bc1 be0a4971cee2e4fb
401f14f8247a836b 401f14f824945ef2
c197d638eb610bb5 c197d638eb333333
3d5e68ede7273977 3d5e68ede6f66b85
bfb024ab8e0a58bf bfb024ab8e294723
416eb23e00097239 416eb23e00000000
bd3570cacdd9b862 bd3570cacdbf69e1
bdb788989805b5e7 bdb7889898a6555f
40e7cbb5a2eb6b9b 40e7cbb5a29c779a
3e670c75d47e7179 3e670c75d4e5a161
40ae7dcc1f137a4e 40ae7dcc1e7967cb
3fd2cdb1fb90c4aa 3fd2cdb1fb208369
41ae3cb9a8952724 41ae3cb9a8000000
3f624fc5e2e67967 3f624fc5e28e2f4c
3ed9a57b58906506 3ed9a57b5866b2be
3e418daebd82be93 3e418daebd5981fe
3e749df538c17bfd 3e749df538c172ad
bfe6f68be673848c bfe6f68be63053b8
3f0185a1fb60d531 3f0185a1fb9f31f2
bf834772128202aa bf8347721295a694
bf73aa30e150dab1 bf73aa30e13675ba
bfdee5792667321a bfdee579263197fe
3e052fa40de69947 3e052fa40e1194b0
be98af54ccb84c23 be98af54cd402095
bef1ded49764f4f1 bef1ded496be799e
3f8375f158e9c4b3 3f8375f158fe3503
3e63e8152e1e1d08 3e63e8152e5128d3
c1ca397169e72658 c1ca39716a000000
4033cfd09656e610 4033cfd095af294e
3ed3253d06958d0b 3ed3253d06b135fb
be74ad2b8985c08f be74ad2b897dd230
c0b84f29e663c735 c0b84f29e646f156
42539158599398dd 4253915859680000
3ed85bd2f4e3486d 3ed85bd2f501254a
c038674f893fe77b c038674f89ab0f0d
c120faf293454025 c120faf29374bc6a
3e00003ec40ad337 3e00003ec416f0b0
3d72fb381696f3b1 3d72fb38155f4abb
c1ca9ac245d3e9f1 c1ca9ac246000000
3f9cd6b29227a980 3f9cd6b291a8ad8a
be69cf14702f1cf6 be69cf146fc4fa91
c16026cfb951149c c16026cfb947ae14
bda4897e7e893192 bda4897e7eb7445a
3f4c973795cab828 3f4c973795c1e198
3d7081b358251ead 3d7081b358158994
40107ca02ab138fe 40107ca02ad845d2
3e4d79a95fa3042b 3e4d79a95edd59f8
3d69c067949ba3fd 3d69c06794c6d805
c15fed8aff4e7c14 c15fed8aff5c28f6
3e71b3112c8ecdb0 3e71b3112c6a48aa
bdc35238d75edb7c bdc35238d79d8c57
41c98c41b3973224 41c98c41b3800000
c0f2396ced61aad1 c0f2396ced916873
bda51efb6b32f2ee bda51efb6b21ebd8
c0808535e031fc7a c0808535e071c53f
bfbdf7e528d04ba0 bfbdf7e52964fe31
c2380f34660f0219 c2380f3464b80000
3f4474f7dc4f1c0a 3f4474f7dc37cc8a
408c1cc90893efd8 408c1cc908a265f1
3db4c99f810a6540 3db4c99f80258790
41fd687a6a134e60 41fd687a6a400000
3fe92c0981df096a 3fe92c0981c522c6
3f828b422db30c0d 3f828b422dc1ee18
c1231e2a61f06123 c1231e2a61cac083
3f5d72078f68a47b 3f5d72078f577fa5
c119714d340a53f3 c119714d34395810
414e5b5d935a55b1 414e5b5d93333333
c118f75d8663e9eb c118f75d8624dd2f
3d6a0a5bc3e58912 3d6a0a5bc3e01892
c1cc79c543b2a4cf c1cc79c543800000
40477e2b74c86846 40477e2b74f8d061
bf0bf0222f90aee7 bf0bf0222fdb1057
3da577d1e94d641d 3da577d1e97c986d
bfebac0ffbfdc1e9 bfebac0ffc2a1027
3dff8b4638f68258 3dff8b463928704b
3e3261be66b2f995 3e3261be668196d5
bdf685091ae68c4f bdf685091a5cd9ec
4086d05641cd84b2 4086d05641f64495
c231c58c1024c7ab c231c58c10500000
3f4bdaa29351674a 3f4bdaa2938a5646
3e7201630b872193 3e7201630b526edf
3fb96ec8d248b0e8 3fb96ec8d257ab57
40a37e4376e83846 40a37e43775b8130
c1ab8740bd4eed56 c1ab8740be000000
3f140c022d3109ae 3f140c022d06cbef
bf500794dd16d997 bf500794dd0ee9c9
c254b5072ab10cf9 c254b5072ae60000
40a3043008b9673a 40a30430092ccf6c
3fa55c59c826a16c 3fa55c59c81d6597
bf0dc5b44f2ac4e1 bf0dc5b44f003a4a
3f1440fde0c9d97f 3f1440fde0e281ff
3d65a7ac36fa4d36 3d65a7ac371943a3
3f6d0665dc779c9a 3f6d0665dceb0865
3e9fbec768937df8 3e9fbec768c57e78
c089016685c039b7 c089016685db76b4
3d96546614c7c510 3d965466149dcf9a
41c32b3642d26bb8 41c32b3643000000
3edfb201aed369f6 3edfb201aea05f71
be79d1be774a4899 be79d1be775f7fac
40f29914d507dc4f 40f29914d4fdf3b6
bdd51fea988bb3d0 bdd51fea986541f9
424de54a75203c52 424de54a745c0000
c101e28825fae557 c101e28826e978d5
c162f767587eacbc c162f76758a3d70a
3fa5d796fa6b8670 3fa5d796fa33e17a
c190ea30aa4b8ef7 c190ea30aa666666
3e433e3cb5df61c1 3e433e3cb60902a0
3fb8c2cf85dea6f3 3fb8c2cf85d53ab1
be6f3286e2f52791 be6f3286e2d68186
c07d7d25331b8575 c07d7d25332acfb7
40d44d25a245568e 40d44d25a1cac083
3e252122ed93244d 3e252122ee361458
c232e5f9c0f30058 c232e5f9c1240000
c1f5af85b22abe0a c1f5af85b1e00000
40520e234a5c0416 40520e234a87e38f
c050c96d1348135f c050c96d1328d1b0
becd053b2f7cc335 becd053b2f517537
41c1cc5f3b3cf748 41c1cc5f3b000000
be74072478b47664 be74072478856dee
'@
function Get-BenchSets { param([string]$Like) return @($script:ioLog | Where-Object { $_ -like "set $Like" }) }
function New-BenchKitTable {
    # 30 kit slots; 6650F27 in slot 28 as commissioned 14.11, 7 standards plus one S2VNA type outside SOLT.
    param([int]$Slots = 30, [int]$KitSlot = 28)
    $table = @()
    for ($slot = 1; $slot -le $Slots; $slot++) {
        $table += [pscustomobject]@{ label = "85032F-$slot"; standards = @(@{ TYPE = 'LOAD'; LABel = 'Broadband'; DELay = 0; LOSS = 0; Z0 = 50 }) }
    }
    $table[$KitSlot - 1] = [pscustomobject]@{ label = ' 6650F27 '; standards = @(
        @{ TYPE = 'OPEN'; LABel = 'OPEN -F-'; DELay = 2.9e-11; LOSS = 1.3e9; Z0 = 50; C0 = 49.43; C1 = -310.13; C2 = 23.17; C3 = -0.16 }
        @{ TYPE = 'open'; LABel = ' OPEN -M- '; DELay = 3.1e-11; LOSS = 1.3e9; Z0 = 50; C0 = 62.54; C1 = 1284; C2 = -106.2; C3 = 4.1 }
        @{ TYPE = 'SHORT'; LABel = 'SHORT -F-'; DELay = 3.1e-11; LOSS = 1.3e9; Z0 = 50; L0 = 2.08; L1 = -140.8; L2 = 7.8; L3 = -0.09 }
        @{ TYPE = 'SHORT'; LABel = 'SHORT -M-'; DELay = 2.9e-11; LOSS = 1.3e9; Z0 = 50; L0 = 0; L1 = 0; L2 = 0; L3 = 0 }
        @{ TYPE = 'ARBI'; LABel = 'Не SOLT' }
        @{ TYPE = 'LOAD'; LABel = 'LOAD -F-'; DELay = 0; LOSS = 0; Z0 = 50 }
        @{ TYPE = 'LOAD'; LABel = 'LOAD -M-'; DELay = 0; LOSS = 0; Z0 = 50 }
        @{ TYPE = 'THRU'; LABel = 'THRU'; DELay = 5.1e-11; LOSS = 2.2e9; Z0 = 50 }) }
    return $table
}

Describe 'ВАЦ v2: округление до 9 значащих цифр как в Python' {
    It 'граничные значения: <Value>' -TestCases @(
        @{ Value = 1.000000005; Expected = 1.0 }, @{ Value = 123456784.5; Expected = 123456784.0 },
        @{ Value = 123456785.5; Expected = 123456786.0 }, @{ Value = 1.000000015; Expected = 1.00000001 },
        @{ Value = 999999999.5; Expected = 1e9 }, @{ Value = -0.3; Expected = -0.3 }) {
        param($Value, $Expected)
        ConvertTo-VnaSig9 $Value | Should Be $Expected
    }
    It 'совпадает с таблицей Python по битам на всех значениях' {
        $mismatches = @()
        foreach ($line in ($script:sig9Table -split "`n")) {
            $pair = $line.Trim().Split(' ')
            $value = [BitConverter]::Int64BitsToDouble([Convert]::ToInt64($pair[0], 16))
            $actual = '{0:x16}' -f [BitConverter]::DoubleToInt64Bits((ConvertTo-VnaSig9 $value))
            if ($actual -cne $pair[1]) { $mismatches += "$($pair[0]) -> $actual, Python $($pair[1])" }
        }
        ($mismatches -join '; ') | Should Be ''
        ($script:sig9Table -split "`n").Count | Should BeGreaterThan 250
    }
}

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
        @{ Case = 'расширение портов'; Path = 'SENSe(1).CORRection.EXTension.STATe'; Value = $true; Text = 'расширение портов' },
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
    It 'профиль v1 выключает расширение портов и отказывает, если его включили' {
        $script:ioValues['SENSe(1).CORRection.EXTension.STATe'] = $true
        Initialize-BenchCalibrated
        $script:ioValues['SENSe(1).CORRection.EXTension.STATe'] | Should Be $false
        $script:ioValues['SENSe(1).CORRection.EXTension.STATe'] = $true
        { Invoke-BenchOp capture @{ profile = $script:profile; orientation = 'both'; port_to_end = @{ '1' = 'А'; '2' = 'Б' } } 1 } | Should Throw 'EXTension'
        { Invoke-BenchOp acm_confidence @{ profile = $script:profile; module_serial = '26140008' } } | Should Throw 'расширение портов'
    }
    It 'apply_screen и store_memory возвращают выбранный оператором график' {
        Initialize-BenchCalibrated
        $script:selected = 3
        [void](Invoke-BenchOp apply_screen @{ screen = (New-BenchScreen); screen_hash = $script:benchHash })
        $script:selected | Should Be 3
        $script:selected = 4
        [void](Invoke-BenchOp store_memory @{ screen_hash = $script:benchHash; traces = @(2,5) })
        $script:selected | Should Be 4
        $screen = New-BenchScreen; $screen.traces = @($screen.traces[0], $screen.traces[1])
        $script:selected = 5
        [void](Invoke-BenchOp apply_screen @{ screen = $screen; screen_hash = ('c' * 64) })
        $script:selected | Should Be 1
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
    It 'read_kit находит набор по метке, читает меры в единицах S2VNA и возвращает выбранный оператором набор' {
        Initialize-BenchCalibrated
        $script:kitSlots = New-BenchKitTable; $script:ioValues['SENSe(1).CORRection.COLLect.CKIT.SELect'] = 3; $script:ioLog.Clear()
        $result = Invoke-BenchOp read_kit @{ label = '6650F27' }
        $result.serial | Should Be '26030095'
        $result.label | Should Be '6650F27'
        @($result.standards).Count | Should Be 7
        (@($result.standards | ForEach-Object { $_.type }) -join ',') | Should Be 'OPEN,OPEN,SHORT,SHORT,LOAD,LOAD,THRU'
        $open = $result.standards[0]
        $open.label | Should Be 'OPEN -F-'
        (@($open.c) -join ',') | Should Be '49.43,-310.13,23.17,-0.16'
        (@($open.l) -join ',') | Should Be '0,0,0,0'
        $open.delay | Should Be 2.9e-11
        $open.loss | Should Be 1.3e9
        $open.z0 | Should Be 50
        $result.standards[1].label | Should Be 'OPEN -M-'
        (@($result.standards[2].l) -join ',') | Should Be '2.08,-140.8,7.8,-0.09'
        (@($result.standards[2].c) -join ',') | Should Be '0,0,0,0'
        (@($result.standards[4].c) + @($result.standards[4].l) | Where-Object { $_ -ne 0 }).Count | Should Be 0
        $result.standards[6].delay | Should Be 5.1e-11
        @($result.standards[6].c).Count | Should Be 4
        # Only kit selection is written, and the operator's kit is back.
        @($script:ioLog | Where-Object { $_ -like 'set *' -and $_ -ne 'set SENSe(1).CORRection.COLLect.CKIT.SELect' }).Count | Should Be 0
        $script:ioValues['SENSe(1).CORRection.COLLect.CKIT.SELect'] | Should Be 3
        @($script:ioLog | Where-Object { $_ -like 'get *STAN(*' -and $_ -notlike 'get *STAN(*).TYPE' -and $_ -like '*STAN(5).*' }).Count | Should Be 0
    }
    It 'read_kit: набора нет или он не единственный — отказ, выбранный набор возвращён' {
        Initialize-BenchCalibrated
        $script:kitSlots = New-BenchKitTable; $script:ioValues['SENSe(1).CORRection.COLLect.CKIT.SELect'] = 5
        { Invoke-BenchOp read_kit @{ label = '6650F28' } } | Should Throw 'нет калибровочного набора «6650F28»'
        $script:ioValues['SENSe(1).CORRection.COLLect.CKIT.SELect'] | Should Be 5
        $script:kitSlots[1].label = '6650F27'
        { Invoke-BenchOp read_kit @{ label = '6650F27' } } | Should Throw 'номера 2, 28'
        $script:ioValues['SENSe(1).CORRection.COLLect.CKIT.SELect'] | Should Be 5
        { Invoke-BenchOp read_kit @{ label = '6650f27' } } | Should Throw 'нет калибровочного набора'
    }
    It 'read_kit: нечисловой коэффициент или больше 12 мер — отказ, выбранный набор возвращён' {
        Initialize-BenchCalibrated
        $script:kitSlots = New-BenchKitTable; $script:ioValues['SENSe(1).CORRection.COLLect.CKIT.SELect'] = 7
        $script:kitSlots[27].standards[0].C2 = [double]::NaN
        { Invoke-BenchOp read_kit @{ label = '6650F27' } } | Should Throw 'недопустимое число в описании меры (C2)'
        $script:ioValues['SENSe(1).CORRection.COLLect.CKIT.SELect'] | Should Be 7
        $script:kitSlots[27].standards[0].C2 = 23.17
        $many = @(); for ($i = 0; $i -lt 13; $i++) { $many += @{ TYPE = 'LOAD'; LABel = "L$i"; DELay = 0; LOSS = 0; Z0 = 50 } }
        $script:kitSlots[27].standards = $many
        { Invoke-BenchOp read_kit @{ label = '6650F27' } } | Should Throw 'больше 12'
        $script:ioValues['SENSe(1).CORRection.COLLect.CKIT.SELect'] | Should Be 7
        $script:kitSlots[27].standards = @(@{ TYPE = 'ARBI'; LABel = 'x' })
        { Invoke-BenchOp read_kit @{ label = '6650F27' } } | Should Throw 'нет мер OPEN/SHORT/LOAD/THRU'
        $script:ioValues['SENSe(1).CORRection.COLLect.CKIT.SELect'] | Should Be 7
    }
    It 'read_kit: неверная метка, протокол 1, до prepare и после release отклоняются без перебора наборов' {
        $script:kitSlots = New-BenchKitTable
        { Invoke-BenchOp read_kit @{ label = '6650F27' } } | Should Throw 'исходное состояние не сохранено'
        { Invoke-BenchOp read_kit @{ label = '6650F27' } 1 } | Should Throw 'протокол управления версии 2'
        Initialize-BenchCalibrated; $script:ioLog.Clear()
        foreach ($bad in @('', '   ', "66`n50F27", ('x' * 65))) { { Invoke-BenchOp read_kit @{ label = $bad } } | Should Throw 'неверная метка' }
        { Invoke-BenchOp read_kit @{} } | Should Throw 'неверная метка'
        @($script:ioLog | Where-Object { $_ -like '*CKIT*' }).Count | Should Be 0
        [void](Invoke-BenchOp release @{ screen_hash = $null })
        { Invoke-BenchOp read_kit @{ label = '6650F27' } } | Should Throw 'уже завершено'
    }
    It 'read_kit: прибор заменён во время чтения — ошибка, не квитанция' {
        Initialize-BenchCalibrated
        $script:kitSlots = New-BenchKitTable
        Mock Read-S2KitByLabel { $script:benchApp.Name = 'Planar, S50180, 26030096, 26.2.2/2'; @{ label = '6650F27'; standards = @() } }
        { Invoke-BenchOp read_kit @{ label = '6650F27' } } | Should Throw 'прибор заменён'
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

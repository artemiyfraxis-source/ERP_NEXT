# Pester 3.4+: the OB.019 command runner writes only ERP's writable registers, only while the machine is idle, reports
# the read-back, and turns every failure into a report instead of an exception. mbd and ERP are mocked; the last block
# writes through the real mbd.ps1 -Mock -LocalOnly on a free port.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('ob019c-' + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
. (Join-Path $root 'ob019-writer.ps1') -LogPath (Join-Path $tmp 'commands.log') -MbdScript (Join-Path $root 'mbd.ps1') -BridgeScript (Join-Path $root 'ob019-bridge.ps1')

function New-Command($Blocks, $States = @(102, 107), $Bits = @()) {
    return $script:Json.DeserializeObject((ConvertTo-JsonLite @{ id = 'c-1'; blocks = $Blocks; bits = $Bits; states = $States }))
}

function Reply-Mbd([string]$Line, [int]$State = 102, [string]$Version = '1.2.0') {
    $request = $script:Json.DeserializeObject($Line)
    if ($request['cmd'] -eq 'version') {
        if (-not $Version) { return '{"ok":false,"error":"unknown cmd version"}' }
        return ConvertTo-JsonLite @{ ok = $true; result = @{ version = $Version } }
    }
    if ($request['cmd'] -eq 'setbit') {
        $word = 0x0A0A -bor [int]$request['value']
        return ConvertTo-JsonLite @{ ok = $true; result = @{ addr = [int]$request['addr']; bit = 0; value = [int]$request['value']; readback = @($word) } }
    }
    if ($request['cmd'] -eq 'read') {
        return ConvertTo-JsonLite @{ ok = $true; result = @{ addr = [int]$request['addr']; values = @($State) } }
    }
    $values = [int[]]@($request['values'] | ForEach-Object { [int]$_ })
    return ConvertTo-JsonLite @{ ok = $true; result = @{ addr = [int]$request['addr']; written = $values; readback = $values } }
}

Describe 'write allow-list' {
    It 'accepts the job, wire and marking text registers' {
        Test-BlockWritable @{ addr = 32; values = @(32500, 0, 500, 0) } | Should Be $true
        Test-BlockWritable @{ addr = 440; values = @(17201) + @(0) * 14 } | Should Be $true
        Test-BlockWritable @{ addr = 20; values = @(2) } | Should Be $true
    }
    It 'accepts the other HMI pages: speeds, middle strip, slitting, coiling, marking' {
        Test-BlockWritable @{ addr = 100; values = @(5, 5, 5, 5, 5) } | Should Be $true
        Test-BlockWritable @{ addr = 197; values = @(65486) } | Should Be $true
        Test-BlockWritable @{ addr = 1703; values = @(1250, 0, 300) } | Should Be $true
        Test-BlockWritable @{ addr = 1750; values = @(100, 900, 50, 999) } | Should Be $true
        Test-BlockWritable @{ addr = 401; values = @(500, 0) } | Should Be $true
    }
    It 'writes switches only as single bits, never as whole words' {
        foreach ($a in @(132, 400, 422, 434, 435, 436, 604)) {
            Test-BlockWritable @{ addr = $a; values = @(1) } | Should Be $false
            Test-BitWritable @{ addr = $a; bit = 0; value = 1 } | Should Be $true
        }
        foreach ($pair in @(@(421, 0), @(390, 0), @(133, 0), @(142, 0), @(599, 0), @(600, 1), @(400, 1))) {
            Test-BitWritable @{ addr = $pair[0]; bit = $pair[1]; value = 1 } | Should Be $false
        }
        Test-BitWritable @{ addr = 400; bit = 0; value = 2 } | Should Be $false
    }
    It 'refuses commands, the HMI context, passwords, SHA, date, unknown registers and out-of-range words' {
        foreach ($a in @(599, 600, 730, 1087, 1088, 1089, 1203, 1207, 1211, 1682, 1684, 1686, 390, 133, 142, 200, 44, 403, 421)) {
            Test-BlockWritable @{ addr = $a; values = @(1) } | Should Be $false
        }
        Test-BlockWritable @{ addr = 1722; values = @(1, 0, 0) } | Should Be $false
        Test-BlockWritable @{ addr = 42; values = @(1, 2, 3) } | Should Be $false
        Test-BlockWritable @{ addr = 32; values = @(70000) } | Should Be $false
    }
}

Describe 'running a command' {
    It 'checks the state first, then writes and returns the read-back' {
        $script:sent = @()
        Mock Invoke-MbdLine { $script:sent += $Line; Reply-Mbd $Line 102 }
        $result = Invoke-MachineCommand (New-Command @(@{ addr = 32; values = @(32600, 0) }))
        $result['ok'] | Should Be $true
        $result['readback']['32'] | Should Be 32600
        $result['readback']['33'] | Should Be 0
        $script:Json.DeserializeObject($script:sent[0])['cmd'] | Should Be 'version'
        $script:Json.DeserializeObject($script:sent[1])['cmd'] | Should Be 'read'
        $script:Json.DeserializeObject($script:sent[2])['cmd'] | Should Be 'write'
    }
    It 'sets a switch bit through setbit and reports the word read back' {
        $script:sent = @()
        Mock Invoke-MbdLine { $script:sent += $Line; Reply-Mbd $Line 102 }
        $result = Invoke-MachineCommand (New-Command @() @(102, 107) @(@{ addr = 400; bit = 0; value = 1 }))
        $result['ok'] | Should Be $true
        $result['readback']['400'] | Should Be 0x0A0B
        $setbit = $script:Json.DeserializeObject($script:sent[2])
        ($setbit['cmd'] + ' D' + $setbit['addr'] + '.' + $setbit['bit'] + '=' + $setbit['value']) | Should Be 'setbit D400.0=1'
        ($script:sent -join ' ') -match '"write"' | Should Be $false
    }
    It 'refuses the whole command for a switch bit ERP may not write: nothing written' {
        $script:sent = @()
        Mock Invoke-MbdLine { $script:sent += $Line; Reply-Mbd $Line 102 }
        $result = Invoke-MachineCommand (New-Command @(@{ addr = 32; values = @(1, 0) }) @(102, 107) @(@{ addr = 421; bit = 0; value = 1 }))
        $result['ok'] | Should Be $false
        $result['error'] | Should Match 'D421.0'
        ($script:sent -join ' ') -match '"write"|"setbit"' | Should Be $false
    }
    It 'an old mbd (no version, or older than 1.2.0) gets nothing of the command' {
        foreach ($old in @('', '1.1.9')) {
            $script:sent = @(); $script:old = $old
            Mock Invoke-MbdLine { $script:sent += $Line; Reply-Mbd $Line 102 $script:old }
            $result = Invoke-MachineCommand (New-Command @(@{ addr = 32; values = @(1, 0) }) @(102, 107) @(@{ addr = 400; bit = 0; value = 1 }))
            $result['ok'] | Should Be $false
            $result['error'] | Should Match '^mbd outdated'
            $script:sent.Count | Should Be 1
        }
    }
    It 'the feed-roller bit D604.0 needs mbd 1.3.0; 1.2.0 still does everything else' {
        $script:sent = @()
        Mock Invoke-MbdLine { $script:sent += $Line; Reply-Mbd $Line 102 '1.2.0' }
        $result = Invoke-MachineCommand (New-Command @() @(102, 107) @(@{ addr = 604; bit = 0; value = 1 }))
        $result['ok'] | Should Be $false
        $result['error'] | Should Be 'mbd outdated: 1.2.0, need 1.3.0'
        $script:sent.Count | Should Be 1
        (Invoke-MachineCommand (New-Command @() @(102, 107) @(@{ addr = 400; bit = 0; value = 1 })))['ok'] | Should Be $true
        $script:sent = @()
        Mock Invoke-MbdLine { $script:sent += $Line; Reply-Mbd $Line 102 '1.3.0' }
        $result = Invoke-MachineCommand (New-Command @() @(102, 107) @(@{ addr = 604; bit = 0; value = 1 }))
        $result['ok'] | Should Be $true
        $script:Json.DeserializeObject($script:sent[2])['cmd'] | Should Be 'setbit'
    }
    It 'writes nothing while the machine runs' {
        $script:sent = @()
        Mock Invoke-MbdLine { $script:sent += $Line; Reply-Mbd $Line 103 }
        $result = Invoke-MachineCommand (New-Command @(@{ addr = 32; values = @(32600, 0) }))
        $result['ok'] | Should Be $false
        $result['error'] | Should Be 'state 103'
        ($script:sent -join ' ') -match '"write"' | Should Be $false
    }
    It 'writes nothing when one block is outside the allow-list' {
        $script:sent = @()
        Mock Invoke-MbdLine { $script:sent += $Line; Reply-Mbd $Line 102 }
        $result = Invoke-MachineCommand (New-Command @(@{ addr = 32; values = @(1, 0) }, @{ addr = 599; values = @(1) }))
        $result['ok'] | Should Be $false
        $result['error'] | Should Match 'D599'
        ($script:sent -join ' ') -match '"write"' | Should Be $false
    }
    It 'reports the vendor HMI holding the port' {
        Mock Invoke-MbdLine {
            if ($Line -match '"version"') { return '{"ok":true,"result":{"version":"1.2.0"}}' }
            '{"ok":false,"error":"busy: PXS_HMI is running, bus refused"}'
        }
        $result = Invoke-MachineCommand (New-Command @(@{ addr = 32; values = @(1, 0) }))
        $result['ok'] | Should Be $false
        $result['error'] | Should Match 'PXS_HMI'
    }
}

Describe 'ERP exchange' {
    It 'takes a command, runs it and posts the result with the read-back' {
        $script:posted = @()
        Mock Invoke-MbdLine { Reply-Mbd $Line 107 }
        Mock Invoke-Erp {
            $script:posted += @{ path = $Path; body = $Body }
            if ($Path -like '*/next') { return @{ code = 200; text = '{"command":{"id":"c-9","blocks":[{"addr":20,"values":[1]}],"states":[102,107]}}' } }
            return @{ code = 200; text = '{"status":"done"}' }
        }
        $result = Step-Commands 'token'
        $result['ok'] | Should Be $true
        $script:posted.Count | Should Be 2
        $script:posted[1]['path'] | Should Be '/api/production/machines/agent/writes/c-9/result'
        $script:posted[1]['body'] | Should Match '"readback":\{"20":1\}'
        $script:posted[1]['body'] | Should Match '"ok":true'
        $script:posted[1]['body'] | Should Match '"inventory":"\\u041e\\u0411.019"'
        $script:posted[0]['body'] | Should Match '"runner_version":"1.3.0"'
    }
    It 'does nothing when ERP has no command' {
        Mock Invoke-Erp { @{ code = 200; text = '{"command":null}' } }
        Step-Commands 'token' | Should Be $null
    }
}

# A fake mbd for machine commands: D766 = $script:state, D782 = $script:alarm; pulse replies as mbd.ps1 does.
function Reply-Machine([string]$Line) {
    $request = $script:Json.DeserializeObject($Line)
    $script:bus += $Line
    if ($request['cmd'] -eq 'pulse') {
        if ($script:pulseFails) { throw 'mbd closed the connection without a reply' }
        $script:pulsed += ('D' + $request['addr'] + '.' + $request['bit'] + '/' + $request['hold_ms'])
        $script:state = $script:stateAfter
        return ConvertTo-JsonLite @{ ok = $true; ms = 1600; result = @{ addr = $request['addr']; bit = $request['bit']
            hold_ms = $request['hold_ms']; before = 0; set = $true; cleared = $script:clears; after = 0 } }
    }
    if ([int]$request['addr'] -eq 604) { return ConvertTo-JsonLite @{ ok = $true; ms = 1; result = @{ addr = 604; values = @($script:wheel) } } }
    $values = @()
    for ($i = 0; $i -lt [int]$request['count']; $i++) { $values += 0 }
    $values[0] = $script:state; $values[16] = $script:alarm
    return ConvertTo-JsonLite @{ ok = $true; ms = 1; result = @{ addr = [int]$request['addr']; values = $values } }
}

function Reset-Machine([int]$State, [int]$Alarm = 0, [int]$After = 103, [int]$Wheel = 0) {
    $script:state = $State; $script:alarm = $Alarm; $script:stateAfter = $After; $script:wheel = $Wheel
    $script:bus = @(); $script:pulsed = @(); $script:pulseFails = $false; $script:clears = $true
}

function New-Control([string]$Action, [int]$DeadlineMs = 4000) {
    return @{ id = 'k-1'; kind = 'control'; action = $Action; deadline_ms = $DeadlineMs }
}

function Get-Fresh { return [Diagnostics.Stopwatch]::StartNew() }

Describe 'machine commands on the PC' {
    It 'Start in Ready holds D599.0 for 1.5 s and reports the state before and after' {
        Mock Test-HmiOpen { $false }; Mock Invoke-MbdLine { Reply-Machine $Line }
        Reset-Machine 102
        $outcome = Invoke-ControlCommand (New-Control 'start') (Get-Fresh)
        ($outcome['outcome'] + '/' + $outcome['reason']) | Should Be 'done/ok'
        $script:pulsed -join ',' | Should Be 'D599.0/1500'
        $outcome['before']['766'] | Should Be 102
        $outcome['after']['766'] | Should Be 103
    }
    It 'Start outside Ready is refused on the PC: nothing pulsed' {
        Mock Test-HmiOpen { $false }; Mock Invoke-MbdLine { Reply-Machine $Line }
        Reset-Machine 100
        $outcome = Invoke-ControlCommand (New-Control 'start') (Get-Fresh)
        ($outcome['outcome'] + '/' + $outcome['reason']) | Should Be 'failed/state'
        $script:pulsed.Count | Should Be 0
    }
    It 'Start with the feed rollers lifted (D604.0 = 1) is refused on the PC: nothing pulsed' {
        Mock Test-HmiOpen { $false }; Mock Invoke-MbdLine { Reply-Machine $Line }
        Reset-Machine 102 0 103 0x0A01
        $outcome = Invoke-ControlCommand (New-Control 'start') (Get-Fresh)
        ($outcome['outcome'] + '/' + $outcome['reason'] + '/' + $outcome['detail']) | Should Be 'failed/rollers_up/D604.0=1'
        $script:pulsed.Count | Should Be 0
        Reset-Machine 102 0 103 0x0A00
        (Invoke-ControlCommand (New-Control 'start') (Get-Fresh))['outcome'] | Should Be 'done'
        Reset-Machine 103 0 104 1
        (Invoke-ControlCommand (New-Control 'stop') (Get-Fresh))['outcome'] | Should Be 'done'
    }
    It 'Reset only in Please reset / E-stop / Total finish' {
        Mock Test-HmiOpen { $false }; Mock Invoke-MbdLine { Reply-Machine $Line }
        foreach ($state in @(100, 105, 107)) {
            Reset-Machine $state 0 102
            (Invoke-ControlCommand (New-Control 'reset') (Get-Fresh))['outcome'] | Should Be 'done'
            $script:pulsed -join ',' | Should Be 'D600.1/1500'
        }
        Reset-Machine 103
        (Invoke-ControlCommand (New-Control 'reset') (Get-Fresh))['reason'] | Should Be 'state'
        $script:pulsed.Count | Should Be 0
    }
    It 'Stop runs in any state; Clear alarm only with an alarm' {
        Mock Test-HmiOpen { $false }; Mock Invoke-MbdLine { Reply-Machine $Line }
        Reset-Machine 103 0 104
        (Invoke-ControlCommand (New-Control 'stop') (Get-Fresh))['outcome'] | Should Be 'done'
        $script:pulsed -join ',' | Should Be 'D599.2/1500'
        Reset-Machine 104 0
        (Invoke-ControlCommand (New-Control 'alarm_clear') (Get-Fresh))['reason'] | Should Be 'no_alarm'
        Reset-Machine 104 6 104
        (Invoke-ControlCommand (New-Control 'alarm_clear') (Get-Fresh))['outcome'] | Should Be 'done'
        $script:pulsed -join ',' | Should Be 'D600.4/1500'
    }
    It 'a command past its deadline never touches the bus' {
        Mock Test-HmiOpen { $false }; Mock Invoke-MbdLine { Reply-Machine $Line }
        Reset-Machine 102
        $outcome = Invoke-ControlCommand (New-Control 'start' 0) (Get-Fresh)
        ($outcome['outcome'] + '/' + $outcome['reason']) | Should Be 'expired/too_late'
        $script:bus.Count | Should Be 0
    }
    It 'PXS_HMI open: refused before the bus' {
        Mock Test-HmiOpen { $true }; Mock Invoke-MbdLine { Reply-Machine $Line }
        Reset-Machine 102
        $outcome = Invoke-ControlCommand (New-Control 'stop') (Get-Fresh)
        ($outcome['outcome'] + '/' + $outcome['reason']) | Should Be 'failed/hmi_open'
        $script:bus.Count | Should Be 0
    }
    It 'mbd refuses because the HMI holds COM8: failed, not unknown' {
        Mock Test-HmiOpen { $false }
        Mock Invoke-MbdLine { '{"ok":false,"ms":3,"error":"busy: PXS_HMI is running, bus refused"}' }
        $outcome = Invoke-ControlCommand (New-Control 'stop') (Get-Fresh)
        ($outcome['outcome'] + '/' + $outcome['reason']) | Should Be 'failed/hmi_open'
    }
    It 'no reply during the hold: unknown, never done' {
        Mock Test-HmiOpen { $false }; Mock Invoke-MbdLine { Reply-Machine $Line }
        Reset-Machine 102
        $script:pulseFails = $true
        $outcome = Invoke-ControlCommand (New-Control 'start') (Get-Fresh)
        ($outcome['outcome'] + '/' + $outcome['reason']) | Should Be 'unknown/no_reply'
    }
    It 'bit not cleared: unknown' {
        Mock Test-HmiOpen { $false }; Mock Invoke-MbdLine { Reply-Machine $Line }
        Reset-Machine 102
        $script:clears = $false
        $outcome = Invoke-ControlCommand (New-Control 'start') (Get-Fresh)
        ($outcome['outcome'] + '/' + $outcome['reason']) | Should Be 'unknown/not_cleared'
    }
    It 'an unknown command (E-stop included) and a foreign bit are refused before the bus' {
        Mock Test-HmiOpen { $false }; Mock Invoke-MbdLine { Reply-Machine $Line }
        Reset-Machine 103
        foreach ($name in @('estop', 'jog', 'write')) {
            (Invoke-ControlCommand (New-Control $name) (Get-Fresh))['reason'] | Should Be 'unknown_command'
        }
        { Invoke-Pulse 599 3 } | Should Throw
        { Invoke-Pulse 600 8 } | Should Throw
        $script:bus.Count | Should Be 0
    }
}

Describe 'machine commands through the ERP exchange' {
    It 'Stop from the queue: pulsed, the outcome posted with reason and states' {
        Mock Test-HmiOpen { $false }; Mock Invoke-MbdLine { Reply-Machine $Line }
        Reset-Machine 103 0 104
        $script:posted = @()
        Mock Invoke-Erp {
            $script:posted += @{ path = $Path; body = $Body }
            if ($Path -like '*/next') { return @{ code = 200; text = '{"command":{"id":"k-7","kind":"control","action":"stop","deadline_ms":4500}}' } }
            return @{ code = 200; text = '{"status":"done"}' }
        }
        (Step-Commands 'token')['outcome'] | Should Be 'done'
        $script:posted.Count | Should Be 2
        $script:posted[1]['path'] | Should Be '/api/production/machines/agent/writes/k-7/result'
        $result = $script:Json.DeserializeObject($script:posted[1]['body'])
        ($result['outcome'] + '/' + $result['reason'] + '/' + $result['ok']) | Should Be 'done/ok/True'
        $result['before']['766'] | Should Be 103
        $result['after']['766'] | Should Be 104
    }
    It 'retries the result while ERP does not answer' {
        Mock Test-HmiOpen { $false }; Mock Invoke-MbdLine { Reply-Machine $Line }; Mock Start-Sleep { }
        Reset-Machine 103 0 104
        $script:tries = 0
        Mock Invoke-Erp {
            if ($Path -like '*/next') { return @{ code = 200; text = '{"command":{"id":"k-8","kind":"control","action":"stop","deadline_ms":4500}}' } }
            $script:tries++
            if ($script:tries -lt 2) { return @{ code = 0; text = '' } }
            return @{ code = 200; text = '{"status":"done"}' }
        }
        [void](Step-Commands 'token')
        $script:tries | Should Be 2
    }
}

Describe 'against mbd.ps1 -Mock -LocalOnly' {
    $listener = New-Object System.Net.Sockets.TcpListener ([System.Net.IPAddress]::Loopback, 0)
    $listener.Start(); $port = $listener.LocalEndpoint.Port; $listener.Stop()
    $mbd = Start-Process -PassThru -WindowStyle Hidden powershell.exe -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + (Join-Path $root 'mbd.ps1') + '"'), '-Mock',
        '-LocalOnly', '-Port', $port, '-Dir', ('"' + $tmp + '"'))
    try {
        $ready = $false
        for ($i = 0; $i -lt 50 -and -not $ready; $i++) {
            try { $probe = New-Object System.Net.Sockets.TcpClient('127.0.0.1', $port); $probe.Close(); $ready = $true } catch { Start-Sleep -Milliseconds 200 }
        }
        It 'writes the order name (D940..) through the real service and reads it back' {
            $ready | Should Be $true
            $MbdPort = $port
            # The mock PLC starts with zeros: state 0 is not idle, so allow it for this run only.
            $result = Invoke-MachineCommand (New-Command @(@{ addr = 940; values = @(17732, 8262) + @(0) * 13 }) @(0))
            $result['error'] | Should Be ''
            $result['ok'] | Should Be $true
            $result['readback']['940'] | Should Be 17732
            $result['readback']['954'] | Should Be 0
        }
        It 'the real service refuses a write of command bits, the HMI page, the date, passwords and SHA' {
            $MbdPort = $port
            foreach ($a in @(599, 600, 730, 1087, 1203, 1682, 1686, 44)) {
                $reply = $script:Json.DeserializeObject((Invoke-MbdLine (ConvertTo-JsonLite @{ cmd = 'write'; addr = $a; values = @(1) })))
                $reply['ok'] | Should Be $false
                $reply['error'] | Should Match 'write not allowed'
            }
            $reply = $script:Json.DeserializeObject((Invoke-MbdLine (ConvertTo-JsonLite @{ cmd = 'write'; addr = 100; values = @(7) })))
            $reply['ok'] | Should Be $true
        }
        It 'sets a switch bit through the real service after checking its version' {
            $MbdPort = $port
            Get-MbdVersion | Should Be '1.3.0'
            $result = Invoke-MachineCommand (New-Command @() @(0) @(@{ addr = 434; bit = 0; value = 1 }))
            $result['error'] | Should Be ''
            $result['readback']['434'] | Should Be 1
        }
        It 'pulses Stop through the real service: bit set, held, cleared' {
            $MbdPort = $port
            Mock Test-HmiOpen { $false }
            $outcome = Invoke-ControlCommand (New-Control 'stop') ([Diagnostics.Stopwatch]::StartNew())
            ($outcome['outcome'] + '/' + $outcome['reason'] + '/' + $outcome['detail']) | Should Be 'done/ok/held 1500 ms'
            (Read-Range 599 1)[0] | Should Be 0
        }
    } finally {
        if (-not $mbd.HasExited) { Stop-Process -Id $mbd.Id -Force }
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

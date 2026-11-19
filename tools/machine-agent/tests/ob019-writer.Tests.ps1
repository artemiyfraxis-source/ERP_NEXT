# Pester 3.4+: the OB.019 command runner writes only ERP's writable registers, only while the machine is idle, reports
# the read-back, and turns every failure into a report instead of an exception. mbd and ERP are mocked; the last block
# writes through the real mbd.ps1 -Mock -LocalOnly on a free port.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('ob019c-' + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
. (Join-Path $root 'ob019-writer.ps1') -LogPath (Join-Path $tmp 'commands.log') -MbdScript (Join-Path $root 'mbd.ps1') -BridgeScript (Join-Path $root 'ob019-bridge.ps1')

function New-Command($Blocks, $States = @(102, 107)) {
    return $script:Json.DeserializeObject((ConvertTo-JsonLite @{ id = 'c-1'; blocks = $Blocks; states = $States }))
}

function Reply-Mbd([string]$Line, [int]$State = 102) {
    $request = $script:Json.DeserializeObject($Line)
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
    It 'refuses commands, the HMI context, speeds and out-of-range words' {
        Test-BlockWritable @{ addr = 599; values = @(1) } | Should Be $false
        Test-BlockWritable @{ addr = 730; values = @(13) } | Should Be $false
        Test-BlockWritable @{ addr = 100; values = @(5) } | Should Be $false
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
        $script:Json.DeserializeObject($script:sent[0])['cmd'] | Should Be 'read'
        $script:Json.DeserializeObject($script:sent[1])['cmd'] | Should Be 'write'
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
        Mock Invoke-MbdLine { '{"ok":false,"error":"busy: PXS_HMI is running, bus refused"}' }
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
    }
    It 'does nothing when ERP has no command' {
        Mock Invoke-Erp { @{ code = 200; text = '{"command":null}' } }
        Step-Commands 'token' | Should Be $null
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
    } finally {
        if (-not $mbd.HasExited) { Stop-Process -Id $mbd.Id -Force }
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

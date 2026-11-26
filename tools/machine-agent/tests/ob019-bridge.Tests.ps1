# Pester 3.4+: the OB.019 bridge is read-only, reads only the allow-list, maps mbd failures to link states and
# builds the ERP report. mbd is mocked; the last block runs the real mbd.ps1 -Mock -LocalOnly on a free port.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('ob019-' + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
. (Join-Path $root 'ob019-bridge.ps1') -LogPath (Join-Path $tmp 'bridge.log') -MbdScript (Join-Path $root 'mbd.ps1')

function Reply-Values([string]$Line) {
    $request = $script:Json.DeserializeObject($Line)
    $values = @()
    for ($i = 0; $i -lt [int]$request['count']; $i++) { $values += ([int]$request['addr'] + $i) % 65536 }
    return ConvertTo-JsonLite @{ ok = $true; ms = 1; result = @{ addr = [int]$request['addr']; values = $values } }
}

Describe 'read only' {
    It 'refuses every mbd command but read' {
        foreach ($command in @('write', 'pulse', 'status')) { { Assert-ReadOnly $command } | Should Throw }
        { Assert-ReadOnly 'read' } | Should Not Throw
    }
    It 'sends only read requests, exactly the allow-listed ranges' {
        $script:sent = @()
        Mock Invoke-MbdLine { $script:sent += $Line; Reply-Values $Line }
        $snapshot = Read-Snapshot
        $snapshot['link'] | Should Be 'ok'
        $script:sent.Count | Should Be 9
        foreach ($line in $script:sent) { $script:Json.DeserializeObject($line)['cmd'] | Should Be 'read' }
        ($script:sent -join ' ') -match '"write"|"pulse"|"status"' | Should Be $false
    }
    It 'keeps only allow-listed words (D29..D31 read but not sent)' {
        Mock Invoke-MbdLine { Reply-Values $Line }
        $words = (Read-Snapshot)['registers']
        $words.Count | Should Be 248
        $words['54'] | Should Be 54
        $words['55'] | Should Be 55
        $words.ContainsKey('766') | Should Be $true
        $words['766'] | Should Be 766
        $words['440'] | Should Be 440
        $words.ContainsKey('29') | Should Be $false
        $words.ContainsKey('21') | Should Be $false
        $words.ContainsKey('720') | Should Be $false
    }
    It 'reads the slow HMI pages only every SettingsSec and sends them from the last reading in between' {
        $script:SlowWords = @{}
        $script:sent = @()
        Mock Invoke-MbdLine { $script:sent += $Line; Reply-Values $Line }
        (Read-Snapshot)['registers'].Count | Should Be 248
        $script:sent.Count | Should Be 9
        $script:sent = @()
        $again = (Read-Snapshot)['registers']
        $script:sent.Count | Should Be 5
        $again.Count | Should Be 248
        $again['1753'] | Should Be 1753
    }
    It 'the bridge source carries no bus write command' {
        $source = [IO.File]::ReadAllText((Join-Path $root 'ob019-bridge.ps1'))
        $source -match "cmd\s*=\s*'(write|pulse|status)'" | Should Be $false
    }
}

Describe 'link states' {
    It 'vendor HMI holds COM8 -> port_busy' {
        Mock Invoke-MbdLine { '{"ok":false,"ms":3,"error":"busy: PXS_HMI is running, bus refused"}' }
        $snapshot = Read-Snapshot
        $snapshot['link'] | Should Be 'port_busy'
        $snapshot['error'] | Should Match 'PXS_HMI'
        $snapshot['registers'].Count | Should Be 0
    }
    It 'PLC silent -> no_plc' {
        Mock Invoke-MbdLine { '{"ok":false,"ms":1500,"error":"no valid reply reading D20"}' }
        (Read-Snapshot)['link'] | Should Be 'no_plc'
    }
    It 'mbd refuses this client -> no_service' {
        Mock Invoke-MbdLine { '{"ok":false,"error":"forbidden"}' }
        (Read-Snapshot)['link'] | Should Be 'no_service'
    }
    It 'mbd not running -> no_service' {
        Mock Invoke-MbdLine { throw 'mbd does not answer on 127.0.0.1:5020' }
        (Read-Snapshot)['link'] | Should Be 'no_service'
    }
}

Describe 'report' {
    It 'builds the ERP body: protocol 1, OB.019 in Cyrillic as \u escapes, words keyed by address' {
        $body = ConvertTo-JsonLite (New-Report @{ link = 'ok'; error = ''; registers = @{ '766' = 102 } } 'boot-1' 7)
        $body | Should Match '"proto":1'
        $body | Should Match '"inventory":"\\u041e\\u0411.019"'
        $body | Should Match '"registers":\{"766":102\}'
        $body | Should Match '"seq":7'
        $body | Should Match '"service_version":"mbd [0-9a-f]{12}"'
    }
    It 'sends on change or after the heartbeat' {
        $now = Get-Date
        Test-ShouldSend 'a' 'a' $now $now | Should Be $false
        Test-ShouldSend 'b' 'a' $now $now | Should Be $true
        Test-ShouldSend 'a' 'a' $now.AddSeconds(-5) $now | Should Be $true
    }
    It 'no TLS 1.2 on this runtime is a clear error, not a hang' {
        if (([enum]::GetNames([Net.SecurityProtocolType])) -contains 'Tls12') { { Enable-Tls12 } | Should Not Throw }
        else { { Enable-Tls12 } | Should Throw }
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
        It 'reads a full snapshot through the real service' {
            $ready | Should Be $true
            $MbdPort = $port
            $snapshot = Read-Snapshot
            $snapshot['link'] | Should Be 'ok'
            $snapshot['registers'].Count | Should Be 248
        }
    } finally {
        if (-not $mbd.HasExited) { Stop-Process -Id $mbd.Id -Force }
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

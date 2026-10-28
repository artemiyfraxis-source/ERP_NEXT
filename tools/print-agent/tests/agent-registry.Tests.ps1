# Pester (3.x/5.x): реестр агентов - отчёт серверу, автопоиск сканера, самообновление, разбор скриптов (сеть и WIA замоканы).
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('pa-' + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
. (Join-Path $here '..\agent.ps1') -LogPath (Join-Path $tmp 'a.log') -ConfigPath (Join-Path $tmp 'none.json')

Describe 'скрипты агента' {
    foreach ($name in 'agent.ps1', 'install.ps1', 'uninstall.ps1') {
        It "$name разбирается без ошибок" {
            $errors = $null
            [void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path (Join-Path $here "..\$name")).Path, [ref]$null, [ref]$errors)
            @($errors).Count | Should Be 0
        }
    }
    It 'agent.ps1 хранит версию, которую читает сервер' {
        (Get-Content -Raw -Encoding UTF8 (Join-Path $here '..\agent.ps1')) -match '\$script:AgentVersion\s*=\s*''\d+(\.\d+)*''' | Should Be $true
    }
    It 'install.ps1 лежит с BOM (Windows PowerShell 5.1 иначе портит кириллицу)' {
        $head = [IO.File]::ReadAllBytes((Join-Path $here '..\install.ps1'))[0..2]
        ($head -join ',') | Should Be '239,187,191'
    }
}

Describe 'Get-AgentScanners' {
    It 'адрес eSCL из настроек - один сканер, WIA не опрашивается' {
        Mock Get-WiaScanners {}
        $list = @(Get-AgentScanners -ScannerAddress '10.0.0.5')
        $list.Count | Should Be 1
        $list[0].id | Should Be '10.0.0.5'
        Assert-MockCalled Get-WiaScanners -Times 0 -Exactly -Scope It
    }
    It 'без адреса - то, что нашла Windows; нет сканеров - пустой список' {
        Mock Get-WiaScanners { @{ id = '{1}'; name = 'Pantum BM2300W' } }
        @(Get-AgentScanners -ScannerAddress '').Count | Should Be 1
        Mock Get-WiaScanners { @() }
        @(Get-AgentScanners -ScannerAddress '').Count | Should Be 0
    }
}

Describe 'Send-Heartbeat' {
    It 'шлёт номер ПК, имя, версию, адрес и сканеры' {
        Mock Get-MachineId { 'guid-1' }
        Mock Get-LanIp { '192.168.1.20' }
        Mock Get-AgentScanners { @{ id = '{1}'; name = 'Pantum' } }
        Mock Invoke-ErpApi { [pscustomobject]@{ ok = $true; update = $null } }
        Mock Invoke-AgentUpdate {}
        Send-Heartbeat -Base 'http://x' -AuthToken 't' -PrinterName '' -ScannerAddress ''
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Url -eq 'http://x/api/stock/scan/agent/heartbeat' -and $Body.machine_id -eq 'guid-1' -and $Body.lan_ip -eq '192.168.1.20' `
                -and $Body.version -eq $script:AgentVersion -and @($Body.scanners).Count -eq 1 -and $Body.scanners[0].name -eq 'Pantum'
        }
        Assert-MockCalled Invoke-AgentUpdate -Times 0 -Exactly -Scope It
    }
    It 'без сканеров сообщает пустой список, а новая версия на сервере запускает обновление' {
        Mock Get-MachineId { 'guid-1' }
        Mock Get-LanIp { '' }
        Mock Get-AgentScanners { @() }
        Mock Invoke-ErpApi { [pscustomobject]@{ ok = $true; update = [pscustomobject]@{ version = '9.9.9'; sha256 = 'x'; path = '/f' } } }
        Mock Invoke-AgentUpdate { $false }
        Send-Heartbeat -Base 'http://x' -AuthToken 't' -PrinterName 'TSC' -ScannerAddress ''
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { @($Body.scanners).Count -eq 0 }
        Assert-MockCalled Invoke-AgentUpdate -Times 1 -Exactly -Scope It
    }
}

Describe 'Invoke-AgentUpdate' {
    BeforeEach {
        $script:agentFile = Join-Path $tmp 'agent-live.ps1'
        Set-Content -Path $script:agentFile -Value '# старая версия' -Encoding UTF8
    }
    It 'хэш совпал и файл разбирается -> файл заменён, старый сохранён как .bak' {
        $newText = '$script:AgentVersion = ''9.9.9''' + "`r`n" + 'Write-Host ok'
        Mock Invoke-WebRequest { Set-Content -Path $OutFile -Value $newText -Encoding UTF8 }
        $probe = Join-Path $tmp 'probe.ps1'
        Set-Content -Path $probe -Value $newText -Encoding UTF8
        $hash = (Get-FileHash -Path $probe -Algorithm SHA256).Hash
        $update = [pscustomobject]@{ version = '9.9.9'; sha256 = $hash; path = '/f' }
        Invoke-AgentUpdate -Base 'http://x' -Update $update -AgentPath $script:agentFile -NoRestart | Should Be $true
        (Get-Content -Raw $script:agentFile) -match '9\.9\.9' | Should Be $true
        (Get-Content -Raw ($script:agentFile + '.bak')) -match 'старая' | Should Be $true
        Test-Path ($script:agentFile + '.new') | Should Be $false
    }
    It 'хэш не совпал -> агент не тронут, временный файл убран' {
        Mock Invoke-WebRequest { Set-Content -Path $OutFile -Value 'Write-Host evil' -Encoding UTF8 }
        $update = [pscustomobject]@{ version = '9.9.9'; sha256 = ('0' * 64); path = '/f' }
        Invoke-AgentUpdate -Base 'http://x' -Update $update -AgentPath $script:agentFile -NoRestart | Should Be $false
        (Get-Content -Raw $script:agentFile) -match 'старая' | Should Be $true
        Test-Path ($script:agentFile + '.new') | Should Be $false
    }
    It 'скачанный файл с ошибкой синтаксиса не ставится, даже если хэш верен' {
        $broken = 'function { ('
        Mock Invoke-WebRequest { Set-Content -Path $OutFile -Value $broken -Encoding UTF8 }
        $probe = Join-Path $tmp 'probe2.ps1'
        Set-Content -Path $probe -Value $broken -Encoding UTF8
        $update = [pscustomobject]@{ version = '9.9.9'; sha256 = (Get-FileHash -Path $probe -Algorithm SHA256).Hash; path = '/f' }
        Invoke-AgentUpdate -Base 'http://x' -Update $update -AgentPath $script:agentFile -NoRestart | Should Be $false
        (Get-Content -Raw $script:agentFile) -match 'старая' | Should Be $true
    }
    It 'сервер недоступен -> $false, без исключения' {
        Mock Invoke-WebRequest { throw 'нет сети' }
        $update = [pscustomobject]@{ version = '9.9.9'; sha256 = 'abc'; path = '/f' }
        Invoke-AgentUpdate -Base 'http://x' -Update $update -AgentPath $script:agentFile -NoRestart | Should Be $false
    }
}

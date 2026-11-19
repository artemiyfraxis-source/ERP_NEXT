$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here '..\erp.ps1')

function New-TestVnaOffline {
    param([string]$Root, [object[]]$Files = @())
    $null = New-Item -ItemType Directory -Path (Join-Path $Root 'files') -Force
    $instrument = [pscustomobject]@{ id = 'adapter-9'; inventory_number = 'СИ.009'; name = 'ADP1A'; serial_number = 'N8B140'; files = @($Files) }
    @{ instruments = @($instrument) } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $Root 'instruments.json') -Encoding UTF8
    return New-VnaErpSession -OfflineDirectory $Root
}

Describe 'VNA config validation' {
    BeforeEach { $configPath = Join-Path $TestDrive 'config.json' }
    It 'requires a local config' {
        { Read-VnaConfig -Path (Join-Path $TestDrive 'missing.json') } | Should Throw
    }
    It 'rejects invalid JSON' {
        '{' | Set-Content -LiteralPath $configPath -Encoding UTF8
        { Read-VnaConfig -Path $configPath } | Should Throw
    }
    It 'requires server_url and refuses saved credentials' {
        '{}' | Set-Content -LiteralPath $configPath -Encoding UTF8
        { Read-VnaConfig -Path $configPath } | Should Throw
        '{"server_url":"https://erp.test","password":"do-not-save"}' | Set-Content -LiteralPath $configPath -Encoding UTF8
        { Read-VnaConfig -Path $configPath } | Should Throw
    }
    It 'rejects HTTP, placeholders, credentials, paths and query parameters' {
        foreach ($value in @('http://erp.test', 'https://example.invalid', 'https://example.com', 'https://YOUR_SERVER', 'https://user:password@erp.test', 'https://erp.test/app', 'https://erp.test/?secret=a', 'https://erp.test/#fragment')) {
            @{ server_url = $value } | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
            { Read-VnaConfig -Path $configPath } | Should Throw
        }
    }
    It 'normalizes a valid HTTPS root address' {
        '{"server_url":"https://erp.test/"}' | Set-Content -LiteralPath $configPath -Encoding UTF8
        (Read-VnaConfig -Path $configPath).server_url | Should Be 'https://erp.test'
    }
}

Describe 'VNA ERP authentication and HTTP requests (mocked)' {
    BeforeEach {
        Mock Invoke-WebRequest { throw 'Unexpected network' }
        Mock Invoke-RestMethod { throw 'Unexpected network' }
    }
    It 'logs in once with secure password and reads the host CSRF cookie' {
        Mock Invoke-RestMethod {
            param($Uri, $WebSession)
            $cookie = New-Object Net.Cookie('__Host-erp_csrf', 'host-token', '/')
            $cookie.Secure = $true
            $WebSession.Cookies.Add([Uri]$Uri, $cookie)
            return [pscustomobject]@{ ok = $true }
        }
        $session = New-VnaErpSession -ServerUrl 'https://erp.test' -Login 'operator' -Password (ConvertTo-SecureString 'secret' -AsPlainText -Force)
        $session.Login | Should Be 'operator'
        (Get-VnaCsrfToken -Session $session) | Should Be 'host-token'
        Assert-MockCalled Invoke-RestMethod -Times 1 -Exactly -Scope It -ParameterFilter {
            $Uri -eq 'https://erp.test/api/auth/login' -and $Method -eq 'Post' -and $MaximumRedirection -eq 0 -and
            ([Text.Encoding]::UTF8.GetString($Body) | ConvertFrom-Json).login -eq 'operator' -and
            ([Text.Encoding]::UTF8.GetString($Body) | ConvertFrom-Json).password -eq 'secret'
        }
    }
    It 'refuses a successful login response without CSRF cookie' {
        Mock Invoke-RestMethod { [pscustomobject]@{ ok = $true } }
        { New-VnaErpSession -ServerUrl 'https://erp.test' -Login 'operator' -Password (ConvertTo-SecureString 'secret' -AsPlainText -Force) } | Should Throw
    }
    It 'sends POST with session cookie and current CSRF header using UTF8 JSON' {
        $web = New-Object Microsoft.PowerShell.Commands.WebRequestSession
        $web.Cookies.Add([Uri]'https://erp.test/', (New-Object Net.Cookie('erp_csrf', 'token', '/')))
        $session = [pscustomobject]@{ Offline = $false; ServerUrl = 'https://erp.test'; WebSession = $web }
        Mock Invoke-RestMethod { [pscustomobject]@{ ok = $true } }
        $null = Invoke-VnaErpRequest -Session $session -ApiPath '/api/production/instruments/1/files' -Method Post -Body @{ kind = 'Прочее' }
        Assert-MockCalled Invoke-RestMethod -Times 1 -Exactly -Scope It -ParameterFilter {
            $Method -eq 'Post' -and $Headers['X-CSRF-Token'] -eq 'token' -and $MaximumRedirection -eq 0 -and
            ([Text.Encoding]::UTF8.GetString($Body) | ConvertFrom-Json).kind -eq 'Прочее'
        }
    }
    It 'uses the real instrument file download route and session' {
        $web = New-Object Microsoft.PowerShell.Commands.WebRequestSession
        $session = [pscustomobject]@{ Offline = $false; ServerUrl = 'https://erp.test'; WebSession = $web }
        $target = Join-Path $TestDrive 'download.s2p'
        Mock Invoke-WebRequest { Set-Content -LiteralPath $OutFile -Value '# HZ S RI R 50' }
        Invoke-VnaErpRequest -Session $session -ApiPath '/api/production/instruments/1/files/2' -OutFile $target
        Assert-MockCalled Invoke-WebRequest -Times 1 -Exactly -Scope It -ParameterFilter {
            $Uri -eq 'https://erp.test/api/production/instruments/1/files/2' -and $Method -eq 'Get' -and $UseBasicParsing -and $MaximumRedirection -eq 0
        }
        Test-Path -LiteralPath $target | Should Be $true
    }
    It 'blocks transport invocation for offline sessions and unsafe API paths' {
        { Invoke-VnaErpRequest -Session ([pscustomobject]@{ Offline = $true }) -ApiPath '/api/production/instruments' } | Should Throw
        { Invoke-VnaErpRequest -Session ([pscustomobject]@{ Offline = $false }) -ApiPath 'https://other.test/api/x' } | Should Throw
        Assert-MockCalled Invoke-RestMethod -Times 0 -Exactly -Scope It
    }
}

Describe 'VNA fixture identity and local ERP mode' {
    BeforeEach {
        $root = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        $cache = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        Mock Invoke-RestMethod { throw 'Offline must not access ERP' }
        Mock Invoke-WebRequest { throw 'Offline must not access ERP' }
    }
    It 'looks up exactly the requested inventory number' {
        $session = New-TestVnaOffline -Root $root
        (Get-VnaInstrument -Session $session -Number 'си.009').serial_number | Should Be 'N8B140'
        { Get-VnaInstrument -Session $session -Number 'СИ.09' } | Should Throw
        { Get-VnaInstrument -Session $session -Number '../СИ.009' } | Should Throw
    }
    It 'requires exactly one matching instrument' {
        $session = New-TestVnaOffline -Root $root
        $path = Join-Path $root 'instruments.json'
        $database = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        $database.instruments = @($database.instruments[0], $database.instruments[0])
        $database | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $path -Encoding UTF8
        { Get-VnaInstrument -Session $session -Number 'СИ.009' } | Should Throw
    }
    It 'refuses a fixture with no measured file' {
        $session = New-TestVnaOffline -Root $root
        $instrument = Get-VnaInstrument -Session $session -Number 'СИ.009'
        { Get-VnaFixtureFile -Session $session -Instrument $instrument -CacheDirectory $cache } | Should Throw
    }
    It 'requires measured naming and full inventory or serial token' {
        $instrument = [pscustomobject]@{ inventory_number = 'СИ.009'; serial_number = 'N8B140' }
        foreach ($name in @('Замер ВАЦ, СИ.0099.s2p', 'Замер ВАЦ, N8B1400.s2p', 'Паспорт СИ.009.s2p', 'Замер ВАЦ СИ.009.txt', 'Замер ВАЦ СИ.008 N8B140.s2p')) {
            Test-VnaFixtureFileIdentity -Instrument $instrument -File ([pscustomobject]@{ file_name = $name }) | Should Be $false
        }
        foreach ($name in @('Замер ВАЦ, СИ.009.s2p', 'Замер ВАЦ N8B140.s2p')) {
            Test-VnaFixtureFileIdentity -Instrument $instrument -File ([pscustomobject]@{ file_name = $name }) | Should Be $true
        }
    }
    It 'weekly check reads only the file marked as the reference curve' {
        $session = New-TestVnaOffline -Root $root -Files @(
            [pscustomobject]@{ id = 'old'; file_name = 'Замер ВАЦ, СИ.009, 16.11.s2p'; is_reference = $false },
            [pscustomobject]@{ id = 'ref'; file_name = 'Замер ВАЦ, СИ.009, 18.11.s2p'; is_reference = $true })
        '# HZ S RI R 50' | Set-Content -LiteralPath (Join-Path (Join-Path $root 'files') 'ref.s2p') -Encoding UTF8
        $instrument = Get-VnaInstrument -Session $session -Number 'СИ.009'
        $curve = Get-VnaReferenceCurve -Session $session -Instrument $instrument -CacheDirectory $cache
        $curve.FileName | Should Be 'Замер ВАЦ, СИ.009, 18.11.s2p'
        Test-Path -LiteralPath $curve.Path | Should Be $true
    }
    It 'says the reference curve is not assigned instead of guessing by file name' {
        $session = New-TestVnaOffline -Root $root -Files @([pscustomobject]@{ id = 'file9'; file_name = 'Замер ВАЦ, СИ.009.s2p' })
        $instrument = Get-VnaInstrument -Session $session -Number 'СИ.009'
        { Get-VnaReferenceCurve -Session $session -Instrument $instrument -CacheDirectory $cache } | Should Throw 'Эталонная кривая не назначена'
    }
    It 'downloads a matching fixture once and reuses the cache' {
        $session = New-TestVnaOffline -Root $root -Files @([pscustomobject]@{ id = 'file9'; file_name = 'Замер ВАЦ, СИ.009.s2p' })
        $source = Join-Path (Join-Path $root 'files') 'file9.s2p'
        '# HZ S RI R 50' | Set-Content -LiteralPath $source -Encoding UTF8
        $instrument = Get-VnaInstrument -Session $session -Number 'СИ.009'
        $first = Get-VnaFixtureFile -Session $session -Instrument $instrument -CacheDirectory $cache
        Remove-Item -LiteralPath $source
        $second = Get-VnaFixtureFile -Session $session -Instrument $instrument -CacheDirectory $cache
        $first | Should Be $second
        Test-Path -LiteralPath $second | Should Be $true
        Assert-MockCalled Invoke-WebRequest -Times 0 -Exactly -Scope It
    }
    It 'refuses an advertised fixture whose local file is absent' {
        $session = New-TestVnaOffline -Root $root -Files @([pscustomobject]@{ id = 'absent'; file_name = 'Замер ВАЦ, СИ.009.s2p' })
        $instrument = Get-VnaInstrument -Session $session -Number 'СИ.009'
        { Get-VnaFixtureFile -Session $session -Instrument $instrument -CacheDirectory $cache } | Should Throw
    }
    It 'refuses ambiguous measured files' {
        $session = New-TestVnaOffline -Root $root -Files @(
            [pscustomobject]@{ id = 'a'; file_name = 'Замер ВАЦ, СИ.009.s2p' },
            [pscustomobject]@{ id = 'b'; file_name = 'Замер ВАЦ N8B140.s2p' }
        )
        $instrument = Get-VnaInstrument -Session $session -Number 'СИ.009'
        { Get-VnaFixtureFile -Session $session -Instrument $instrument -CacheDirectory $cache } | Should Throw
    }
    It 'blocks local file path traversal from untrusted metadata' {
        $session = New-TestVnaOffline -Root $root -Files @([pscustomobject]@{ id = '..\escape'; file_name = 'Замер ВАЦ, СИ.009.s2p' })
        $instrument = Get-VnaInstrument -Session $session -Number 'СИ.009'
        { Get-VnaFixtureFile -Session $session -Instrument $instrument -CacheDirectory $cache } | Should Throw
    }
    It 'refuses attachment and fixture lookup after the card serial changes under the same id' {
        $session = New-TestVnaOffline -Root $root -Files @([pscustomobject]@{ id = 'a'; file_name = 'Замер ВАЦ, СИ.009.s2p' })
        $instrument = Get-VnaInstrument -Session $session -Number 'СИ.009'
        $databasePath = Join-Path $root 'instruments.json'
        $database = Get-Content -LiteralPath $databasePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $database.instruments[0].serial_number = 'REPLACEMENT-SERIAL'
        $database | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $databasePath -Encoding UTF8
        $path = Join-Path $root 'result.s2p'
        'sweep' | Set-Content -LiteralPath $path -Encoding UTF8
        { Get-VnaInstrumentFiles -Session $session -Instrument $instrument } | Should Throw
        { Get-VnaFixtureFile -Session $session -Instrument $instrument -CacheDirectory $cache } | Should Throw
        { Add-VnaInstrumentFile -Session $session -Instrument $instrument -FileName 'Замер ВАЦ СИ.009.s2p' -Path $path } | Should Throw
        @(Get-ChildItem -LiteralPath (Join-Path $root 'files')).Count | Should Be 0
    }
    It 'refreshes the card before upload, refuses duplicates, and preserves old bytes after approval' {
        $session = New-TestVnaOffline -Root $root
        $instrument = Get-VnaInstrument -Session $session -Number 'СИ.009'
        $path = Join-Path $root 'result.s2p'
        'original' | Set-Content -LiteralPath $path -Encoding UTF8
        $first = Add-VnaInstrumentFile -Session $session -Instrument $instrument -FileName 'Замер ВАЦ СИ.009.s2p' -Path $path
        { Add-VnaInstrumentFile -Session $session -Instrument $instrument -FileName 'Замер ВАЦ СИ.009.s2p' -Path $path } | Should Throw
        'new' | Set-Content -LiteralPath $path -Encoding UTF8
        $second = Add-VnaInstrumentFile -Session $session -Instrument $instrument -FileName 'Замер ВАЦ СИ.009.s2p' -Path $path -AllowDuplicate
        $fresh = Get-VnaInstrument -Session $session -Number 'СИ.009'
        @($fresh.files).Count | Should Be 2
        $first.file.id | Should Not Be $second.file.id
        (Get-Content -LiteralPath (Join-Path (Join-Path $root 'files') ($first.file.id + '.s2p')) -Raw -Encoding UTF8).Trim() | Should Be 'original'
        Assert-MockCalled Invoke-RestMethod -Times 0 -Exactly -Scope It
    }
    It 'posts an attachment with the production schema, without deleting old files' {
        $session = [pscustomobject]@{ Offline = $false; ServerUrl = 'https://erp.test' }
        $instrument = [pscustomobject]@{ id = 'adapter-9'; inventory_number = 'СИ.009' }
        Mock Get-VnaInstrumentFiles { @() }
        Mock Invoke-VnaErpRequest { [pscustomobject]@{ file = [pscustomobject]@{ id = 'created' } } }
        $path = Join-Path $root 'result.s2p'
        $null = New-Item -ItemType Directory -Force -Path $root
        'sweep' | Set-Content -LiteralPath $path -Encoding UTF8
        $result = Add-VnaInstrumentFile -Session $session -Instrument $instrument -FileName 'Замер ВАЦ СИ.009.s2p' -Path $path
        $result.file.id | Should Be 'created'
        Assert-MockCalled Invoke-VnaErpRequest -Times 1 -Exactly -Scope It -ParameterFilter {
            $ApiPath -eq '/api/production/instruments/adapter-9/files' -and $Method -eq 'Post' -and
            $Body.kind -eq 'Прочее' -and $Body.file_name -eq 'Замер ВАЦ СИ.009.s2p' -and $Body.content_base64.Length -gt 0
        }
    }
}

# ERP transport for Windows PowerShell 5.1. Dot-source; no network or prompts on import.

function Assert-VnaServerUrl {
    param([Parameter(Mandatory = $true)][string]$ServerUrl)
    $uri = $null
    if (-not [Uri]::TryCreate($ServerUrl.Trim(), [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -ne 'https' -or [string]::IsNullOrWhiteSpace($uri.Host) -or
        $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/' -or
        $uri.Host -match '(?i)(^|\.)(example\.(com|org|net)|invalid)$|[<>]|change[-_]?me|your[-_]?|placeholder') {
        throw 'Укажите в config.json настоящий корневой HTTPS-адрес ERP без логина, пароля и параметров.'
    }
    return $uri.AbsoluteUri.TrimEnd('/')
}

function Read-VnaConfig {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw 'Нет config.json. Создайте файл с полем server_url по инструкции README.'
    }
    try { $config = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw 'Некорректный JSON в config.json.' }
    if (-not $config -or -not ($config.PSObject.Properties.Name -contains 'server_url') -or
        -not ($config.server_url -is [string])) { throw 'В config.json необходимо строковое поле server_url.' }
    $allowed = @('server_url')
    foreach ($property in $config.PSObject.Properties.Name) {
        if ($property -notin $allowed) { throw "Недопустимое поле config.json: $property. Учётные данные в файле не сохраняются." }
    }
    $config.server_url = Assert-VnaServerUrl -ServerUrl $config.server_url
    return $config
}

function New-VnaErpSession {
    [CmdletBinding(DefaultParameterSetName = 'Online')]
    param(
        [Parameter(Mandatory = $true, ParameterSetName = 'Online')][string]$ServerUrl,
        [Parameter(Mandatory = $true, ParameterSetName = 'Online')][string]$Login,
        [Parameter(Mandatory = $true, ParameterSetName = 'Online')][Security.SecureString]$Password,
        [Parameter(Mandatory = $true, ParameterSetName = 'Offline')][string]$OfflineDirectory
    )
    if ($PSCmdlet.ParameterSetName -eq 'Offline') {
        $root = [IO.Path]::GetFullPath($OfflineDirectory)
        if (-not (Test-Path -LiteralPath (Join-Path $root 'instruments.json') -PathType Leaf)) {
            throw 'В папке Offline нет instruments.json.'
        }
        return [pscustomobject]@{ Offline = $true; OfflineDirectory = $root; Login = 'offline'; ServerUrl = ''; WebSession = $null }
    }
    $base = Assert-VnaServerUrl -ServerUrl $ServerUrl
    if ([string]::IsNullOrWhiteSpace($Login)) { throw 'Не указан логин ERP.' }
    $webSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $passwordPointer = [IntPtr]::Zero
    $plainPassword = $null
    $json = $null
    try {
        $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
        $json = @{ login = $Login; password = $plainPassword } | ConvertTo-Json -Compress
        # Redirects are refused so credentials cannot be forwarded to a different origin.
        $null = Invoke-RestMethod -Uri ($base + '/api/auth/login') -Method Post -WebSession $webSession `
            -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($json)) `
            -TimeoutSec 120 -MaximumRedirection 0 -ErrorAction Stop
    }
    catch { throw 'Вход в ERP не выполнен. Проверьте адрес, учётные данные и доступность сервера.' }
    finally {
        if ($passwordPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer) }
        $plainPassword = $null
        $json = $null
    }
    $session = [pscustomobject]@{ Offline = $false; OfflineDirectory = ''; Login = $Login; ServerUrl = $base; WebSession = $webSession }
    $null = Get-VnaCsrfToken -Session $session
    return $session
}

function Get-VnaCsrfToken {
    param([Parameter(Mandatory = $true)]$Session)
    $cookies = $Session.WebSession.Cookies.GetCookies([Uri]($Session.ServerUrl + '/'))
    foreach ($name in '__Host-erp_csrf', 'erp_csrf') {
        $cookie = $cookies[$name]
        if ($cookie -and -not [string]::IsNullOrWhiteSpace($cookie.Value)) { return $cookie.Value }
    }
    throw 'ERP не выдал CSRF-cookie; повторите вход.'
}

function Invoke-VnaErpRequest {
    param(
        [Parameter(Mandatory = $true)]$Session,
        [Parameter(Mandatory = $true)][string]$ApiPath,
        [ValidateSet('Get', 'Post')][string]$Method = 'Get',
        $Body,
        [string]$OutFile
    )
    if ($Session.Offline) { throw 'Сетевой запрос запрещён в Offline.' }
    if (-not $ApiPath.StartsWith('/api/') -or $ApiPath.Contains('..') -or $ApiPath.Contains('?') -or $ApiPath.Contains('#')) {
        throw 'Недопустимый путь ERP API.'
    }
    $parameters = @{
        Uri = $Session.ServerUrl + $ApiPath; Method = $Method; WebSession = $Session.WebSession
        TimeoutSec = 120; MaximumRedirection = 0; ErrorAction = 'Stop'
    }
    if ($Method -eq 'Post') {
        $parameters.Headers = @{ 'X-CSRF-Token' = (Get-VnaCsrfToken -Session $Session) }
        $parameters.ContentType = 'application/json; charset=utf-8'
        $parameters.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 20 -Compress))
    }
    if ($OutFile) {
        $parameters.OutFile = $OutFile
        $parameters.UseBasicParsing = $true
        $null = Invoke-WebRequest @parameters
        return
    }
    return Invoke-RestMethod @parameters
}

function Get-VnaInstrumentList {
    param([Parameter(Mandatory = $true)]$Session)
    if ($Session.Offline) {
        $response = Get-Content -LiteralPath (Join-Path $Session.OfflineDirectory 'instruments.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    else { $response = Invoke-VnaErpRequest -Session $Session -ApiPath '/api/production/instruments' }
    if (-not $response -or -not ($response.PSObject.Properties.Name -contains 'instruments')) {
        throw 'ERP не вернул список instruments.'
    }
    return @($response.instruments)
}

function Get-VnaInstrument {
    param([Parameter(Mandatory = $true)]$Session, [Parameter(Mandatory = $true)][string]$Number)
    $numberText = $Number.Trim().ToUpperInvariant()
    if ($numberText -notmatch '^СИ\.\d+$') { throw 'Номер должен иметь вид СИ.035.' }
    $matches = @(Get-VnaInstrumentList -Session $Session | Where-Object { $_.inventory_number -eq $numberText })
    if ($matches.Count -ne 1) { throw "По номеру $numberText найдено карточек: $($matches.Count). Нужна ровно одна." }
    if ([string]::IsNullOrWhiteSpace([string]$matches[0].id)) { throw 'У карточки СИ отсутствует id.' }
    return $matches[0]
}

function Get-VnaInstrumentFiles {
    param([Parameter(Mandatory = $true)]$Session, [Parameter(Mandatory = $true)]$Instrument)
    $fresh = Get-VnaInstrument -Session $Session -Number $Instrument.inventory_number
    if ([string]$fresh.id -ne [string]$Instrument.id -or [string]$fresh.serial_number -cne [string]$Instrument.serial_number) {
        throw 'Карточка СИ или её серийный номер изменились. Начните замер заново.'
    }
    return @($fresh.files | Where-Object { $null -ne $_ })
}

function Test-VnaFixtureFileIdentity {
    param([Parameter(Mandatory = $true)]$Instrument, [Parameter(Mandatory = $true)]$File)
    $name = [string]$File.file_name
    if ($name -notmatch '(?i)Замер\s+ВАЦ' -or [IO.Path]::GetExtension($name) -ine '.s2p') { return $false }
    $numbers = @([regex]::Matches($name, '(?i)СИ\.\d+') | ForEach-Object { $_.Value })
    # A file explicitly labelled with another inventory number cannot be borrowed by serial.
    if ($numbers.Count -gt 0 -and ($numbers -notcontains [string]$Instrument.inventory_number -or $numbers.Count -ne 1)) { return $false }
    foreach ($identity in @([string]$Instrument.inventory_number, [string]$Instrument.serial_number)) {
        if ([string]::IsNullOrWhiteSpace($identity)) { continue }
        $pattern = '(?i)(?<![\p{L}\p{N}])' + [regex]::Escape($identity.Trim()) + '(?![\p{L}\p{N}])'
        if ($name -match $pattern) { return $true }
    }
    return $false
}

function Get-VnaFixtureFile {
    param(
        [Parameter(Mandatory = $true)]$Session,
        [Parameter(Mandatory = $true)]$Instrument,
        [Parameter(Mandatory = $true)][string]$CacheDirectory
    )
    $files = @(Get-VnaInstrumentFiles -Session $Session -Instrument $Instrument | Where-Object {
        Test-VnaFixtureFileIdentity -Instrument $Instrument -File $_
    })
    if ($files.Count -eq 0) { throw "У $($Instrument.inventory_number) нет собственного файла «Замер ВАЦ» с правильным номером или серийным номером." }
    if ($files.Count -gt 1) { throw "У $($Instrument.inventory_number) несколько подходящих замеров. Оставьте однозначный набор файлов в локальной копии или уточните файл в ERP." }
    $file = $files[0]
    if ([string]::IsNullOrWhiteSpace([string]$file.id)) { throw 'У файла переходника отсутствует id.' }
    $source = $(if ($Session.Offline) { $Session.OfflineDirectory } else { $Session.ServerUrl })
    $key = $source + '|' + [string]$Instrument.id + '|' + [string]$file.id
    $hashAlgorithm = [Security.Cryptography.SHA256]::Create()
    try { $hash = ([BitConverter]::ToString($hashAlgorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($key)))).Replace('-', '').ToLowerInvariant() }
    finally { $hashAlgorithm.Dispose() }
    $null = New-Item -ItemType Directory -Force -Path $CacheDirectory
    $cached = Join-Path $CacheDirectory ($hash + '.s2p')
    if (-not (Test-Path -LiteralPath $cached -PathType Leaf)) {
        $temporary = $cached + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
        try {
            if ($Session.Offline) {
                if ([string]$file.id -notmatch '^[A-Za-z0-9_-]+$') { throw 'Недопустимый id локального файла.' }
                $sourceFile = Join-Path (Join-Path $Session.OfflineDirectory 'files') ([string]$file.id + '.s2p')
                if (-not (Test-Path -LiteralPath $sourceFile -PathType Leaf)) { throw "Нет локального файла $($file.id).s2p." }
                Copy-Item -LiteralPath $sourceFile -Destination $temporary -ErrorAction Stop
            }
            else {
                $instrumentId = [Uri]::EscapeDataString([string]$Instrument.id)
                $fileId = [Uri]::EscapeDataString([string]$file.id)
                Invoke-VnaErpRequest -Session $Session -ApiPath "/api/production/instruments/$instrumentId/files/$fileId" -OutFile $temporary
            }
            if (-not (Test-Path -LiteralPath $temporary -PathType Leaf) -or (Get-Item -LiteralPath $temporary).Length -eq 0) {
                throw 'ERP вернул пустой файл переходника.'
            }
            Move-Item -LiteralPath $temporary -Destination $cached -ErrorAction Stop
        }
        finally { if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -ErrorAction Stop } }
    }
    return $cached
}

function Add-VnaInstrumentFile {
    param(
        [Parameter(Mandatory = $true)]$Session,
        [Parameter(Mandatory = $true)]$Instrument,
        [Parameter(Mandatory = $true)][string]$FileName,
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$AllowDuplicate
    )
    if ([string]::IsNullOrWhiteSpace($FileName) -or $FileName -match '[\r\n/\\]' -or [IO.Path]::GetExtension($FileName) -ine '.s2p') {
        throw 'Недопустимое имя файла замера.'
    }
    $files = @(Get-VnaInstrumentFiles -Session $Session -Instrument $Instrument)
    if (-not $AllowDuplicate -and @($files | Where-Object { $_.file_name -eq $FileName }).Count -gt 0) {
        throw 'В карточке уже есть файл с таким именем. Требуется подтверждение повторного вложения.'
    }
    $content = [IO.File]::ReadAllBytes([IO.Path]::GetFullPath($Path))
    if ($content.Length -lt 1 -or $content.Length -gt 20MB) { throw 'Файл замера должен занимать от 1 байта до 20 МиБ.' }
    if ($Session.Offline) {
        $id = [guid]::NewGuid().ToString()
        $file = [pscustomobject]@{ id = $id; kind = 'Прочее'; file_name = $FileName; size = $content.Length; uploaded_at = [DateTime]::UtcNow.ToString('o'); uploaded_by_name = $Session.Login }
        $databasePath = Join-Path $Session.OfflineDirectory 'instruments.json'
        $database = Get-Content -LiteralPath $databasePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $targets = @($database.instruments | Where-Object { [string]$_.id -eq [string]$Instrument.id })
        if ($targets.Count -ne 1) { throw 'Не найдена однозначная локальная карточка.' }
        $filesDirectory = Join-Path $Session.OfflineDirectory 'files'
        $null = New-Item -ItemType Directory -Force -Path $filesDirectory
        [IO.File]::WriteAllBytes((Join-Path $filesDirectory ($id + '.s2p')), $content)
        $targets[0] | Add-Member -MemberType NoteProperty -Name files -Value @($files + $file) -Force
        $temporaryDatabase = $databasePath + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
        try {
            [IO.File]::WriteAllText($temporaryDatabase, ($database | ConvertTo-Json -Depth 30), (New-Object Text.UTF8Encoding($false)))
            Move-Item -LiteralPath $temporaryDatabase -Destination $databasePath -Force -ErrorAction Stop
        }
        finally { if (Test-Path -LiteralPath $temporaryDatabase -PathType Leaf) { Remove-Item -LiteralPath $temporaryDatabase -ErrorAction Stop } }
        return [pscustomobject]@{ file = $file }
    }
    $instrumentId = [Uri]::EscapeDataString([string]$Instrument.id)
    # POST appends; existing attachment bytes are never deleted or replaced.
    return Invoke-VnaErpRequest -Session $Session -ApiPath "/api/production/instruments/$instrumentId/files" -Method Post `
        -Body @{ kind = 'Прочее'; file_name = $FileName; content_base64 = [Convert]::ToBase64String($content) }
}

<#
 Агент печати этикеток Fraxis ERP (TSC по USB, RAW TSPL через очередь Windows).
 Каждые 3 с забирает задание у сервера, отправляет байты в очередь, сообщает результат.
 Параметры: -DryRun (не печатать, результат ok=false "dry run"), -Once (один опрос и выход).
 Сканер УПД: -Scanner <адрес> (eSCL; без адреса - WIA), -TestScan (один скан в файл, без ERP).
 Сканер с любого ПК (28.10.2026): агент называет себя серверу (MachineGuid + имя ПК), раз в ~2 мин сообщает версию, найденные
 WIA-сканеры (подключили позже - подхватится сам) и адрес в сети; принтер не обязателен.
 Свой сканер (28.10.2026): задание приходит с выбранным человеком сканером (WIA DeviceID или имя) - сканирует он;
 его нет на этом ПК - ошибка без подмены; без выбора WIA допускает только единственное устройство.
 Новая версия агента лежит на сервере: агент скачивает её, сверяет SHA-256, подменяет себя и перезапускает задачу.
#>
[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $env:ProgramData 'FraxisPrintAgent\config.json'),
    [string]$LogPath = (Join-Path $env:ProgramData 'FraxisPrintAgent\agent.log'),
    [string]$Server,
    [string]$Token,
    [string]$Printer,
    [string]$Scanner,
    [switch]$DryRun,
    [switch]$Once,
    [switch]$TestScan
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$script:LogMaxBytes = 1MB
# Версия агента: сервер читает эту строку из файла и сравнивает с тем, что агент сообщил (поднять число при каждом изменении).
$script:AgentVersion = '2.7.0'
$script:VnaIdnNextProbe = [datetime]::MinValue
$script:TaskName = 'Fraxis — печать этикеток'
$script:HeartbeatEverySec = 120

function Write-Log {
    param([string]$Message)
    try {
        $dir = Split-Path -Parent $LogPath
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        if ((Test-Path $LogPath) -and ((Get-Item $LogPath).Length -gt $script:LogMaxBytes)) {
            $old = $LogPath + '.1'
            if (Test-Path $old) { Remove-Item $old -Force }
            Move-Item $LogPath $old -Force
        }
        $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
        [IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine, (New-Object Text.UTF8Encoding($true)))
    } catch {}
}

function Read-AgentConfig {
    param([string]$Path)
    $cfg = @{ server = ''; token = ''; printer = ''; scanner = ''; arinstFolders = $null }
    if ($Path -and (Test-Path $Path)) {
        $raw = Get-Content -Raw -Encoding UTF8 $Path | ConvertFrom-Json
        if ($raw.server) { $cfg.server = [string]$raw.server }
        if ($raw.printer) { $cfg.printer = [string]$raw.printer }
        if ($raw.scanner) { $cfg.scanner = [string]$raw.scanner }
        if ($null -ne $raw.arinstFolders) { $cfg.arinstFolders = @($raw.arinstFolders) }
        if ($raw.tokenProtected) {
            # DPAPI: расшифровывается только тем пользователем Windows, который ставил агент
            $sec = ConvertTo-SecureString -String $raw.tokenProtected
            $cfg.token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
        } elseif ($raw.token) { $cfg.token = [string]$raw.token }
    }
    return $cfg
}

function Initialize-RawPrinter {
    if ('ErpRawPrinter' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;

public static class ErpRawPrinter {
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  public class DOCINFO {
    [MarshalAs(UnmanagedType.LPWStr)] public string pDocName;
    [MarshalAs(UnmanagedType.LPWStr)] public string pOutputFile;
    [MarshalAs(UnmanagedType.LPWStr)] public string pDataType;
  }

  [DllImport("winspool.drv", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool OpenPrinter(string src, out IntPtr hPrinter, IntPtr pd);
  [DllImport("winspool.drv", SetLastError = true)]
  public static extern bool ClosePrinter(IntPtr hPrinter);
  [DllImport("winspool.drv", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool StartDocPrinter(IntPtr hPrinter, int level, [In, MarshalAs(UnmanagedType.LPStruct)] DOCINFO di);
  [DllImport("winspool.drv", SetLastError = true)]
  public static extern bool EndDocPrinter(IntPtr hPrinter);
  [DllImport("winspool.drv", SetLastError = true)]
  public static extern bool StartPagePrinter(IntPtr hPrinter);
  [DllImport("winspool.drv", SetLastError = true)]
  public static extern bool EndPagePrinter(IntPtr hPrinter);
  [DllImport("winspool.drv", SetLastError = true)]
  public static extern bool WritePrinter(IntPtr hPrinter, IntPtr pBytes, int dwCount, out int dwWritten);

  static string Err() {
    int code = Marshal.GetLastWin32Error();
    return code + " (" + new System.ComponentModel.Win32Exception(code).Message + ")";
  }

  public static void Send(string printer, byte[] bytes) {
    IntPtr handle;
    if (!OpenPrinter(printer, out handle, IntPtr.Zero))
      throw new Exception("Принтер не открывается: " + Err());
    try {
      DOCINFO info = new DOCINFO();
      info.pDocName = "ERP label";
      info.pDataType = "RAW";
      if (!StartDocPrinter(handle, 1, info))
        throw new Exception("Очередь не приняла задание: " + Err());
      try {
        if (!StartPagePrinter(handle))
          throw new Exception("Страница не началась: " + Err());
        IntPtr buffer = Marshal.AllocCoTaskMem(bytes.Length);
        try {
          Marshal.Copy(bytes, 0, buffer, bytes.Length);
          int written;
          if (!WritePrinter(handle, buffer, bytes.Length, out written))
            throw new Exception("Байты не записались: " + Err());
          if (written != bytes.Length)
            throw new Exception("Записано " + written + " из " + bytes.Length + " байт");
        } finally { Marshal.FreeCoTaskMem(buffer); }
        EndPagePrinter(handle);
      } finally { EndDocPrinter(handle); }
    } finally { ClosePrinter(handle); }
  }
}
'@
}

# Отправка байтов в очередь. Вынесено в функцию, чтобы тесты подменяли её.
function Send-RawToPrinter {
    param([string]$PrinterName, [byte[]]$Bytes)
    Initialize-RawPrinter
    [ErpRawPrinter]::Send($PrinterName, $Bytes)
}

function Invoke-ErpApi {
    param([string]$Url, [hashtable]$Body, [string]$AuthToken, [string]$MachineId)
    $json = $Body | ConvertTo-Json -Compress -Depth 5
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    $headers = @{ Authorization = "Bearer $AuthToken" }
    if ($MachineId) { $headers['X-Agent-Machine-Id'] = $MachineId }
    Invoke-RestMethod -Method Post -Uri $Url -Body $bytes `
        -ContentType 'application/json; charset=utf-8' `
        -Headers $headers -TimeoutSec 20
}

function Send-JobResult {
    param([string]$Base, [string]$AuthToken, [string]$JobId, [bool]$Ok, [string]$ErrorText)
    $body = @{ ok = $Ok; error = $ErrorText }
    Invoke-ErpApi -Url "$Base/api/stock/labels/agent/jobs/$JobId/result" -Body $body -AuthToken $AuthToken | Out-Null
}

# Один опрос. Возвращает 'idle' (заданий нет) или 'done' (задание обработано).
# Сетевые ошибки claim/result пробрасываются наверх — их обрабатывает цикл.
function Invoke-PollOnce {
    param([string]$Base, [string]$AuthToken, [string]$PrinterName, [switch]$DryRun)
    $claim = Invoke-ErpApi -Url "$Base/api/stock/labels/agent/claim" `
        -Body @{ printer = $PrinterName; host = $env:COMPUTERNAME } -AuthToken $AuthToken
    $job = $claim.job
    if (-not $job) { return 'idle' }
    $id = [string]$job.id
    $ok = $false; $err = ''
    try {
        $bytes = [Convert]::FromBase64String([string]$job.tspl_b64)
        if ($DryRun) {
            Write-Log "DRY RUN: задание $id, копий $($job.copies), $($bytes.Length) байт, принтер '$PrinterName' - печать пропущена"
            $err = 'dry run'
        } else {
            Send-RawToPrinter -PrinterName $PrinterName -Bytes $bytes
            $ok = $true
            Write-Log "Напечатано: задание $id, копий $($job.copies), $($bytes.Length) байт"
        }
    } catch {
        $err = $_.Exception.Message
        Write-Log "Ошибка печати задания ${id}: $err"
    }
    Send-JobResult -Base $Base -AuthToken $AuthToken -JobId $id -Ok $ok -ErrorText $err
    return 'done'
}

# ---------------------------------------------------------------- Реестр агентов (сканер с любого ПК)

# Постоянный номер ПК: MachineGuid Windows (не меняется при смене сети, имени, пользователя).
function Get-MachineId {
    try {
        $guid = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid
        if ($guid) { return ([string]$guid).ToLower() }
    } catch {}
    return ([string]$env:COMPUTERNAME).ToLower()
}

# Адрес ПК в локальной сети: интерфейс со шлюзом по умолчанию; запасной путь - DNS-имя ПК.
function Get-LanIp {
    try {
        $cfg = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1
        if ($cfg -and $cfg.IPv4Address) { return [string]$cfg.IPv4Address[0].IPAddress }
    } catch {}
    try {
        $ip = [Net.Dns]::GetHostAddresses($env:COMPUTERNAME) | Where-Object { $_.AddressFamily -eq 'InterNetwork' } | Select-Object -First 1
        if ($ip) { return $ip.IPAddressToString }
    } catch {}
    return ''
}

# WIA-сканеры, которые Windows видит сейчас (тип 1). Нет сканеров - пустой список. Вынесено в функцию для тестов.
function Get-WiaScanners {
    $found = @()
    try {
        $dm = New-Object -ComObject WIA.DeviceManager
        foreach ($di in $dm.DeviceInfos) {
            if ($di.Type -ne 1) { continue }
            $name = ''
            try { $name = [string]$di.Properties.Item('Name').Value } catch {}
            if (-not $name) { $name = 'Сканер' }
            $found += @{ id = [string]$di.DeviceID; name = $name }
        }
    } catch {}
    return $found
}

# Сканеры агента: все WIA-устройства и настроенный адрес eSCL. Выбор в задании задаёт способ сканирования.
function Get-AgentScanners {
    param([string]$ScannerAddress)
    Get-WiaScanners
    if ($ScannerAddress) { @{ id = $ScannerAddress; name = "Сканер по сети $ScannerAddress (eSCL)" } }
}

# ProgID lookup never activates S2VNA. These boundaries are mocked in the tests.
function Test-VnaRegistered { return $null -ne [Type]::GetTypeFromProgID('S2VNA.Application') }
function Get-VnaApplication {
    param([switch]$RunningOnly)
    # S2VNA does not publish itself in ROT. Its COM class attaches to the existing application.
    # Check here as well as in the capability probe: heartbeat must not activate a stopped program.
    if ($RunningOnly -and -not (Get-Process -Name S2VNA -ErrorAction SilentlyContinue)) {
        throw 'S2VNA не запущена'
    }
    return New-Object -ComObject 'S2VNA.Application'
}
function Release-VnaApplication {
    param($Application)
    if ($null -ne $Application -and [Runtime.InteropServices.Marshal]::IsComObject($Application)) {
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($Application)
    }
}
function Get-CachedVnaName {
    $path = Join-Path (Split-Path -Parent ([IO.Path]::GetFullPath($ConfigPath))) 'vna-idn.txt'
    try {
        if (Test-Path -LiteralPath $path) {
            $name = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8).Trim()
            if ($name -and $name.Length -le 500 -and $name -notmatch '[\r\n]') { return $name }
        }
    } catch {
        Write-Log "ВАЦ: не удалось прочитать сохранённое имя прибора: $($_.Exception.Message)"
    }
    return 'S2VNA'
}
function Save-VnaName {
    param([string]$Name)
    try {
        $dir = Split-Path -Parent ([IO.Path]::GetFullPath($ConfigPath))
        [void][IO.Directory]::CreateDirectory($dir)
        [IO.File]::WriteAllText((Join-Path $dir 'vna-idn.txt'), $Name, [Text.Encoding]::UTF8)
    } catch {
        # A local state error must not discard a valid captured trace.
        Write-Log "ВАЦ: не удалось сохранить имя прибора: $($_.Exception.Message)"
    }
}
function Get-VnaCapability {
    if (-not (Test-VnaRegistered)) { return $null }
    $name = Get-CachedVnaName
    # Heartbeat attaches only while S2VNA is running; it never intentionally starts the application.
    if (-not (Get-Process -Name S2VNA -ErrorAction SilentlyContinue)) {
        return @{ name = $name; ready = $false; reason = 's2vna_not_running' }
    }
    try {
        $devices = @(Get-PnpDevice -PresentOnly -ErrorAction Stop | Where-Object {
            $_.InstanceId -match '^USB\\VID_2226&' -and $_.Status -eq 'OK'
        })
    } catch {
        Write-Log "ВАЦ: не удалось проверить подключение USB: $($_.Exception.Message)"
        return @{ name = $name; ready = $false; reason = 'probe_failed' }
    }
    if (-not $devices.Count) { return @{ name = $name; ready = $false; reason = 'device_not_connected' } }
    $now = Get-Date
    if ($now -ge $script:VnaIdnNextProbe) {
        # Throttle failed attaches too; COM must not be probed every 2 minutes.
        $script:VnaIdnNextProbe = $now.AddMinutes(10)
        $application = $null
        try {
            $application = Get-VnaApplication -RunningOnly
            $idn = ([string]$application.Name).Trim()
            if ($idn.Length -le 500 -and $idn -notmatch '[\r\n]' -and $idn -match '^[^,]+,[^,]+,\s*[^,\s][^,]*,[^,]+$') {
                Save-VnaName -Name $idn
                $name = $idn
            }
        } catch {
            Write-Log "ВАЦ: имя работающего прибора пока не прочитано, используется сохранённое: $($_.Exception.Message)"
        } finally { Release-VnaApplication $application }
    }
    return @{ name = $name; ready = $true; reason = '' }
}

function Test-ArinstInstalled {
    if (-not $env:APPDATA) { return $false }
    return @(Get-ChildItem -Path (Join-Path $env:APPDATA 'ArinstVL*\ArinstVL.exe') -File -ErrorAction SilentlyContinue).Count -gt 0
}

function Get-ArinstFolders {
    $cfg = Read-AgentConfig -Path $ConfigPath
    if ($null -ne $cfg.arinstFolders) { return $cfg.arinstFolders }
    $profilePath = [Environment]::GetFolderPath('UserProfile')
    if (-not $profilePath) { $profilePath = $env:USERPROFILE }
    if (-not $profilePath) { throw 'Не удалось определить папки пользователя — задайте arinstFolders в настройках агента' }
    $documents = [Environment]::GetFolderPath('MyDocuments')
    $desktop = [Environment]::GetFolderPath('Desktop')
    if (-not $documents) { $documents = Join-Path $profilePath 'Documents' }
    if (-not $desktop) { $desktop = Join-Path $profilePath 'Desktop' }
    return @($documents, $desktop, (Join-Path $profilePath 'Downloads'))
}

function Invoke-ArinstCapture {
    $now = [DateTime]::UtcNow
    try {
        $files = @(foreach ($folder in (Get-ArinstFolders)) {
            if (-not $folder -or -not (Test-Path -LiteralPath $folder -PathType Container)) { continue }
            # One child level only. Do not follow junctions into unrelated trees.
            $dirs = @($folder) + @(Get-ChildItem -LiteralPath $folder -Directory -ErrorAction Stop |
                Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
                ForEach-Object { $_.FullName })
            foreach ($dir in $dirs) {
                Get-ChildItem -LiteralPath $dir -File -ErrorAction Stop | Where-Object {
                    $_.Extension -in @('.s1p', '.s2p') -and
                    -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
                    $_.LastWriteTimeUtc -ge $now.AddMinutes(-15) -and $_.LastWriteTimeUtc -le $now
                }
            }
        })
    } catch {
        Write-Log "Arinst: ошибка поиска сохранённой трассы: $($_.Exception.Message)"
        throw 'Не удалось найти файл Arinst — проверьте arinstFolders и доступ к папкам в настройках агента'
    }
    $file = $files | Sort-Object -Property @{ Expression = 'LastWriteTimeUtc'; Descending = $true }, FullName | Select-Object -First 1
    if (-not $file) { throw 'Сохраните трассу в Arinst Virtual Lab (.s1p) и повторите — свежих файлов за 15 минут нет' }
    # Bound the read even if the file grows after enumeration; deny writes while taking this snapshot.
    $stream = $null
    try {
        $stream = [IO.File]::Open($file.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        if ($stream.Length -gt 2MB) { throw 'Файл ВАЦ больше 2 МиБ' }
        [byte[]]$bytes = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $read = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($read -eq 0) { throw 'Не удалось прочитать файл ВАЦ — сохраните трассу повторно' }
            $offset += $read
        }
        return @{ Bytes = $bytes; FileName = $file.Name; Ext = $file.Extension.TrimStart('.'); Mime = 'application/octet-stream'; Name = 'Arinst VNA-DL' }
    } catch {
        if ($_.Exception.Message -match '^(Файл ВАЦ|Не удалось прочитать)') { throw }
        throw 'Не удалось прочитать файл ВАЦ — проверьте доступ к сохранённой трассе Arinst'
    } finally { if ($stream) { $stream.Dispose() } }
}

function Get-VnaSettings {
    param($Scpi, $Calculation)
    # S2VNA manual part 2: COM getters, pp. 1123-1125, 1420-1424,
    # 1696, 1700, 1703, 1970, 2015, 2017, 2079. Never write these properties.
    $readers = [ordered]@{
        points = { $Scpi.SENSe(1).SWEep.POINts }
        start_hz = { $Scpi.SENSe(1).FREQuency.STARt }
        stop_hz = { $Scpi.SENSe(1).FREQuency.STOP }
        if_bandwidth_hz = { $Scpi.SENSe(1).BANDwidth.RESolution }
        averaging_enabled = { $Scpi.SENSe(1).AVERage.STATe }
        averaging_factor = { $Scpi.SENSe(1).AVERage.COUNt }
        smoothing_enabled = { $Calculation.SELected.SMOothing.STATe }
        smoothing_aperture_percent = { $Calculation.SELected.SMOothing.APERture }
        correction_enabled = { $Scpi.SENSe(1).CORRection.STATe }
        correction_status = { $Calculation.SELected.CORRection.STATus }
    }
    $settings = [ordered]@{}
    $unavailable = @()
    foreach ($key in $readers.Keys) {
        try {
            $value = & $readers[$key]
            if ($null -eq $value) { throw 'Property unavailable' }
            if ($key -like '*_enabled') {
                if ($value -isnot [bool] -and [string]$value -notin @('0', '1')) { throw 'Invalid Boolean' }
                $value = [bool][int]$value
            } elseif ($key -eq 'correction_status') {
                $value = ([string]$value).Trim().ToUpperInvariant()
                if ($value -notin @('NONE', 'COR', 'C?', 'C!', 'RC', 'RC?', 'RC!')) { throw 'Unknown correction status' }
            } else {
                $value = [double]$value
                if ([double]::IsNaN($value) -or [double]::IsInfinity($value) -or $value -lt 0) { throw 'Invalid setting' }
                if ($key -in @('points', 'averaging_factor')) {
                    if ($value -lt 1 -or $value -ne [Math]::Truncate($value)) { throw 'Invalid count' }
                    $value = [int]$value
                }
            }
            $settings[$key] = $value
        } catch {
            # Optional metadata must never discard a captured trace or turn an unknown state into OFF.
            $unavailable += $key
        }
    }
    if ($unavailable.Count) { $settings['unavailable'] = @($unavailable) }
    return $settings
}

function Get-VnaSnapshot {
    param($Application)
    $scpi = $Application.SCPI
    # Channel 1, selected trace. SERVICE is read-only; PARameter(1) is not necessarily the selected trace.
    $trace = [int]$scpi.SERVice.CHANnel(1).TRACe(1).ACTive
    if ($trace -lt 1) { throw 'ВАЦ: активная трасса не определена' }
    $calc = $scpi.CALCulate(1)
    $parameter = ([string]$calc.PARameter($trace).DEFine).Trim().ToUpperInvariant()
    if ($parameter -notin @('S11', 'S22')) { throw "Активная трасса — ${parameter}: выберите S11 или S22 в S2VNA" }
    $settings = Get-VnaSettings -Scpi $scpi -Calculation $calc
    $frequencies = @($scpi.SENSe(1).FREQuency.DATA)
    $data = @($calc.SELected.DATA.SDATa)
    $after = @($scpi.SENSe(1).FREQuency.DATA)
    $settingsAfter = Get-VnaSettings -Scpi $scpi -Calculation $calc
    if ([int]$scpi.SERVice.CHANnel(1).TRACe(1).ACTive -ne $trace -or
        ([string]$calc.PARameter($trace).DEFine).Trim().ToUpperInvariant() -ne $parameter -or
        ($frequencies -join '|') -ne ($after -join '|') -or
        (ConvertTo-Json -InputObject $settings -Compress) -ne (ConvertTo-Json -InputObject $settingsAfter -Compress)) {
        throw 'ВАЦ: настройки изменились во время чтения — повторите снятие трассы'
    }
    $settings['s_parameter'] = $parameter
    return @{ Name = [string]$Application.Name; Parameter = $parameter; Frequencies = $frequencies; Data = $data; Settings = $settings }
}

function Invoke-VnaCapture {
    $app = $null
    try {
        try { $app = Get-VnaApplication } catch { throw 'ВАЦ: не удалось подключиться к S2VNA — проверьте установку программы' }
        if (-not $app.Ready) { throw 'ВАЦ не готов — подключите прибор и дождитесь готовности S2VNA' }
        try { $trace = Get-VnaSnapshot -Application $app }
        catch {
            if ($_.Exception.Message -match '^(ВАЦ|Активная трасса)') { throw }
            throw 'ВАЦ: не удалось прочитать трассу S2VNA — проверьте канал 1 и повторите'
        }
        $frequencies = @($trace.Frequencies); $data = @($trace.Data)
        if ($frequencies.Count -lt 2 -or $data.Count -ne 2 * $frequencies.Count) { throw 'ВАЦ: число частот и точек трассы не совпадает' }
        if ($frequencies.Count -gt 100000) { throw 'Файл ВАЦ больше 2 МиБ — уменьшите число точек в S2VNA' }
        $culture = [Globalization.CultureInfo]::InvariantCulture
        $text = New-Object Text.StringBuilder
        $name = ([string]$trace.Name -replace '[\r\n]', ' ').Trim()
        if (-not $name -or $name.Length -gt 500) { throw 'ВАЦ: не удалось прочитать имя прибора S2VNA' }
        [void]$text.AppendLine("! S2VNA $name $([DateTime]::UtcNow.ToString('o', $culture)) $($trace.Parameter)")
        [void]$text.AppendLine('# Hz S RI R 50')
        $maxStep = 0.0
        for ($i = 0; $i -lt $frequencies.Count; $i++) {
            $values = @([double]$frequencies[$i], [double]$data[2 * $i], [double]$data[2 * $i + 1])
            foreach ($value in $values) {
                if ([double]::IsNaN($value) -or [double]::IsInfinity($value)) { throw 'ВАЦ: трасса содержит недопустимые числа' }
            }
            if ($values[0] -le 0) { throw 'ВАЦ: частота должна быть больше нуля' }
            if ($i -gt 0) { $maxStep = [Math]::Max($maxStep, [Math]::Abs($values[0] - [double]$frequencies[$i - 1])) }
            [void]$text.AppendLine(($values | ForEach-Object { $_.ToString('G17', $culture) }) -join ' ')
            if ($text.Length -gt 2 * 1024 * 1024) { throw 'Файл ВАЦ больше 2 МиБ — уменьшите число точек в S2VNA' }
        }
        [byte[]]$bytes = [Text.Encoding]::UTF8.GetBytes($text.ToString())
        if ($bytes.Length -gt 2 * 1024 * 1024) { throw 'Файл ВАЦ больше 2 МиБ' }
        Save-VnaName -Name $name
        $trace.Settings['frequency_step_hz'] = $maxStep
        return @{ Bytes = $bytes; Ext = 's1p'; Mime = 'application/octet-stream'; Name = $name; CaptureSettings = $trace.Settings }
    } finally { Release-VnaApplication $app }
}

# Скачать новую версию, сверить SHA-256 и текст, подменить себя и перезапустить задачу. $NoRestart - для тестов.
# Возвращает $true, если файл заменён. Ошибки не бросает: агент продолжает работать на старой версии.
function Invoke-AgentUpdate {
    param([string]$Base, $Update, [string]$AgentPath = $PSCommandPath, [switch]$NoRestart)
    if (-not $Update -or -not $Update.version -or -not $Update.sha256 -or -not $AgentPath) { return $false }
    $tmp = $AgentPath + '.new'
    try {
        Write-Log "Обновление: на сервере версия $($Update.version), у агента $($script:AgentVersion)"
        Invoke-WebRequest -Uri ($Base + [string]$Update.path) -OutFile $tmp -UseBasicParsing -TimeoutSec 60
        $actual = (Get-FileHash -Path $tmp -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne ([string]$Update.sha256).ToLower()) { throw "хэш скачанного файла не совпал ($actual)" }
        $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($tmp, [ref]$null, [ref]$errors)
        if ($errors -and $errors.Count -gt 0) { throw "скачанный файл не разбирается: $($errors[0].Message)" }
        Copy-Item -Path $AgentPath -Destination ($AgentPath + '.bak') -Force
        Move-Item -Path $tmp -Destination $AgentPath -Force
    } catch {
        Write-Log "Обновление не удалось: $($_.Exception.Message). Работаю на версии $($script:AgentVersion)"
        Remove-Item -Path $tmp -Force -ErrorAction SilentlyContinue
        return $false
    }
    Write-Log "Обновление: файл заменён на версию $($Update.version), перезапуск задачи"
    if ($NoRestart) { return $true }
    # Задача сейчас запущена (это мы): после выхода помощник запускает её заново уже с новым файлом.
    $helper = "Start-Sleep -Seconds 4; Start-ScheduledTask -TaskName '$($script:TaskName)'"
    Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-Command', $helper) -WindowStyle Hidden
    exit 0
}

# Отчёт серверу: кто я (номер, имя ПК), версия, сканеры, адрес в сети. Ответ может содержать update - новую версию.
function Send-Heartbeat {
    param([string]$Base, [string]$AuthToken, [string]$PrinterName, [string]$ScannerAddress)
    $scanners = @(Get-AgentScanners -ScannerAddress $ScannerAddress)
    $body = @{
        machine_id = (Get-MachineId); pc_name = [string]$env:COMPUTERNAME; version = $script:AgentVersion
        lan_ip = (Get-LanIp); printer = [string]$PrinterName; scanners = $scanners
    }
    $vna = Get-VnaCapability
    if ($vna) { $body.vna = $vna }
    $body.vna_sources = @()
    if ($vna) { $body.vna_sources += 's2vna' }
    if (Test-ArinstInstalled) { $body.vna_sources += 'arinst' }
    $reply = Invoke-ErpApi -Url "$Base/api/stock/scan/agent/heartbeat" -Body $body -AuthToken $AuthToken
    $names = ($scanners | ForEach-Object { $_.name }) -join '; '
    $line = "Отчёт серверу: сканеры: $(if ($names) { $names } else { 'не найдены' })"
    if ($line -ne $script:LastHeartbeatLine) { Write-Log $line; $script:LastHeartbeatLine = $line }
    if ($reply -and $reply.update) { [void](Invoke-AgentUpdate -Base $Base -Update $reply.update) }
}

function Start-AgentLoop {
    param([string]$Base, [string]$AuthToken, [string]$PrinterName, [switch]$DryRun, [switch]$Once, [string]$ScannerAddress)
    $delay = 3
    $nextBeat = Get-Date
    Write-Log "Агент $($script:AgentVersion) запущен: сервер $Base, принтер '$PrinterName'$(if ($DryRun) { ', DRY RUN' }), сканер: $(if ($ScannerAddress) { $ScannerAddress } else { 'USB (WIA), автопоиск' })"
    while ($true) {
        if ((Get-Date) -ge $nextBeat) {
            try { Send-Heartbeat -Base $Base -AuthToken $AuthToken -PrinterName $PrinterName -ScannerAddress $ScannerAddress }
            catch { Write-Log "Отчёт серверу не ушёл: $($_.Exception.Message)" }
            $nextBeat = (Get-Date).AddSeconds($script:HeartbeatEverySec)
        }
        $state = 'error'
        try {
            # Без принтера (ПК только со сканером) этикетки не опрашиваем.
            $state = if ($PrinterName) { Invoke-PollOnce -Base $Base -AuthToken $AuthToken -PrinterName $PrinterName -DryRun:$DryRun } else { 'idle' }
            $delay = 3
        } catch {
            Write-Log "Сеть/сервер: $($_.Exception.Message). Повтор через $delay с"
            if ($Once) { throw }
            Start-Sleep -Seconds $delay
            $delay = [Math]::Min($delay * 2, 30)
            continue
        }
        # Скан УПД: свои ошибки ловит сама функция, цикл они не роняют
        $scanState = 'idle'
        try { $scanState = Invoke-ScanPollOnce -Base $Base -AuthToken $AuthToken -ScannerAddress $ScannerAddress } catch { Write-Log "Скан: ошибка $($_.Exception.Message)" }
        if ($Once) { return }
        if ($state -eq 'idle' -and $scanState -ne 'done') { Start-Sleep -Seconds 3 }
    }
}

# ---------------------------------------------------------------- Сканер УПД
# eSCL (AirScan, Mopria) по адресу сканера из настроек; без адреса - WIA (USB).

$script:EsclTemplate = @'
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm" xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">
<pwg:Version>2.63</pwg:Version>
<pwg:ScanRegions><pwg:ScanRegion>
<pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset><pwg:Width>2480</pwg:Width><pwg:Height>3508</pwg:Height>
</pwg:ScanRegion></pwg:ScanRegions>
<pwg:InputSource>Platen</pwg:InputSource>
<scan:ColorMode>Grayscale8</scan:ColorMode>
<scan:XResolution>300</scan:XResolution>
<scan:YResolution>300</scan:YResolution>
<pwg:DocumentFormat>{MIME}</pwg:DocumentFormat>
<scan:DocumentFormatExt>{MIME}</scan:DocumentFormatExt>
</scan:ScanSettings>
'@

# Проверка сертификата отключается только на время обращения к сканеру (у него самоподписанный).
function Initialize-TlsSkip {
    if ('ErpTlsSkip' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System.Net;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
public static class ErpTlsSkip {
  public static void On() { ServicePointManager.ServerCertificateValidationCallback = delegate (object s, X509Certificate c, X509Chain ch, SslPolicyErrors e) { return true; }; }
  public static void Off() { ServicePointManager.ServerCertificateValidationCallback = null; }
}
'@
}

# Один HTTP-запрос к сканеру. Status 0 = нет соединения. Исключений не бросает.
function Invoke-EsclRequest {
    param([string]$Method, [string]$Url, [string]$Body, [string]$ContentType, [int]$TimeoutSec = 30)
    $result = @{ Status = 0; Location = ''; Bytes = [byte[]]@(); Error = '' }
    try {
        $req = [Net.WebRequest]::Create($Url)
        $req.Method = $Method
        $req.Timeout = $TimeoutSec * 1000
        $req.ReadWriteTimeout = $TimeoutSec * 1000
        if ($Body) {
            $data = [Text.Encoding]::UTF8.GetBytes($Body)
            $req.ContentType = $ContentType
            $req.ContentLength = $data.Length
            $out = $req.GetRequestStream()
            try { $out.Write($data, 0, $data.Length) } finally { $out.Close() }
        }
        try { $resp = $req.GetResponse() } catch [Net.WebException] {
            if (-not $_.Exception.Response) { throw }
            $resp = $_.Exception.Response
        }
        try {
            $result.Status = [int]$resp.StatusCode
            $result.Location = [string]$resp.Headers['Location']
            $ms = New-Object IO.MemoryStream
            $stream = $resp.GetResponseStream()
            try { $stream.CopyTo($ms) } finally { $stream.Close() }
            $result.Bytes = $ms.ToArray()
        } finally { $resp.Close() }
    } catch {
        $result.Error = $_.Exception.Message
    }
    return $result
}

function Get-EsclBases {
    param([string]$Address)
    if ($Address -match '^https?://') { return @(($Address.TrimEnd('/') -replace '/eSCL.*$', '')) }
    $addr = $Address.TrimEnd('/')
    return @("http://$addr", "https://$addr")
}

# Возможности сканера (GET /eSCL/ScannerCapabilities) одной строкой для журнала; $null - не отвечает.
function Get-EsclCapabilities {
    param([string]$Address)
    Initialize-TlsSkip
    [ErpTlsSkip]::On()
    try {
        foreach ($base in (Get-EsclBases $Address)) {
            $r = Invoke-EsclRequest -Method GET -Url "$base/eSCL/ScannerCapabilities" -TimeoutSec 10
            if ($r.Status -eq 200) {
                $text = [Text.Encoding]::UTF8.GetString($r.Bytes)
                $model = [regex]::Match($text, '<[^>]*MakeAndModel>([^<]*)<').Groups[1].Value
                $formats = ([regex]::Matches($text, '<[^>]*DocumentFormat(?:Ext)?>([^<]*)<') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique) -join ', '
                $platen = if ($text -match 'Platen') { 'да' } else { 'нет' }
                return "$base : модель '$model', форматы: $formats, стекло (Platen): $platen"
            }
        }
    } finally { [ErpTlsSkip]::Off() }
    return $null
}

# Скан одним форматом. Connect=$false - нет связи по этой схеме; Retry=$true - формат не принят.
function Invoke-EsclScanBase {
    param([string]$Base, [string]$Mime)
    $xml = $script:EsclTemplate.Replace('{MIME}', $Mime)
    $r = $null
    for ($i = 0; $i -lt 5; $i++) {
        $r = Invoke-EsclRequest -Method POST -Url "$Base/eSCL/ScanJobs" -Body $xml -ContentType 'text/xml' -TimeoutSec 30
        if ($r.Status -ne 503) { break }
        Start-Sleep -Seconds 2
    }
    if ($r.Status -eq 0) { return @{ Connect = $false; Error = $r.Error } }
    if ($r.Status -eq 400 -or $r.Status -eq 409) { return @{ Connect = $true; Retry = $true; Error = "сканер отклонил формат $Mime (код $($r.Status))" } }
    if ($r.Status -ne 201 -or -not $r.Location) { return @{ Connect = $true; Error = "сканер не принял задание (код $($r.Status))" } }
    $loc = $r.Location
    if ($loc -notmatch '^https?://') { $loc = $Base + '/' + $loc.TrimStart('/') }
    $loc = $loc.TrimEnd('/')
    try {
        $deadline = (Get-Date).AddSeconds(30)
        while ($true) {
            $d = Invoke-EsclRequest -Method GET -Url "$loc/NextDocument" -TimeoutSec 90
            if ($d.Status -eq 503 -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1; continue }
            break
        }
        if ($d.Status -eq 200 -and $d.Bytes.Length -gt 0) {
            $ext = if ($Mime -eq 'application/pdf') { 'pdf' } else { 'jpg' }
            return @{ Connect = $true; Bytes = $d.Bytes; Ext = $ext; Mime = $Mime }
        }
        if ($d.Status -eq 0) { return @{ Connect = $true; Error = "сканер не отдал страницу: $($d.Error)" } }
        return @{ Connect = $true; Error = "сканер не отдал страницу (код $($d.Status))" }
    } finally {
        Invoke-EsclRequest -Method DELETE -Url $loc -TimeoutSec 5 | Out-Null
    }
}

function Invoke-EsclScan {
    param([string]$Address)
    Initialize-TlsSkip
    [ErpTlsSkip]::On()
    try {
        $lastError = 'нет связи со сканером'
        foreach ($base in (Get-EsclBases $Address)) {
            foreach ($mime in @('application/pdf', 'image/jpeg')) {
                $r = Invoke-EsclScanBase -Base $base -Mime $mime
                if (-not $r.Connect) { $lastError = "нет связи со сканером $base ($($r.Error))"; break }
                if ($r.Bytes) { return $r }
                $lastError = $r.Error
                if (-not $r.Retry) { break }
            }
        }
        throw "Сканер: $lastError"
    } finally { [ErpTlsSkip]::Off() }
}

# Какое WIA-устройство сканирует: выбранное человеком ($Device = DeviceID или уникальное имя).
# Возвращает DeviceInfo или $null. Вынесено в функцию для тестов (на вход - любые объекты с Type/DeviceID/Name).
function Select-WiaDevice {
    param($DeviceInfos, [string]$Device)
    $scanners = @($DeviceInfos | Where-Object { $_.Type -eq 1 })
    if (-not $Device) {
        if ($scanners.Count -gt 1) { throw 'Найдено несколько сканеров WIA — выберите сканер в ERP' }
        if ($scanners.Count -eq 1) { return $scanners[0] }
        return $null
    }
    $matches = @()
    foreach ($di in $scanners) {
        if ([string]$di.DeviceID -eq $Device) { return $di }
        $name = ''
        try { $name = [string]$di.Properties.Item('Name').Value } catch { $name = [string]$di.Name }
        if ($name -and $name -eq $Device) { $matches += $di }
    }
    if ($matches.Count -eq 1) { return $matches[0] }
    if ($matches.Count -gt 1) { throw 'Имя сканера неоднозначно — выберите устройство по идентификатору в ERP' }
    return $null
}

# Normalize WIA driver output (including Pantum BMP) to JPEG quality 80.
function ConvertTo-ScanPage {
    param([byte[]]$Bytes)
    Add-Type -AssemblyName System.Drawing
    $inputStream = New-Object IO.MemoryStream(,$Bytes)
    $src = $rgb = $graphics = $outputStream = $ep = $null
    try {
        $src = [Drawing.Image]::FromStream($inputStream)
        $rgb = New-Object Drawing.Bitmap($src.Width, $src.Height, [Drawing.Imaging.PixelFormat]::Format24bppRgb)
        $graphics = [Drawing.Graphics]::FromImage($rgb)
        $graphics.DrawImage($src, 0, 0, $src.Width, $src.Height)
        $codec = [Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
        $ep = New-Object Drawing.Imaging.EncoderParameters 1
        $ep.Param[0] = New-Object Drawing.Imaging.EncoderParameter ([Drawing.Imaging.Encoder]::Quality, [long]80)
        $outputStream = New-Object IO.MemoryStream
        $rgb.Save($outputStream, $codec, $ep)
        return @{ Bytes = $outputStream.ToArray(); Width = $src.Width; Height = $src.Height }
    } finally {
        foreach ($resource in @($graphics, $rgb, $src, $outputStream, $ep, $inputStream)) {
            if ($null -ne $resource) { $resource.Dispose() }
        }
    }
}

# Minimal PDF writer: one JPEG XObject per page; offsets and lengths are byte counts.
function New-ScanPdf {
    param([object[]]$Pages, [double]$DpiX = 300, [double]$DpiY = 300)
    if (-not $Pages.Count -or $DpiX -le 0 -or $DpiY -le 0) { throw 'Нет страниц или неверное разрешение скана' }
    $stream = New-Object IO.MemoryStream
    $offsets = New-Object 'Collections.Generic.List[long]'
    $ascii = [Text.Encoding]::ASCII
    $culture = [Globalization.CultureInfo]::InvariantCulture
    $write = { param([string]$text) $data = $ascii.GetBytes($text); $stream.Write($data, 0, $data.Length) }
    $object = { param([string]$text) $offsets.Add($stream.Position); & $write "$($offsets.Count) 0 obj`n$text`nendobj`n" }
    try {
        & $write "%PDF-1.4`n"
        & $object '<< /Type /Catalog /Pages 2 0 R >>'
        $kids = for ($i = 0; $i -lt $Pages.Count; $i++) { "$((3 + 3 * $i)) 0 R" }
        & $object "<< /Type /Pages /Count $($Pages.Count) /Kids [ $($kids -join ' ') ] >>"
        foreach ($page in $Pages) {
            if ($page.Width -le 0 -or $page.Height -le 0 -or -not $page.Bytes.Length) { throw 'Пустая страница скана' }
            $pageId = $offsets.Count + 1
            $width = ([double]$page.Width * 72 / $DpiX).ToString('0.####', $culture)
            $height = ([double]$page.Height * 72 / $DpiY).ToString('0.####', $culture)
            & $object "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 $width $height] /Resources << /XObject << /Im0 $($pageId + 1) 0 R >> >> /Contents $($pageId + 2) 0 R >>"
            $offsets.Add($stream.Position)
            & $write "$($offsets.Count) 0 obj`n<< /Type /XObject /Subtype /Image /Width $($page.Width) /Height $($page.Height) /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length $($page.Bytes.Length) >>`nstream`n"
            $stream.Write($page.Bytes, 0, $page.Bytes.Length)
            & $write "`nendstream`nendobj`n"
            $commands = "q $width 0 0 $height 0 0 cm /Im0 Do Q`n"
            & $object "<< /Length $($ascii.GetByteCount($commands)) >>`nstream`n$($commands)endstream"
        }
        $xref = $stream.Position
        & $write "xref`n0 $($offsets.Count + 1)`n0000000000 65535 f `n"
        foreach ($offset in $offsets) { & $write ($offset.ToString('0000000000', $culture) + " 00000 n `n") }
        & $write "trailer`n<< /Size $($offsets.Count + 1) /Root 1 0 R >>`nstartxref`n$xref`n%%EOF`n"
        return ,$stream.ToArray()
    } finally { $stream.Dispose() }
}

function ConvertTo-ScanDocument {
    param([object[]]$Pages, [double]$DpiX = 300, [double]$DpiY = 300)
    try {
        $pdf = New-ScanPdf -Pages $Pages -DpiX $DpiX -DpiY $DpiY
        return @{ Bytes = $pdf; Ext = 'pdf'; Mime = 'application/pdf' }
    } catch {
        Write-Log "Скан: создание PDF не удалось: $($_.Exception.Message)"
        # One upload per job: do not silently discard additional sheets on failure.
        if ($Pages.Count -ne 1) { throw 'Не удалось создать многостраничный PDF; повторите сканирование' }
        Write-Log 'Скан: отправляю единственную страницу в JPEG'
        return @{ Bytes = $Pages[0].Bytes; Ext = 'jpg'; Mime = 'image/jpeg' }
    }
}

function Invoke-WiaScan {
    param([string]$Device)
    $dm = New-Object -ComObject WIA.DeviceManager
    $info = Select-WiaDevice -DeviceInfos $dm.DeviceInfos -Device $Device
    if (-not $info) { throw 'Выбранный сканер WIA не найден — проверьте подключение и выберите сканер в ERP' }
    $scanner = $info.Connect()
    $feeder = $false
    $originalSource = $null
    try {
        $originalSource = [int]$scanner.Properties.Item('3088').Value
        if (([int]$scanner.Properties.Item('3087').Value -band 1) -ne 0) {
            $scanner.Properties.Item('3088').Value = 1
        }
        $feeder = (([int]$scanner.Properties.Item('3088').Value -band 1) -ne 0)
    } catch { Write-Log "Скан: источник WIA по умолчанию: $($_.Exception.Message)" }
    try {
        $item = $scanner.Items.Item(1)
        foreach ($p in @(@(6146, 2), @(6147, 300), @(6148, 300))) {
            try { $item.Properties.Item([string]$p[0]).Value = $p[1] } catch {}
        }
        $dpiX = $dpiY = 300
        try { $dpiX = [double]$item.Properties.Item('6147').Value; $dpiY = [double]$item.Properties.Item('6148').Value } catch {}
        $pages = New-Object 'Collections.Generic.List[object]'
        do {
            try { $img = $item.Transfer('{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}') }
            catch {
                $cause = $_.Exception
                while ($cause) {
                    # WIA_ERROR_PAPER_EMPTY is normal only after a feeder page was read.
                    if ($feeder -and $pages.Count -gt 0 -and $cause.HResult -eq -2145320957) { break }
                    $cause = $cause.InnerException
                }
                if ($cause) { break }
                throw
            }
            $pages.Add((ConvertTo-ScanPage -Bytes ([byte[]]$img.FileData.BinaryData)))
        } while ($feeder)
        return ConvertTo-ScanDocument -Pages $pages.ToArray() -DpiX $dpiX -DpiY $dpiY
    } finally {
        if ($null -ne $originalSource) {
            try { $scanner.Properties.Item('3088').Value = $originalSource }
            catch { Write-Log "Скан: не удалось восстановить источник WIA: $($_.Exception.Message)" }
        }
    }
}

function Invoke-ScannerScan {
    param([string]$Address, [string]$Device)
    if ($Address -and ((-not $Device) -or $Device -eq $Address)) {
        $scan = Invoke-EsclScan -Address $Address
        if ($scan.Mime -eq 'image/jpeg') {
            return ConvertTo-ScanDocument -Pages @((ConvertTo-ScanPage -Bytes $scan.Bytes))
        }
        return $scan
    }
    return Invoke-WiaScan -Device $Device
}

# multipart/form-data байтами: тело бинарное, заголовки в UTF-8, CRLF.
function New-MultipartBody {
    param([string]$Boundary, [string]$FieldName, [string]$FileName, [string]$ContentType, [byte[]]$Bytes, $CaptureSettings)
    $ms = New-Object IO.MemoryStream
    $utf8 = New-Object Text.UTF8Encoding($false)
    $head = "--$Boundary`r`nContent-Disposition: form-data; name=`"$FieldName`"; filename=`"$FileName`"`r`nContent-Type: $ContentType`r`n`r`n"
    $h = $utf8.GetBytes($head)
    $ms.Write($h, 0, $h.Length)
    $ms.Write($Bytes, 0, $Bytes.Length)
    if ($null -ne $CaptureSettings) {
        $json = ConvertTo-Json -InputObject $CaptureSettings -Depth 4 -Compress
        $part = $utf8.GetBytes("`r`n--$Boundary`r`nContent-Disposition: form-data; name=`"capture_settings`"`r`nContent-Type: application/json; charset=utf-8`r`n`r`n$json")
        $ms.Write($part, 0, $part.Length)
    }
    $t = $utf8.GetBytes("`r`n--$Boundary--`r`n")
    $ms.Write($t, 0, $t.Length)
    # The comma keeps the byte[] whole: a bare `return` unrolls it into Object[] and the upload goes out as text (422).
    return ,$ms.ToArray()
}

function Send-ScanFile {
    param([string]$Url, [string]$AuthToken, [byte[]]$Bytes, [string]$FileName, [string]$ContentType, [string]$MachineId, $CaptureSettings)
    $boundary = 'frx' + [Guid]::NewGuid().ToString('N')
    [byte[]]$body = New-MultipartBody -Boundary $boundary -FieldName 'file' -FileName $FileName -ContentType $ContentType -Bytes $Bytes -CaptureSettings $CaptureSettings
    $headers = @{ Authorization = "Bearer $AuthToken" }
    if ($MachineId) { $headers['X-Agent-Machine-Id'] = $MachineId }
    Invoke-RestMethod -Method Post -Uri $Url -Body $body -ContentType "multipart/form-data; boundary=$boundary" `
        -Headers $headers -TimeoutSec 60 | Out-Null
}

# Один опрос заданий сканирования: 'idle' / 'done' / 'error'. Исключения наружу не выходят.
function Invoke-ScanPollOnce {
    param([string]$Base, [string]$AuthToken, [string]$ScannerAddress)
    $id = ''
    $machineId = Get-MachineId
    try {
        $claim = Invoke-ErpApi -Url "$Base/api/stock/scan/agent/claim" -Body @{ host = $env:COMPUTERNAME; machine_id = $machineId; kinds = @('scan', 'vna') } -AuthToken $AuthToken
        $script:ScanClaimError = ''
        if (-not $claim.job) { return 'idle' }
        $id = [string]$claim.job.id
        $device = [string]$claim.job.target_scanner
    } catch {
        $msg = $_.Exception.Message
        if ($msg -ne $script:ScanClaimError) { Write-Log "Скан: опрос заданий не удался: $msg"; $script:ScanClaimError = $msg }
        return 'error'
    }
    $how = if ($ScannerAddress) { "сканер $ScannerAddress (eSCL)" } elseif ($device) { "USB (WIA), выбран $device" } else { 'USB (WIA)' }
    Write-Log "Скан: задание $id, $how"
    $resultUrl = "$Base/api/stock/scan/agent/jobs/$id/result"
    try {
        $scan = if ($claim.job.kind -eq 'vna') {
            if ($claim.job.source -eq 'arinst') { Invoke-ArinstCapture }
            elseif (-not $claim.job.source -or $claim.job.source -eq 's2vna') { Invoke-VnaCapture }
            else { throw 'Неизвестный источник ВАЦ — обновите агент ERP' }
        } else { Invoke-ScannerScan -Address $ScannerAddress -Device $device }
        $fileName = if ($scan.FileName) { $scan.FileName } else { "capture.$($scan.Ext)" }
        Send-ScanFile -Url $resultUrl -AuthToken $AuthToken -Bytes $scan.Bytes -FileName $fileName -ContentType $scan.Mime -MachineId $machineId -CaptureSettings $scan.CaptureSettings
        Write-Log "Скан отправлен: задание $id, $($scan.Bytes.Length) байт, $($scan.Mime)"
    } catch {
        $err = $_.Exception.Message
        Write-Log "Скан: ошибка задания ${id}: $err"
        try { Invoke-ErpApi -Url $resultUrl -Body @{ error = $err } -AuthToken $AuthToken -MachineId $machineId | Out-Null }
        catch { Write-Log "Скан: не удалось сообщить об ошибке: $($_.Exception.Message)" }
        return 'error'
    }
    return 'done'
}

# -TestScan: один скан выбранным способом в файл, без ERP.
function Invoke-TestScan {
    param([string]$ScannerAddress, [string]$Dir)
    if (-not $Dir) { $Dir = Join-Path $env:ProgramData 'FraxisPrintAgent' }
    if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir -Force | Out-Null }
    try {
        if ($ScannerAddress) {
            $caps = Get-EsclCapabilities -Address $ScannerAddress
            $line = if ($caps) { "возможности сканера - $caps" } else { "/eSCL/ScannerCapabilities на $ScannerAddress не отвечает" }
            Write-Log "Скан: $line"
            Write-Host $line
        }
        Write-Host 'Положите лист на стекло, сканирую...'
        $scan = Invoke-ScannerScan -Address $ScannerAddress
        $path = Join-Path $Dir "test-scan.$($scan.Ext)"
        [IO.File]::WriteAllBytes($path, $scan.Bytes)
        Write-Log "Скан: тест, $($scan.Bytes.Length) байт сохранено в $path"
        Write-Host "Готово: $path ($($scan.Bytes.Length) байт)"
    } catch {
        Write-Log "Скан: ошибка теста: $($_.Exception.Message)"
        Write-Host "Ошибка: $($_.Exception.Message)"
        throw
    }
}

# Точка входа; при dot-source (тесты) не запускается.
if ($MyInvocation.InvocationName -ne '.') {
    $cfg = Read-AgentConfig -Path $ConfigPath
    if ($Server) { $cfg.server = $Server }
    if ($Token) { $cfg.token = $Token }
    if ($Printer) { $cfg.printer = $Printer }
    if ($Scanner) { $cfg.scanner = $Scanner }
    if ($TestScan) { Invoke-TestScan -ScannerAddress $cfg.scanner; return }
    if (-not $cfg.server -or -not $cfg.token) {
        Write-Log "Нет настроек (сервер/токен) в $ConfigPath"
        throw "Агент не настроен: запустите install.ps1"
    }
    Start-AgentLoop -Base $cfg.server.TrimEnd('/') -AuthToken $cfg.token -PrinterName $cfg.printer -DryRun:$DryRun -Once:$Once -ScannerAddress $cfg.scanner
}

<#
 Агент печати этикеток Fraxis ERP (TSC по USB, RAW TSPL через очередь Windows).
 Каждые 3 с забирает задание у сервера, отправляет байты в очередь, сообщает результат.
 Параметры: -DryRun (не печатать, результат ok=false "dry run"), -Once (один опрос и выход).
 Сканер УПД: -Scanner <адрес> (eSCL; без адреса - WIA), -TestScan (один скан в файл, без ERP).
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
    $cfg = @{ server = ''; token = ''; printer = ''; scanner = '' }
    if ($Path -and (Test-Path $Path)) {
        $raw = Get-Content -Raw -Encoding UTF8 $Path | ConvertFrom-Json
        if ($raw.server) { $cfg.server = [string]$raw.server }
        if ($raw.printer) { $cfg.printer = [string]$raw.printer }
        if ($raw.scanner) { $cfg.scanner = [string]$raw.scanner }
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
    param([string]$Url, [hashtable]$Body, [string]$AuthToken)
    $json = $Body | ConvertTo-Json -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    Invoke-RestMethod -Method Post -Uri $Url -Body $bytes `
        -ContentType 'application/json; charset=utf-8' `
        -Headers @{ Authorization = "Bearer $AuthToken" } -TimeoutSec 20
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

function Start-AgentLoop {
    param([string]$Base, [string]$AuthToken, [string]$PrinterName, [switch]$DryRun, [switch]$Once, [string]$ScannerAddress)
    $delay = 3
    Write-Log "Агент запущен: сервер $Base, принтер '$PrinterName'$(if ($DryRun) { ', DRY RUN' }), сканер: $(if ($ScannerAddress) { $ScannerAddress } else { 'USB (WIA)' })"
    while ($true) {
        $state = 'error'
        try {
            $state = Invoke-PollOnce -Base $Base -AuthToken $AuthToken -PrinterName $PrinterName -DryRun:$DryRun
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

# USB-сканер через WIA: первое устройство типа "сканер", JPEG.
function Invoke-WiaScan {
    $dm = New-Object -ComObject WIA.DeviceManager
    $info = $null
    foreach ($di in $dm.DeviceInfos) { if ($di.Type -eq 1) { $info = $di; break } }
    if (-not $info) { throw 'Сканер не найден: адрес не задан, а USB-сканера (WIA) нет' }
    $item = $info.Connect().Items.Item(1)
    foreach ($p in @(@(6146, 2), @(6147, 300), @(6148, 300))) {
        try { $item.Properties.Item([string]$p[0]).Value = $p[1] } catch {}
    }
    $WiaJpeg = '{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}'
    $img = $item.Transfer($WiaJpeg)
    # The Pantum driver ignores the asked format and hands an uncompressed BMP (9–26 MB, over the server's 21 MB).
    # WIA.ImageProcess «Convert» fails from PowerShell (FormatID cast), so the BMP is re-encoded by System.Drawing.
    if ($img.FormatID -eq $WiaJpeg) { return @{ Bytes = [byte[]]$img.FileData.BinaryData; Ext = 'jpg'; Mime = 'image/jpeg' } }
    $bmp = Join-Path $env:TEMP ('fraxis-scan-' + [Guid]::NewGuid().ToString('N') + '.bmp')
    $img.SaveFile($bmp)
    Add-Type -AssemblyName System.Drawing
    $src = [Drawing.Image]::FromFile($bmp)
    try {
        $codec = [Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
        $ep = New-Object Drawing.Imaging.EncoderParameters 1
        $ep.Param[0] = New-Object Drawing.Imaging.EncoderParameter ([Drawing.Imaging.Encoder]::Quality, [long]80)
        $ms = New-Object IO.MemoryStream
        $src.Save($ms, $codec, $ep)
        return @{ Bytes = $ms.ToArray(); Ext = 'jpg'; Mime = 'image/jpeg' }
    } finally { $src.Dispose(); Remove-Item $bmp -Force -ErrorAction SilentlyContinue }
}

# Скан выбранным способом. Вынесено в функцию, чтобы тесты подменяли её.
function Invoke-ScannerScan {
    param([string]$Address)
    if ($Address) { return Invoke-EsclScan -Address $Address }
    return Invoke-WiaScan
}

# multipart/form-data байтами: тело бинарное, заголовки в UTF-8, CRLF.
function New-MultipartBody {
    param([string]$Boundary, [string]$FieldName, [string]$FileName, [string]$ContentType, [byte[]]$Bytes)
    $ms = New-Object IO.MemoryStream
    $utf8 = New-Object Text.UTF8Encoding($false)
    $head = "--$Boundary`r`nContent-Disposition: form-data; name=`"$FieldName`"; filename=`"$FileName`"`r`nContent-Type: $ContentType`r`n`r`n"
    $h = $utf8.GetBytes($head)
    $ms.Write($h, 0, $h.Length)
    $ms.Write($Bytes, 0, $Bytes.Length)
    $t = $utf8.GetBytes("`r`n--$Boundary--`r`n")
    $ms.Write($t, 0, $t.Length)
    return $ms.ToArray()
}

function Send-ScanFile {
    param([string]$Url, [string]$AuthToken, [byte[]]$Bytes, [string]$FileName, [string]$ContentType)
    $boundary = 'frx' + [Guid]::NewGuid().ToString('N')
    $body = New-MultipartBody -Boundary $boundary -FieldName 'file' -FileName $FileName -ContentType $ContentType -Bytes $Bytes
    Invoke-RestMethod -Method Post -Uri $Url -Body $body -ContentType "multipart/form-data; boundary=$boundary" `
        -Headers @{ Authorization = "Bearer $AuthToken" } -TimeoutSec 60 | Out-Null
}

# Один опрос заданий сканирования: 'idle' / 'done' / 'error'. Исключения наружу не выходят.
function Invoke-ScanPollOnce {
    param([string]$Base, [string]$AuthToken, [string]$ScannerAddress)
    $id = ''
    try {
        $claim = Invoke-ErpApi -Url "$Base/api/stock/scan/agent/claim" -Body @{ host = $env:COMPUTERNAME } -AuthToken $AuthToken
        $script:ScanClaimError = ''
        if (-not $claim.job) { return 'idle' }
        $id = [string]$claim.job.id
    } catch {
        $msg = $_.Exception.Message
        if ($msg -ne $script:ScanClaimError) { Write-Log "Скан: опрос заданий не удался: $msg"; $script:ScanClaimError = $msg }
        return 'error'
    }
    $how = if ($ScannerAddress) { "сканер $ScannerAddress (eSCL)" } else { 'USB (WIA)' }
    Write-Log "Скан: задание $id, $how"
    $resultUrl = "$Base/api/stock/scan/agent/jobs/$id/result"
    try {
        $scan = Invoke-ScannerScan -Address $ScannerAddress
        Send-ScanFile -Url $resultUrl -AuthToken $AuthToken -Bytes $scan.Bytes -FileName "scan.$($scan.Ext)" -ContentType $scan.Mime
        Write-Log "Скан отправлен: задание $id, $($scan.Bytes.Length) байт, $($scan.Mime)"
    } catch {
        $err = $_.Exception.Message
        Write-Log "Скан: ошибка задания ${id}: $err"
        try { Invoke-ErpApi -Url $resultUrl -Body @{ error = $err } -AuthToken $AuthToken | Out-Null }
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
    if (-not $cfg.server -or -not $cfg.token -or -not $cfg.printer) {
        Write-Log "Нет настроек (сервер/токен/принтер) в $ConfigPath"
        throw "Агент не настроен: запустите install.ps1"
    }
    Start-AgentLoop -Base $cfg.server.TrimEnd('/') -AuthToken $cfg.token -PrinterName $cfg.printer -DryRun:$DryRun -Once:$Once -ScannerAddress $cfg.scanner
}

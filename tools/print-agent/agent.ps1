<#
 Агент печати этикеток Fraxis ERP (TSC по USB, RAW TSPL через очередь Windows).
 Каждые 3 с забирает задание у сервера, отправляет байты в очередь, сообщает результат.
 Параметры: -DryRun (не печатать, результат ok=false "dry run"), -Once (один опрос и выход).
#>
[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $env:ProgramData 'FraxisPrintAgent\config.json'),
    [string]$LogPath = (Join-Path $env:ProgramData 'FraxisPrintAgent\agent.log'),
    [string]$Server,
    [string]$Token,
    [string]$Printer,
    [switch]$DryRun,
    [switch]$Once
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
    $cfg = @{ server = ''; token = ''; printer = '' }
    if ($Path -and (Test-Path $Path)) {
        $raw = Get-Content -Raw -Encoding UTF8 $Path | ConvertFrom-Json
        if ($raw.server) { $cfg.server = [string]$raw.server }
        if ($raw.printer) { $cfg.printer = [string]$raw.printer }
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
    param([string]$Base, [string]$AuthToken, [string]$PrinterName, [switch]$DryRun, [switch]$Once)
    $delay = 3
    Write-Log "Агент запущен: сервер $Base, принтер '$PrinterName'$(if ($DryRun) { ', DRY RUN' })"
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
        if ($Once) { return }
        if ($state -eq 'idle') { Start-Sleep -Seconds 3 }
    }
}

# Точка входа; при dot-source (тесты) не запускается.
if ($MyInvocation.InvocationName -ne '.') {
    $cfg = Read-AgentConfig -Path $ConfigPath
    if ($Server) { $cfg.server = $Server }
    if ($Token) { $cfg.token = $Token }
    if ($Printer) { $cfg.printer = $Printer }
    if (-not $cfg.server -or -not $cfg.token -or -not $cfg.printer) {
        Write-Log "Нет настроек (сервер/токен/принтер) в $ConfigPath"
        throw "Агент не настроен: запустите install.ps1"
    }
    Start-AgentLoop -Base $cfg.server.TrimEnd('/') -AuthToken $cfg.token -PrinterName $cfg.printer -DryRun:$DryRun -Once:$Once
}

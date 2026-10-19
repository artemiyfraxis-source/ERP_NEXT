"""Sending a job to the printer (Roller_Buyer `labels/printer.ts`).

The MB341 is on USB, so there is no TCP port 9100: the TSPL bytes go to the Windows print queue as a RAW job — the
spooler hands the bytes to the printer as they are, the driver rasterises nothing of its own. The system function
`WritePrinter` is called by a small PowerShell helper through `Add-Type`. The script goes in `-EncodedCommand`, not as
a file: script execution is disabled by policy on the owner's machines and `-File` would not run there.

Not on Windows (the production Linux server) nothing is sent: the job is only saved and the result says so."""

from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.shared import stage_options

RAW_PRINT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
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

  public static void Send(string printer, byte[] bytes) {
    IntPtr handle;
    if (!OpenPrinter(printer, out handle, IntPtr.Zero))
      throw new Exception("Принтер не открывается: " + Marshal.GetLastWin32Error());
    try {
      DOCINFO info = new DOCINFO();
      info.pDocName = "ERP label";
      info.pDataType = "RAW";
      if (!StartDocPrinter(handle, 1, info))
        throw new Exception("Очередь не приняла задание: " + Marshal.GetLastWin32Error());
      try {
        if (!StartPagePrinter(handle))
          throw new Exception("Страница не началась: " + Marshal.GetLastWin32Error());
        IntPtr buffer = Marshal.AllocCoTaskMem(bytes.Length);
        try {
          Marshal.Copy(bytes, 0, buffer, bytes.Length);
          int written;
          if (!WritePrinter(handle, buffer, bytes.Length, out written))
            throw new Exception("Байты не записались: " + Marshal.GetLastWin32Error());
          if (written != bytes.Length)
            throw new Exception("Записано " + written + " из " + bytes.Length + " байт");
        } finally { Marshal.FreeCoTaskMem(buffer); }
        EndPagePrinter(handle);
      } finally { EndDocPrinter(handle); }
    } finally { ClosePrinter(handle); }
  }
}
'@
try {
  [ErpRawPrinter]::Send($PrinterName, [System.IO.File]::ReadAllBytes($Path))
  Write-Output 'OK'
} catch {
  # One line: otherwise the whole PowerShell sheet with the encoded command comes out, and a person needs the reason.
  $reason = $_.Exception.Message
  if ($_.Exception.InnerException) { $reason = $_.Exception.InnerException.Message }
  Write-Output ('ERR ' + ($reason -replace '\s+', ' '))
}
"""

LIST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Get-CimInstance -ClassName Win32_Printer | Select-Object -ExpandProperty Name
"""

# Windows codes met in warehouse life, in words.
WIN32_REASONS = {
    "5": "нет прав на очередь печати — сервер запущен не под тем пользователем",
    "1722": "служба печати недоступна: проверьте, что «Диспетчер печати» запущен",
    "1801": "принтера с таким именем в системе нет — проверьте имя в настройках",
}

NOT_WINDOWS = "Сервер ERP работает не на Windows — в очередь печати не отправлено, задание сохранено файлом"
NO_PRINTER = "Принтер не выбран в настройках — задание сохранено, напечатайте вручную"


@dataclass(frozen=True)
class SendResult:
    sent: bool
    reason: str | None  # why not sent, in words


def on_windows() -> bool:
    return sys.platform == "win32"


def _explain(reason: str) -> str:
    match = re.search(r"(\d{1,5})\s*$", reason.strip())
    known = WIN32_REASONS.get(match.group(1)) if match else None
    return f"{reason.strip()} — {known}" if known else reason.strip()


def _encode(script: str, args: dict[str, str]) -> str:
    # Single-quoted PowerShell strings are literal (no `$` expansion, no escapes but a doubled quote).
    bound = "\n".join(f"${name} = '{value.replace(chr(39), chr(39) * 2)}'" for name, value in args.items())
    # Output in UTF-8 from the first line: by default PowerShell writes the console code page (cp866 on a Russian
    # Windows), and any Russian word — in a printer name or an error text — would arrive garbled.
    prologue = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8"
    # PowerShell wants the script in UTF-16LE, base64-encoded.
    return base64.b64encode(f"{prologue}\n{bound}\n{script}".encode("utf-16-le")).decode("ascii")


def _powershell(script: str, args: dict[str, str] | None = None, timeout: float = 30) -> str:
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", _encode(script, args or {})],
        capture_output=True, timeout=timeout, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.stdout.decode("utf-8", errors="replace")


def list_printers() -> list[str]:
    """Printers installed in Windows — for the settings. Not Windows or no spooler — an empty list."""
    if not on_windows():
        return []
    try:
        output = _powershell(LIST_SCRIPT)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def send_raw(printer_name: str, path: Path) -> SendResult:
    """Send a saved job file to the queue. The file is saved before the call and stays in any case: if the queue
    refuses, the job is not lost — it can be poured into the printer by hand (`copy /b файл \\\\.\\принтер`)."""
    if not printer_name:
        return SendResult(False, NO_PRINTER)
    if not on_windows():
        return SendResult(False, NOT_WINDOWS)
    try:
        answer = _powershell(RAW_PRINT_SCRIPT, {"PrinterName": printer_name, "Path": str(path)}).strip()
    except (OSError, subprocess.SubprocessError) as error:
        # Printing is not where the server should fall over: the storekeeper sees the reason and prints again.
        first = str(error).splitlines()[0].strip() if str(error) else type(error).__name__
        return SendResult(False, f"Не удалось вызвать очередь печати: {first}")
    if answer.startswith("OK"):
        return SendResult(True, None)
    return SendResult(False, f"Принтер не принял задание: {_explain(answer[4:] if answer.startswith('ERR') else answer)}")


PRINT_DIR_ENV = "ERP_LABEL_PRINT_DIR"


def print_dir() -> Path:
    """Where job files are saved: ERP_LABEL_PRINT_DIR when set; on the stage a `print` folder next to the
    ERP_STAGE_OPTIONS file; else the system temp folder (`erp-label-print`). ERP keeps attachments in the database,
    it has no data folder of its own."""
    explicit = os.getenv(PRINT_DIR_ENV, "").strip()
    if explicit:
        return Path(explicit)
    options = os.getenv(stage_options.ENV, "").strip()
    if options:
        return Path(options).parent / "print"
    return Path(tempfile.gettempdir()) / "erp-label-print"

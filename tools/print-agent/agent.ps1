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
 Управление Godex из ERP (2.10.0, 19.11.2026): режим отделителя (peel-off) по профилю станка — агент держит его в DevMode
 своей учётной записи и .DEFAULT и перед каждой этикеткой проверяет вывод драйвера на ^O1; этикетки маркировки уходят в
 очередь Windows по одной; команда «Очистить принтер» из ERP убирает задания ERP из очереди и очищает буфер Godex.
#>
[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $env:ProgramData 'FraxisPrintAgent\config.json'),
    [string]$LogPath = (Join-Path $env:ProgramData 'FraxisPrintAgent\agent.log'),
    [string]$Server,
    [string]$Token,
    [string]$Printer,
    [string]$MarkingPrinter,
    [string]$Scanner,
    [switch]$DryRun,
    [switch]$Once,
    [switch]$TestScan,
    [string]$VnaWorkerRequest
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$script:LogMaxBytes = 1MB
# Версия агента: сервер читает эту строку из файла и сравнивает с тем, что агент сообщил (поднять число при каждом изменении).
$script:AgentVersion = '2.10.0'
$script:VnaIdnNextProbe = [datetime]::MinValue
$script:TaskName = 'Fraxis — печать этикеток'
$script:HeartbeatEverySec = 120
$script:HeartbeatDevices = $null
$script:VnaControlInitialized = $false
$script:VnaWorker = $null

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
    $cfg = @{ server = ''; token = ''; printer = ''; markingPrinter = ''; scanner = ''; arinstFolders = $null }
    if ($Path -and (Test-Path $Path)) {
        $raw = Get-Content -Raw -Encoding UTF8 $Path | ConvertFrom-Json
        if ($raw.server) { $cfg.server = [string]$raw.server }
        if ($raw.printer) { $cfg.printer = [string]$raw.printer }
        if ($raw.markingPrinter) { $cfg.markingPrinter = [string]$raw.markingPrinter }
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

  public static void Send(string printer, byte[] bytes) { Send(printer, bytes, "ERP label"); }

  public static void Send(string printer, byte[] bytes, string document) {
    IntPtr handle;
    if (!OpenPrinter(printer, out handle, IntPtr.Zero))
      throw new Exception("Принтер не открывается: " + Err());
    try {
      DOCINFO info = new DOCINFO();
      info.pDocName = document;
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
    param([string]$PrinterName, [byte[]]$Bytes, [string]$DocumentName = 'ERP label')
    Initialize-RawPrinter
    [ErpRawPrinter]::Send($PrinterName, $Bytes, $DocumentName)
}

# Exact custom media must never round-trip through PaperSize (hundredths of an inch).
# The controller retains the complete driver DEVMODE and uses its validated native DC.
function Initialize-GdiPrinter {
    if ('ErpGdiPrinter' -as [type]) { return }
    Add-Type -AssemblyName System.Drawing
    Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Printing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Runtime.InteropServices;

public static class ErpGdiPrinter {
  const int Fields = 72, Orientation = 76, Paper = 78, Length = 80, Width = 82;
  const int Scale = 84, Copies = 86, Quality = 90, Color = 92, Duplex = 94, YResolution = 96, Collate = 100;
  const int DmPaperUser = 256;
  // Label drivers (Godex EZ2350i, Seagull) drop flags they do not support. Only media, orientation and
  // resolution are required; the effective DC is verified with GetDeviceCaps before StartDoc.
  const int DmScale = 16, DmCopies = 256, DmYResolution = 8192;
  const int Required = 1 | 2 | 4 | 8 | 1024;            // orientation, paper size/length/width, print quality
  const int Optional = DmScale | DmCopies | 2048 | 4096 | DmYResolution | 32768; // requested only if the driver offers them
  [DllImport("winspool.drv", EntryPoint="OpenPrinterW", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern bool OpenPrinter(string name, out IntPtr handle, IntPtr defaults);
  [DllImport("winspool.drv")] static extern bool ClosePrinter(IntPtr handle);
  [DllImport("winspool.drv", EntryPoint="DocumentPropertiesW", CharSet=CharSet.Unicode)]
  static extern int DocumentProperties(IntPtr window, IntPtr printer, string name, IntPtr output, IntPtr input, int flags);
  [DllImport("gdi32.dll", EntryPoint="CreateDCW", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern IntPtr CreateDC(string driver, string device, string output, IntPtr mode);
  [DllImport("gdi32.dll")] static extern bool DeleteDC(IntPtr dc);
  [DllImport("gdi32.dll")] static extern int GetDeviceCaps(IntPtr dc, int index);
  [DllImport("winspool.drv", EntryPoint="GetPrinterW", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern bool GetPrinter(IntPtr printer, int level, IntPtr buffer, int size, out int needed);
  [DllImport("winspool.drv", EntryPoint="SetPrinterW", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern bool SetPrinter(IntPtr printer, int level, IntPtr buffer, int command);
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  class DocInfo {
    public int size = Marshal.SizeOf(typeof(DocInfo));
    [MarshalAs(UnmanagedType.LPWStr)] public string name = "ERP marking";
    // lpszOutput: a file instead of the printer port (DOCINFO, StartDoc) - the driver's own output, never printed.
    [MarshalAs(UnmanagedType.LPWStr)] public string output = null;
    public IntPtr type = IntPtr.Zero;
    public int flags = 0;
  }
  [DllImport("gdi32.dll", EntryPoint="StartDocW", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern int StartDoc(IntPtr dc, DocInfo info);
  [DllImport("gdi32.dll")] static extern int StartPage(IntPtr dc);
  [DllImport("gdi32.dll")] static extern int EndPage(IntPtr dc);
  [DllImport("gdi32.dll")] static extern int EndDoc(IntPtr dc);
  [DllImport("gdi32.dll")] static extern int AbortDoc(IntPtr dc);
  static short Read(byte[] b, int offset) { return BitConverter.ToInt16(b, offset); }
  static void Write(byte[] b, int offset, int value) { Buffer.BlockCopy(BitConverter.GetBytes((short)value), 0, b, offset, 2); }
  static void CheckBuffer(byte[] b) {
    if (b == null || b.Length < 220 || (ushort)Read(b,68) < 220 ||
        (uint)(ushort)Read(b,68) + (ushort)Read(b,70) > b.Length)
      throw new InvalidOperationException("Invalid complete DEVMODEW");
  }
  public static int Tenths(double mm) {
    if (Double.IsNaN(mm) || Double.IsInfinity(mm) || mm <= 0 || mm > 500 || Math.Abs(mm*10-Math.Round(mm*10)) > 0.000001)
      throw new InvalidOperationException("Media must use exact 0.1 mm units (0 < mm <= 500)");
    return checked((int)Math.Round(mm*10));
  }
  public static byte[] RequestMode(byte[] original, double width, double height) {
    CheckBuffer(original);
    byte[] b = (byte[])original.Clone();
    int offered = BitConverter.ToInt32(b,Fields);
    int fields = (offered & ~65536 & ~Optional) | Required | (offered & Optional); // Clear DM_FORMNAME.
    Buffer.BlockCopy(BitConverter.GetBytes(fields),0,b,Fields,4);
    // The server already turned the raster so that its Y axis is the feed (marking_render feed_direction):
    // width runs across the head, height along the feed. That is portrait stock 1:1. A GDI landscape page
    // would swap the stock and rotate once more; the Godex/Seagull driver also clamps a 20 mm width to 25.4 mm.
    Write(b,Orientation,1); Write(b,Paper,DmPaperUser);
    Write(b,Width,Tenths(width)); Write(b,Length,Tenths(height));
    Write(b,Scale,100); Write(b,Copies,1); Write(b,Quality,300); Write(b,YResolution,300);
    Write(b,Color,1); Write(b,Duplex,1); Write(b,Collate,0);
    Array.Clear(b,102,64);
    return b;
  }
  public static void ValidateMode(byte[] expected, byte[] actual) {
    CheckBuffer(expected); CheckBuffer(actual);
    int kept = BitConverter.ToInt32(actual,Fields);
    if ((kept & Required) != Required)
      throw new InvalidOperationException("Driver removed required DEVMODE fields");
    // dmPaperSize is only the driver's name for the media: Seagull drivers answer their own custom index for a
    // user size. The exact length/width in 0.1 mm and the DC's physical size (ValidateGeometry) are what bind.
    foreach (int offset in new int[] { Orientation, Length, Width, Quality })
      if (Read(expected,offset) != Read(actual,offset))
        throw new InvalidOperationException("Driver substituted DEVMODE field at " + offset);
    // Fields a driver keeps must still mean one unscaled 300 dpi copy; colour, duplex and collation are irrelevant.
    if ((kept & DmScale) != 0 && Read(actual,Scale) != 100) throw new InvalidOperationException("Driver substituted DEVMODE field at " + Scale);
    if ((kept & DmCopies) != 0 && Read(actual,Copies) != 1) throw new InvalidOperationException("Driver substituted DEVMODE field at " + Copies);
    if ((kept & DmYResolution) != 0 && Read(actual,YResolution) != 300) throw new InvalidOperationException("Driver substituted DEVMODE field at " + YResolution);
  }
  public static byte[] ValidateWithDriver(string name, double width, double height) {
    IntPtr printer, input = IntPtr.Zero, output = IntPtr.Zero;
    if (!OpenPrinter(name,out printer,IntPtr.Zero)) throw new InvalidOperationException("Printer unavailable: " + name);
    try {
      int count = DocumentProperties(IntPtr.Zero,printer,name,IntPtr.Zero,IntPtr.Zero,0);
      if (count < 220 || count > 1048576) throw new InvalidOperationException("Invalid driver DEVMODE size");
      input = Marshal.AllocHGlobal(count); output = Marshal.AllocHGlobal(count);
      if (DocumentProperties(IntPtr.Zero,printer,name,input,IntPtr.Zero,2) != 1)
        throw new InvalidOperationException("Cannot read DEVMODE");
      byte[] original = new byte[count]; Marshal.Copy(input,original,0,count);
      byte[] requested = RequestMode(original,width,height); Marshal.Copy(requested,0,input,count);
      if (DocumentProperties(IntPtr.Zero,printer,name,output,input,10) != 1)
        throw new InvalidOperationException("Driver rejected DEVMODE");
      byte[] actual = new byte[count]; Marshal.Copy(output,actual,0,count);
      ValidateMode(requested,actual); return actual;
    } finally {
      if (input != IntPtr.Zero) Marshal.FreeHGlobal(input);
      if (output != IntPtr.Zero) Marshal.FreeHGlobal(output);
      ClosePrinter(printer);
    }
  }
  static IntPtr OpenNamed(string name) {
    IntPtr printer;
    if (!OpenPrinter(name,out printer,IntPtr.Zero)) throw new InvalidOperationException("Printer unavailable: " + name);
    return printer;
  }
  static byte[] CopyMode(IntPtr mode) {
    if (mode == IntPtr.Zero) return null;
    int size = (ushort)Marshal.ReadInt16(mode,68) + (ushort)Marshal.ReadInt16(mode,70);
    byte[] b = new byte[size]; Marshal.Copy(mode,b,0,size); CheckBuffer(b); return b;
  }
  // PRINTER_INFO_8 (global defaults: the printer's «Default DevMode») or PRINTER_INFO_9 (this account's DevModePerUser).
  static byte[] LevelMode(string name, int level) {
    IntPtr printer = OpenNamed(name);
    try {
      int needed; GetPrinter(printer,level,IntPtr.Zero,0,out needed);
      if (needed <= 0) return null;
      IntPtr buffer = Marshal.AllocHGlobal(needed);
      try {
        if (!GetPrinter(printer,level,buffer,needed,out needed)) throw new InvalidOperationException("GetPrinter level " + level + " failed: " + Marshal.GetLastWin32Error());
        return CopyMode(Marshal.ReadIntPtr(buffer));
      } finally { Marshal.FreeHGlobal(buffer); }
    } finally { ClosePrinter(printer); }
  }
  public static byte[] GlobalMode(string name) {
    byte[] mode = LevelMode(name,8);
    if (mode == null) throw new InvalidOperationException("Printer has no default DEVMODE");
    return mode;
  }
  public static byte[] UserMode(string name) { return LevelMode(name,9); }
  public static void SetUserMode(string name, byte[] mode) {
    CheckBuffer(mode);
    IntPtr printer = OpenNamed(name), dm = Marshal.AllocHGlobal(mode.Length), info = Marshal.AllocHGlobal(IntPtr.Size);
    try {
      Marshal.Copy(mode,0,dm,mode.Length); Marshal.WriteIntPtr(info,dm);
      if (!SetPrinter(printer,9,info,0)) throw new InvalidOperationException("SetPrinter level 9 failed: " + Marshal.GetLastWin32Error());
    } finally { Marshal.FreeHGlobal(info); Marshal.FreeHGlobal(dm); ClosePrinter(printer); }
  }
  // What a job of this account gets without any request: DevModePerUser merged over the printer defaults.
  public static byte[] CurrentMode(string name) {
    IntPtr printer = OpenNamed(name), output = IntPtr.Zero;
    try {
      int count = DocumentProperties(IntPtr.Zero,printer,name,IntPtr.Zero,IntPtr.Zero,0);
      if (count < 220 || count > 1048576) throw new InvalidOperationException("Invalid driver DEVMODE size");
      output = Marshal.AllocHGlobal(count);
      if (DocumentProperties(IntPtr.Zero,printer,name,output,IntPtr.Zero,2) != 1) throw new InvalidOperationException("Cannot read DEVMODE");
      byte[] mode = new byte[count]; Marshal.Copy(output,mode,0,count); CheckBuffer(mode); return mode;
    } finally { if (output != IntPtr.Zero) Marshal.FreeHGlobal(output); ClosePrinter(printer); }
  }
  // One blank page through the driver with this DEVMODE into a file: the spooler despools the driver's printer
  // language (EZPL header with ^O) to `path` instead of the port. Returns the Windows job id.
  public static int Capture(string name, byte[] mode, string path, string document) {
    CheckBuffer(mode);
    IntPtr buffer = Marshal.AllocHGlobal(mode.Length), dc;
    try { Marshal.Copy(mode,0,buffer,mode.Length); dc = CreateDC("WINSPOOL",name,null,buffer); }
    finally { Marshal.FreeHGlobal(buffer); }
    if (dc == IntPtr.Zero) throw new InvalidOperationException("Cannot create printer DC");
    try {
      DocInfo info = new DocInfo(); info.name = document; info.output = path;
      int job = StartDoc(dc,info);
      if (job <= 0) throw new InvalidOperationException("StartDoc (capture) failed");
      bool ok = false;
      try {
        if (StartPage(dc) <= 0 || EndPage(dc) <= 0 || EndDoc(dc) <= 0) throw new InvalidOperationException("Capture page failed");
        ok = true;
      } finally { if (!ok) AbortDoc(dc); }
      return job;
    } finally { DeleteDC(dc); }
  }
  public static void ValidateGeometry(double width, double height, int pixelsW, int pixelsH,
      int dpiX, int dpiY, int physicalW, int physicalH, int offsetX, int offsetY, int printableW, int printableH) {
    if (dpiX != 300 || dpiY != 300 || Math.Abs(width*300/25.4-pixelsW) > 0.500001 ||
        Math.Abs(height*300/25.4-pixelsH) > 0.500001)
      throw new InvalidOperationException("Raster or device resolution mismatch");
    if (Math.Abs(physicalW-width*300/25.4) > 1.01 || Math.Abs(physicalH-height*300/25.4) > 1.01 ||
        offsetX < 0 || offsetY < 0 || printableW <= 0 || printableH <= 0 ||
        offsetX+printableW > physicalW+1 || offsetY+printableH > physicalH+1)
      throw new InvalidOperationException("Driver substituted physical media or printable bounds");
  }
  static string Mm(int dots) { return (dots*25.4/300).ToString("0.00", System.Globalization.CultureInfo.GetCultureInfo("ru-RU")); }
  // Never silently clip or shift ink: the driver's hard margins (Godex/Seagull: 36 dots left, 15 right) are
  // physical. White outside the printable rect is safe. The refusal names both rectangles in mm from the label's
  // top-left corner so the profile's text zones can be moved inside.
  public static void CheckInk(Bitmap bitmap, int ox, int oy, int pw, int ph) {
    int minX=int.MaxValue, minY=int.MaxValue, maxX=-1, maxY=-1; bool outside=false;
    for (int y=0;y<bitmap.Height;y++) for (int x=0;x<bitmap.Width;x++) {
      if (bitmap.GetPixel(x,y).R == 255) continue;
      if (x<minX) minX=x; if (x>maxX) maxX=x; if (y<minY) minY=y; if (y>maxY) maxY=y;
      if (x<ox || y<oy || x>=ox+pw || y>=oy+ph) outside=true;
    }
    if (outside)
      throw new InvalidOperationException("Raster ink outside printable bounds: драйвер печатает по ширине " +
        Mm(ox) + "–" + Mm(ox+pw) + " мм, по подаче " + Mm(oy) + "–" + Mm(oy+ph) + " мм; краска по ширине " +
        Mm(minX) + "–" + Mm(maxX+1) + " мм, по подаче " + Mm(minY) + "–" + Mm(maxY+1) +
        " мм. Сдвиньте текстовые зоны профиля внутрь печатаемой области");
  }
  // Drive the no-dialog controller's single-page lifecycle explicitly below. Calling the
  // managed PrintDocument.Print loop would rebuild DEVMODE from rounded PaperSize.
  public sealed class Controller : StandardPrintController {
    readonly string printer; readonly byte[] mode; readonly Bitmap bitmap; readonly double width, height;
    IntPtr dc; Graphics graphics; bool completed;
    public bool SubmissionStarted; public int JobId; public string DocName = "ERP marking";
    public Controller(string name, byte[] validatedMode, Bitmap image, double w, double h) {
      printer=name; mode=validatedMode; bitmap=image; width=w; height=h;
    }
    public override void OnStartPrint(PrintDocument document, PrintEventArgs e) {
      try {
        IntPtr buffer=Marshal.AllocHGlobal(mode.Length);
        try { Marshal.Copy(mode,0,buffer,mode.Length); dc=CreateDC("WINSPOOL",printer,null,buffer); }
        finally { Marshal.FreeHGlobal(buffer); }
        if (dc == IntPtr.Zero) throw new InvalidOperationException("Cannot create printer DC");
        int ox=GetDeviceCaps(dc,112), oy=GetDeviceCaps(dc,113), pw=GetDeviceCaps(dc,8), ph=GetDeviceCaps(dc,10);
        ValidateGeometry(width,height,bitmap.Width,bitmap.Height,GetDeviceCaps(dc,88),GetDeviceCaps(dc,90),
          GetDeviceCaps(dc,110),GetDeviceCaps(dc,111),ox,oy,pw,ph);
        CheckInk(bitmap,ox,oy,pw,ph);
        SubmissionStarted=true;
        DocInfo info=new DocInfo(); info.name=DocName;
        JobId=StartDoc(dc,info);
        if (JobId<=0) throw new InvalidOperationException("StartDoc failed; delivery unknown");
      } catch { Cleanup(); throw; }
    }
    public override Graphics OnStartPage(PrintDocument document, PrintPageEventArgs e) {
      if (StartPage(dc)<=0) throw new InvalidOperationException("StartPage failed");
      graphics=Graphics.FromHdc(dc);
      graphics.PageUnit=GraphicsUnit.Pixel; graphics.PageScale=1;
      graphics.TranslateTransform(-GetDeviceCaps(dc,112),-GetDeviceCaps(dc,113));
      graphics.InterpolationMode=InterpolationMode.NearestNeighbor;
      graphics.PixelOffsetMode=PixelOffsetMode.Half;
      return graphics;
    }
    public override void OnEndPage(PrintDocument document, PrintPageEventArgs e) {
      if (graphics!=null) { graphics.Dispose(); graphics=null; }
      if (EndPage(dc)<=0) throw new InvalidOperationException("EndPage failed");
    }
    public override void OnEndPrint(PrintDocument document, PrintEventArgs e) {
      try {
        if (e.Cancel) throw new InvalidOperationException("Print cancelled; delivery unknown");
        if (EndDoc(dc)<=0) throw new InvalidOperationException("EndDoc failed; delivery unknown");
        completed=true;
      } finally { Cleanup(); }
    }
    public void Cleanup() {
      if (graphics!=null) { graphics.Dispose(); graphics=null; }
      if (dc!=IntPtr.Zero) { if (JobId>0 && !completed) AbortDoc(dc); DeleteDC(dc); dc=IntPtr.Zero; }
    }
  }
  public static void Print(string printer, Bitmap image, double width, double height, Controller controller) {
    using (PrintDocument document=new PrintDocument()) {
      document.PrinterSettings.PrinterName=printer;
      document.PrinterSettings.Copies=1;
      document.DefaultPageSettings.Margins=new Margins(0,0,0,0);
      document.OriginAtMargins=false;
      document.PrintController=controller;
      PrintEventArgs printEvent = new PrintEventArgs();
      try {
        controller.OnStartPrint(document,printEvent);
        // Native geometry was verified before submission; no managed media conversion is used.
        PrintPageEventArgs page = new PrintPageEventArgs(null,Rectangle.Empty,Rectangle.Empty,document.DefaultPageSettings);
        Graphics graphics = controller.OnStartPage(document,page);
        // 1:1 in device pixels: the raster is already the label at 300 dpi (ValidateGeometry checked it against the
        // millimetres). Scaling to width*300/25.4 (e.g. 1092.52 for 1093 dots) would drop a column.
        graphics.DrawImage(image,new Rectangle(0,0,image.Width,image.Height),0,0,image.Width,image.Height,GraphicsUnit.Pixel);
        controller.OnEndPage(document,page);
        controller.OnEndPrint(document,printEvent);
      } finally { controller.Cleanup(); }
    }
  }
}
'@
}

function Get-AgentPrinters {
    param([string]$PrinterName, [string]$MarkingPrinterName)
    if ($PrinterName -and $PrinterName -eq $MarkingPrinterName) { throw 'Очереди TSC и маркировки должны различаться' }
    $installed = @()
    if ($PrinterName -or $MarkingPrinterName) {
        try { $installed = @(Get-Printer -ErrorAction Stop) }
        catch { Write-Log "Не удалось проверить очереди принтеров: $($_.Exception.Message)" }
    }
    foreach ($target in @(@{ name = $PrinterName; kind = 'tspl' }, @{ name = $MarkingPrinterName; kind = 'gdi_png' })) {
        if (-not $target.name) { continue }
        $queue = $installed | Where-Object { $_.Name -eq $target.name } | Select-Object -First 1
        @{ name = $target.name; driver = [string]$queue.DriverName; port = [string]$queue.PortName; kind = $target.kind; present = [bool]$queue }
    }
}

function Get-PrintReceiptRoot { return Join-Path (Split-Path -Parent ([IO.Path]::GetFullPath($ConfigPath))) 'print-receipts' }

function Get-PrintReceiptPath {
    param([string]$JobId, [string]$Attempt)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $key = ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($JobId + ':' + $Attempt)))).Replace('-', '').ToLowerInvariant() }
    finally { $hash.Dispose() }
    $root = Get-PrintReceiptRoot
    $archive = Join-Path (Join-Path $root 'acknowledged') ($key + '.json')
    if (Test-Path -LiteralPath $archive) { return $archive }
    return Join-Path $root ($key + '.json')
}

function Move-AcknowledgedPrintReceipt {
    param([string]$Path)
    $archive = Join-Path (Get-PrintReceiptRoot) 'acknowledged'
    [void][IO.Directory]::CreateDirectory($archive)
    [IO.File]::Move($Path, (Join-Path $archive ([IO.Path]::GetFileName($Path))))
}

function Save-PrintReceipt {
    param([string]$Path, $Receipt)
    [void][IO.Directory]::CreateDirectory((Split-Path -Parent $Path))
    $temporary = $Path + '.tmp'
    [IO.File]::WriteAllText($temporary, ($Receipt | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
    # Force the write before any spool call. File.Replace is atomic on local NTFS.
    $stream = [IO.File]::Open($temporary, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Flush($true) } finally { $stream.Dispose() }
    if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary, $Path, [NullString]::Value) }
    else { [IO.File]::Move($temporary, $Path) }
}

# HTTP status of a failed Invoke-RestMethod (PS 5.1 WebException and PS 7 HttpResponseException); 0 - no answer.
function Get-ErpErrorStatus {
    param($ErrorRecord)
    try { if ($ErrorRecord.Exception.Response) { return [int]$ErrorRecord.Exception.Response.StatusCode } } catch {}
    return 0
}

# Sends pending receipts. An `unknown` receipt stays pending after the server acknowledged it: re-sending the same
# receipt is idempotent and answers the job's status, so the agent sees when the operator resolved the job in ERP
# («Проверка» → spooled/failed) and only then marks it resolved. Returns $true while an unresolved unknown remains
# (the marking lane stays paused; TSPL keeps printing). 404/409/403 are terminal: the server will never take this
# receipt (the job is gone, already has another result, or belongs to another token) and holds the job itself.
function Sync-PrintReceipts {
    param([string]$Base, [string]$AuthToken)
    $root = Get-PrintReceiptRoot
    $blocked = $false
    if (-not (Test-Path -LiteralPath $root)) { return $false }
    foreach ($file in @(Get-ChildItem -LiteralPath $root -Filter '*.json' -File)) {
        $receipt = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8) | ConvertFrom-Json
        $pending = $receipt.payload.outcome -eq 'unknown' -and -not $receipt.resolved
        if ($receipt.acknowledged -and -not $pending) { Move-AcknowledgedPrintReceipt $file.FullName; continue }
        if ($receipt.base -ne $Base -or $receipt.machine_id -ne (Get-MachineId)) {
            if ($receipt.acknowledged) { $blocked = $true; continue }
            throw 'Квитанция печати принадлежит другому серверу или ПК'
        }
        $body = @{ attempt = $receipt.payload.attempt; outcome = $receipt.payload.outcome; message = $receipt.payload.message }
        if ($receipt.payload.spool_job_id) { $body.spool_job_id = $receipt.payload.spool_job_id }
        if ($receipt.payload.tracked) { $body.tracked = $true }
        $status = ''
        try {
            $reply = Invoke-ErpApi -Url "$Base/api/stock/labels/jobs/$($receipt.job_id)/result" -Body $body -AuthToken $AuthToken -MachineId $receipt.machine_id
            if ($reply -and $reply.status) { $status = [string]$reply.status }
        } catch {
            $code = Get-ErpErrorStatus $_
            if ($code -notin @(403, 404, 409)) { throw }
            Write-Log "Квитанция задания $($receipt.job_id) закрыта сервером (HTTP $code): $($_.Exception.Message)"
            $status = 'closed'
        }
        $changed = -not $receipt.acknowledged
        if ($pending -and $status -and $status -ne 'unknown') {
            $receipt | Add-Member -NotePropertyName resolved -NotePropertyValue $true -Force
            Write-Log "Неизвестный результат задания $($receipt.job_id) сверен в ERP: $status"
            $pending = $false; $changed = $true
        }
        if ($changed) {
            $receipt.acknowledged = $true
            Save-PrintReceipt $file.FullName $receipt
        }
        if ($pending) { $blocked = $true } else { Move-AcknowledgedPrintReceipt $file.FullName }
    }
    return $blocked
}

function ConvertFrom-MarkingJob {
    param($Job, [string]$MarkingPrinterName)
    if (-not $Job.id -or -not $Job.attempt -or $Job.format -ne 'gdi_png' -or
        -not $MarkingPrinterName -or $Job.printer -cne $MarkingPrinterName) { throw 'Неверный формат, попытка или очередь задания маркировки' }
    if ($Job.copies -ne 1 -or $Job.dpi -ne 300 -or $Job.orientation -cnotin @('portrait', 'landscape')) { throw 'Неверные копии, DPI или ориентация' }
    Initialize-GdiPrinter
    [void][ErpGdiPrinter]::Tenths([double]$Job.width_mm)
    [void][ErpGdiPrinter]::Tenths([double]$Job.height_mm)
    if ([string]$Job.sha256 -notmatch '^[a-fA-F0-9]{64}$' -or ([string]$Job.png_b64).Length -gt 16MB) { throw 'Неверный SHA-256 или слишком большой PNG' }
    [byte[]]$bytes = [Convert]::FromBase64String([string]$Job.png_b64)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $actual = ([BitConverter]::ToString($hash.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $hash.Dispose() }
    if ($actual -ne $Job.sha256) { throw 'SHA-256 PNG не совпадает' }
    # Check PNG IHDR before decoding, including dimensions and monochrome encoding, to bound allocation.
    if ($bytes.Length -lt 33 -or ([BitConverter]::ToString($bytes[0..7])) -ne '89-50-4E-47-0D-0A-1A-0A' -or
        ([Text.Encoding]::ASCII.GetString($bytes,12,4)) -ne 'IHDR' -or $bytes[24] -ne 1 -or $bytes[25] -ne 0) { throw 'Требуется однобитный PNG' }
    $w = [double]$Job.width_mm * 300 / 25.4; $h = [double]$Job.height_mm * 300 / 25.4
    [byte[]]$wb = $bytes[16..19]; [array]::Reverse($wb)
    [byte[]]$hb = $bytes[20..23]; [array]::Reverse($hb)
    if ([Math]::Abs([BitConverter]::ToUInt32($wb,0) - $w) -gt 0.500001 -or
        [Math]::Abs([BitConverter]::ToUInt32($hb,0) - $h) -gt 0.500001 -or $w*$h -gt 16000000) { throw 'Размер растра не соответствует миллиметрам и 300 DPI' }
    return ,$bytes
}

function Send-PngToPrinter {
    param($Job, [byte[]]$Bytes)
    $stream = New-Object IO.MemoryStream(,$Bytes)
    $bitmap = $controller = $null
    try {
        $bitmap = New-Object Drawing.Bitmap($stream)
        $mode = Get-JobDriverMode $Job
        if ($Job.peel) { $mode = Confirm-JobPeelMode $Job $mode }
        $controller = New-Object ErpGdiPrinter+Controller($Job.printer, $mode, $bitmap, [double]$Job.width_mm, [double]$Job.height_mm)
        $controller.DocName = Get-MarkingDocName ([string]$Job.id)
        [ErpGdiPrinter]::Print($Job.printer, $bitmap, [double]$Job.width_mm, [double]$Job.height_mm, $controller)
        return @{ outcome = 'spooled'; message = 'Передано принтеру'; spool_job_id = $controller.JobId }
    } catch {
        return @{ outcome = $(if ($controller -and $controller.SubmissionStarted) { 'unknown' } else { 'failed' }); message = $_.Exception.Message }
    } finally {
        if ($controller) { $controller.Cleanup() }
        if ($bitmap) { $bitmap.Dispose() }
        $stream.Dispose()
    }
}

# --- Honest print status -----------------------------------------------------------------------------------------
# «Передано принтеру» only means Windows accepted the job. The printer (Godex in peel-off mode takes one label per
# take) may still hold it, so the agent keeps watching each spooled job in the Windows queue until it leaves it:
# printed / cancelled are final, an error (offline, paper out, paused...) is reported and watched until it clears.
# The state goes to ERP at /labels/jobs/<id>/print-state; ERP shows «напечатано N из M · в очереди K».
function Get-PrintWatchRoot { return Join-Path (Get-PrintReceiptRoot) 'watch' }

function Get-PrintWatchPath {
    param([string]$JobId, [string]$Attempt)
    return Join-Path (Get-PrintWatchRoot) ([IO.Path]::GetFileName((Get-PrintReceiptPath $JobId $Attempt)))
}

# One Windows spool job (Get-PrintJob row, or $null once it left the queue) -> the job's print state for ERP.
# printed: Printed/Complete flag, or gone from the queue without being deleted; cancelled: gone after a Deleting
# flag; error: a blocking flag; queued: still spooling/printing (the printer may be holding it).
function Resolve-SpoolJobState {
    param($Job, [bool]$WasDeleting = $false)
    if (-not $Job) {
        if ($WasDeleting) { return @{ state = 'cancelled'; message = 'Задание удалено из очереди печати'; final = $true; deleting = $true } }
        return @{ state = 'printed'; message = ''; final = $true; deleting = $false }
    }
    $status = [string]$Job.JobStatus
    if ($status -match 'Printed|Complete') { return @{ state = 'printed'; message = ''; final = $true; deleting = $false } }
    if ($status -match 'Deleting|Deleted') { return @{ state = 'queued'; message = ''; final = $false; deleting = $true } }
    $reasons = @()
    if ($status -match 'Offline') { $reasons += 'принтер не в сети' }
    if ($status -match 'PaperOut') { $reasons += 'нет бумаги или этикеток' }
    if ($status -match 'Paused') { $reasons += 'очередь приостановлена' }
    if ($status -match 'UserIntervention') { $reasons += 'нужно вмешательство оператора' }
    if ($status -match 'Blocked') { $reasons += 'очередь устройства заблокирована' }
    if ($status -match 'Error' -and -not $reasons.Count) { $reasons += 'ошибка печати' }
    if ($reasons.Count) { return @{ state = 'error'; message = 'Ошибка: ' + ($reasons -join ', '); final = $false; deleting = $false } }
    return @{ state = 'queued'; message = ''; final = $false; deleting = $false }
}

function Save-PrintWatch {
    param([string]$JobId, [string]$Attempt, [string]$Base, [string]$MachineId, [string]$Printer, $SpoolJobId)
    $watch = @{ base = $Base; machine_id = $MachineId; job_id = $JobId; attempt = $Attempt; printer = $Printer
        spool_job_id = [int]$SpoolJobId; doc_name = (Get-MarkingDocName $JobId); deleting = $false; last_state = ''; last_message = ''
        started_at = (Get-Date).ToUniversalTime().ToString('o') }
    Save-PrintReceipt (Get-PrintWatchPath $JobId $Attempt) $watch
}

# Walks every watched job once: reads the Windows queue per printer, reports a changed state to ERP. Network errors
# keep the watch for the next pass; 403/404/409 mean ERP will never take it, so the watch ends.
function Update-PrintWatches {
    param([string]$Base, [string]$AuthToken)
    $root = Get-PrintWatchRoot
    if (-not (Test-Path -LiteralPath $root)) { return }
    $queues = @{}
    foreach ($file in @(Get-ChildItem -LiteralPath $root -Filter '*.json' -File)) {
        $watch = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8) | ConvertFrom-Json
        if ($watch.base -ne $Base -or $watch.machine_id -ne (Get-MachineId)) { continue }
        $printer = [string]$watch.printer
        if (-not $queues.ContainsKey($printer)) {
            try { $queues[$printer] = @(Get-PrintJob -PrinterName $printer -ErrorAction Stop) }
            catch { Write-Log "Очередь '$printer' не прочитана: $($_.Exception.Message)"; $queues[$printer] = $null }
        }
        if ($null -eq $queues[$printer]) { continue }
        $job = $queues[$printer] | Where-Object { [int]$_.ID -eq [int]$watch.spool_job_id -and
            (-not $watch.doc_name -or -not [string]$_.DocumentName -or [string]$_.DocumentName -eq [string]$watch.doc_name) } | Select-Object -First 1
        $result = Resolve-SpoolJobState $job ([bool]$watch.deleting)
        $changed = $false
        if ([bool]$watch.deleting -ne [bool]$result.deleting) { $watch.deleting = [bool]$result.deleting; $changed = $true }
        $report = $result.state -ne 'queued' -and ($result.state -ne $watch.last_state -or $result.message -ne $watch.last_message)
        if ($report) {
            $body = @{ attempt = $watch.attempt; state = $result.state; message = $result.message }
            try { Invoke-ErpApi -Url "$Base/api/stock/labels/jobs/$($watch.job_id)/print-state" -Body $body -AuthToken $AuthToken -MachineId $watch.machine_id | Out-Null }
            catch {
                $code = Get-ErpErrorStatus $_
                if ($code -in @(403, 404, 409)) { Write-Log "Итог печати задания $($watch.job_id) не принят (HTTP $code)"; [IO.File]::Delete($file.FullName); continue }
                Write-Log "Итог печати не ушёл в ERP: $($_.Exception.Message)"; continue
            }
            Write-Log "Задание $($watch.job_id): $($result.state) $($result.message)"
            $watch.last_state = $result.state; $watch.last_message = $result.message; $changed = $true
        }
        if ($result.final) { [IO.File]::Delete($file.FullName) }
        elseif ($changed) { Save-PrintReceipt $file.FullName $watch }
    }
}

function Invoke-MarkingPollOnce {
    param([string]$Base, [string]$AuthToken, [string]$PrinterName, [string]$MarkingPrinterName, [switch]$DryRun)
    # An unresolved unknown pauses only the marking lane; warehouse TSPL labels keep printing.
    $paused = Sync-PrintReceipts $Base $AuthToken
    # Never let the status watcher stop the print lane.
    try { Update-PrintWatches $Base $AuthToken } catch { Write-Log "Слежение за очередью печати: $($_.Exception.Message)" }
    $printers = @(Get-AgentPrinters $PrinterName $MarkingPrinterName)
    $markingPresent = [bool]($printers | Where-Object { $_.present -and $_.kind -eq 'gdi_png' })
    # One marking label at a time (2.10.0): the next is claimed only after the previous one left the Windows queue,
    # so a reboot mid-spool can never reorder a long queue; a failed peel-off check holds the lane too.
    $hold = if ($markingPresent -and -not $paused) { Get-MarkingHold $Base $MarkingPrinterName } else { '' }
    if ($hold -and $hold -ne $script:LastMarkingHold) { Write-Log "Маркировка ждёт: $hold" }
    $script:LastMarkingHold = $hold
    $capabilities = @($printers | Where-Object { $_.present -and -not (($paused -or $hold) -and $_.kind -eq 'gdi_png') } | ForEach-Object { $_.kind })
    # A present marking queue keeps polling (empty capabilities): «Очистить принтер» must reach a held or paused lane.
    if (-not $capabilities.Count -and -not $markingPresent) { if ($paused) { return 'blocked' }; return 'idle' }
    $machine = Get-MachineId
    # The server routes TSPL by the exact queue name (and records the host), so both are always sent.
    $claimBody = @{ machine_id = $machine; capabilities = $capabilities; printer = [string]$PrinterName; host = [string]$env:COMPUTERNAME }
    $claim = Invoke-ErpApi -Url "$Base/api/stock/labels/jobs/claim" -Body $claimBody -AuthToken $AuthToken -MachineId $machine
    if ($null -ne $claim.marking_printers) { Update-MarkingPrinterSettings @($claim.marking_printers) $MarkingPrinterName -DryRun:$DryRun }
    if ($claim.command) {
        Invoke-PrinterCommand $Base $AuthToken $claim.command $MarkingPrinterName -DryRun:$DryRun
        return 'done'
    }
    $job = $claim.job
    if (-not $job) { if ($paused -or $hold) { return 'blocked' }; return 'idle' }
    # TSPL keeps its established receipt and payload shapes, even on a capable agent.
    if ($job.format -eq 'tspl' -or (-not $job.format -and $job.tspl_b64)) {
        Invoke-TsplJob $Base $AuthToken $PrinterName $job -DryRun:$DryRun
        return 'done'
    }
    if (-not $job.id -or -not $job.attempt) { throw 'Сервер вернул задание без id/attempt' }
    $path = Get-PrintReceiptPath ([string]$job.id) ([string]$job.attempt)
    if (Test-Path -LiteralPath $path) { return 'blocked' } # A repeated claim never prints twice.
    $receipt = @{ base = $Base; machine_id = $machine; job_id = [string]$job.id; printer = [string]$job.printer; acknowledged = $false; resolved = $false
        payload = @{ attempt = $job.attempt; outcome = 'failed'; message = '' } }
    try {
        # Never print marking that was not asked for (lane paused or queue missing): it becomes a failed receipt.
        if ($capabilities -notcontains 'gdi_png') { throw 'Маркировка на этом ПК на паузе или очередь Godex не найдена' }
        $bytes = ConvertFrom-MarkingJob $job $MarkingPrinterName
    }
    catch { $receipt.payload.message = $_.Exception.Message; Save-PrintReceipt $path $receipt; [void](Sync-PrintReceipts $Base $AuthToken); return 'done' }
    if ($DryRun) { $receipt.payload.message = 'dry run'; Save-PrintReceipt $path $receipt }
    else {
        $receipt.payload.outcome = 'unknown'; $receipt.payload.message = 'Результат неизвестен: агент прерван при передаче в очередь'
        Save-PrintReceipt $path $receipt
        $receipt.payload = Send-PngToPrinter $job $bytes
        $receipt.payload.attempt = $job.attempt
        $spoolId = 0
        if ($receipt.payload.outcome -eq 'spooled' -and [int]::TryParse([string]$receipt.payload.spool_job_id, [ref]$spoolId) -and $spoolId -gt 0) {
            # Watch before the receipt: after a crash the agent still reports how the job ended.
            Save-PrintWatch ([string]$job.id) ([string]$job.attempt) $Base $machine ([string]$job.printer) $spoolId
            $receipt.payload.tracked = $true
        }
        Save-PrintReceipt $path $receipt
    }
    [void](Sync-PrintReceipts $Base $AuthToken)
    return 'done'
}

# --- Godex control from ERP (2.10.0, owner 19.11.2026) -----------------------------------------------------------
# Peel-off: the ОБ.101 profile says the flags are handed out one by one through the dispenser. EZPL `^On`
# (GoDEX EZPL User Manual, «^On — label dispenser (peel) / applicator»: 0 off, 1 dispenser, 2 applicator) comes from
# the driver's DevMode. The agent runs as SYSTEM, whose own DevModePerUser once had it off while the printer default
# had it on: every job carried ^O0 and flags crumpled. So the driver output itself is checked: a blank page is
# rendered through the driver into a file (DOCINFO.lpszOutput) and its EZPL header must carry ^O1.
$script:PeelDocName = 'ERP peel check'
$script:CommandDocName = 'ERP printer command'
$script:PeelCaptureTimeoutSec = 20
$script:CommandSendTimeoutSec = 20
$script:PeelRetrySec = 60
$script:PeelRequired = @{}
$script:PeelInitialized = @{}
$script:PeelBlocked = @{}
$script:LastMarkingHold = ''

function Get-MarkingDocName { param([string]$JobId) return "ERP marking $JobId" }
function Remove-SpoolJob { param($Job) Remove-PrintJob -InputObject $Job -ErrorAction Stop }

# The first EZPL `^O<n>` at a line start of the driver output; $null when the driver sent none.
function Get-DispenserMode {
    param([byte[]]$Bytes)
    if (-not $Bytes) { return $null }
    $text = [Text.Encoding]::GetEncoding(28591).GetString($Bytes)
    $match = [regex]::Match($text, '(?:^|[\r\n])\^O(\d)')
    if ($match.Success) { return [int]$match.Groups[1].Value }
    return $null
}

function Format-PeelError {
    param($Found, [string]$Where = '')
    $seen = if ($null -eq $Found) { 'нет команды ^O' } else { "^O$Found" }
    return "Godex: отделитель (peel-off) выключен$Where — драйвер выдаёт $seen вместо ^O1. Этикетка не напечатана, " +
        'маркировка на этом принтере на паузе. Включите «Отделитель» в настройках печати Godex по умолчанию ' +
        '(Принтеры → Godex → Настройки печати), агент повторит проверку через минуту'
}

# Driver output of one blank page with this DEVMODE, never sent to the printer.
function Invoke-DriverCapture {
    param([string]$PrinterName, [byte[]]$Mode)
    Initialize-GdiPrinter
    $dir = Join-Path (Split-Path -Parent ([IO.Path]::GetFullPath($ConfigPath))) 'peel-check'
    [void][IO.Directory]::CreateDirectory($dir)
    $file = Join-Path $dir ([guid]::NewGuid().ToString('N') + '.prn')
    try {
        $id = [ErpGdiPrinter]::Capture($PrinterName, $Mode, $file, $script:PeelDocName)
        $deadline = (Get-Date).AddSeconds($script:PeelCaptureTimeoutSec)
        while ($true) {
            $left = @(Get-PrintJob -PrinterName $PrinterName -ErrorAction Stop | Where-Object {
                [int]$_.ID -eq $id -and [string]$_.DocumentName -eq $script:PeelDocName -and [string]$_.JobStatus -notmatch 'Printed|Complete' })
            if (-not $left.Count -and (Test-Path -LiteralPath $file)) { break }
            if ((Get-Date) -gt $deadline) {
                foreach ($job in $left) { try { Remove-SpoolJob $job } catch {} }
                throw "Проверка режима отделителя не завершилась за $($script:PeelCaptureTimeoutSec) с: очередь «$PrinterName» занята или приостановлена"
            }
            Start-Sleep -Milliseconds 200
        }
        return ,([IO.File]::ReadAllBytes($file))
    } finally { if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue } }
}

# Device boundaries, one function each so the tests replace them.
function Get-PeelCurrentMode { param([string]$PrinterName) Initialize-GdiPrinter; return ,([ErpGdiPrinter]::CurrentMode($PrinterName)) }
function Get-PeelDefaultMode { param([string]$PrinterName) Initialize-GdiPrinter; return ,([ErpGdiPrinter]::GlobalMode($PrinterName)) }
function Set-PeelUserMode { param([string]$PrinterName, [byte[]]$Mode) Initialize-GdiPrinter; [ErpGdiPrinter]::SetUserMode($PrinterName, $Mode) }
function Get-JobDriverMode { param($Job) Initialize-GdiPrinter; return ,([ErpGdiPrinter]::ValidateWithDriver($Job.printer, [double]$Job.width_mm, [double]$Job.height_mm)) }
function Get-AgentAccountSid { return [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
function Get-PerUserDevMode {
    param([string]$Hive, [string]$PrinterName)
    $key = "Registry::HKEY_USERS\$Hive\Printers\DevModePerUser"
    if (-not (Test-Path -LiteralPath $key)) { return $null }
    $item = Get-ItemProperty -LiteralPath $key -Name $PrinterName -ErrorAction SilentlyContinue
    if ($item) { return ,([byte[]]$item.$PrinterName) }
    return $null
}
function Set-PerUserDevMode {
    param([string]$Hive, [string]$PrinterName, [byte[]]$Mode)
    $key = "Registry::HKEY_USERS\$Hive\Printers\DevModePerUser"
    if (-not (Test-Path -LiteralPath $key)) { New-Item -Path $key -Force | Out-Null }
    New-ItemProperty -LiteralPath $key -Name $PrinterName -Value $Mode -PropertyType Binary -Force | Out-Null
}

# Before any DevMode write: the agent account's and .DEFAULT per-user DevMode of this queue, as they were.
function Save-DevModeBackup {
    param([string]$PrinterName)
    $root = Join-Path (Split-Path -Parent ([IO.Path]::GetFullPath($ConfigPath))) 'devmode-backup'
    $entries = @()
    foreach ($hive in @(@((Get-AgentAccountSid), '.DEFAULT') | Select-Object -Unique)) {
        $value = Get-PerUserDevMode $hive $PrinterName
        $entries += @{ hive = $hive; devmode_b64 = $(if ($value) { [Convert]::ToBase64String($value) } else { $null }) }
    }
    $path = Join-Path $root ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.json')
    Save-PrintReceipt $path @{ printer = $PrinterName; saved_at = (Get-Date).ToUniversalTime().ToString('o'); per_user = $entries }
    return $path
}

# The queue's current DevMode must make the driver emit ^O1. If not, and the printer's default DevMode does, the
# default becomes the per-user DevMode of the agent's account and .DEFAULT (backup first). Throws a Russian reason.
function Initialize-PeelMode {
    param([string]$PrinterName)
    $found = Get-DispenserMode (Invoke-DriverCapture $PrinterName (Get-PeelCurrentMode $PrinterName))
    if ($found -eq 1) { return 'ok' }
    $backup = Save-DevModeBackup $PrinterName
    $default = Get-PeelDefaultMode $PrinterName
    $defaultFound = Get-DispenserMode (Invoke-DriverCapture $PrinterName $default)
    if ($defaultFound -ne 1) { throw (Format-PeelError $defaultFound ' и в настройках агента, и в настройках принтера по умолчанию') }
    foreach ($hive in @(@((Get-AgentAccountSid), '.DEFAULT') | Select-Object -Unique)) { Set-PerUserDevMode $hive $PrinterName $default }
    try { Set-PeelUserMode $PrinterName $default } catch { Write-Log "Godex '$PrinterName': SetPrinter(9) не принят: $($_.Exception.Message)" }
    $after = Get-DispenserMode (Invoke-DriverCapture $PrinterName (Get-PeelCurrentMode $PrinterName))
    if ($after -ne 1) { throw (Format-PeelError $after ' после восстановления из настроек по умолчанию') }
    $was = if ($null -eq $found) { 'без ^O' } else { "^O$found" }
    Write-Log "Godex '$PrinterName': отделитель в DevMode учётной записи агента был выключен ($was) — восстановлен из настроек принтера по умолчанию; копия прежних настроек: $backup"
    return 'repaired'
}

function Set-PeelBlocked {
    param([string]$PrinterName, [string]$Message)
    $script:PeelBlocked[$PrinterName] = @{ message = $Message; retry = (Get-Date).AddSeconds($script:PeelRetrySec) }
}

# Before each peel-off job: the very DEVMODE of the job must produce ^O1; one repair attempt, else the job fails
# (nothing is spooled) and the marking lane holds until a later check passes.
function Confirm-JobPeelMode {
    param($Job, [byte[]]$Mode)
    $printer = [string]$Job.printer
    try {
        $found = Get-DispenserMode (Invoke-DriverCapture $printer $Mode)
        if ($found -eq 1) { return ,$Mode }
        Write-Log "Godex '$printer': вывод драйвера для задания $($Job.id) без ^O1 — восстанавливаю режим отделителя"
        [void](Initialize-PeelMode $printer)
        $fresh = Get-JobDriverMode $Job
        $found = Get-DispenserMode (Invoke-DriverCapture $printer $fresh)
        if ($found -ne 1) { throw (Format-PeelError $found) }
        return ,$fresh
    } catch {
        Set-PeelBlocked $printer $_.Exception.Message
        throw
    }
}

# The claim reply says which marking queues run peel-off. The first time per process (agent start) the DevMode is
# checked and repaired.
function Update-MarkingPrinterSettings {
    param($Settings, [string]$MarkingPrinterName, [switch]$DryRun)
    if (-not $MarkingPrinterName) { return }
    $mine = @($Settings | Where-Object { [string]$_.name -ceq $MarkingPrinterName }) | Select-Object -First 1
    $peel = [bool]($mine -and $mine.peel)
    $script:PeelRequired[$MarkingPrinterName] = $peel
    if (-not $peel) { $script:PeelBlocked.Remove($MarkingPrinterName); return }
    if ($script:PeelInitialized[$MarkingPrinterName] -or $DryRun) { return }
    $script:PeelInitialized[$MarkingPrinterName] = $true
    try { Write-Log "Godex '$MarkingPrinterName': режим отделителя — $(Initialize-PeelMode $MarkingPrinterName)" }
    catch { Set-PeelBlocked $MarkingPrinterName $_.Exception.Message; Write-Log "Godex '$MarkingPrinterName': $($_.Exception.Message)" }
}

# '' when the marking lane may take the next label, else why it waits.
function Test-MarkingQueueBusy {
    param([string]$Base, [string]$PrinterName)
    $root = Get-PrintWatchRoot
    if (Test-Path -LiteralPath $root) {
        foreach ($file in @(Get-ChildItem -LiteralPath $root -Filter '*.json' -File)) {
            $watch = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8) | ConvertFrom-Json
            if ($watch.base -eq $Base -and $watch.machine_id -eq (Get-MachineId) -and [string]$watch.printer -ceq $PrinterName) {
                return "предыдущая этикетка (задание $($watch.job_id)) ещё в очереди Windows"
            }
        }
    }
    try { $jobs = @(Get-PrintJob -PrinterName $PrinterName -ErrorAction Stop) }
    catch { return "очередь «$PrinterName» не прочитана: $($_.Exception.Message)" }
    $ours = @($jobs | Where-Object { [string]$_.DocumentName -like 'ERP marking*' })
    if ($ours.Count) { return "в очереди Windows задание маркировки ($($ours[0].DocumentName))" }
    return ''
}

function Get-MarkingHold {
    param([string]$Base, [string]$PrinterName)
    $block = $script:PeelBlocked[$PrinterName]
    if ($block) {
        if ((Get-Date) -lt $block.retry) { return $block.message }
        try {
            [void](Initialize-PeelMode $PrinterName)
            $script:PeelBlocked.Remove($PrinterName)
            Write-Log "Godex '$PrinterName': режим отделителя в порядке, маркировка продолжается"
        } catch { Set-PeelBlocked $PrinterName $_.Exception.Message; return $_.Exception.Message }
    }
    return (Test-MarkingQueueBusy $Base $PrinterName)
}

# Removed jobs must end as «Отменено», never as «Напечатано», in the spool watch.
function Set-PrintWatchesDeleting {
    param([string]$PrinterName, [int[]]$SpoolIds)
    $root = Get-PrintWatchRoot
    if (-not (Test-Path -LiteralPath $root)) { return }
    foreach ($file in @(Get-ChildItem -LiteralPath $root -Filter '*.json' -File)) {
        $watch = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8) | ConvertFrom-Json
        if ([string]$watch.printer -ceq $PrinterName -and $SpoolIds -contains [int]$watch.spool_job_id) {
            $watch.deleting = $true
            Save-PrintReceipt $file.FullName $watch
        }
    }
}

function Remove-ErpMarkingJobs {
    param([string]$PrinterName)
    $jobs = @(Get-PrintJob -PrinterName $PrinterName -ErrorAction Stop | Where-Object {
        $name = [string]$_.DocumentName; $name -like 'ERP marking*' -or $name -eq $script:PeelDocName -or $name -eq $script:CommandDocName })
    if (-not $jobs.Count) { return 0 }
    Set-PrintWatchesDeleting $PrinterName @($jobs | ForEach-Object { [int]$_.ID })
    foreach ($job in $jobs) { Remove-SpoolJob $job }
    return $jobs.Count
}

function Wait-PrinterCommandSent {
    param([string]$PrinterName)
    $deadline = (Get-Date).AddSeconds($script:CommandSendTimeoutSec)
    while ($true) {
        $left = @(Get-PrintJob -PrinterName $PrinterName -ErrorAction Stop | Where-Object {
            [string]$_.DocumentName -eq $script:CommandDocName -and [string]$_.JobStatus -notmatch 'Printed|Complete' })
        if (-not $left.Count) { return }
        if ((Get-Date) -gt $deadline) {
            foreach ($job in $left) { try { Remove-SpoolJob $job } catch {} }
            throw "Godex не принял команду очистки за $($script:CommandSendTimeoutSec) с: проверьте, что принтер включён и на связи"
        }
        Start-Sleep -Milliseconds 250
    }
}

# «Очистить принтер» from ERP: remove this queue's ERP jobs from Windows, then empty the Godex's own buffer.
function Invoke-PrinterCommand {
    param([string]$Base, [string]$AuthToken, $Command, [string]$MarkingPrinterName, [switch]$DryRun)
    $machine = Get-MachineId
    $body = @{ machine_id = $machine; attempt = [string]$Command.attempt; outcome = 'failed'; message = ''; removed_jobs = 0; buffer_cleared = $false }
    try {
        if ([string]$Command.kind -cne 'clear') { throw "Неизвестная команда принтера: $($Command.kind)" }
        if (-not $MarkingPrinterName -or [string]$Command.printer -cne $MarkingPrinterName) {
            throw "Очередь «$($Command.printer)» не настроена для маркировки на этом ПК"
        }
        $body.removed_jobs = Remove-ErpMarkingJobs $MarkingPrinterName
        if ($DryRun) { throw "DRY RUN: удалено заданий $($body.removed_jobs), буфер Godex не очищался" }
        # GoDEX EZPL User Manual, «~S,n — analogue press control keys» (p. 34): n = BUFCLR — the printer stops printing
        # immediately, empties its buffer (serial and variable data too) and stays in standby. ~S,CANCEL is only the
        # Cancel key that clears an error state. https://www.manualsdir.com/manuals/736893/godex-ezpl.html?page=34
        Send-RawToPrinter -PrinterName $MarkingPrinterName -Bytes ([Text.Encoding]::ASCII.GetBytes("~S,BUFCLR`r`n")) -DocumentName $script:CommandDocName
        Wait-PrinterCommandSent $MarkingPrinterName
        $body.buffer_cleared = $true; $body.outcome = 'done'
        $body.message = "Удалено заданий ERP из очереди Windows: $($body.removed_jobs); буфер Godex очищен (~S,BUFCLR)"
    } catch { $body.message = $_.Exception.Message }
    Write-Log "Команда принтера $($Command.id) ($($Command.kind)): $($body.outcome) — $($body.message)"
    Invoke-ErpApi -Url "$Base/api/stock/labels/printer-commands/$($Command.id)/result" -Body $body -AuthToken $AuthToken -MachineId $machine | Out-Null
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
    Invoke-TsplJob $Base $AuthToken $PrinterName $job -DryRun:$DryRun
    return 'done'
}

function Invoke-TsplJob {
    param([string]$Base, [string]$AuthToken, [string]$PrinterName, $Job, [switch]$DryRun)
    $id = [string]$job.id
    $ok = $false; $err = ''
    try {
        if (-not $PrinterName -or ($job.printer -and $job.printer -cne $PrinterName) -or ($job.format -and $job.format -ne 'tspl')) {
            throw 'Неверная очередь или формат TSPL'
        }
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
}

# ---------------------------------------------------------------- Реестр агентов (сканер с любого ПК)

# Постоянный номер ПК: MachineGuid Windows (не меняется при смене сети, имени, пользователя).
# ПК-клоны одного образа (два SK-20250818JOKI у станков, 18.11.2026) имеют одинаковый MachineGuid — для них установщик
# с -NewMachineId кладёт свой GUID в machine-id.txt рядом с config.json, и он важнее MachineGuid.
function Get-MachineId {
    try {
        $override = Join-Path (Split-Path -Parent ([IO.Path]::GetFullPath($ConfigPath))) 'machine-id.txt'
        if (Test-Path -LiteralPath $override) {
            $own = ([string](Get-Content -LiteralPath $override -Raw)).Trim().ToLower()
            if ($own -match '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') { return $own }
        }
    } catch {}
    try {
        $guid = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid
        if ($guid) { return ([string]$guid).ToLower() }
    } catch {}
    return ([string]$env:COMPUTERNAME).ToLower()
}

# Виртуальные адаптеры (Hyper-V/WSL/Docker, VPN-туннели вроде happ-xray с «шлюзом» 0.0.0.0) не дают адрес ПК в сети цеха.
$script:VirtualAdapter = 'Hyper-V|vEthernet|WSL|Docker|VirtualBox|VMware|TAP-|Wintun|WireGuard|OpenVPN|xray|Happ|Loopback'

# Лучший адрес из кандидатов @{ Ip; Gateway; Virtual }: адаптер с настоящим шлюзом 192.168.x → 10.x → 172.16–31.x → иной;
# затем Tailscale 100.64/10; затем адаптер без шлюза. Виртуальные, 127.x, 169.254.x отбрасываются. Нет кандидатов — ''.
function Select-LanIp {
    param($Candidates)
    $best = ''; $bestRank = [int]::MaxValue
    foreach ($item in @($Candidates)) {
        $text = [string]$item.Ip; $ip = $null
        if (-not [Net.IPAddress]::TryParse($text, [ref]$ip) -or $ip.AddressFamily -ne 'InterNetwork') { continue }
        $b = $ip.GetAddressBytes()
        if ($b[0] -eq 127 -or $b[0] -eq 0 -or ($b[0] -eq 169 -and $b[1] -eq 254)) { continue }
        if ($b[0] -eq 100 -and $b[1] -ge 64 -and $b[1] -le 127) { $rank = 5 }
        elseif ($item.Virtual) { continue }
        else {
            $rank = if ($b[0] -eq 192 -and $b[1] -eq 168) { 0 } elseif ($b[0] -eq 10) { 1 }
                elseif ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) { 2 } else { 3 }
            if (-not $item.Gateway) { $rank += 10 }
        }
        if ($rank -lt $bestRank) { $best = $text; $bestRank = $rank }
    }
    return $best
}

# Адрес ПК в локальной сети (Select-LanIp); запасной путь - DNS-имя ПК.
function Get-LanIp {
    try {
        $found = @(Get-NetIPConfiguration -ErrorAction Stop | Where-Object { $_.NetAdapter.Status -eq 'Up' } | ForEach-Object {
            $config = $_
            $gateway = @($config.IPv4DefaultGateway | Where-Object { $_.NextHop -and [string]$_.NextHop -ne '0.0.0.0' }).Count -gt 0
            $virtual = "$($config.InterfaceAlias) $($config.InterfaceDescription)" -match $script:VirtualAdapter
            foreach ($address in @($config.IPv4Address)) {
                if ($address) { @{ Ip = [string]$address.IPAddress; Gateway = $gateway; Virtual = $virtual } }
            }
        })
        $pick = Select-LanIp $found
        if ($pick) { return $pick }
    } catch {}
    try {
        $names = @([Net.Dns]::GetHostAddresses($env:COMPUTERNAME) | Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
            ForEach-Object { @{ Ip = $_.IPAddressToString; Gateway = $true; Virtual = $false } })
        $pick = Select-LanIp $names
        if ($pick) { return $pick }
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

# Touchstone-файлы Arinst, сохранённые за последние 15 минут (папки arinstFolders и один уровень вложенности).
function Find-ArinstTraces {
    $now = [DateTime]::UtcNow
    return @(foreach ($folder in (Get-ArinstFolders)) {
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
}

# Arinst на этом ПК есть на деле, а не только установлен: Arinst Virtual Lab запущена (без прибора она не меряет) или
# за 15 минут сохранена трасса, которую можно снять. Одна установленная программа прибор не означает (владелец 18.11:
# ПК владельца с Arinst VL без прибора показывался в «Станках» вторым Arinst).
function Test-ArinstPresent {
    if (-not (Test-ArinstInstalled)) { return $false }
    if (Get-Process -Name 'ArinstVL' -ErrorAction SilentlyContinue) { return $true }
    try { return @(Find-ArinstTraces).Count -gt 0 } catch { return $false }
}

function Invoke-ArinstCapture {
    try {
        $files = Find-ArinstTraces
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
    if (($script:VnaWorker -and $script:VnaWorker.command) -or (Test-Path -LiteralPath (Join-Path (Get-VnaControlRoot) 'owner.json'))) {
        Write-Log 'Обновление отложено: сессия ВАЦ ещё владеет прибором'
        return $false
    }
    if ($script:VnaWorker) { Stop-VnaWorker $script:VnaWorker 'ВАЦ: проверка остановлена для обновления' }
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
    param([string]$Base, [string]$AuthToken, [string]$PrinterName, [string]$ScannerAddress, [switch]$Lightweight, [string]$MarkingPrinterName)
    $devices = $script:HeartbeatDevices
    if (-not $Lightweight) {
        $devices = @{
            scanners = @(Get-AgentScanners -ScannerAddress $ScannerAddress); printer = [string]$PrinterName
            lan_ip = (Get-LanIp); arinst = (Test-ArinstPresent)
            printers = @(Get-AgentPrinters $PrinterName $MarkingPrinterName)
        }
        $script:HeartbeatDevices = $devices
    }
    # A restarted parent may already own a session before its first full discovery.
    if (-not $devices) { $devices = @{ scanners = @(); printer = [string]$PrinterName; printers = @(); lan_ip = ''; arinst = $false } }
    $scanners = $devices.scanners
    $body = @{
        machine_id = (Get-MachineId); pc_name = [string]$env:COMPUTERNAME; version = $script:AgentVersion
        lan_ip = $devices.lan_ip; printer = $devices.printer; scanners = $scanners
        printers = @($devices.printers | Where-Object { $null -ne $_ })
    }
    $vna = if ($script:VnaControlInitialized -or $Lightweight) {
        $observed = Get-VnaObservation
        if ($observed) { @{ name = $observed.name; ready = [bool]$observed.ready } }
    } else { Get-VnaCapability }
    if ($vna) { $body.vna = $vna }
    if (($script:VnaControlInitialized -or $Lightweight) -and $observed) {
        $control = Get-VnaControlCapability $observed
        if ($control) { $body.vna_control = $control }
    }
    # Recovery still needs a live protocol report when the instrument observation is unavailable.
    # Empty methods allow no new calibration; readiness and serial are never inferred.
    if ($Lightweight -and -not $body.vna_control) { $body.vna_control = @{ version = 1; methods = @() } }
    $body.vna_sources = @()
    if ($vna) { $body.vna_sources += 's2vna' }
    if ($devices.arinst) { $body.vna_sources += 'arinst' }
    $reply = Invoke-ErpApi -Url "$Base/api/stock/scan/agent/heartbeat" -Body $body -AuthToken $AuthToken
    $names = ($scanners | ForEach-Object { $_.name }) -join '; '
    $line = "Отчёт серверу: сканеры: $(if ($names) { $names } else { 'не найдены' })"
    if ($line -ne $script:LastHeartbeatLine) { Write-Log $line; $script:LastHeartbeatLine = $line }
    if (-not $Lightweight -and $reply -and $reply.update) { [void](Invoke-AgentUpdate -Base $Base -Update $reply.update) }
}

function Start-AgentLoop {
    param([string]$Base, [string]$AuthToken, [string]$PrinterName, [switch]$DryRun, [switch]$Once, [string]$ScannerAddress, [string]$MarkingPrinterName)
    if ($PrinterName -and $PrinterName -eq $MarkingPrinterName) { throw 'Очереди TSC и маркировки должны различаться' }
    $delay = 3
    $nextBeat = Get-Date
    $lastBeat = $nextBeat
    $lastBeatWasLightweight = $false
    Initialize-VnaControl
    $controlDelay = 0
    Write-Log "Агент $($script:AgentVersion) запущен: сервер $Base, принтер '$PrinterName'$(if ($DryRun) { ', DRY RUN' }), сканер: $(if ($ScannerAddress) { $ScannerAddress } else { 'USB (WIA), автопоиск' })"
    while ($true) {
        $controlError = $false
        try { Update-VnaWorker $Base $AuthToken }
        catch { Write-Log "ВАЦ: связь/исполнитель: $($_.Exception.Message)"; $controlError = $true }
        $freshObservation = Get-VnaObservation
        if ($freshObservation -and $freshObservation.ready -and -not $script:VnaFirstObservationReported) {
            $nextBeat = Get-Date; $script:VnaFirstObservationReported = $true
        }
        # Winspool and scanner discovery can block without a timeout. Keep them out of
        # the control path, including gaps between commands while the session still needs restoration.
        $owner = Read-VnaJson (Join-Path (Get-VnaControlRoot) 'owner.json')
        $controlActive = ($script:VnaWorker -and $script:VnaWorker.command) -or ($owner -and -not $owner.restored)
        $beatEvery = if ($controlActive) { [Math]::Min(60, $script:HeartbeatEverySec) } else { $script:HeartbeatEverySec }
        if ($controlActive -and $nextBeat -gt $lastBeat.AddSeconds($beatEvery)) { $nextBeat = $lastBeat.AddSeconds($beatEvery) }
        if ((Get-Date) -ge $nextBeat -or ($lastBeatWasLightweight -and -not $controlActive)) {
            try { Send-Heartbeat -Base $Base -AuthToken $AuthToken -PrinterName $PrinterName -ScannerAddress $ScannerAddress -Lightweight:$controlActive -MarkingPrinterName $MarkingPrinterName }
            catch { Write-Log "Отчёт серверу не ушёл: $($_.Exception.Message)" }
            $lastBeat = Get-Date; $lastBeatWasLightweight = [bool]$controlActive
            $nextBeat = $lastBeat.AddSeconds($beatEvery)
        }
        $state = 'error'
        try {
            # Без принтера (ПК только со сканером) этикетки не опрашиваем.
            $state = if ($MarkingPrinterName -and -not $controlActive) {
                Invoke-MarkingPollOnce -Base $Base -AuthToken $AuthToken -PrinterName $PrinterName -MarkingPrinterName $MarkingPrinterName -DryRun:$DryRun
            } elseif ($PrinterName -and -not $controlActive) {
                Invoke-PollOnce -Base $Base -AuthToken $AuthToken -PrinterName $PrinterName -DryRun:$DryRun
            } else { 'idle' }
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
        if ($scanState -eq 'error' -or $controlError) { $controlDelay = [Math]::Min(30, [Math]::Max(3, $controlDelay * 2)) } else { $controlDelay = 0 }
        $pause = if ($controlDelay) { 1000 * $controlDelay } elseif ($script:VnaWorker -and $script:VnaWorker.command) { 500 } elseif ($script:VnaPollAfterMs -eq 500) { 500 } else { 3000 }
        # While a control command runs or the session needs restoration, only bounded network calls precede this wait.
        # Even under backoff, service the worker watchdog every 500 ms.
        for ($elapsed = 0; $elapsed -lt $pause; $elapsed += 500) {
            Start-Sleep -Milliseconds 500
            if ($script:VnaWorker -and ([DateTime]::UtcNow - $script:VnaWorker.started).TotalSeconds -gt 120) {
                Stop-VnaWorker $script:VnaWorker 'ВАЦ: превышено время выполнения; результат неизвестен'
            }
        }
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
        $claimBody = @{ host = $env:COMPUTERNAME; machine_id = $machineId; kinds = @('scan', 'vna') }
        if ($script:VnaControlInitialized -and -not $DryRun) {
            if ((Test-Path -LiteralPath (Join-Path (Get-VnaControlRoot) 'owner.json')) -or
                (Test-Path -LiteralPath (Join-Path (Get-VnaControlRoot) 'quarantine.json')) -or
                ($script:VnaWorker -and $script:VnaWorker.command)) { $claimBody.kinds = @('vna_control') }
            $observation = Get-VnaObservation
            if ($observation -and $observation.ready) {
                if ($claimBody.kinds -notcontains 'vna_control') { $claimBody.kinds += 'vna_control' }
                $claimBody.observed_vna_serial = $observation.serial
            }
        }
        $claim = Invoke-ErpApi -Url "$Base/api/stock/scan/agent/claim" -Body $claimBody -AuthToken $AuthToken
        $script:VnaPollAfterMs = $claim.poll_after_ms
        $script:ScanClaimError = ''
        if (-not $claim.job) { return 'idle' }
        if ($claim.job.kind -eq 'vna_control') {
            if ($DryRun) { throw 'ВАЦ: управление запрещено в пробном режиме' }
            $command = $claim.job
            [void][guid]::Parse($command.command_id); [void][guid]::Parse($command.session_id)
            if ($command.protocol_version -ne 1 -or $command.id -cne $command.command_id) { throw 'ВАЦ: неподдерживаемый конверт команды' }
            if ($script:VnaWorker -and $script:VnaWorker.command) {
                if ($script:VnaWorker.command.command_id -cne $command.command_id) {
                    Stop-VnaWorker $script:VnaWorker 'ВАЦ: команда заменена сервером; выполнение неизвестно'
                } else { return 'active' }
            }
            $entry = Read-VnaJson (Get-VnaCommandPath $command)
            if ($entry) { [void](Get-VnaJournal $command); Sync-VnaReceipts $Base $AuthToken; return 'active' }
            if ($script:VnaWorker) { Stop-VnaWorker $script:VnaWorker 'ВАЦ: проверка прибора завершена' }
            Start-VnaWorker $Base $AuthToken $command
            return 'active'
        }
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
        if (($script:VnaWorker -and $script:VnaWorker.command) -or
            ($script:VnaControlInitialized -and (Test-Path -LiteralPath (Join-Path (Get-VnaControlRoot) 'owner.json')))) {
            # P2 can fall through to scanner jobs even with kinds=['vna_control'].
            throw 'Сканирование отложено: ПК занят управляемой сессией ВАЦ; повторите после её завершения'
        }
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

# VNA control stays in this file: the existing release API ships one hashed script.
function Get-VnaControlRoot {
    $root = Join-Path (Split-Path -Parent ([IO.Path]::GetFullPath($ConfigPath))) 'vna-control'
    [void][IO.Directory]::CreateDirectory($root)
    return $root
}
function Write-VnaJson {
    param([string]$Path, $Value)
    $tmp = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    $json = ConvertTo-Json -InputObject $Value -Depth 70 -Compress
    $stream = [IO.File]::Open($tmp, 'CreateNew', 'Write', 'None')
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($json)
        $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true)
    } finally { $stream.Dispose() }
    if ([IO.File]::Exists($Path)) { [IO.File]::Replace($tmp, $Path, [NullString]::Value) }
    else { [IO.File]::Move($tmp, $Path) }
}
function Read-VnaJson {
    param([string]$Path)
    if ([IO.File]::Exists($Path)) { return ([IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8) | ConvertFrom-Json) }
    return $null
}
function Get-VnaDigest {
    param([string]$Text)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-', '').ToLowerInvariant() }
    finally { $hash.Dispose() }
}
function ConvertTo-VnaCanonical {
    param($Value)
    # Stable local fingerprints. Registry fingerprints are pinned in the commissioned manifest,
    # rather than recomputed with PowerShell's different JSON number/Unicode serialization.
    if ($null -eq $Value) { return 'null' }
    if ($Value -is [Collections.IDictionary] -or $Value -is [pscustomobject]) {
        $names = if ($Value -is [Collections.IDictionary]) { @($Value.Keys) } else { @($Value.PSObject.Properties.Name) }
        $parts = foreach ($name in ($names | Sort-Object -CaseSensitive)) {
            (ConvertTo-Json -InputObject ([string]$name) -Compress) + ':' + (ConvertTo-VnaCanonical $Value.$name)
        }
        return '{' + ($parts -join ',') + '}'
    }
    if ($Value -is [Collections.IEnumerable] -and $Value -isnot [string]) {
        $parts = foreach ($item in $Value) { ConvertTo-VnaCanonical $item }
        return '[' + ($parts -join ',') + ']'
    }
    return (ConvertTo-Json -InputObject $Value -Compress -Depth 5)
}
function Assert-VnaEqual {
    param($Actual, $Expected, [string]$Label)
    if ((ConvertTo-VnaCanonical $Actual) -cne (ConvertTo-VnaCanonical $Expected)) {
        # COM numeric Variants may be integer or double for the same exact value.
        if ($Actual -is [ValueType] -and $Expected -is [ValueType] -and
            $Actual -isnot [bool] -and $Expected -isnot [bool] -and [double]$Actual -eq [double]$Expected) { return }
        throw "ВАЦ: проверка чтением не совпала: $Label"
    }
}
function Get-VnaSerial {
    param($Application)
    $parts = ([string]$Application.Name).Split(',')
    if (-not $Application.Ready -or $parts.Count -ne 4 -or -not $parts[2].Trim()) { throw 'ВАЦ: прибор не готов или серийный номер не прочитан' }
    return $parts[2].Trim()
}
function Test-VnaNormalToken {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return -not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function New-VnaDeviceMutex {
    param([string]$Serial)
    return New-Object Threading.Mutex($false, ('Global\FraxisVna-' + (Get-VnaDigest $Serial)))
}
function Invoke-S2Io {
    param($Scpi, [string]$Path, [ValidateSet('get','set','call')][string]$Mode = 'get', $Value)
    # Paths are fixed driver literals; no command envelope can supply a COM path.
    $parts = $Path.Split('.'); $node = $Scpi
    for ($i = 0; $i -lt $parts.Count; $i++) {
        $part = $parts[$i]; $last = $i -eq $parts.Count - 1
        if ($part -match '^([A-Za-z0-9]+)\(([0-9]+)(?:,([12]))?\)$') {
            $member = $Matches[1]; $index = [int]$Matches[2]; $secondIndex = $Matches[3]
            if ($last -and $Mode -eq 'set') {
                $arguments = @($index); if ($secondIndex) { $arguments += [int]$secondIndex }; $arguments += ,$Value
                $node.GetType().InvokeMember($member, [Reflection.BindingFlags]::SetProperty, $null, $node, $arguments) | Out-Null
                return
            }
            $node = if ($secondIndex) { $node.$member($index, [int]$secondIndex) } else { $node.$member($index) }
        } elseif (-not $last) { $node = $node.$part }
        elseif ($Mode -eq 'set') { $node.$part = $Value; return }
        elseif ($Mode -eq 'call') { $node.$part() | Out-Null; return }
        else { return $node.$part }
    }
    return $node
}
function Set-S2Checked {
    param($Scpi, [string]$Path, $Value)
    Invoke-S2Io $Scpi $Path set $Value
    Assert-VnaEqual (Invoke-S2Io $Scpi $Path) $Value $Path
}
function Set-S2Ports {
    param($Scpi, [string]$Path, [int[]]$Ports)
    # Never retry an acquisition after an arbitrary COM exception: it may have executed.
    # The commissioned int[] Variant binding is used once. Ambiguous errors require recovery.
    Invoke-S2Io $Scpi $Path set ([int[]]$Ports)
}
function Invoke-S2KitFile {
    param($Scpi, [ValidateSet('STORe','LOAD')][string]$Direction, [int]$Slot, [string]$Path)
    $target = $Scpi.MMEMory.$Direction
    $target.GetType().InvokeMember('CKIT', [Reflection.BindingFlags]::SetProperty, $null, $target, @($Slot, $Path)) | Out-Null
}
function Get-S2ManualDefinition {
    param($Scpi, $Kit, $Profile)
    $prefix = 'SENSe(1).CORRection.COLLect.CKIT'
    Set-S2Checked $Scpi "$prefix.SELect" ([int]$Kit.slot)
    Assert-VnaEqual (Invoke-S2Io $Scpi "$prefix.LABel") $Kit.definition.label 'название набора SOLT'
    $actualStandards = @()
    foreach ($standard in @($Kit.definition.standards)) {
        if ($standard.index -lt 1 -or $standard.index -gt 100 -or $standard.properties.TYPE -cnotin @('OPEN','SHORT','LOAD','THRU')) { throw 'ВАЦ: неверный манифест мер SOLT' }
        $required = @('LABel','TYPE','FMINimum','FMAXimum','DELay','LOSS','Z0')
        if ($standard.properties.TYPE -ceq 'OPEN') { $required += @('C0','C1','C2','C3') }
        if ($standard.properties.TYPE -ceq 'SHORT') { $required += @('L0','L1','L2','L3') }
        $values = [ordered]@{}
        foreach ($property in $required) {
            if ($null -eq $standard.properties.$property) { throw 'ВАЦ: неполное определение меры SOLT' }
            $value = Invoke-S2Io $Scpi "$prefix.STAN($($standard.index)).$property"
            Assert-VnaEqual $value $standard.properties.$property ('мера SOLT ' + $property)
            $values[$property] = $value
        }
        if ($values.FMINimum -gt $Profile.start_hz -or $values.FMAXimum -lt $Profile.stop_hz -or $values.Z0 -ne 50) { throw 'ВАЦ: мера SOLT не покрывает профиль' }
        $actualStandards += @{ index = [int]$standard.index; properties = $values }
    }
    if (-not $actualStandards.Count) { throw 'ВАЦ: манифест SOLT пуст' }
    return @{ label = $Kit.definition.label; standards = $actualStandards }
}
function Get-S2TraceState {
    param($Scpi)
    $paths = @('FORMat','SMOothing.STATe','MATH.FUNCtion','CONVersion.STATe','FILTer.GATE.TIME.STATe',
        'LIMit.DATA','LIMit.DISPlay.STATe','CORRection.STATus')
    $state = [ordered]@{}
    foreach ($path in $paths) { $state[$path] = Invoke-S2Io $Scpi ('CALCulate(1).SELected.' + $path) }
    return $state
}
function Get-S2State {
    param($Scpi)
    $paths = @('SENSe(1).SWEep.TYPE','SENSe(1).SWEep.POINts','SENSe(1).FREQuency.STARt','SENSe(1).FREQuency.STOP',
        'SENSe(1).BANDwidth.RESolution','SENSe(1).AVERage.STATe','SENSe(1).AVERage.COUNt',
        'SENSe(1).CORRection.IMPedance.INPut.MAGNitude','SENSe(1).CORRection.STATe',
        'SENSe(1).CORRection.COLLect.CKIT.SELect','SENSe(1).CORRection.COLLect.CKIT.LABel',
        'SENSe(1).CORRection.TRIGger.FREE.STATe','INITiate(1).CONTinuous','TRIGger.SEQuence.SOURce',
        'MMEMory.STORe.STYPe','CALCulate(1).FSIMulator.STATe','CALCulate(1).PARameter(1).COUNt')
    foreach ($port in 1,2) {
        foreach ($tail in 'COUPle','LEVel.IMMediate.AMPLitude','LEVel.SLOPe.STATe') { $paths += "SOURce(1).POWer.PORT($port).$tail" }
    }
    $values = [ordered]@{}
    foreach ($path in $paths) { $values[$path] = Invoke-S2Io $Scpi $path }
    $selected = [int](Invoke-S2Io $Scpi 'SERVice.CHANnel(1).TRACe(1).ACTive')
    $count = [int]$values['CALCulate(1).PARameter(1).COUNt']
    if ($count -lt 1 -or $count -gt 16 -or $selected -lt 1 -or $selected -gt $count) { throw 'ВАЦ: невозможно сохранить набор трасс' }
    $traces = @()
    try {
        for ($traceIndex = 1; $traceIndex -le $count; $traceIndex++) {
            $parameter = Invoke-S2Io $Scpi "CALCulate(1).PARameter($traceIndex).DEFine"
            Invoke-S2Io $Scpi "CALCulate(1).PARameter($traceIndex).SELect" call
            $traces += @{ parameter = $parameter; settings = (Get-S2TraceState $Scpi) }
        }
    } finally { Invoke-S2Io $Scpi "CALCulate(1).PARameter($selected).SELect" call }
    Assert-VnaEqual (Invoke-S2Io $Scpi 'SERVice.CHANnel(1).TRACe(1).ACTive') $selected 'выбранная трасса после чтения состояния'
    return @{ values = $values; selected = $selected; traces = $traces }
}
function Get-S2ProfileWrites {
    param($Profile)
    $writes = [ordered]@{
        'SENSe(1).SWEep.TYPE' = 'LIN'; 'SENSe(1).FREQuency.STARt' = $Profile.start_hz
        'SENSe(1).FREQuency.STOP' = $Profile.stop_hz; 'SENSe(1).SWEep.POINts' = $Profile.points
        'SENSe(1).BANDwidth.RESolution' = $Profile.if_bandwidth_hz; 'SENSe(1).AVERage.STATe' = $false
        'SENSe(1).CORRection.IMPedance.INPut.MAGNitude' = 50; 'SOURce(1).POWer.PORT(1).COUPle' = $false
    }
    foreach ($port in 1,2) {
        $writes["SOURce(1).POWer.PORT($port).LEVel.IMMediate.AMPLitude"] = $Profile.power_dbm
        $writes["SOURce(1).POWer.PORT($port).LEVel.SLOPe.STATe"] = $false
    }
    $writes['CALCulate(1).PARameter(1).COUNt'] = @($Profile.traces).Count
    $writes['CALCulate(1).FSIMulator.STATe'] = $false
    return $writes
}
function Assert-S2Profile {
    param($Scpi, $Profile, [switch]$Apply)
    if ($Profile.sweep -cne 'LIN' -or $Profile.impedance_ohm -ne 50 -or $Profile.averaging -ne $false -or
        $Profile.smoothing -ne $false -or $Profile.processing -cne 'off' -or $Profile.start_hz -le 0 -or
        $Profile.stop_hz -le $Profile.start_hz -or $Profile.points -lt 2 -or $Profile.points -gt 200001 -or
        $Profile.if_bandwidth_hz -lt 1 -or $Profile.if_bandwidth_hz -gt 1000 -or
        $Profile.power_dbm -lt -30 -or $Profile.power_dbm -gt 0 -or
        @($Profile.traces).Count -notin @(1,4) -or @($Profile.traces | Select-Object -Unique).Count -ne @($Profile.traces).Count -or
        @($Profile.traces | Where-Object { $_ -cnotin @('S11','S21','S12','S22') }).Count) { throw 'ВАЦ: недопустимый профиль' }
    foreach ($number in @($Profile.start_hz,$Profile.stop_hz,$Profile.points,$Profile.if_bandwidth_hz,$Profile.power_dbm,$Profile.vswr_limit)) {
        if ([double]::IsNaN([double]$number) -or [double]::IsInfinity([double]$number)) { throw 'ВАЦ: недопустимое число профиля' }
    }
    if ($Apply) {
        if ($Profile.points -gt (Invoke-S2Io $Scpi 'SERVice.SWEep.POINts') -or
            $Profile.start_hz -lt (Invoke-S2Io $Scpi 'SERVice.SWEep.FREQuency.MINimum') -or
            $Profile.stop_hz -gt (Invoke-S2Io $Scpi 'SERVice.SWEep.FREQuency.MAXimum')) { throw 'ВАЦ: профиль превышает возможности прибора' }
        Invoke-S2Io $Scpi 'ABORt' call
        Set-S2Checked $Scpi 'INITiate(1).CONTinuous' $false
    }
    $writes = Get-S2ProfileWrites $Profile
    foreach ($path in $writes.Keys) {
        if ($Apply) { Set-S2Checked $Scpi $path $writes[$path] }
        else { Assert-VnaEqual (Invoke-S2Io $Scpi $path) $writes[$path] $path }
    }
    for ($traceIndex = 1; $traceIndex -le @($Profile.traces).Count; $traceIndex++) {
        $parameter = [string]$Profile.traces[$traceIndex - 1]
        $path = "CALCulate(1).PARameter($traceIndex).DEFine"
        if ($Apply) { Set-S2Checked $Scpi $path $parameter } else { Assert-VnaEqual (Invoke-S2Io $Scpi $path) $parameter $path }
        Invoke-S2Io $Scpi "CALCulate(1).PARameter($traceIndex).SELect" call
        $format = if ($parameter -in @('S11','S22')) { 'SWR' } else { 'MLOG' }
        $settings = [ordered]@{ FORMat = $format; 'SMOothing.STATe' = $false; 'MATH.FUNCtion' = 'OFF'
            'CONVersion.STATe' = $false; 'FILTer.GATE.TIME.STATe' = $false
            'LIMit.DATA' = @(1,1,$Profile.start_hz,$Profile.stop_hz,$Profile.vswr_limit,$Profile.vswr_limit); 'LIMit.DISPlay.STATe' = $true }
        foreach ($tail in $settings.Keys) {
            $path = 'CALCulate(1).SELected.' + $tail
            if ($Apply) { Set-S2Checked $Scpi $path $settings[$tail] } else { Assert-VnaEqual (Invoke-S2Io $Scpi $path) $settings[$tail] $path }
        }
    }
}
function Get-S2Correction {
    param($Scpi, $Traces)
    $found = @{}; $count = [int](Invoke-S2Io $Scpi 'CALCulate(1).PARameter(1).COUNt')
    for ($traceIndex = 1; $traceIndex -le $count; $traceIndex++) {
        $parameter = [string](Invoke-S2Io $Scpi "CALCulate(1).PARameter($traceIndex).DEFine")
        if ($parameter -in $Traces) {
            if ($found.ContainsKey($parameter)) { throw 'ВАЦ: повторная трасса' }
            Invoke-S2Io $Scpi "CALCulate(1).PARameter($traceIndex).SELect" call
            $status = [string](Invoke-S2Io $Scpi 'CALCulate(1).SELected.CORRection.STATus')
            if ($status -cne 'COR') { throw "ВАЦ: нет полной коррекции $parameter ($status)" }
            $found[$parameter] = $status
        }
    }
    if ($found.Count -ne @($Traces).Count) { throw 'ВАЦ: требуемые трассы отсутствуют' }
    return $found
}
function ConvertFrom-S2AcmInfo {
    param([string]$Info)
    $fields = @($Info | ConvertFrom-Csv -Header (0..30 | ForEach-Object { "f$_" }))
    if ($fields.Count -ne 1) { throw 'ВАЦ: неверный ответ АКМ' }
    $row = $fields[0]
    $numbers = @()
    foreach ($field in @($row.f5, $row.f6)) {
        if ($field -notmatch '^\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\s*([kMG]?Hz)?\s*$') { throw 'ВАЦ: не прочитана полоса АКМ' }
        $number = [double]::Parse($Matches[1], [Globalization.CultureInfo]::InvariantCulture)
        $scale = switch -CaseSensitive ($Matches[2]) { 'kHz' { 1e3 } 'MHz' { 1e6 } 'GHz' { 1e9 } default { 1 } }
        $numbers += $number * $scale
    }
    if (-not $row.f0 -or -not $row.f1 -or $numbers[0] -ge $numbers[1]) { throw 'ВАЦ: не прочитаны сведения АКМ' }
    return @{ model = $row.f0.Trim(); serial = $row.f1.Trim(); min_hz = $numbers[0]; max_hz = $numbers[1]; raw = $Info }
}
function Assert-S2Acm {
    param($Scpi, [string]$Serial, [double]$StartHz, [double]$StopHz, $Definition)
    $info = ConvertFrom-S2AcmInfo ([string](Invoke-S2Io $Scpi 'SENSe(1).CORRection.COLLect.ECAL.INFormation'))
    if ($info.serial -cne $Serial -or $info.min_hz -gt $StartHz -or $info.max_hz -lt $StopHz -or $StartHz -ge $StopHz) { throw 'ВАЦ: серийный номер или полоса АКМ не соответствуют плану' }
    if ($Definition) {
        Assert-VnaEqual $info.model $Definition.model 'модель АКМ'
        Assert-VnaEqual $info.serial $Definition.serial 'номер АКМ'
        Assert-VnaEqual $info.min_hz $Definition.min_hz 'нижняя частота АКМ'
        Assert-VnaEqual $info.max_hz $Definition.max_hz 'верхняя частота АКМ'
        # Temperature is live telemetry, not a coefficient/characterization identity.
        $live = @($info.raw | ConvertFrom-Csv -Header (0..30 | ForEach-Object { "f$_" }))[0]
        foreach ($entry in $Definition.identity_fields.PSObject.Properties) {
            if ($entry.Name -notmatch '^f(?:[0-9]|[12][0-9]|30)$' -or $entry.Name -eq 'f2') { throw 'ВАЦ: неверный манифест АКМ' }
            Assert-VnaEqual ([string]$live.($entry.Name)).Trim() ([string]$entry.Value).Trim() ('АКМ ' + $entry.Name)
        }
        if (-not $Definition.identity_fields.f3 -or -not $Definition.identity_fields.f4 -or -not $Definition.identity_fields.f7) { throw 'ВАЦ: манифест не содержит характеризацию, дату и число точек АКМ' }
    }
    return $info
}
function ConvertTo-S2Artifact {
    param($Frequencies, $Data, $Profile)
    $frequenciesArray = @($Frequencies); $traces = @($Profile.traces)
    if ($frequenciesArray.Count -ne $Profile.points) { throw 'ВАЦ: неполная сетка частот' }
    $order = if ($traces.Count -eq 4) { @('S11','S21','S12','S22') } else { $traces }
    foreach ($traceName in $order) { if (@($Data[$traceName]).Count -ne 2 * $frequenciesArray.Count) { throw 'ВАЦ: неполные комплексные данные' } }
    $text = New-Object Text.StringBuilder
    [void]$text.AppendLine('# Hz S RI R 50')
    for ($i = 0; $i -lt $frequenciesArray.Count; $i++) {
        $expected = [double]$Profile.start_hz + ($Profile.stop_hz - $Profile.start_hz) * $i / ($frequenciesArray.Count - 1)
        $frequency = [double]$frequenciesArray[$i]
        if ([Math]::Abs($frequency - $expected) -gt [Math]::Max(0.001, [Math]::Abs($expected) * 1e-10)) { throw 'ВАЦ: сетка частот не соответствует профилю' }
        $values = @($frequency)
        foreach ($traceName in $order) { $values += @($Data[$traceName][2*$i], $Data[$traceName][2*$i+1]) }
        $row = foreach ($value in $values) {
            if ([double]::IsNaN([double]$value) -or [double]::IsInfinity([double]$value)) { throw 'ВАЦ: недопустимое число в трассе' }
            ([double]$value).ToString('G17', [Globalization.CultureInfo]::InvariantCulture)
        }
        [void]$text.AppendLine($row -join ' ')
        if ($text.Length -gt 2MB) { throw 'ВАЦ: файл больше 2 МиБ' }
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes($text.ToString())
    $extension = if ($traces.Count -eq 4) { 's2p' } else { 's1p' }
    return @{ file_name = "measurement.$extension"; content_base64 = [Convert]::ToBase64String($bytes); traces = $traces }
}
function Get-VnaManifest {
    return Read-VnaJson (Join-Path (Get-VnaControlRoot) 'commissioned.json')
}
function Get-VnaControlCapability {
    param($Observation)
    if (-not $Observation -or -not $Observation.ready) { return $null }
    $methods = @(); $manifest = Get-VnaManifest
    if ($manifest -and $manifest.instrument_serial -ceq $Observation.serial -and $manifest.ordinary_state_verified -eq $true) {
        foreach ($kit in @($manifest.kits)) {
            if ($kit.method -ceq 'acm' -and $kit.registry_fingerprint -match '^[a-f0-9]{64}$' -and $kit.definition.serial) { $methods = @('acm'); break }
        }
    }
    return @{ version = 1; methods = $methods }
}
function Invoke-VnaSessionApi {
    param([string]$Base, [string]$AuthToken, [string]$Path, $Body, [string]$Method = 'Get')
    $headers = @{ Authorization = "Bearer $AuthToken"; 'X-Agent-Machine-Id' = (Get-MachineId) }
    $parameters = @{ Method = $Method; Uri = $Base + $Path; Headers = $headers; TimeoutSec = 10 }
    if ($null -ne $Body) {
        $parameters.ContentType = 'application/json; charset=utf-8'
        $parameters.Body = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Body -Depth 70 -Compress))
    }
    return Invoke-RestMethod @parameters
}
function Assert-VnaLease {
    param($Command, $Status)
    $receipt = @($Status.commands | Where-Object { $_.command_id -ceq $Command.command_id })
    if (-not $Status.lease_held -or $Status.session_id -cne $Command.session_id -or
        $Status.instrument_serial -cne $Command.instrument_serial -or $Status.instrument_id -cne $Command.instrument_id -or
        $Status.version -ne $Command.expected_version -or $Status.step -ne $Command.expected_step -or
        $receipt.Count -ne 1 -or $receipt[0].status -cne 'claimed' -or
        $receipt[0].expected_version -ne $Command.expected_version -or $receipt[0].expected_step -ne $Command.expected_step -or
        ($Status.state -in @('aborted','recorded','failed','recovery_required')) -or
        ($Status.state -eq 'aborting' -and $Command.op -notin @('abort','restore'))) { throw 'ВАЦ: команда больше не владеет прибором или шагом сессии' }
}
function Get-VnaCommandPath {
    param($Command)
    $name = ([guid]::Parse($Command.command_id)).ToString() + '.json'
    $root = Get-VnaControlRoot
    $archive = Join-Path (Join-Path $root 'acknowledged') $name
    if ([IO.File]::Exists($archive)) { return $archive }
    return Join-Path $root $name
}
function New-VnaReceipt {
    param($Command, $Result, [string]$ErrorText, [Nullable[bool]]$BackupSaved = $null)
    $payload = @{ session_id = $Command.session_id; expected_version = $Command.expected_version; expected_step = $Command.expected_step }
    if ($ErrorText) { $payload.error = $ErrorText.Substring(0, [Math]::Min(2000, $ErrorText.Length)) } else { $payload.result = $Result }
    if ($ErrorText -and $Command.op -ceq 'prepare' -and $null -ne $BackupSaved) { $payload.backup_saved = [bool]$BackupSaved }
    return $payload
}
function Get-VnaJournal {
    param($Command)
    $path = Get-VnaCommandPath $Command
    $hash = Get-VnaDigest (ConvertTo-VnaCanonical $Command)
    $entry = Read-VnaJson $path
    if ($entry) {
        if ($entry.command_hash -cne $hash) { throw 'ВАЦ: содержимое повторной команды изменилось' }
        if ($entry.state -in @('received','executing')) {
            # The parent only consults an active worker after fencing it. On restart no COM is repeated.
            if ($entry.state -ceq 'executing') {
                Write-VnaJson (Join-Path (Get-VnaControlRoot) 'quarantine.json') @{
                    session_id = $Command.session_id; serial = $Command.instrument_serial; created_at = [DateTime]::UtcNow.ToString('o') }
            }
            $entry.state = 'uncertain'; $entry.payload = New-VnaReceipt $Command $null 'ВАЦ: выполнение прервано; результат неизвестен, требуется восстановление'
            Write-VnaJson $path $entry
        }
        return $entry
    }
    $entry = [pscustomobject]@{ command = $Command; command_hash = $hash; state = 'received'; received_at = [DateTime]::UtcNow.ToString('o')
        executing_at = $null; completed_at = $null; acknowledged = $false; payload = $null }
    Write-VnaJson $path $entry
    return $entry
}
function Invoke-S2Command {
    param($Application, $Command, $Status, [string]$OwnerPath, [hashtable]$Preparation)
    # A pre-existing owner/fence means missing files do not prove an untouched instrument.
    if ($Preparation -and ((Test-Path -LiteralPath $OwnerPath) -or
        (Test-Path -LiteralPath (Join-Path (Get-VnaControlRoot) 'quarantine.json')))) { $Preparation.backup_saved = $null }
    $serial = Get-VnaSerial $Application
    if ($serial -cne $Command.instrument_serial) { throw 'ВАЦ: серийный номер прибора не соответствует сессии' }
    Assert-VnaLease $Command $Status
    $quarantinePath = Join-Path (Get-VnaControlRoot) 'quarantine.json'
    $quarantine = Read-VnaJson $quarantinePath
    if ($quarantine) {
        # Killing a COM client does not cancel an operation in S2VNA's separate process.
        # Only an explicit abort after an operator restart of S2VNA may clear this fence.
        $processes = @(Get-Process -Name S2VNA -ErrorAction Stop)
        if ($Command.op -cne 'abort' -or $quarantine.serial -cne $serial -or
            $quarantine.session_id -cne $Command.session_id -or $processes.Count -ne 1 -or
            $processes[0].StartTime.ToUniversalTime() -le [DateTime]::Parse($quarantine.created_at).ToUniversalTime()) {
            throw 'ВАЦ: прежняя COM-операция могла продолжиться; перезапустите S2VNA и выполните восстановление сессии в ERP'
        }
    }
    $scpi = $Application.SCPI; $argsValue = $Command.args
    $owner = Read-VnaJson $OwnerPath
    if ($owner -and ($owner.session_id -cne $Command.session_id -or $owner.serial -cne $serial)) { throw 'ВАЦ: локальная копия принадлежит другой сессии; требуется сверка с ERP' }
    if ($Command.op -ne 'prepare' -and -not $owner) { throw 'ВАЦ: исходное состояние не сохранено' }
    $result = @{ serial = $serial }
    $collect = 'SENSe(1).CORRection.COLLect'
    switch -CaseSensitive ($Command.op) {
        'prepare' {
            if ($owner) { throw 'ВАЦ: исходное состояние этой сессии уже сохранено' }
            $backupId = [guid]::NewGuid().ToString()
            $root = Get-VnaControlRoot
            $statePath = Join-Path $root ($backupId + '.sta')
            $kitPath = Join-Path $root ($backupId + '.ckit')
            # Reading all traces temporarily selects them. A failed selection/restore is ambiguous.
            if ($Preparation) { $Preparation.backup_saved = $null }
            $original = Get-S2State $scpi
            if ($Preparation) { $Preparation.backup_saved = $false }
            foreach ($capabilityPath in @('SERVice.SWEep.POINts','SERVice.SWEep.FREQuency.MINimum','SERVice.SWEep.FREQuency.MAXimum')) {
                $capacityValue = [double](Invoke-S2Io $scpi $capabilityPath)
                if ([double]::IsNaN($capacityValue) -or [double]::IsInfinity($capacityValue) -or $capacityValue -le 0) { throw 'ВАЦ: возможности прибора не прочитаны' }
            }
            $slot = [int](Invoke-S2Io $scpi "$collect.CKIT.SELect")
            $owner = [pscustomobject]@{ session_id = $Command.session_id; serial = $serial; backup_id = $backupId
                state_path = $statePath; kit_path = $kitPath; slot = $slot; original = $original
                state_hash = ''; kit_hash = ''; definition_hash = ''; calibration = $null; profile = $null; restored = $false
                prepare_command_id = $Command.command_id }
            # Reserve durable ownership before any state/kit store write, even if backup subsequently fails.
            Write-VnaJson $OwnerPath $owner
            if ($Preparation) { $Preparation.backup_saved = $null }
            Set-S2Checked $scpi 'MMEMory.STORe.STYPe' 'CDST'
            Invoke-S2Io $scpi 'MMEMory.STORe.STATe' set $statePath
            if (-not [IO.File]::Exists($statePath) -or (Get-Item -LiteralPath $statePath).Length -eq 0) { throw 'ВАЦ: резервная копия состояния не записана' }
            if ($Preparation) { $Preparation.backup_saved = $true }
            $owner.state_hash = (Get-FileHash -LiteralPath $statePath -Algorithm SHA256).Hash
            Write-VnaJson $OwnerPath $owner
            Invoke-S2KitFile $scpi STORe $slot $kitPath
            foreach ($file in @($statePath,$kitPath)) { if (-not [IO.File]::Exists($file) -or (Get-Item -LiteralPath $file).Length -eq 0) { throw 'ВАЦ: резервная копия не записана' } }
            $owner.kit_hash = (Get-FileHash -LiteralPath $kitPath -Algorithm SHA256).Hash
            Write-VnaJson $OwnerPath $owner
            $result.ready = $true; $result.backup_id = $backupId; $result.state_saved = $true; $result.kit_assignments_saved = $true
        }
        'apply_profile' {
            Assert-S2Profile $scpi $argsValue.profile -Apply
            $owner.profile = $argsValue.profile; Write-VnaJson $OwnerPath $owner
            $result.profile = $argsValue.profile
        }
        'begin_calibration' {
            $manifest = Get-VnaManifest
            if (-not $manifest -or $manifest.instrument_serial -cne $serial -or $manifest.ordinary_state_verified -ne $true) { throw 'ВАЦ: нет проверенного манифеста станции' }
            $kits = @($manifest.kits | Where-Object { $_.kit_id -ceq $argsValue.kit_id -and $_.method -ceq $argsValue.method })
            if ($kits.Count -ne 1 -or $kits[0].registry_fingerprint -cne $argsValue.kit_fingerprint) { throw 'ВАЦ: определение набора в ERP не соответствует манифесту станции' }
            $kit = $kits[0]
            if (-not $owner.profile) { throw 'ВАЦ: профиль ещё не применён' }
            Assert-S2Profile $scpi $owner.profile
            foreach ($standard in $argsValue.standards) {
                $matches = @($kit.standards | Where-Object { (ConvertTo-VnaCanonical $_) -ceq (ConvertTo-VnaCanonical $standard) })
                if ($matches.Count -ne 1) { throw 'ВАЦ: стандарт или перестановка портов отсутствуют в манифесте' }
            }
            $actualDefinition = $kit.definition
            if ($argsValue.method -ceq 'acm') {
                $definition = $Status.kit_definitions.($argsValue.kit_id)
                [void](Assert-S2Acm $scpi $definition.serial_number $owner.profile.start_hz $owner.profile.stop_hz $kit.definition)
            } elseif ($argsValue.method -cin @('solt1','solt2')) {
                # The implementation is available for commissioning but never advertised as a station method yet.
                # Refuse changing a kit that was not backed up during prepare.
                if ([int]$kit.slot -ne [int]$owner.slot) { throw 'ВАЦ: набор SOLT должен быть выбран до начала сессии для резервирования' }
                $actualDefinition = Get-S2ManualDefinition $scpi $kit $owner.profile
                foreach ($standard in @($argsValue.standards)) {
                    $mapping = @($kit.assignments | Where-Object { $_.kind -ceq $standard.kind -and $_.label -ceq $standard.label -and
                        (ConvertTo-VnaCanonical @($_.ports)) -ceq (ConvertTo-VnaCanonical @($standard.ports)) })
                    if ($mapping.Count -ne 1) { throw 'ВАЦ: назначение меры отсутствует в манифесте' }
                    $item = $mapping[0]
                    $definition = @($actualDefinition.standards | Where-Object { $_.index -eq $item.standard_index })
                    if ($definition.Count -ne 1 -or $definition[0].properties.TYPE -cne $standard.kind -or $definition[0].properties.LABel -cne $standard.label) { throw 'ВАЦ: мера не соответствует назначению' }
                    Set-S2Checked $scpi "$collect.CKIT.ORDer.SELect" ([int]$item.class_index)
                    $indices = @($standard.ports) -join ','
                    Set-S2Checked $scpi ("$collect.CKIT.ORDer." + $standard.kind + "($indices)") ([int]$item.standard_index)
                }
                Invoke-S2Io $scpi "$collect.CLEar" call
                Set-S2Checked $scpi 'TRIGger.SEQuence.SOURce' 'INT'
                Set-S2Checked $scpi 'SENSe(1).CORRection.TRIGger.FREE.STATe' $true
                if ($argsValue.method -ceq 'solt1') { Invoke-S2Io $scpi "$collect.METHod.SOLT1" set 1 }
                else { Set-S2Ports $scpi "$collect.METHod.SOLT2" @(1,2) }
            } else {
                throw 'ВАЦ: неизвестный способ калибровки'
            }
            $owner.definition_hash = Get-VnaDigest (ConvertTo-VnaCanonical $actualDefinition)
            $owner.calibration = @{ method = $argsValue.method; kit_id = $argsValue.kit_id; standards = @($argsValue.standards); definition = $kit.definition; kit = $kit }
            Write-VnaJson $OwnerPath $owner
            $result.kit_fingerprint = $argsValue.kit_fingerprint; $result.definition_hash = $owner.definition_hash
        }
        'acm_calibrate' {
            if (-not $owner.calibration -or $owner.calibration.method -cne 'acm') { throw 'ВАЦ: АКМ не проверен' }
            $standard = @($owner.calibration.standards | Where-Object { $_.kind -ceq 'ACM' })
            if ($standard.Count -ne 1) { throw 'ВАЦ: нет схемы подключения АКМ' }
            Assert-VnaEqual @($argsValue.ports) @(1,2) 'порты прибора'
            Assert-VnaEqual @($argsValue.kit_ports) @($standard[0].kit_ports) 'порты АКМ'
            Assert-VnaEqual $argsValue.start_hz $owner.profile.start_hz 'полоса АКМ'
            Assert-VnaEqual $argsValue.stop_hz $owner.profile.stop_hz 'полоса АКМ'
            Assert-S2Profile $scpi $owner.profile
            $info = Assert-S2Acm $scpi $argsValue.module_serial $argsValue.start_hz $argsValue.stop_hz $owner.calibration.definition
            Set-S2Checked $scpi "$collect.ECAL.ORIentation.STATe" $true
            # Auto orientation detects the physically connected permutation, which was checked against the plan.
            Set-S2Ports $scpi "$collect.ECAL.SOLT2" @(1,2)
            $result.acquired = $true; $result.module_serial = $info.serial; $result.module_min_hz = $info.min_hz; $result.module_max_hz = $info.max_hz
        }
        'measure_standard' {
            if (-not $owner.calibration -or $owner.calibration.method -cnotin @('solt1','solt2')) { throw 'ВАЦ: ручная калибровка не начата' }
            $standard = $argsValue.standard
            $matching = @($owner.calibration.standards | Where-Object { (ConvertTo-VnaCanonical $_) -ceq (ConvertTo-VnaCanonical $standard) })
            if ($matching.Count -ne 1 -or $standard.kind -cnotin @('OPEN','SHORT','LOAD','THRU')) { throw 'ВАЦ: мера не соответствует плану' }
            Assert-S2Profile $scpi $owner.profile
            $actualDefinition = Get-S2ManualDefinition $scpi $owner.calibration.kit $owner.profile
            Assert-VnaEqual (Get-VnaDigest (ConvertTo-VnaCanonical $actualDefinition)) $owner.definition_hash 'определения перед получением меры'
            Assert-VnaEqual (Invoke-S2Io $scpi 'TRIGger.SEQuence.SOURce') 'INT' 'триггер SOLT'
            Assert-VnaEqual (Invoke-S2Io $scpi 'SENSe(1).CORRection.TRIGger.FREE.STATe') $true 'свободный триггер SOLT'
            if ($standard.kind -ceq 'THRU') { Set-S2Ports $scpi "$collect.ACQuire.THRU" @($standard.ports) }
            else {
                if (@($standard.ports).Count -ne 1 -or $standard.ports[0] -notin @(1,2)) { throw 'ВАЦ: неверный порт меры' }
                Invoke-S2Io $scpi ("$collect.ACQuire." + $standard.kind) set ([int]$standard.ports[0])
            }
            $result.acquired = $true
        }
        'save_calibration' {
            if (-not $owner.calibration -or $argsValue.method -cne $owner.calibration.method) { throw 'ВАЦ: способ калибровки не соответствует сессии' }
            Assert-S2Profile $scpi $owner.profile
            Assert-VnaEqual @($argsValue.traces) @($owner.profile.traces) 'трассы калибровки'
            if ($argsValue.method -cne 'acm') { Invoke-S2Io $scpi "$collect.SAVE" call }
            $result.method = $argsValue.method; $result.correction = Get-S2Correction $scpi $argsValue.traces
        }
        'capture' {
            Assert-VnaEqual $argsValue.profile $owner.profile 'сохранённый профиль'
            Assert-S2Profile $scpi $argsValue.profile
            [void](Get-S2Correction $scpi $argsValue.profile.traces)
            Set-S2Checked $scpi 'INITiate(1).CONTinuous' $true
            Set-S2Checked $scpi 'TRIGger.SEQuence.SOURce' 'BUS'
            Invoke-S2Io $scpi 'TRIGger.SEQuence.SINGle' call
            if ((Invoke-S2Io $scpi 'IEEE4882.OPC') -ne 1) { throw 'ВАЦ: развёртка не завершена' }
            $frequencies = @(Invoke-S2Io $scpi 'SENSe(1).FREQuency.DATA')
            $data = @{}
            for ($traceIndex = 1; $traceIndex -le @($argsValue.profile.traces).Count; $traceIndex++) {
                $parameter = [string](Invoke-S2Io $scpi "CALCulate(1).PARameter($traceIndex).DEFine")
                $data[$parameter] = @(Invoke-S2Io $scpi "CALCulate(1).TRACe($traceIndex).DATA.SDATa")
            }
            Assert-S2Profile $scpi $argsValue.profile
            Assert-VnaEqual @(Invoke-S2Io $scpi 'SENSe(1).FREQuency.DATA') $frequencies 'частоты после чтения'
            $result.correction = Get-S2Correction $scpi $argsValue.profile.traces
            $result.profile = $argsValue.profile; $result.artifacts = @((ConvertTo-S2Artifact $frequencies $data $argsValue.profile))
        }
        'abort' {
            Invoke-S2Io $scpi 'ABORt' call
            Set-S2Checked $scpi 'INITiate(1).CONTinuous' $false
            Invoke-S2Io $scpi "$collect.CLEar" call
            if ($quarantine) { Remove-Item -LiteralPath $quarantinePath -Force }
            $result.stopped = $true; $result.calibration_cleared = $true
        }
        'restore' {
            foreach ($pair in @(@($owner.state_path,$owner.state_hash),@($owner.kit_path,$owner.kit_hash))) {
                if (-not $pair[1] -or -not [IO.File]::Exists($pair[0]) -or (Get-FileHash -LiteralPath $pair[0] -Algorithm SHA256).Hash -cne $pair[1]) { throw 'ВАЦ: резервная копия отсутствует или повреждена' }
            }
            Invoke-S2Io $scpi 'ABORt' call
            Set-S2Checked $scpi 'INITiate(1).CONTinuous' $false
            Invoke-S2KitFile $scpi LOAD ([int]$owner.slot) ([string]$owner.kit_path)
            Invoke-S2Io $scpi 'MMEMory.LOAD.STATe' set $owner.state_path
            # CDST contains the measurement/calibration; restore the store mode changed to create it.
            Set-S2Checked $scpi 'MMEMory.STORe.STYPe' $owner.original.values.'MMEMory.STORe.STYPe'
            Assert-VnaEqual (Get-S2State $scpi) $owner.original 'восстановленное состояние и назначения набора'
            $verifyKitPath = $owner.kit_path + '.verify'
            Invoke-S2KitFile $scpi STORe ([int]$owner.slot) $verifyKitPath
            if ((Get-FileHash -LiteralPath $verifyKitPath -Algorithm SHA256).Hash -cne $owner.kit_hash) { throw 'ВАЦ: восстановленные определения набора не совпали' }
            $owner.restored = $true; Write-VnaJson $OwnerPath $owner
            $result.restored = $true
        }
        default { throw 'ВАЦ: неизвестная типовая операция' }
    }
    return $result
}

function Invoke-VnaWorker {
    param([string]$RequestPath)
    $request = Read-VnaJson $RequestPath
    $application = $null; $mutex = $null; $locked = $false; $entry = $null; $preparation = $null
    try {
        if ($request.command) {
            $entry = Get-VnaJournal $request.command
            if ($entry.state -ne 'received') { return }
            if ($request.command.op -ceq 'prepare') {
                $preparation = @{ backup_saved = $null }
                if (-not (Test-Path -LiteralPath (Join-Path (Get-VnaControlRoot) 'owner.json')) -and
                    -not (Test-Path -LiteralPath (Join-Path (Get-VnaControlRoot) 'quarantine.json'))) { $preparation.backup_saved = $false }
            }
        }
        if (-not (Test-VnaNormalToken)) { throw 'ВАЦ: запустите агент без прав администратора Windows' }
        if (-not (Test-VnaRegistered) -or -not (Get-Process -Name S2VNA -ErrorAction SilentlyContinue)) { throw 'ВАЦ: S2VNA не запущена' }
        $application = Get-VnaApplication -RunningOnly
        $serial = Get-VnaSerial $application
        Write-VnaJson (Join-Path (Get-VnaControlRoot) 'observation.json') @{
            serial = $serial; name = [string]$application.Name; ready = $true; observed_at = [DateTime]::UtcNow.ToString('o') }
        if (-not $request.command) { return }
        $command = $request.command
        $beforeLockBackup = $preparation.backup_saved
        if ($preparation) { $preparation.backup_saved = $null }
        $mutex = New-VnaDeviceMutex $serial
        try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) {
            if ($preparation) { $preparation.backup_saved = $null }
            throw 'ВАЦ: другой исполнитель ещё владеет прибором'
        }
        if ($preparation) { $preparation.backup_saved = $beforeLockBackup }
        $auth = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR((ConvertTo-SecureString $request.token_protected)))
        $status = Invoke-VnaSessionApi $request.base $auth ("/api/stock/vna/sessions/" + $command.session_id)
        Assert-VnaLease $command $status
        # Persist before entering any mutating driver operation, including the state store.
        $entry.state = 'executing'; $entry.executing_at = [DateTime]::UtcNow.ToString('o')
        Write-VnaJson (Get-VnaCommandPath $command) $entry
        $result = Invoke-S2Command $application $command $status (Join-Path (Get-VnaControlRoot) 'owner.json') -Preparation $preparation
        $entry.payload = New-VnaReceipt $command $result
        $entry.state = 'completed'; $entry.completed_at = [DateTime]::UtcNow.ToString('o')
        Write-VnaJson (Get-VnaCommandPath $command) $entry
    } catch {
        if ($entry) {
            $entry.payload = New-VnaReceipt $request.command $null ('ВАЦ: ' + $_.Exception.Message) -BackupSaved $preparation.backup_saved
            $entry.state = 'completed'; $entry.completed_at = [DateTime]::UtcNow.ToString('o')
            Write-VnaJson (Get-VnaCommandPath $request.command) $entry
        } else {
            Write-VnaJson (Join-Path (Get-VnaControlRoot) 'observation.json') @{
                ready = $false; serial = ''; name = 'S2VNA'; registered = (Test-VnaRegistered); observed_at = [DateTime]::UtcNow.ToString('o') }
        }
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        if ($mutex) { $mutex.Dispose() }
        Release-VnaApplication $application
    }
}
function Start-VnaWorker {
    param([string]$Base, [string]$AuthToken, $Command)
    $root = Get-VnaControlRoot
    $path = Join-Path $root 'worker-request.json'
    $request = @{ base = $Base; command = $Command; token_protected = (ConvertFrom-SecureString (ConvertTo-SecureString $AuthToken -AsPlainText -Force)) }
    Write-VnaJson $path $request
    $executable = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = '-NoProfile -NonInteractive -Sta -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $PSCommandPath +
        '" -ConfigPath "' + ([IO.Path]::GetFullPath($ConfigPath)) + '" -VnaWorkerRequest "' + $path + '"'
    $process = Start-Process -FilePath $executable -ArgumentList $arguments -WindowStyle Hidden -PassThru
    $script:VnaWorker = @{ process = $process; command = $Command; started = [DateTime]::UtcNow }
}
function Stop-VnaWorker {
    param($Worker, [string]$Reason)
    if (-not $Worker.process.HasExited) {
        $Worker.process.Kill()
        if (-not $Worker.process.WaitForExit(5000)) { throw 'ВАЦ: не удалось остановить прежнего исполнителя; восстановление запрещено' }
    }
    if ($Worker.command) {
        $path = Get-VnaCommandPath $Worker.command; $entry = Read-VnaJson $path
        if (-not $entry) { $entry = Get-VnaJournal $Worker.command }
        # Cancellation can race completion. Preserve the exact finished receipt for reconciliation.
        if ($entry.state -ne 'completed') {
            if ($entry.state -in @('executing','uncertain')) {
                Write-VnaJson (Join-Path (Get-VnaControlRoot) 'quarantine.json') @{
                    session_id = $Worker.command.session_id; serial = $Worker.command.instrument_serial; created_at = [DateTime]::UtcNow.ToString('o') }
            }
            $entry.state = 'uncertain'; $entry.payload = New-VnaReceipt $Worker.command $null $Reason
            Write-VnaJson $path $entry
        }
    }
    $Worker.process.Dispose()
    $script:VnaWorker = $null
}
function Initialize-VnaControl {
    if (-not $script:VnaParentMutex) {
        # One control parent per PC, including copies started with another config path/user session.
        # Otherwise separate journals could replay the same claimed command after a device-mutex handoff.
        $script:VnaParentMutex = New-Object Threading.Mutex($false, 'Global\FraxisVnaParent')
        $acquired = $false
        try { $acquired = $script:VnaParentMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) {
            $script:VnaParentMutex.Dispose(); $script:VnaParentMutex = $null
            throw 'ВАЦ: другой экземпляр агента уже управляет этим ПК'
        }
    }
    # A parent crash must not leave an orphaned COM client racing the replacement agent.
    $marker = '-VnaWorkerRequest "' + (Join-Path (Get-VnaControlRoot) 'worker-request.json') + '"'
    foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and $_.ProcessId -ne $PID
    })) {
        $handle = Get-Process -Id $process.ProcessId -ErrorAction Stop
        $request = Read-VnaJson (Join-Path (Get-VnaControlRoot) 'worker-request.json')
        Stop-VnaWorker @{ process = $handle; command = $request.command } 'ВАЦ: агент перезапущен; выполнение неизвестно'
    }
    $script:VnaControlInitialized = $true
    $script:VnaNextProbe = [DateTime]::MinValue
    $script:VnaNextFence = [DateTime]::MinValue
    $script:VnaFirstObservationReported = $false
}
function Get-VnaObservation {
    $observation = Read-VnaJson (Join-Path (Get-VnaControlRoot) 'observation.json')
    if ($observation -and $observation.registered -eq $false) { return $null }
    $maxAge = if ($script:VnaWorker -and $script:VnaWorker.command) { 120 } else { 10 }
    if ($observation -and ([DateTime]::UtcNow - [DateTime]::Parse($observation.observed_at).ToUniversalTime()).TotalSeconds -le $maxAge) { return $observation }
    return $null
}
function Sync-VnaReceipts {
    param([string]$Base, [string]$AuthToken)
    foreach ($file in @(Get-ChildItem -LiteralPath (Get-VnaControlRoot) -Filter '*.json')) {
        if ($file.BaseName -notmatch '^[a-f0-9-]{36}$') { continue }
        $entry = Read-VnaJson $file.FullName
        if ($entry.acknowledged) { continue }
        if ($script:VnaWorker -and $script:VnaWorker.command.command_id -ceq $entry.command.command_id) { continue }
        if ($entry.state -in @('received','executing')) { $entry = Get-VnaJournal $entry.command }
        if ($entry.state -notin @('completed','uncertain')) { continue }
        $accepted = $false
        try {
            $null = Invoke-VnaSessionApi $Base $AuthToken ("/api/stock/vna/commands/" + $entry.command.command_id + '/result') $entry.payload Post
            $accepted = $true
        } catch {
            $statusCode = 0
            if ($_.Exception.Response) { $statusCode = [int]$_.Exception.Response.StatusCode }
            if ($statusCode -notin @(403,404,409,422)) { throw }
            $status = Invoke-VnaSessionApi $Base $AuthToken ("/api/stock/vna/sessions/" + $entry.command.session_id)
            $remote = @($status.commands | Where-Object { $_.command_id -ceq $entry.command.command_id })
            if ($remote.Count -ne 1 -or $remote[0].status -notin @('done','failed','cancelled','uncertain')) { throw }
            # A rejected invalid payload is not marked accepted while the server still says claimed.
        }
        $entry | Add-Member NoteProperty receipt_accepted $accepted -Force
        $entry.acknowledged = $true; Write-VnaJson $file.FullName $entry
        $archive = Join-Path (Get-VnaControlRoot) 'acknowledged'; [void][IO.Directory]::CreateDirectory($archive)
        [IO.File]::Move($file.FullName, (Join-Path $archive $file.Name))
    }
    $ownerPath = Join-Path (Get-VnaControlRoot) 'owner.json'; $owner = Read-VnaJson $ownerPath
    if ($owner -and -not $script:VnaWorker) {
        $untouched = $false
        if ($owner.prepare_command_id -and -not (Test-Path -LiteralPath (Join-Path (Get-VnaControlRoot) 'quarantine.json'))) {
            $receipt = Read-VnaJson (Get-VnaCommandPath @{ command_id = $owner.prepare_command_id })
            $untouched = $receipt -and $receipt.acknowledged -and $receipt.receipt_accepted -and $receipt.state -ceq 'completed' -and
                $receipt.command.op -ceq 'prepare' -and $receipt.command.session_id -ceq $owner.session_id -and
                $receipt.command.instrument_serial -ceq $owner.serial -and $receipt.payload.error -and
                $receipt.payload.backup_saved -is [bool] -and $receipt.payload.backup_saved -eq $false
        }
        if (-not $owner.restored -and -not $untouched) { return }
        $status = Invoke-VnaSessionApi $Base $AuthToken ("/api/stock/vna/sessions/" + $owner.session_id)
        $prepareFailed = $untouched -and $status.state -ceq 'failed' -and
            @($status.commands | Where-Object { $_.command_id -ceq $owner.prepare_command_id -and $_.status -ceq 'failed' }).Count -eq 1
        if (-not $status.lease_held -and $status.session_id -ceq $owner.session_id -and $status.instrument_serial -ceq $owner.serial -and
            (($owner.restored -and $status.state -in @('recorded','aborted','failed')) -or $prepareFailed)) {
            # Keep private backup files and journals for diagnostics; release only the local ownership marker.
            Remove-Item -LiteralPath $ownerPath -Force
        }
    }
}
function Update-VnaWorker {
    param([string]$Base, [string]$AuthToken)
    if (-not $script:VnaControlInitialized) { Initialize-VnaControl }
    $worker = $script:VnaWorker
    if ($worker) {
        if ($worker.process.HasExited) { $worker.process.Dispose(); $script:VnaWorker = $null }
        elseif (([DateTime]::UtcNow - $worker.started).TotalSeconds -gt $(if ($worker.command) { 120 } else { 8 })) {
            Stop-VnaWorker $worker 'ВАЦ: превышено время выполнения; результат неизвестен, требуется восстановление'
        } elseif ($worker.command -and [DateTime]::UtcNow -ge $script:VnaNextFence) {
            $script:VnaNextFence = [DateTime]::UtcNow.AddSeconds(2)
            # Network failure uses the caller's backoff, which also services the 120 s watchdog.
            $status = Invoke-VnaSessionApi $Base $AuthToken ("/api/stock/vna/sessions/" + $worker.command.session_id)
            try { Assert-VnaLease $worker.command $status }
            catch { Stop-VnaWorker $worker 'ВАЦ: команда отменена сервером; прежний исполнитель остановлен' }
        }
    }
    Sync-VnaReceipts $Base $AuthToken
    if (-not $script:VnaWorker -and [DateTime]::UtcNow -ge $script:VnaNextProbe) {
        $script:VnaNextProbe = [DateTime]::UtcNow.AddSeconds(3)
        Start-VnaWorker $Base $AuthToken $null
    }
}

# Точка входа; при dot-source (тесты) не запускается.
if ($MyInvocation.InvocationName -ne '.') {
    if ($VnaWorkerRequest) { Invoke-VnaWorker $VnaWorkerRequest; return }
    $cfg = Read-AgentConfig -Path $ConfigPath
    if ($Server) { $cfg.server = $Server }
    if ($Token) { $cfg.token = $Token }
    if ($Printer) { $cfg.printer = $Printer }
    if ($MarkingPrinter) { $cfg.markingPrinter = $MarkingPrinter }
    if ($Scanner) { $cfg.scanner = $Scanner }
    if ($TestScan) { Invoke-TestScan -ScannerAddress $cfg.scanner; return }
    if (-not $cfg.server -or -not $cfg.token) {
        Write-Log "Нет настроек (сервер/токен) в $ConfigPath"
        throw "Агент не настроен: запустите install.ps1"
    }
    Start-AgentLoop -Base $cfg.server.TrimEnd('/') -AuthToken $cfg.token -PrinterName $cfg.printer -DryRun:$DryRun -Once:$Once -ScannerAddress $cfg.scanner -MarkingPrinterName $cfg.markingPrinter
}

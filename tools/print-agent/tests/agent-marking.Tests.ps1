$script:MarkingPng = 'iVBORw0KGgoAAAANSUhEUgAABEUAAADsAQAAAAC6PCUgAAAAz0lEQVR4nO3OMQEAIAzAsPk3Dd+eCICjUZA5v5jXgdVETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdRETdREFyKq1QjyZd2IAAAAAElFTkSuQmCC'
# Pester 3.4+, Windows PowerShell 5.1. No printer, network or production data required.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('pa-' + [guid]::NewGuid())
[void][IO.Directory]::CreateDirectory($tmp)
. (Join-Path $here '..\agent.ps1') -LogPath (Join-Path $tmp 'marking.log') -ConfigPath (Join-Path $tmp 'config.json')
Initialize-GdiPrinter
$script:RealSavePrintReceipt = ${function:Save-PrintReceipt}

function New-TestMode {
    [byte[]]$mode = New-Object byte[] 232
    [Buffer]::BlockCopy([BitConverter]::GetBytes([int16]220),0,$mode,68,2)
    [Buffer]::BlockCopy([BitConverter]::GetBytes([int16]12),0,$mode,70,2)
    $mode[220] = 123
    return ,$mode
}

function New-TestMarkingJob {
    # A complete 300 dpi, 92.5 x 20 mm, one-bit grayscale blank PNG is injected below.
    $bytes = [Convert]::FromBase64String($script:MarkingPng)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $sha = ([BitConverter]::ToString($hash.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() } finally { $hash.Dispose() }
    return [pscustomobject]@{ id = 'mark-1'; attempt = 'attempt-1'; format = 'gdi_png'; printer = 'Godex 中文';
        width_mm = 92.5; height_mm = 20; orientation = 'portrait'; dpi = 300; copies = 1;
        png_b64 = $script:MarkingPng; sha256 = $sha }
}

Describe 'Exact marking DEVMODE' {
    It 'keeps 92.5 mm as 925 tenths and preserves driver-private bytes' {
        $original = New-TestMode
        $requested = [ErpGdiPrinter]::RequestMode($original,92.5,20,$false)
        [BitConverter]::ToInt16($requested,82) | Should Be 925
        [BitConverter]::ToInt16($requested,80) | Should Be 200
        [BitConverter]::ToInt16($requested,78) | Should Be 0
        [BitConverter]::ToInt16($requested,84) | Should Be 100
        [BitConverter]::ToInt16($requested,86) | Should Be 1
        [BitConverter]::ToInt16($requested,90) | Should Be 300
        [BitConverter]::ToInt16($requested,96) | Should Be 300
        $requested[220] | Should Be 123
        $original[82] | Should Be 0
        [ErpGdiPrinter]::ValidateMode($requested,$requested)
    }
    It 'landscape swaps portrait stock dimensions once' {
        $mode = [ErpGdiPrinter]::RequestMode((New-TestMode),92.5,20,$true)
        [BitConverter]::ToInt16($mode,82) | Should Be 200
        [BitConverter]::ToInt16($mode,80) | Should Be 925
        [BitConverter]::ToInt16($mode,76) | Should Be 2
    }
    foreach ($offset in @(76,78,80,82,84,86,90,92,94,96,100)) {
        It "rejects driver substitution of field $offset" {
            $mode = [ErpGdiPrinter]::RequestMode((New-TestMode),92.5,20,$false)
            $returned = $mode.Clone(); $returned[$offset] = $returned[$offset] -bxor 1
            { [ErpGdiPrinter]::ValidateMode($mode,$returned) } | Should Throw 'substituted'
        }
    }
    It 'rejects a driver that removes required fields' {
        $mode = [ErpGdiPrinter]::RequestMode((New-TestMode),92.5,20,$false)
        $returned = $mode.Clone(); $returned[72] = 0
        { [ErpGdiPrinter]::ValidateMode($mode,$returned) } | Should Throw 'removed'
    }
    It 'rejects missing private DEVMODE data' {
        $mode = New-TestMode
        { [ErpGdiPrinter]::RequestMode([byte[]]$mode[0..219],92.5,20,$false) } | Should Throw 'complete'
    }
    foreach ($value in @(0,-1,92.55,501,[double]::NaN,[double]::PositiveInfinity)) {
        It "rejects invalid media $value" { { [ErpGdiPrinter]::Tenths($value) } | Should Throw }
    }
}

Describe 'Marking device geometry' {
    foreach ($size in @(@(10,60),@(20,50),@(30,60),@(40,60),@(92.5,20))) {
        It "accepts preset $($size[0]) x $($size[1]) mm without fit-to-page" {
            $w = [int][Math]::Round($size[0]*300/25.4); $h = [int][Math]::Round($size[1]*300/25.4)
            [ErpGdiPrinter]::ValidateGeometry($size[0],$size[1],$w,$h,300,300,$w,$h,0,0,$w,$h)
        }
    }
    It 'accepts native 300 dpi and subpixel millimetre rounding' {
        [ErpGdiPrinter]::ValidateGeometry(92.5,20,1093,236,300,300,1093,236,0,0,1093,236)
    }
    It 'rejects resolution substitution' {
        { [ErpGdiPrinter]::ValidateGeometry(92.5,20,1093,236,203,203,1093,236,0,0,1093,236) } | Should Throw 'resolution'
    }
    It 'rejects fit-to-page paper substitution' {
        { [ErpGdiPrinter]::ValidateGeometry(92.5,20,1093,236,300,300,1181,236,0,0,1181,236) } | Should Throw 'physical'
    }
    It 'rejects raster size substitution' {
        { [ErpGdiPrinter]::ValidateGeometry(92.5,20,1092,236,300,300,1093,236,0,0,1093,236) } | Should Throw 'resolution'
    }
    It 'rejects impossible printable bounds' {
        { [ErpGdiPrinter]::ValidateGeometry(92.5,20,1093,236,300,300,1093,236,5,0,1093,236) } | Should Throw 'bounds'
    }
}

Describe 'Marking payload' {
    It 'decodes the exact approved one-bit PNG' {
        $job = New-TestMarkingJob
        $bytes = ConvertFrom-MarkingJob $job 'Godex 中文'
        $stream = New-Object IO.MemoryStream(,$bytes)
        $image = $null
        try {
            $image = [Drawing.Image]::FromStream($stream)
            $image.Width | Should Be 1093
            $image.Height | Should Be 236
        } finally { if ($image) { $image.Dispose() }; $stream.Dispose() }
    }
    It 'rejects another queue without any fallback' {
        { ConvertFrom-MarkingJob (New-TestMarkingJob) 'Godex renamed' } | Should Throw 'очередь'
    }
    It 'rejects multiple copies' {
        $job = New-TestMarkingJob; $job.copies = 2
        { ConvertFrom-MarkingJob $job 'Godex 中文' } | Should Throw 'копии'
    }
    It 'rejects checksum mismatch' {
        $job = New-TestMarkingJob; $job.sha256 = '0' * 64
        { ConvertFrom-MarkingJob $job 'Godex 中文' } | Should Throw 'SHA-256'
    }
    It 'rejects a raster with a different physical size' {
        $job = New-TestMarkingJob; $job.width_mm = 93
        { ConvertFrom-MarkingJob $job 'Godex 中文' } | Should Throw 'Размер'
    }
    It 'reads UTF-8 queue names independently of system locale' {
        $path = Join-Path $TestDrive 'unicode-config.json'
        [IO.File]::WriteAllText($path,'{"markingPrinter":"Godex 中文 ОБ.101","printer":"TSC Склад"}',(New-Object Text.UTF8Encoding($false)))
        $cfg = Read-AgentConfig $path
        $cfg.markingPrinter | Should Be 'Godex 中文 ОБ.101'
        $cfg.printer | Should Be 'TSC Склад'
    }
}

Describe 'Marking discovery and protocol' {
    BeforeEach {
        $script:ConfigPath = Join-Path (Join-Path $TestDrive ([guid]::NewGuid().ToString())) 'config.json'
        Mock Get-MachineId { 'machine-1' }
        Mock Get-Printer { @([pscustomobject]@{ Name = 'Godex 中文'; DriverName = 'Seagull'; PortName = 'USB001' },
            [pscustomobject]@{ Name = 'TSC'; DriverName = 'TSC driver'; PortName = 'USB002' }) }
        Mock Send-PngToPrinter { @{ outcome = 'spooled'; message = 'Передано принтеру'; spool_job_id = 77 } }
        Mock Save-PrintReceipt { & $script:RealSavePrintReceipt $Path $Receipt }
        Mock Send-RawToPrinter {}
        Mock Invoke-ErpApi { if ($Url -like '*/claim') { @{ job = (New-TestMarkingJob) } } }
    }
    It 'reports driver, port, kind and missing target without enumerating arbitrary queues' {
        $targets = @(Get-AgentPrinters 'TSC' 'Renamed Godex')
        $targets.Count | Should Be 2
        $targets[0].driver | Should Be 'TSC driver'
        $targets[0].kind | Should Be 'tspl'
        $targets[0].present | Should Be $true
        $targets[1].present | Should Be $false
        $targets[1].kind | Should Be 'gdi_png'
    }
    It 'rejects sharing the TSPL queue with marking' { { Get-AgentPrinters 'TSC' 'TSC' } | Should Throw 'различаться' }
    It 'claims by machine and capabilities and reports attempt plus spool id' {
        Invoke-MarkingPollOnce 'http://test' 'token' 'TSC' 'Godex 中文' | Should Be 'done'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Url -eq 'http://test/api/stock/labels/jobs/claim' -and $Body.machine_id -eq 'machine-1' -and
            $MachineId -eq 'machine-1' -and $Body.capabilities -contains 'tspl' -and $Body.capabilities -contains 'gdi_png'
        }
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Url -eq 'http://test/api/stock/labels/jobs/mark-1/result' -and $Body.attempt -eq 'attempt-1' -and
            $Body.outcome -eq 'spooled' -and $Body.spool_job_id -eq 77 -and $MachineId -eq 'machine-1'
        }
        Assert-MockCalled Send-RawToPrinter -Times 0 -Exactly -Scope It
    }
    It 'never claims when configured queues are absent' {
        Mock Get-Printer { @() }
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' | Should Be 'idle'
        Assert-MockCalled Invoke-ErpApi -Times 0 -Exactly -Scope It
    }
    It 'a renamed queue fails the job and never receives TSPL' {
        Invoke-MarkingPollOnce 'http://test' 'token' 'TSC' 'Renamed Godex' | Should Be 'done'
        Assert-MockCalled Send-PngToPrinter -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Body.outcome -eq 'failed' }
    }
    It 'dry run has a durable failed receipt without printing' {
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' -DryRun | Should Be 'done'
        Assert-MockCalled Send-PngToPrinter -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Body.outcome -eq 'failed' -and $Body.message -eq 'dry run' }
    }
    It 'retries reporting after a lost response without reprinting' {
        $script:ReportBroken = $true
        Mock Invoke-ErpApi {
            if ($Url -like '*/claim') { return @{ job = (New-TestMarkingJob) } }
            if ($script:ReportBroken) { throw 'network lost' }
        }
        { Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' } | Should Throw 'network lost'
        $script:ReportBroken = $false
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' | Should Be 'blocked'
        Assert-MockCalled Send-PngToPrinter -Times 1 -Exactly -Scope It
        $receipt = Get-Content -Raw -Encoding UTF8 (Get-PrintReceiptPath 'mark-1' 'attempt-1') | ConvertFrom-Json
        $receipt.acknowledged | Should Be $true
        $receipt.payload.outcome | Should Be 'spooled'
    }
    It 'persists unknown before submitting and blocks new work after interrupted submission' {
        Mock Send-PngToPrinter { throw 'process interrupted' }
        { Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' } | Should Throw 'process interrupted'
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' | Should Be 'blocked'
        Assert-MockCalled Send-PngToPrinter -Times 1 -Exactly -Scope It
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/claim' }
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Body.outcome -eq 'unknown' }
    }
    It 'a GDI submission error with uncertain delivery pauses the lane' {
        Mock Send-PngToPrinter { @{ outcome = 'unknown'; message = 'EndDoc failed' } }
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' | Should Be 'done'
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' | Should Be 'blocked'
        Assert-MockCalled Send-PngToPrinter -Times 1 -Exactly -Scope It
    }
    It 'journal failure prevents spool submission' {
        Mock Save-PrintReceipt { throw 'disk full' }
        { Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' } | Should Throw 'disk full'
        Assert-MockCalled Send-PngToPrinter -Times 0 -Exactly -Scope It
    }
    It 'archives acknowledged receipts and does not reread history on every poll' {
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' | Should Be 'done'
        @(Get-ChildItem (Get-PrintReceiptRoot) -Filter '*.json').Count | Should Be 0
        (Get-PrintReceiptPath 'mark-1' 'attempt-1') | Should Match 'acknowledged'
        # Archived contents need not be read during sync, but their existence still prevents reprinting.
        [IO.File]::WriteAllText((Get-PrintReceiptPath 'mark-1' 'attempt-1'),'{archived history}')
        Sync-PrintReceipts 'http://test' 'token' | Should Be $false
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' | Should Be 'blocked'
        Assert-MockCalled Send-PngToPrinter -Times 1 -Exactly -Scope It
    }
    It 'acknowledged history allows reinstalling against another server' {
        $path = Get-PrintReceiptPath 'historic' 'attempt-1'
        Save-PrintReceipt $path @{ base = 'http://old'; machine_id = 'old-machine'; acknowledged = $true; payload = @{ outcome = 'spooled' } }
        Sync-PrintReceipts 'http://new' 'token' | Should Be $false
        Assert-MockCalled Invoke-ErpApi -Times 0 -Exactly -Scope It
    }
    It 'never reports pending receipts to another server' {
        $path = Get-PrintReceiptPath 'historic' 'attempt-1'
        Save-PrintReceipt $path @{ base = 'http://old'; machine_id = 'machine-1'; acknowledged = $false; payload = @{ outcome = 'spooled' } }
        { Sync-PrintReceipts 'http://new' 'token' } | Should Throw 'другому серверу'
        Assert-MockCalled Invoke-ErpApi -Times 0 -Exactly -Scope It
    }
    It 'keeps a TSPL claim on the TSC route with the legacy result shape' {
        Mock Invoke-ErpApi { if ($Url -like '*/claim') { @{ job = @{ id = 'tspl-1'; format = 'tspl'; printer = 'TSC'; copies = 1; tspl_b64 = 'UFJJTlQgMQ==' } } } }
        Invoke-MarkingPollOnce 'http://test' 'token' 'TSC' 'Godex 中文' | Should Be 'done'
        Assert-MockCalled Send-RawToPrinter -Times 1 -Exactly -Scope It -ParameterFilter { $PrinterName -eq 'TSC' }
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/agent/jobs/tspl-1/result' -and $Body.ok }
        Assert-MockCalled Send-PngToPrinter -Times 0 -Exactly -Scope It
    }
    It 'never routes a TSPL job to the marking printer' {
        Mock Invoke-ErpApi { if ($Url -like '*/claim') { @{ job = @{ id = 'tspl-2'; format = 'tspl'; printer = 'Godex 中文'; tspl_b64 = 'UFJJTlQgMQ==' } } } }
        Invoke-MarkingPollOnce 'http://test' 'token' 'TSC' 'Godex 中文' | Should Be 'done'
        Assert-MockCalled Send-RawToPrinter -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Body.ok -eq $false -and $Body.error -like '*TSPL*' }
    }
}

Describe 'Marking heartbeat' {
    BeforeEach {
        $script:VnaControlInitialized = $false; $script:HeartbeatDevices = $null
        Mock Get-MachineId { 'machine-1' }; Mock Get-LanIp { '' }; Mock Get-AgentScanners { @() }
        Mock Test-ArinstInstalled { $false }; Mock Get-VnaCapability { $null }; Mock Invoke-AgentUpdate {}
        Mock Get-Printer { [pscustomobject]@{ Name = 'Godex 中文'; DriverName = 'Seagull'; PortName = 'USB001' } }
        Mock Invoke-ErpApi { @{} }
    }
    It 'reports the marking queue separately without overwriting the warehouse field' {
        Send-Heartbeat 'http://test' 'token' -PrinterName 'TSC' -MarkingPrinterName 'Godex 中文'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Body.printer -eq 'TSC' -and $Body.printers.Count -eq 2 -and
            $Body.printers[0].kind -eq 'tspl' -and -not $Body.printers[0].present -and
            $Body.printers[1].kind -eq 'gdi_png' -and $Body.printers[1].present -and
            $Body.printers[1].driver -eq 'Seagull' -and $Body.printers[1].port -eq 'USB001'
        }
    }
    It 'uses cached printer inventory during VNA control without discovery' {
        $script:HeartbeatDevices = @{ printer = 'TSC'; printers = @(@{ name = 'Godex 中文'; kind = 'gdi_png'; present = $true }); scanners = @(); lan_ip = ''; arinst = $false }
        Mock Get-VnaObservation { $null }; Mock Get-Printer { throw 'Must not block control' }
        Send-Heartbeat 'http://test' 'token' -PrinterName 'TSC' -MarkingPrinterName 'Godex 中文' -Lightweight
        Assert-MockCalled Get-Printer -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Body.printers.Count -eq 1 -and $Body.printers[0].name -eq 'Godex 中文' -and $Body.vna_control.version -eq 1
        }
    }
}

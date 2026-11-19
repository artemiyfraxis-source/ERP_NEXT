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
        $requested = [ErpGdiPrinter]::RequestMode($original,92.5,20)
        [BitConverter]::ToInt16($requested,82) | Should Be 925
        [BitConverter]::ToInt16($requested,80) | Should Be 200
        [BitConverter]::ToInt16($requested,78) | Should Be 256
        [BitConverter]::ToInt16($requested,84) | Should Be 100
        [BitConverter]::ToInt16($requested,86) | Should Be 1
        [BitConverter]::ToInt16($requested,90) | Should Be 300
        [BitConverter]::ToInt16($requested,96) | Should Be 300
        $requested[220] | Should Be 123
        $original[82] | Should Be 0
        [ErpGdiPrinter]::ValidateMode($requested,$requested)
    }
    It 'maps a wide raster 1:1 onto portrait stock: width across the head, height along the feed' {
        $mode = [ErpGdiPrinter]::RequestMode((New-TestMode),92.5,20)
        [BitConverter]::ToInt16($mode,82) | Should Be 925
        [BitConverter]::ToInt16($mode,80) | Should Be 200
        [BitConverter]::ToInt16($mode,76) | Should Be 1
    }
    It 'accepts the driver''s own paper index when exact length and width are kept (Godex/Seagull, 18.11)' {
        $mode = [ErpGdiPrinter]::RequestMode((New-TestMode),92.5,20)
        $returned = $mode.Clone()
        [Buffer]::BlockCopy([BitConverter]::GetBytes([int16]32003),0,$returned,78,2)
        [ErpGdiPrinter]::ValidateMode($mode,$returned)
    }
    It 'rejects a driver that clamps the 20 mm stock width to 25.4 mm' {
        $mode = [ErpGdiPrinter]::RequestMode((New-TestMode),20,92.5)
        $returned = $mode.Clone()
        [Buffer]::BlockCopy([BitConverter]::GetBytes([int16]254),0,$returned,82,2)
        { [ErpGdiPrinter]::ValidateMode($mode,$returned) } | Should Throw 'substituted DEVMODE field at 82'
    }
    foreach ($offset in @(76,80,82,90)) {
        It "rejects driver substitution of required field $offset" {
            $mode = [ErpGdiPrinter]::RequestMode((New-TestMode),92.5,20)
            $returned = $mode.Clone(); $returned[$offset] = $returned[$offset] -bxor 1
            { [ErpGdiPrinter]::ValidateMode($mode,$returned) } | Should Throw 'substituted'
        }
    }
    It 'requests only required fields from a driver that offers nothing else' {
        $mode = [ErpGdiPrinter]::RequestMode((New-TestMode),92.5,20)
        [BitConverter]::ToInt32($mode,72) | Should Be (1 -bor 2 -bor 4 -bor 8 -bor 1024)
    }
    It 'requests optional fields the driver offers' {
        $original = New-TestMode
        [Buffer]::BlockCopy([BitConverter]::GetBytes([int](16 -bor 256 -bor 2048 -bor 4096 -bor 8192 -bor 32768 -bor 65536)),0,$original,72,4)
        $fields = [BitConverter]::ToInt32([ErpGdiPrinter]::RequestMode($original,92.5,20),72)
        $fields | Should Be (1 -bor 2 -bor 4 -bor 8 -bor 1024 -bor 16 -bor 256 -bor 2048 -bor 4096 -bor 8192 -bor 32768)
    }
    It 'accepts a label driver that drops duplex, collate, colour, scale, copies and Y resolution' {
        $original = New-TestMode
        [Buffer]::BlockCopy([BitConverter]::GetBytes([int](16 -bor 256 -bor 2048 -bor 4096 -bor 8192 -bor 32768)),0,$original,72,4)
        $mode = [ErpGdiPrinter]::RequestMode($original,92.5,20)
        $returned = $mode.Clone()
        [Buffer]::BlockCopy([BitConverter]::GetBytes([int](1 -bor 2 -bor 4 -bor 8 -bor 1024)),0,$returned,72,4)
        foreach ($offset in @(84,86,92,94,96,100)) { $returned[$offset] = $returned[$offset] -bxor 1 }
        [ErpGdiPrinter]::ValidateMode($mode,$returned)
    }
    It 'accepts colour, duplex and collation the driver kept but changed' {
        $original = New-TestMode
        [Buffer]::BlockCopy([BitConverter]::GetBytes([int](2048 -bor 4096 -bor 32768)),0,$original,72,4)
        $mode = [ErpGdiPrinter]::RequestMode($original,92.5,20)
        $returned = $mode.Clone()
        foreach ($offset in @(92,94,100)) { $returned[$offset] = $returned[$offset] -bxor 1 }
        [ErpGdiPrinter]::ValidateMode($mode,$returned)
    }
    foreach ($case in @(@(16,84),@(256,86),@(8192,96))) {
        It "rejects a kept field $($case[1]) with another scale, copy count or Y resolution" {
            $original = New-TestMode
            [Buffer]::BlockCopy([BitConverter]::GetBytes([int]$case[0]),0,$original,72,4)
            $mode = [ErpGdiPrinter]::RequestMode($original,92.5,20)
            $returned = $mode.Clone(); $returned[$case[1]] = $returned[$case[1]] -bxor 2
            { [ErpGdiPrinter]::ValidateMode($mode,$returned) } | Should Throw 'substituted'
        }
    }
    It 'rejects a driver that removes required fields' {
        $mode = [ErpGdiPrinter]::RequestMode((New-TestMode),92.5,20)
        $returned = $mode.Clone(); $returned[72] = $returned[72] -band (-bnot 8)
        { [ErpGdiPrinter]::ValidateMode($mode,$returned) } | Should Throw 'removed'
    }
    It 'rejects missing private DEVMODE data' {
        $mode = New-TestMode
        { [ErpGdiPrinter]::RequestMode([byte[]]$mode[0..219],92.5,20) } | Should Throw 'complete'
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

Describe 'Ink inside the driver printable area (Godex/Seagull 92.5 x 20: offset 36, printable 1042 x 237)' {
    function New-WhiteRaster {
        $bitmap = New-Object Drawing.Bitmap 1093, 236
        $g = [Drawing.Graphics]::FromImage($bitmap); try { $g.Clear([Drawing.Color]::White) } finally { $g.Dispose() }
        return ,$bitmap
    }
    It 'accepts white margins and ink inside the printable rect' {
        $bitmap = New-WhiteRaster
        try {
            $bitmap.SetPixel(36, 0, [Drawing.Color]::Black); $bitmap.SetPixel(1077, 235, [Drawing.Color]::Black)
            [ErpGdiPrinter]::CheckInk($bitmap, 36, 0, 1042, 237)
        } finally { $bitmap.Dispose() }
    }
    It 'refuses ink in the left hard margin and names both rectangles in mm' {
        $bitmap = New-WhiteRaster
        try {
            $bitmap.SetPixel(12, 12, [Drawing.Color]::Black); $bitmap.SetPixel(600, 100, [Drawing.Color]::Black)
            { [ErpGdiPrinter]::CheckInk($bitmap, 36, 0, 1042, 237) } | Should Throw 'по ширине 3,05–91,27 мм, по подаче 0,00–20,07 мм; краска по ширине 1,02–50,88 мм'
        } finally { $bitmap.Dispose() }
    }
    It 'refuses ink in the right hard margin' {
        $bitmap = New-WhiteRaster
        try {
            $bitmap.SetPixel(1078, 10, [Drawing.Color]::Black)
            { [ErpGdiPrinter]::CheckInk($bitmap, 36, 0, 1042, 237) } | Should Throw 'outside printable bounds'
        } finally { $bitmap.Dispose() }
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
            $MachineId -eq 'machine-1' -and $Body.capabilities -contains 'tspl' -and $Body.capabilities -contains 'gdi_png' -and
            $Body.printer -eq 'TSC' -and $Body.host -eq $env:COMPUTERNAME
        }
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Url -eq 'http://test/api/stock/labels/jobs/mark-1/result' -and $Body.attempt -eq 'attempt-1' -and
            $Body.outcome -eq 'spooled' -and $Body.spool_job_id -eq 77 -and $Body.tracked -eq $true -and $MachineId -eq 'machine-1'
        }
        Assert-MockCalled Send-RawToPrinter -Times 0 -Exactly -Scope It
    }
    It 'starts watching the spooled job in the Windows queue' {
        Invoke-MarkingPollOnce 'http://test' 'token' 'TSC' 'Godex 中文' | Should Be 'done'
        $watch = Get-Content -Raw -Encoding UTF8 (Get-PrintWatchPath 'mark-1' 'attempt-1') | ConvertFrom-Json
        $watch.spool_job_id | Should Be 77
        $watch.printer | Should Be 'Godex 中文'
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
    It 'learns the operator resolution from the server and resumes marking' {
        Mock Send-PngToPrinter { @{ outcome = 'unknown'; message = 'EndDoc failed' } }
        $script:ServerStatus = 'unknown'
        Mock Invoke-ErpApi {
            if ($Url -like '*/claim') { return @{ job = (New-TestMarkingJob) } }
            return @{ ok = $true; status = $script:ServerStatus }
        }
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' | Should Be 'done'
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' | Should Be 'blocked'
        $script:ServerStatus = 'failed'  # the operator marked it «Ошибка печати» in ERP
        Sync-PrintReceipts 'http://test' 'token' | Should Be $false
        $receipt = Get-Content -Raw -Encoding UTF8 (Get-PrintReceiptPath 'mark-1' 'attempt-1') | ConvertFrom-Json
        $receipt.resolved | Should Be $true
        $receipt.payload.outcome | Should Be 'unknown'
        (Get-PrintReceiptPath 'mark-1' 'attempt-1') | Should Match 'acknowledged'
        Assert-MockCalled Invoke-ErpApi -Times 3 -Exactly -Scope It -ParameterFilter { $Body.outcome -eq 'unknown' -and $Body.attempt -eq 'attempt-1' }
        Assert-MockCalled Send-PngToPrinter -Times 1 -Exactly -Scope It
    }
    It 'a paused marking lane still claims and prints warehouse TSPL labels' {
        Mock Send-PngToPrinter { @{ outcome = 'unknown'; message = 'EndDoc failed' } }
        Invoke-MarkingPollOnce 'http://test' 'token' 'TSC' 'Godex 中文' | Should Be 'done'
        Mock Invoke-ErpApi { if ($Url -like '*/claim') { @{ job = @{ id = 'tspl-3'; format = 'tspl'; copies = 1; tspl_b64 = 'UFJJTlQgMQ==' } } } else { @{ ok = $true; status = 'unknown' } } }
        Invoke-MarkingPollOnce 'http://test' 'token' 'TSC' 'Godex 中文' | Should Be 'done'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Url -like '*/claim' -and @($Body.capabilities).Count -eq 1 -and $Body.capabilities -contains 'tspl' -and $Body.printer -eq 'TSC'
        }
        Assert-MockCalled Send-RawToPrinter -Times 1 -Exactly -Scope It -ParameterFilter { $PrinterName -eq 'TSC' }
        Assert-MockCalled Send-PngToPrinter -Times 1 -Exactly -Scope It
    }
    It 'treats 409/404/403 on a receipt as final and does not retry it forever' {
        Mock Send-PngToPrinter { @{ outcome = 'unknown'; message = 'EndDoc failed' } }
        Mock Get-ErpErrorStatus { 409 }
        Mock Invoke-ErpApi { if ($Url -like '*/claim') { return @{ job = (New-TestMarkingJob) } }; throw 'Conflict' }
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' | Should Be 'done'
        @(Get-ChildItem (Get-PrintReceiptRoot) -Filter '*.json').Count | Should Be 0
        Sync-PrintReceipts 'http://test' 'token' | Should Be $false
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/result' }
    }
    It 'keeps throwing on a network failure so the receipt is retried' {
        Mock Get-ErpErrorStatus { 0 }
        Mock Invoke-ErpApi { if ($Url -like '*/claim') { return @{ job = (New-TestMarkingJob) } }; throw 'network lost' }
        { Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex 中文' } | Should Throw 'network lost'
        @(Get-ChildItem (Get-PrintReceiptRoot) -Filter '*.json').Count | Should Be 1
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

Describe 'Machine id of cloned PCs' {
    It 'prefers machine-id.txt next to the config over MachineGuid' {
        $ConfigPath = Join-Path $tmp 'config.json'; $idPath = Join-Path $tmp 'machine-id.txt'
        try {
            [IO.File]::WriteAllText($idPath, "  0F1E2D3C-4B5A-6978-8796-A5B4C3D2E1F0`r`n")
            Get-MachineId | Should Be '0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0'
        } finally { Remove-Item -LiteralPath $idPath -Force -ErrorAction SilentlyContinue }
    }
    It 'ignores a malformed machine-id.txt and falls back to MachineGuid' {
        $ConfigPath = Join-Path $tmp 'config.json'; $idPath = Join-Path $tmp 'machine-id.txt'
        try {
            [IO.File]::WriteAllText($idPath, 'not-a-guid')
            Get-MachineId | Should Not Be 'not-a-guid'
            (Get-MachineId) -match '^[0-9a-z-]+$' | Should Be $true
        } finally { Remove-Item -LiteralPath $idPath -Force -ErrorAction SilentlyContinue }
    }
}

Describe 'Marking heartbeat' {
    BeforeEach {
        $script:VnaControlInitialized = $false; $script:HeartbeatDevices = $null
        Mock Get-MachineId { 'machine-1' }; Mock Get-LanIp { '' }; Mock Get-AgentScanners { @() }
        Mock Test-ArinstPresent { $false }; Mock Get-VnaCapability { $null }; Mock Invoke-AgentUpdate {}
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

Describe 'Honest print status (spool job watch)' {
    BeforeEach {
        $script:ConfigPath = Join-Path (Join-Path $TestDrive ([guid]::NewGuid().ToString())) 'config.json'
        Mock Get-MachineId { 'machine-1' }
        $script:Reports = @()
        Mock Invoke-ErpApi { $script:Reports += ,@{ Url = $Url; Body = $Body }; @{ ok = $true } }
        Save-PrintWatch 'mark-9' 'attempt-9' 'http://test' 'machine-1' 'Godex 中文' 31
    }
    It 'classifies the Windows job status' {
        (Resolve-SpoolJobState ([pscustomobject]@{ JobStatus = 'Printing' })).state | Should Be 'queued'
        (Resolve-SpoolJobState ([pscustomobject]@{ JobStatus = 'Spooling, Printing' })).final | Should Be $false
        (Resolve-SpoolJobState ([pscustomobject]@{ JobStatus = 'Printed' })).state | Should Be 'printed'
        (Resolve-SpoolJobState ([pscustomobject]@{ JobStatus = 'Complete' })).final | Should Be $true
        (Resolve-SpoolJobState $null).state | Should Be 'printed'
        (Resolve-SpoolJobState $null $true).state | Should Be 'cancelled'
        (Resolve-SpoolJobState ([pscustomobject]@{ JobStatus = 'Deleting' })).deleting | Should Be $true
        $error1 = Resolve-SpoolJobState ([pscustomobject]@{ JobStatus = 'Printing, Offline' })
        $error1.state | Should Be 'error'
        $error1.message | Should Match 'не в сети'
        (Resolve-SpoolJobState ([pscustomobject]@{ JobStatus = 'PaperOut' })).message | Should Match 'бумаги'
        (Resolve-SpoolJobState ([pscustomobject]@{ JobStatus = 'Paused' })).state | Should Be 'error'
    }
    It 'keeps a job the printer still holds silent and unfinished' {
        Mock Get-PrintJob { [pscustomobject]@{ ID = 31; JobStatus = 'Printing' } }
        Update-PrintWatches 'http://test' 'token'
        $script:Reports.Count | Should Be 0
        Test-Path -LiteralPath (Get-PrintWatchPath 'mark-9' 'attempt-9') | Should Be $true
    }
    It 'reports printed once the job leaves the queue and ends the watch' {
        Mock Get-PrintJob { @() }
        Update-PrintWatches 'http://test' 'token'
        $script:Reports.Count | Should Be 1
        $script:Reports[0].Url | Should Be 'http://test/api/stock/labels/jobs/mark-9/print-state'
        $script:Reports[0].Body.state | Should Be 'printed'
        $script:Reports[0].Body.attempt | Should Be 'attempt-9'
        Test-Path -LiteralPath (Get-PrintWatchPath 'mark-9' 'attempt-9') | Should Be $false
    }
    It 'reports an error once, keeps watching, and reports printed after it clears' {
        $script:Queue = @([pscustomobject]@{ ID = 31; JobStatus = 'Offline' })
        Mock Get-PrintJob { $script:Queue }
        Update-PrintWatches 'http://test' 'token'
        Update-PrintWatches 'http://test' 'token'
        $script:Reports.Count | Should Be 1
        $script:Reports[0].Body.state | Should Be 'error'
        $script:Queue = @()
        Update-PrintWatches 'http://test' 'token'
        $script:Reports.Count | Should Be 2
        $script:Reports[1].Body.state | Should Be 'printed'
    }
    It 'reports cancelled when the job was deleted from the queue' {
        $script:Queue = @([pscustomobject]@{ ID = 31; JobStatus = 'Deleting' })
        Mock Get-PrintJob { $script:Queue }
        Update-PrintWatches 'http://test' 'token'
        $script:Reports.Count | Should Be 0
        $script:Queue = @()
        Update-PrintWatches 'http://test' 'token'
        $script:Reports[0].Body.state | Should Be 'cancelled'
    }
    It 'keeps the watch when ERP is unreachable and drops it when ERP will never take it' {
        Mock Get-PrintJob { @() }
        Mock Get-ErpErrorStatus { 0 }
        Mock Invoke-ErpApi { throw 'network lost' }
        Update-PrintWatches 'http://test' 'token'
        Test-Path -LiteralPath (Get-PrintWatchPath 'mark-9' 'attempt-9') | Should Be $true
        Mock Get-ErpErrorStatus { 409 }
        Update-PrintWatches 'http://test' 'token'
        Test-Path -LiteralPath (Get-PrintWatchPath 'mark-9' 'attempt-9') | Should Be $false
    }
    It 'does not decide anything while the Windows queue cannot be read' {
        Mock Get-PrintJob { throw 'spooler stopped' }
        Update-PrintWatches 'http://test' 'token'
        $script:Reports.Count | Should Be 0
        Test-Path -LiteralPath (Get-PrintWatchPath 'mark-9' 'attempt-9') | Should Be $true
    }
}


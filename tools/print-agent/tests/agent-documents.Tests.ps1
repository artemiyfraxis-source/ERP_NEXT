# Pester (3.x/5.x): МФУ склада (2.12.0) — очередь документов рядом со сканером, печать PDF из ERP, настройки скана.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('pa-' + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
. (Join-Path $here '..\agent.ps1') -LogPath (Join-Path $tmp 'a.log') -ConfigPath (Join-Path $tmp 'none.json')

$script:PdfBytes = [Text.Encoding]::ASCII.GetBytes('%PDF-1.4 test')
$sha = [Security.Cryptography.SHA256]::Create()
$script:PdfSha = -join ($sha.ComputeHash($script:PdfBytes) | ForEach-Object { $_.ToString('x2') })
$sha.Dispose()

function New-DocJob {
    param([string]$Sha = $script:PdfSha, [string]$Printer = 'Pantum BM2300 Series')
    [pscustomobject]@{ id = 'd1'; attempt = 'a1'; printer = $Printer; title = 'Паспорт.pdf'; pages = 1; copies = 3
        sha256 = $Sha; pdf_b64 = [Convert]::ToBase64String($script:PdfBytes) }
}

Describe 'Get-ScannerModel' {
    It 'убирает служебные слова из имени сканера' {
        Get-ScannerModel 'Pantum BM2300 Series Scanner' | Should Be 'Pantum BM2300'
        Get-ScannerModel 'WIA-Сканер' | Should Be ''
    }
}

Describe 'Get-DocumentPrinters' {
    $queues = @(
        [pscustomobject]@{ Name = 'Pantum BM2300 Series'; DriverName = 'Pantum BM2300 Series'; PortName = 'USB002' },
        [pscustomobject]@{ Name = 'TSC MB341'; DriverName = 'TSC MB341'; PortName = 'USB001' },
        [pscustomobject]@{ Name = 'Microsoft Print to PDF'; DriverName = 'Microsoft Print To PDF'; PortName = 'PORTPROMPT:' }
    )
    It 'очередь того же МФУ, что и сканер, — принтер документов' {
        Mock Get-Printer { $queues }
        $found = @(Get-DocumentPrinters @(@{ name = 'Pantum BM2300 Series Scanner' }) '' @('TSC MB341', ''))
        $found.Count | Should Be 1
        $found[0].name | Should Be 'Pantum BM2300 Series'
        $found[0].kind | Should Be 'document'
        $found[0].present | Should Be $true
    }
    It 'без сканера и без настройки очереди не ищет' {
        Mock Get-Printer { throw 'must not list printers' }
        @(Get-DocumentPrinters @() '' @()).Count | Should Be 0
        Assert-MockCalled Get-Printer -Times 0 -Exactly -Scope It
    }
    It 'documentPrinter из настроек: нет такой очереди — сообщает её отсутствующей' {
        Mock Get-Printer { $queues }
        $found = @(Get-DocumentPrinters @() 'Canon iR' @())
        $found.Count | Should Be 1
        $found[0].present | Should Be $false
    }
    It 'очередь этикеток не становится принтером документов' {
        Mock Get-Printer { $queues }
        @(Get-DocumentPrinters @(@{ name = 'TSC MB341 Scanner' }) '' @('TSC MB341')).Count | Should Be 0
    }
}

Describe 'Send-Heartbeat: принтер документов' {
    It 'сообщает очередь МФУ вместе с TSC' {
        $script:VnaControlInitialized = $false; $script:HeartbeatDevices = $null
        Mock Get-MachineId { 'pc-1' }; Mock Get-LanIp { '' }; Mock Test-ArinstPresent { $false }; Mock Get-VnaCapability { $null }
        Mock Invoke-AgentUpdate {}
        Mock Get-AgentScanners { @(@{ id = 'wia-1'; name = 'Pantum BM2300 Series Scanner' }) }
        Mock Get-Printer { @(
            [pscustomobject]@{ Name = 'TSC MB341'; DriverName = 'TSC'; PortName = 'USB001' },
            [pscustomobject]@{ Name = 'Pantum BM2300 Series'; DriverName = 'Pantum'; PortName = 'USB002' }) }
        Mock Invoke-ErpApi { @{} }
        Send-Heartbeat 'http://test' 'token' -PrinterName 'TSC MB341'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Body.printers.Count -eq 2 -and $Body.printers[0].kind -eq 'tspl' -and $Body.printers[0].present -and
            $Body.printers[1].kind -eq 'document' -and $Body.printers[1].name -eq 'Pantum BM2300 Series'
        }
    }
}

Describe 'Invoke-DocumentPollOnce' {
    BeforeEach {
        $script:HeartbeatDevices = @{ printers = @(@{ name = 'Pantum BM2300 Series'; kind = 'document'; present = $true }) }
        Mock Get-MachineId { 'pc-1' }
    }
    It 'без очереди документов сервер не спрашивает' {
        $script:HeartbeatDevices = @{ printers = @(@{ name = 'TSC'; kind = 'tspl'; present = $true }) }
        Mock Invoke-ErpApi { throw 'must not claim' }
        Invoke-DocumentPollOnce -Base 'http://x' -AuthToken 't' | Should Be 'idle'
        Assert-MockCalled Invoke-ErpApi -Times 0 -Exactly -Scope It
    }
    It 'печатает PDF с копиями и сообщает spooled' {
        Mock Invoke-ErpApi -ParameterFilter { $Url -like '*/documents/agent/claim' } { [pscustomobject]@{ job = (New-DocJob) } }
        Mock Invoke-ErpApi -ParameterFilter { $Url -like '*/result' } {}
        Mock Invoke-DocumentPrint { 3 }
        Invoke-DocumentPollOnce -Base 'http://x' -AuthToken 't' | Should Be 'done'
        Assert-MockCalled Invoke-DocumentPrint -Times 1 -Exactly -Scope It -ParameterFilter {
            $PrinterName -eq 'Pantum BM2300 Series' -and $Copies -eq 3 -and $Bytes.Length -eq $script:PdfBytes.Length }
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Url -like '*/documents/agent/jobs/d1/result' -and $Body.outcome -eq 'spooled' -and $Body.attempt -eq 'a1' -and $Body.machine_id -eq 'pc-1' }
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Url -like '*/claim' -and $Body.printers -contains 'Pantum BM2300 Series' -and $TimeoutSec -ge 60 }
    }
}

# Отдельные Describe: в Pester 3 мок с ParameterFilter из предыдущего It перекрывает мок без фильтра.
Describe 'Invoke-DocumentPollOnce: отказы' {
    BeforeEach {
        $script:HeartbeatDevices = @{ printers = @(@{ name = 'Pantum BM2300 Series'; kind = 'document'; present = $true }) }
        Mock Get-MachineId { 'pc-1' }
    }
    It 'повреждённый PDF не печатается, ERP узнаёт причину' {
        Mock Invoke-ErpApi -ParameterFilter { $Url -like '*/claim' } { [pscustomobject]@{ job = (New-DocJob -Sha ('0' * 64)) } }
        Mock Invoke-ErpApi -ParameterFilter { $Url -like '*/result' } {}
        Mock Invoke-DocumentPrint { throw 'must not print' }
        Invoke-DocumentPollOnce -Base 'http://x' -AuthToken 't' | Should Be 'error'
        Assert-MockCalled Invoke-DocumentPrint -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Url -like '*/result' -and $Body.outcome -eq 'failed' -and $Body.message -like '*SHA-256*' }
    }
    It 'ошибка Windows при печати -> failed с текстом' {
        Mock Invoke-ErpApi -ParameterFilter { $Url -like '*/claim' } { [pscustomobject]@{ job = (New-DocJob) } }
        Mock Invoke-ErpApi -ParameterFilter { $Url -like '*/result' } {}
        Mock Invoke-DocumentPrint { throw 'Нет бумаги' }
        Invoke-DocumentPollOnce -Base 'http://x' -AuthToken 't' | Should Be 'error'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter {
            $Url -like '*/result' -and $Body.outcome -eq 'failed' -and $Body.message -eq 'Нет бумаги' }
    }
    It 'чужая очередь не печатается' {
        Mock Invoke-ErpApi -ParameterFilter { $Url -like '*/claim' } { [pscustomobject]@{ job = (New-DocJob -Printer 'Canon') } }
        Mock Invoke-ErpApi -ParameterFilter { $Url -like '*/result' } {}
        Mock Invoke-DocumentPrint { throw 'must not print' }
        Invoke-DocumentPollOnce -Base 'http://x' -AuthToken 't' | Should Be 'error'
        Assert-MockCalled Invoke-DocumentPrint -Times 0 -Exactly -Scope It
    }
    It 'DryRun не печатает' {
        Mock Invoke-ErpApi -ParameterFilter { $Url -like '*/claim' } { [pscustomobject]@{ job = (New-DocJob) } }
        Mock Invoke-ErpApi -ParameterFilter { $Url -like '*/result' } {}
        Mock Invoke-DocumentPrint { throw 'must not print' }
        Invoke-DocumentPollOnce -Base 'http://x' -AuthToken 't' -DryRun | Should Be 'error'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Body.message -eq 'dry run' }
    }
}

Describe 'Get-PrintRectangle' {
    It 'A4 на A4 — в натуральную величину от угла листа' {
        $rect = Get-PrintRectangle 8.27 11.69 827 1169 16 17
        [Math]::Round($rect.X) | Should Be -16
        [Math]::Round($rect.Y) | Should Be -17
        [Math]::Round($rect.Width) | Should Be 827
    }
    It 'A3 на A4 — уменьшается целиком' {
        $rect = Get-PrintRectangle 11.69 16.54 827 1169 0 0
        [Math]::Round($rect.Width) | Should Be 826
        [Math]::Round($rect.Height) | Should Be 1169
    }
}

Describe 'Настройки скана из ERP' {
    It 'eSCL: цвет и разрешение задания' {
        $xml = Get-EsclScanXml -Mime 'application/pdf' -Settings ([pscustomobject]@{ color = 'color'; dpi = 600 })
        $xml | Should Match '<scan:ColorMode>RGB24</scan:ColorMode>'
        $xml | Should Match '<scan:XResolution>600</scan:XResolution>'
    }
    It 'eSCL: без настроек и с чужим разрешением — ч/б 300, как для УПД' {
        $xml = Get-EsclScanXml -Mime 'image/jpeg' -Settings ([pscustomobject]@{ dpi = 1200 })
        $xml | Should Match 'Grayscale8'
        $xml | Should Match '<scan:YResolution>300</scan:YResolution>'
        Get-EsclScanXml -Mime 'image/jpeg' | Should Match 'Grayscale8'
    }
    It 'задание с настройками передаёт их сканеру' {
        Mock Get-MachineId { 'pc-1' }
        Mock Invoke-ErpApi { [pscustomobject]@{ job = [pscustomobject]@{ id = 'p1'; target_scanner = 'wia-1'; settings = [pscustomobject]@{ color = 'color'; dpi = 150 } } } }
        Mock Invoke-ScannerScan { @{ Bytes = [byte[]](1); Ext = 'pdf'; Mime = 'application/pdf' } }
        Mock Send-ScanFile {}
        Invoke-ScanPollOnce -Base 'http://x' -AuthToken 't' -ScannerAddress '' | Should Be 'done'
        Assert-MockCalled Invoke-ScannerScan -Times 1 -Exactly -Scope It -ParameterFilter { $Settings.dpi -eq 150 -and $Settings.color -eq 'color' }
    }
    It 'WIA: цвет — тип изображения 1, разрешение 600' {
        $props = @{ '3087' = @{ Value = 0 }; '3088' = @{ Value = 0 } }
        $props | Add-Member ScriptMethod Item { param($key) $this[$key] } -Force
        $itemProps = @{ '6146' = @{ Value = 2 }; '6147' = @{ Value = 300 }; '6148' = @{ Value = 300 } }
        $itemProps | Add-Member ScriptMethod Item { param($key) $this[$key] } -Force
        $item = [pscustomobject]@{ Properties = $itemProps }
        $item | Add-Member ScriptMethod Transfer { param($format) [pscustomobject]@{ FileData = @{ BinaryData = [byte[]]@(1) } } }
        $items = [pscustomobject]@{ ScanItem = $item }
        $items | Add-Member ScriptMethod Item { param($index) $this.ScanItem }
        $script:fakeInfo = [pscustomobject]@{ Scanner = [pscustomobject]@{ Properties = $props; Items = $items } }
        $script:fakeInfo | Add-Member ScriptMethod Connect { $this.Scanner }
        Mock New-Object -ParameterFilter { $ComObject -eq 'WIA.DeviceManager' } { @{ DeviceInfos = @() } }
        Mock Select-WiaDevice { $script:fakeInfo }
        Mock ConvertTo-ScanPage { @{ Bytes = $Bytes; Width = 600; Height = 900 } }
        $result = Invoke-WiaScan -Settings ([pscustomobject]@{ color = 'color'; dpi = 600 })
        $itemProps['6146'].Value | Should Be 1
        $itemProps['6147'].Value | Should Be 600
        $result.Mime | Should Be 'application/pdf'
        [Text.Encoding]::ASCII.GetString($result.Bytes) | Should Match '/MediaBox \[0 0 72 108\]'
    }
}

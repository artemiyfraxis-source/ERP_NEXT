# Pester 3.4+, Windows PowerShell 5.1. Godex control from ERP (2.10.0): peel-off DevMode, one label at a time,
# «Очистить принтер». Every printer, registry and network boundary is mocked.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('pa-godex-' + [guid]::NewGuid())
[void][IO.Directory]::CreateDirectory($tmp)
. (Join-Path $here '..\agent.ps1') -LogPath (Join-Path $tmp 'godex.log') -ConfigPath (Join-Path $tmp 'config.json')
Initialize-GdiPrinter
$script:RealSavePrintReceipt = ${function:Save-PrintReceipt}

function ConvertTo-Ezpl { param([string]$Text) return ,([Text.Encoding]::ASCII.GetBytes($Text)) }
# Mode byte 0 is the test's «peel bit»: 1 → the driver emits ^O1, anything else → ^O0.
function New-PeelMode { param([byte]$Peel) return ,([byte[]]@($Peel, 7, 7)) }
function New-PeelJob { param([bool]$Peel = $true)
    return [pscustomobject]@{ id = 'mark-5'; attempt = 'a-5'; printer = 'Godex'; width_mm = 92.5; height_mm = 20; peel = $Peel } }

Describe 'EZPL dispenser command in the driver output' {
    It 'reads ^O at a line start of the header' {
        Get-DispenserMode (ConvertTo-Ezpl "^Q20,3`r`n^W92`r`n^O1`r`n^L`r`nE`r`n") | Should Be 1
        Get-DispenserMode (ConvertTo-Ezpl "^Q20,3`r`n^O0`r`n^L`r`n") | Should Be 0
    }
    It 'finds nothing without the command or inside other data' {
        Get-DispenserMode (ConvertTo-Ezpl "^Q20,3`r`n^L`r`nE`r`n") | Should BeNullOrEmpty
        Get-DispenserMode (ConvertTo-Ezpl "AT,10,10,^O1`r`n") | Should BeNullOrEmpty
        Get-DispenserMode $null | Should BeNullOrEmpty
    }
}

Describe 'Peel-off DevMode of the agent account' {
    BeforeEach {
        $script:ConfigPath = Join-Path (Join-Path $TestDrive ([guid]::NewGuid().ToString())) 'config.json'
        $script:Current = New-PeelMode 0
        $script:Default = New-PeelMode 1
        $script:Steps = New-Object Collections.ArrayList
        Mock Get-PeelCurrentMode { ,$script:Current }
        Mock Get-PeelDefaultMode { ,$script:Default }
        Mock Invoke-DriverCapture { if ($Mode[0] -eq 1) { ConvertTo-Ezpl "^O1`r`n" } else { ConvertTo-Ezpl "^O0`r`n" } }
        Mock Get-AgentAccountSid { 'S-1-5-18' }
        Mock Get-PerUserDevMode { [void]$script:Steps.Add("read $Hive"); ,([byte[]]@(9, 9)) }
        Mock Save-PrintReceipt { [void]$script:Steps.Add('backup'); & $script:RealSavePrintReceipt $Path $Receipt }
        Mock Set-PerUserDevMode { [void]$script:Steps.Add("write $Hive"); $script:Current = $Mode }
        Mock Set-PeelUserMode { [void]$script:Steps.Add('setprinter') }
    }
    It 'leaves a DevMode that already emits ^O1 untouched' {
        $script:Current = New-PeelMode 1
        Initialize-PeelMode 'Godex' | Should Be 'ok'
        Assert-MockCalled Set-PerUserDevMode -Times 0 -Exactly -Scope It
        Assert-MockCalled Save-PrintReceipt -Times 0 -Exactly -Scope It
    }
    It 'backs up both hives, then writes the printer default to the agent account and .DEFAULT' {
        Initialize-PeelMode 'Godex' | Should Be 'repaired'
        @($script:Steps) -join ',' | Should Be 'read S-1-5-18,read .DEFAULT,backup,write S-1-5-18,write .DEFAULT,setprinter'
        Assert-MockCalled Set-PerUserDevMode -Times 2 -Exactly -Scope It -ParameterFilter { $Mode[0] -eq 1 -and $PrinterName -eq 'Godex' }
        $backup = @(Get-ChildItem (Join-Path (Split-Path -Parent $script:ConfigPath) 'devmode-backup') -Filter '*.json')
        $backup.Count | Should Be 1
        $saved = Get-Content -Raw -Encoding UTF8 $backup[0].FullName | ConvertFrom-Json
        $saved.printer | Should Be 'Godex'
        @($saved.per_user).Count | Should Be 2
        $saved.per_user[0].devmode_b64 | Should Be ([Convert]::ToBase64String([byte[]]@(9, 9)))
    }
    It 'refuses with a Russian reason and writes nothing when the printer default is off too' {
        $script:Default = New-PeelMode 0
        { Initialize-PeelMode 'Godex' } | Should Throw 'отделитель (peel-off) выключен'
        Assert-MockCalled Set-PerUserDevMode -Times 0 -Exactly -Scope It
        # No write, no backup: a check failing every minute leaves no pile of copies.
        Assert-MockCalled Save-PrintReceipt -Times 0 -Exactly -Scope It
        Test-Path -LiteralPath (Join-Path (Split-Path -Parent $script:ConfigPath) 'devmode-backup') | Should Be $false
    }
    It 'repairs only the account the agent runs as and leaves .DEFAULT alone for a user install' {
        Mock Get-AgentAccountSid { 'S-1-5-21-1-2-3-1001' }
        Mock Write-Log {}
        Initialize-PeelMode 'Godex' | Should Be 'repaired'
        @($script:Steps) -join ',' | Should Be 'read S-1-5-21-1-2-3-1001,backup,write S-1-5-21-1-2-3-1001,setprinter'
        Assert-MockCalled Write-Log -Scope It -ParameterFilter { $Message -match 'HKU\\.DEFAULT не трогаю' }
    }
    It 'only remembers the peel setting from the claim reply; the check waits for an idle poll' {
        $script:PeelInitialized = @{}; $script:PeelBlocked = @{}; $script:PeelRequired = @{}
        Mock Test-MarkingQueueBusy { '' }
        Update-MarkingPrinterSettings @([pscustomobject]@{ name = 'Godex'; peel = $true }) 'Godex'
        Assert-MockCalled Invoke-DriverCapture -Times 0 -Exactly -Scope It
        Invoke-PeelMaintenance 'http://test' 'Godex'
        Invoke-PeelMaintenance 'http://test' 'Godex'
        Assert-MockCalled Set-PerUserDevMode -Times 2 -Exactly -Scope It  # once per process, both SYSTEM hives
        $script:PeelBlocked.ContainsKey('Godex') | Should Be $false
    }
    It 'holds the lane with the reason for ERP when the repair fails, and skips profiles without peel' {
        $script:PeelInitialized = @{}; $script:PeelBlocked = @{}; $script:PeelRequired = @{}; $script:MarkingHoldReason = ''
        $script:Default = New-PeelMode 0
        Mock Test-MarkingQueueBusy { '' }
        Update-MarkingPrinterSettings @([pscustomobject]@{ name = 'Other'; peel = $true }) 'Godex'
        Invoke-PeelMaintenance 'http://test' 'Godex'
        Assert-MockCalled Invoke-DriverCapture -Times 0 -Exactly -Scope It
        Update-MarkingPrinterSettings @([pscustomobject]@{ name = 'Godex'; peel = $true }) 'Godex'
        Invoke-PeelMaintenance 'http://test' 'Godex'
        $script:PeelBlocked['Godex'].message | Should Match '\^O0'
        Get-MarkingHold 'http://test' 'Godex' | Should Match 'отделитель'
        $script:MarkingHoldReason | Should Match 'отделитель'
        $script:HeartbeatSoon | Should Be $true
        Mock Get-Printer { @([pscustomobject]@{ Name = 'Godex'; DriverName = 'Seagull'; PortName = 'USB001' }) }
        @(Get-AgentPrinters '' 'Godex')[0].hold | Should Match 'отделитель'
        # Turned off in the profile: the hold and its reason go away.
        Update-MarkingPrinterSettings @([pscustomobject]@{ name = 'Godex'; peel = $false }) 'Godex'
        Get-MarkingHold 'http://test' 'Godex' | Should Be ''
        $script:MarkingHoldReason | Should Be ''
    }
    It 'does not check while a marking label is still in the queue' {
        $script:PeelInitialized = @{}; $script:PeelBlocked = @{}; $script:PeelRequired = @{ Godex = $true }
        Mock Test-MarkingQueueBusy { 'предыдущая этикетка ещё в очереди Windows' }
        Invoke-PeelMaintenance 'http://test' 'Godex'
        Assert-MockCalled Invoke-DriverCapture -Times 0 -Exactly -Scope It
    }
}

Describe 'Peel-off check before every job' {
    BeforeEach {
        $script:ConfigPath = Join-Path (Join-Path $TestDrive ([guid]::NewGuid().ToString())) 'config.json'
        $script:PeelBlocked = @{}; $script:PeelRequired = @{}
        Mock Invoke-DriverCapture { if ($Mode[0] -eq 1) { ConvertTo-Ezpl "^O1`r`n" } else { ConvertTo-Ezpl "^O0`r`n" } }
        Mock Write-Log {}
    }
    It 'keeps a job DevMode that emits ^O1' {
        $mode = Confirm-JobPeelMode (New-PeelJob) (New-PeelMode 1)
        $mode[0] | Should Be 1
        Assert-MockCalled Invoke-DriverCapture -Times 1 -Exactly -Scope It
    }
    It 'repairs once and prints with the fresh DevMode' {
        Mock Initialize-PeelMode { 'repaired' }
        Mock Get-JobDriverMode { ,(New-PeelMode 1) }
        $mode = Confirm-JobPeelMode (New-PeelJob) (New-PeelMode 0)
        $mode[0] | Should Be 1
        Assert-MockCalled Initialize-PeelMode -Times 1 -Exactly -Scope It
    }
    It 'fails the job before spooling and pauses marking when ^O1 cannot be restored' {
        Mock Initialize-PeelMode { throw (Format-PeelError 0) }
        Mock Get-JobDriverMode { ,(New-PeelMode 0) }
        Mock New-Object { throw 'must not render or spool' } -ParameterFilter { $TypeName -like 'ErpGdiPrinter*' }
        $png = [Convert]::FromBase64String('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQAAAAA3bvkkAAAACklEQVR4AWNgAAAAAgABc3UBGAAAAABJRU5ErkJggg==')
        $result = Send-PngToPrinter (New-PeelJob) $png
        $result.outcome | Should Be 'failed'
        $result.message | Should Match '\^O0 вместо \^O1'
        $result.halt | Should Be $true  # ERP stops the rest of the batch
        $script:PeelBlocked['Godex'].message | Should Match 'Этикетка не напечатана'
    }
    It 'checks a job without the flag when the claim reply marks its queue peel-off' {
        $script:PeelRequired = @{ Godex = $true }
        Mock Initialize-PeelMode { throw (Format-PeelError 0) }
        Mock Get-JobDriverMode { ,(New-PeelMode 0) }
        $png = [Convert]::FromBase64String('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQAAAAA3bvkkAAAACklEQVR4AWNgAAAAAgABc3UBGAAAAABJRU5ErkJggg==')
        try { $result = Send-PngToPrinter (New-PeelJob $false) $png } finally { $script:PeelRequired = @{} }
        Assert-MockCalled Invoke-DriverCapture -Scope It
        $result.outcome | Should Be 'failed'
        $result.halt | Should Be $true
    }
    It 'does not mark an ordinary print failure as a halt' {
        Mock Get-JobDriverMode { throw 'Драйвер не принял размер' }
        $png = [Convert]::FromBase64String('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQAAAAA3bvkkAAAACklEQVR4AWNgAAAAAgABc3UBGAAAAABJRU5ErkJggg==')
        $result = Send-PngToPrinter (New-PeelJob) $png
        $result.outcome | Should Be 'failed'
        $result.ContainsKey('halt') | Should Be $false
    }
    It 'does not check a profile without peel' {
        Mock Get-JobDriverMode { throw 'stop here' }
        $png = [Convert]::FromBase64String('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQAAAAA3bvkkAAAACklEQVR4AWNgAAAAAgABc3UBGAAAAABJRU5ErkJggg==')
        (Send-PngToPrinter (New-PeelJob $false) $png).message | Should Be 'stop here'
        Assert-MockCalled Invoke-DriverCapture -Times 0 -Exactly -Scope It
    }
}

Describe 'Driver capture of the peel-off check' {
    BeforeEach {
        $script:ConfigPath = Join-Path (Join-Path $TestDrive ([guid]::NewGuid().ToString())) 'config.json'
        Mock Invoke-GdiCapture { [IO.File]::WriteAllBytes($File, [Text.Encoding]::ASCII.GetBytes("^O1`r`n")); 77 }
        # KeepPrintedJobs: the finished check stays listed next to a foreign job.
        $script:Queue = @([pscustomobject]@{ ID = 77; JobStatus = 'Printed, Retained'; DocumentName = 'ERP peel check' },
            [pscustomobject]@{ ID = 78; JobStatus = 'Printing'; DocumentName = 'Документ Word' })
        Mock Get-PrintJob { $script:Queue }
        Mock Remove-SpoolJob {}
    }
    It 'reads the output, then removes only its own finished check job' {
        Get-DispenserMode (Invoke-DriverCapture 'Godex' (New-PeelMode 1)) | Should Be 1
        Assert-MockCalled Remove-SpoolJob -Times 1 -Exactly -Scope It -ParameterFilter { $Job.ID -eq 77 }
        @(Get-ChildItem (Join-Path (Split-Path -Parent $script:ConfigPath) 'peel-check')).Count | Should Be 0
    }
}

Describe 'One marking label at a time and «Очистить принтер»' {
    BeforeEach {
        $script:ConfigPath = Join-Path (Join-Path $TestDrive ([guid]::NewGuid().ToString())) 'config.json'
        $script:PeelBlocked = @{}; $script:PeelInitialized = @{}
        Mock Get-MachineId { 'machine-1' }
        Mock Get-Printer { @([pscustomobject]@{ Name = 'Godex'; DriverName = 'Seagull'; PortName = 'USB001' }) }
        Mock Send-PngToPrinter { @{ outcome = 'spooled'; message = 'Передано принтеру'; spool_job_id = 41 } }
        Mock Send-RawToPrinter {}
        Mock Remove-SpoolJob {}
        $script:Queue = @()
        Mock Get-PrintJob { $script:Queue }
        $script:Command = $null
        $script:Calls = New-Object Collections.ArrayList
        Mock Invoke-ErpApi {
            [void]$script:Calls.Add(@{ Url = $Url; Body = $Body })
            if ($Url -like '*/claim') {
                if ($script:Command) { return @{ job = $null; command = $script:Command; marking_printers = @() } }
                if ($Body.capabilities -contains 'gdi_png') {
                    return @{ job = [pscustomobject]@{ id = 'mark-1'; attempt = 'a-1'; format = 'gdi_png'; printer = 'Godex' }; marking_printers = @() }
                }
                return @{ job = $null; marking_printers = @() }
            }
            return @{ ok = $true; status = 'spooled' }
        }
        Mock ConvertFrom-MarkingJob { ,([byte[]]@(1)) }
    }
    It 'claims the next label only after the previous one left the Windows queue' {
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex' | Should Be 'done'
        $script:Queue = @([pscustomobject]@{ ID = 41; JobStatus = 'Printing'; DocumentName = 'ERP marking mark-1' })
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex' | Should Be 'blocked'
        $claims = @($script:Calls | Where-Object { $_.Url -like '*/claim' })
        $claims.Count | Should Be 2
        @($claims[1].Body.capabilities).Count | Should Be 0
        $script:Queue = @()
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex' | Out-Null
        $claims = @($script:Calls | Where-Object { $_.Url -like '*/claim' })
        $claims[2].Body.capabilities -contains 'gdi_png' | Should Be $true
    }
    It 'holds marking while any ERP marking job sits in the Windows queue, even without a watch' {
        $script:Queue = @([pscustomobject]@{ ID = 1; JobStatus = 'Spooling'; DocumentName = 'ERP marking older' })
        Test-MarkingQueueBusy 'http://test' 'Godex' | Should Match 'ERP marking older'
        # «Сохранять напечатанные документы» (KeepPrintedJobs): a finished job stays listed but no longer holds the lane.
        $script:Queue = @([pscustomobject]@{ ID = 1; JobStatus = 'Printed'; DocumentName = 'ERP marking older' },
            [pscustomobject]@{ ID = 2; JobStatus = 'Complete, Retained'; DocumentName = 'ERP marking other' })
        Test-MarkingQueueBusy 'http://test' 'Godex' | Should Be ''
        $script:Queue = @([pscustomobject]@{ ID = 1; JobStatus = 'Printing'; DocumentName = 'Документ Word' })
        Test-MarkingQueueBusy 'http://test' 'Godex' | Should Be ''
        Mock Get-PrintJob { throw 'spooler stopped' }
        Test-MarkingQueueBusy 'http://test' 'Godex' | Should Match 'не прочитана'
    }
    It 'never takes a recycled Windows job id after a reboot for the watched job' {
        Save-PrintWatch 'mark-1' 'a-1' 'http://test' 'machine-1' 'Godex' 3
        $script:Queue = @([pscustomobject]@{ ID = 3; JobStatus = 'Printing'; DocumentName = 'ERP marking mark-7' })
        Update-PrintWatches 'http://test' 'token'
        Test-Path -LiteralPath (Get-PrintWatchPath 'mark-1' 'a-1') | Should Be $false
        $state = @($script:Calls | Where-Object { $_.Url -like '*/print-state' })
        $state.Count | Should Be 1
    }
    It 'keeps printing warehouse TSPL labels while the peel-off check fails, without a driver check in front of them' {
        Mock Invoke-DriverCapture { throw 'must not capture in front of TSPL' }
        Mock Initialize-PeelMode { throw 'must not check in front of TSPL' }
        $script:PeelRequired = @{ Godex = $true }; $script:PeelInitialized = @{}
        Set-PeelBlocked 'Godex' (Format-PeelError 0)
        $script:PeelBlocked['Godex'].retry = (Get-Date).AddSeconds(-1)  # the retry is already due
        Mock Get-Printer { @([pscustomobject]@{ Name = 'Godex'; DriverName = 'Seagull'; PortName = 'USB001' },
            [pscustomobject]@{ Name = 'TSC'; DriverName = 'TSC'; PortName = 'USB002' }) }
        Mock Invoke-ErpApi {
            [void]$script:Calls.Add(@{ Url = $Url; Body = $Body })
            if ($Url -like '*/claim') { return @{ job = @{ id = 'tspl-1'; format = 'tspl'; printer = 'TSC'; copies = 1; tspl_b64 = 'UFJJTlQgMQ==' }; marking_printers = @() } }
            return @{ ok = $true }
        }
        Invoke-MarkingPollOnce 'http://test' 'token' 'TSC' 'Godex' | Should Be 'done'
        Assert-MockCalled Send-RawToPrinter -Times 1 -Exactly -Scope It -ParameterFilter { $PrinterName -eq 'TSC' }
        $claim = @($script:Calls | Where-Object { $_.Url -like '*/claim' })[0].Body
        @($claim.capabilities) -join ',' | Should Be 'tspl'
        Assert-MockCalled Initialize-PeelMode -Times 0 -Exactly -Scope It
        $script:PeelBlocked = @{}; $script:MarkingHoldReason = ''
    }
    It 'clears the printer: ERP jobs out of Windows, ~S,BUFCLR to the Godex, result to ERP' {
        Save-PrintWatch 'mark-1' 'a-1' 'http://test' 'machine-1' 'Godex' 41
        $script:Queue = @([pscustomobject]@{ ID = 41; JobStatus = 'Printing'; DocumentName = 'ERP marking mark-1' },
            [pscustomobject]@{ ID = 42; JobStatus = 'Spooling'; DocumentName = 'ERP marking mark-2' },
            [pscustomobject]@{ ID = 43; JobStatus = 'Spooling'; DocumentName = 'Документ Word' })
        Mock Remove-SpoolJob { $script:Queue = @($script:Queue | Where-Object { $_.ID -ne $Job.ID }) }
        $script:Command = [pscustomobject]@{ id = 'cmd-1'; attempt = 'c-1'; kind = 'clear'; printer = 'Godex' }
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex' | Should Be 'done'
        Assert-MockCalled Remove-SpoolJob -Times 2 -Exactly -Scope It
        Assert-MockCalled Send-PngToPrinter -Times 0 -Exactly -Scope It
        Assert-MockCalled Send-RawToPrinter -Times 1 -Exactly -Scope It -ParameterFilter {
            [Text.Encoding]::ASCII.GetString($Bytes) -eq "~S,BUFCLR`r`n" -and $DocumentName -eq 'ERP printer command' -and $PrinterName -eq 'Godex'
        }
        $result = @($script:Calls | Where-Object { $_.Url -eq 'http://test/api/stock/labels/printer-commands/cmd-1/result' })[0].Body
        $result.message | Should Match 'BUFCLR'
        $result.outcome | Should Be 'done'
        $result.attempt | Should Be 'c-1'
        $result.removed_jobs | Should Be 2
        $result.buffer_cleared | Should Be $true
        # The removed job ends «Отменено», not «Напечатано».
        (Get-Content -Raw -Encoding UTF8 (Get-PrintWatchPath 'mark-1' 'a-1') | ConvertFrom-Json).deleting | Should Be $true
    }
    It 'reaches a paused lane and refuses a queue that is not this PC''s marking printer' {
        Save-PrintReceipt (Get-PrintReceiptPath 'mark-0' 'a-0') @{ base = 'http://test'; machine_id = 'machine-1'; job_id = 'mark-0'
            acknowledged = $true; resolved = $false; payload = @{ attempt = 'a-0'; outcome = 'unknown'; message = '' } }
        $script:Command = [pscustomobject]@{ id = 'cmd-2'; attempt = 'c-2'; kind = 'clear'; printer = 'Other' }
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex' | Should Be 'done'
        Assert-MockCalled Send-RawToPrinter -Times 0 -Exactly -Scope It
        $result = @($script:Calls | Where-Object { $_.Url -like '*/printer-commands/cmd-2/result' })[0].Body
        $result.outcome | Should Be 'failed'
        $result.message | Should Match 'не настроена'
    }
    It 'reports a Godex that never takes the command as failed' {
        $script:CommandSendTimeoutSec = 0
        $script:Queue = @([pscustomobject]@{ ID = 50; JobStatus = 'Printing'; DocumentName = 'ERP printer command' })
        $script:Command = [pscustomobject]@{ id = 'cmd-3'; attempt = 'c-3'; kind = 'clear'; printer = 'Godex' }
        Mock Remove-ErpMarkingJobs { 0 }
        try { Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex' | Should Be 'done' } finally { $script:CommandSendTimeoutSec = 20 }
        $result = @($script:Calls | Where-Object { $_.Url -like '*/printer-commands/cmd-3/result' })[0].Body
        $result.outcome | Should Be 'failed'
        $result.message | Should Match 'не принял команду очистки'
    }
    It 'never deletes real Windows jobs in a dry run' {
        $script:Queue = @([pscustomobject]@{ ID = 41; JobStatus = 'Printing'; DocumentName = 'ERP marking mark-1' })
        $script:Command = [pscustomobject]@{ id = 'cmd-4'; attempt = 'c-4'; kind = 'clear'; printer = 'Godex' }
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex' -DryRun | Should Be 'done'
        Assert-MockCalled Remove-SpoolJob -Times 0 -Exactly -Scope It
        Assert-MockCalled Send-RawToPrinter -Times 0 -Exactly -Scope It
        $result = @($script:Calls | Where-Object { $_.Url -like '*/printer-commands/cmd-4/result' })[0].Body
        $result.outcome | Should Be 'failed'
        $result.removed_jobs | Should Be 0
        $result.message | Should Match 'DRY RUN'
    }
    It 'sends the halt of a failed peel-off check with the receipt' {
        Mock Send-PngToPrinter { @{ outcome = 'failed'; message = 'Godex: отделитель выключен'; halt = $true } }
        Invoke-MarkingPollOnce 'http://test' 'token' '' 'Godex' | Should Be 'done'
        $result = @($script:Calls | Where-Object { $_.Url -like '*/labels/jobs/mark-1/result' })[0].Body
        $result.outcome | Should Be 'failed'
        $result.halt | Should Be $true
    }
    It 'cuts the hold reason for the heartbeat to 500 characters' {
        $script:MarkingHoldReason = 'Godex: отделитель выключен ' * 40
        try { $hold = @(Get-AgentPrinters '' 'Godex')[0].hold } finally { $script:MarkingHoldReason = '' }
        $hold.Length | Should Be 500
    }
}

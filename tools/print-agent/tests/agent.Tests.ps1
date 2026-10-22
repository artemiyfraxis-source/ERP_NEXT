# Pester (3.x/5.x): claim -> печать (замоканная) -> result.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('pa-' + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
. (Join-Path $here '..\agent.ps1') -LogPath (Join-Path $tmp 'a.log') -ConfigPath (Join-Path $tmp 'none.json')

Describe 'Invoke-PollOnce' {
    $b64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("SIZE 40 mm,30 mm`r`nPRINT 1`r`n"))
    It 'нет задания -> idle, печати нет' {
        Mock Invoke-ErpApi { [pscustomobject]@{ job = $null } }
        Mock Send-RawToPrinter {}
        Invoke-PollOnce -Base 'http://x' -AuthToken 't' -PrinterName 'TSC' | Should Be 'idle'
        Assert-MockCalled Send-RawToPrinter -Times 0 -Exactly -Scope It
    }
    It 'задание -> байты в очередь и result ok=true' {
        Mock Invoke-ErpApi {
            if ($Url -like '*claim') { [pscustomobject]@{ job = [pscustomobject]@{ id = 'j1'; copies = 1; tspl_b64 = $b64 } } }
        }
        Mock Send-RawToPrinter {}
        Invoke-PollOnce -Base 'http://x' -AuthToken 't' -PrinterName 'TSC' | Should Be 'done'
        Assert-MockCalled Send-RawToPrinter -Times 1 -Exactly -Scope It -ParameterFilter { $PrinterName -eq 'TSC' -and $Bytes.Length -eq 27 }
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/jobs/j1/result' -and $Body.ok -eq $true }
    }
    It 'ошибка печати -> result ok=false с текстом' {
        Mock Invoke-ErpApi {
            if ($Url -like '*claim') { [pscustomobject]@{ job = [pscustomobject]@{ id = 'j2'; copies = 1; tspl_b64 = $b64 } } }
        }
        Mock Send-RawToPrinter { throw 'Принтер не открывается: 1801' }
        Invoke-PollOnce -Base 'http://x' -AuthToken 't' -PrinterName 'TSC' | Should Be 'done'
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Url -like '*/jobs/j2/result' -and $Body.ok -eq $false -and $Body.error -like '*1801*' }
    }
    It 'DryRun -> печати нет, ok=false dry run' {
        Mock Invoke-ErpApi {
            if ($Url -like '*claim') { [pscustomobject]@{ job = [pscustomobject]@{ id = 'j3'; copies = 1; tspl_b64 = $b64 } } }
        }
        Mock Send-RawToPrinter {}
        Invoke-PollOnce -Base 'http://x' -AuthToken 't' -PrinterName 'TSC' -DryRun | Out-Null
        Assert-MockCalled Send-RawToPrinter -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-ErpApi -Times 1 -Exactly -Scope It -ParameterFilter { $Body.ok -eq $false -and $Body.error -eq 'dry run' }
    }
}

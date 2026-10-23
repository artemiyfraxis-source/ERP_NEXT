# Pester (3.x/5.x): переустановка останавливает уже запущенный агент (задача и процесс), установка ничего не трогает в системе.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$installPath = Join-Path $here '..\install.ps1'

# install.ps1 выполняется целиком при запуске: берём из него только функции остановки агента.
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Resolve-Path $installPath).Path, [ref]$null, [ref]$parseErrors)
$TaskName = 'Fraxis — печать этикеток'
$functions = $ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in 'Get-AgentProcess', 'Stop-Agent' }, $true)
foreach ($function in $functions) { . ([scriptblock]::Create($function.Extent.Text)) }

Describe 'install.ps1' {
    It 'разбирается без ошибок и содержит Stop-Agent' {
        @($parseErrors).Count | Should Be 0
        @($functions).Count | Should Be 2
    }
}

Describe 'Stop-Agent' {
    It 'нет ни задачи, ни процесса -> $false, ничего не останавливается' {
        Mock Get-ScheduledTask {}
        Mock Stop-ScheduledTask {}
        Mock Get-AgentProcess { @() }
        Mock Stop-Process {}
        Stop-Agent | Should Be $false
        Assert-MockCalled Stop-ScheduledTask -Times 0 -Exactly -Scope It
        Assert-MockCalled Stop-Process -Times 0 -Exactly -Scope It
    }
    It 'живой процесс -> задача остановлена, процесс убит, $true' {
        Mock Get-ScheduledTask { [pscustomobject]@{ State = 'Running' } }
        Mock Stop-ScheduledTask {}
        $script:alive = @([pscustomobject]@{ ProcessId = 4242 })
        Mock Get-AgentProcess { $script:alive }
        Mock Stop-Process { $script:alive = @() }
        Stop-Agent -WaitSeconds 2 | Should Be $true
        Assert-MockCalled Stop-ScheduledTask -Times 1 -Exactly -Scope It
        Assert-MockCalled Stop-Process -Times 1 -Exactly -Scope It -ParameterFilter { $Id -eq 4242 }
    }
    It 'процесс не уходит -> ждёт не дольше заданного и возвращает управление' {
        Mock Get-ScheduledTask {}
        Mock Get-AgentProcess { @([pscustomobject]@{ ProcessId = 7 }) }
        Mock Stop-Process {}
        $watch = [Diagnostics.Stopwatch]::StartNew()
        Stop-Agent -WaitSeconds 1 | Should Be $true
        $watch.Elapsed.TotalSeconds | Should BeLessThan 5
    }
}

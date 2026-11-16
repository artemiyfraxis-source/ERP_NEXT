# Pester (3.x/5.x): переустановка останавливает уже запущенный агент (задача и процесс), установка ничего не трогает в системе.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$installPath = Join-Path $here '..\install.ps1'

# install.ps1 выполняется целиком при запуске: берём из него только функции остановки агента.
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Resolve-Path $installPath).Path, [ref]$null, [ref]$parseErrors)
$TaskName = 'Fraxis — печать этикеток'
$functions = $ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in 'Get-AgentProcess', 'Stop-Agent', 'Assert-AgentVnaIdle', 'Confirm-AgentStopped', 'Exit-AgentLifecycle' }, $true)
foreach ($function in $functions) { . ([scriptblock]::Create($function.Extent.Text)) }

Describe 'install.ps1' {
    It 'разбирается без ошибок и содержит Stop-Agent' {
        @($parseErrors).Count | Should Be 0
        @($functions).Count | Should Be 5
    }
}

Describe 'Защита резервной копии ВАЦ при установке' {
    BeforeEach { $script:Dir = Join-Path $TestDrive ([guid]::NewGuid().ToString()); [void][IO.Directory]::CreateDirectory((Join-Path $script:Dir 'vna-control')) }
    It 'без сессии разрешает установку' { Assert-AgentVnaIdle }
    It 'владеющая прибором сессия запрещает переустановку' {
        [IO.File]::WriteAllText((Join-Path $script:Dir 'vna-control\owner.json'),'{}')
        { Assert-AgentVnaIdle } | Should Throw 'незавершённая сессия'
    }
    It 'неизвестная COM-операция запрещает удаление резервной копии' {
        [IO.File]::WriteAllText((Join-Path $script:Dir 'vna-control\quarantine.json'),'{}')
        { Assert-AgentVnaIdle } | Should Throw 'восстановите'
    }
    It 'journal executing до появления owner запрещает удаление' {
        $journalPath = Join-Path (Join-Path $script:Dir 'vna-control') ([guid]::NewGuid().ToString() + '.json')
        [IO.File]::WriteAllText($journalPath,'{"state":"executing","acknowledged":false}')
        { Assert-AgentVnaIdle } | Should Throw 'подтверждения ERP'
    }
    It 'готовая неподтверждённая квитанция сохраняется при переустановке' {
        $journalPath = Join-Path (Join-Path $script:Dir 'vna-control') ([guid]::NewGuid().ToString() + '.json')
        [IO.File]::WriteAllText($journalPath,'{"state":"completed","acknowledged":false}')
        { Assert-AgentVnaIdle } | Should Throw 'подтверждения ERP'
    }
    It 'команда ещё до журнала запрещает удаление' {
        $requestPath = Join-Path $script:Dir 'vna-control\worker-request.json'
        [IO.File]::WriteAllText($requestPath,'{"command":{"command_id":"11111111-1111-1111-1111-111111111111"}}')
        { Assert-AgentVnaIdle } | Should Throw 'команда исполнителя'
    }
    It 'таймаут остановки не разрешает изменение файлов' {
        Mock Get-AgentProcess { @([pscustomobject]@{ ProcessId = 123 }) }
        { Confirm-AgentStopped } | Should Throw 'Не удалось остановить'
    }
    It 'ошибка повторной проверки освобождает lifecycle mutex' {
        Mock Get-AgentProcess { @() }
        [IO.File]::WriteAllText((Join-Path $script:Dir 'vna-control\owner.json'),'{}')
        $mutex = [pscustomobject]@{ Released = $false; Disposed = $false }
        $mutex | Add-Member ScriptMethod ReleaseMutex { $this.Released = $true }
        $mutex | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
        $script:InstallLifecycleMutex = $mutex
        { Confirm-AgentStopped } | Should Throw 'незавершённая сессия'
        $mutex.Released | Should Be $true
        $mutex.Disposed | Should Be $true
        $script:InstallLifecycleMutex | Should Be $null
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

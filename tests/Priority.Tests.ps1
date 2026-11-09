# Lever (a) tests: target selection is pure and the native call is reversible.
. "$PSScriptRoot\TestHelpers.ps1"
Import-Module (Join-Path $script:RepoRoot 'src\SRG.Priority.psm1') -Force -DisableNameChecking

Describe 'Memory-priority lever' {

    Context 'Target selection (pure)' {
        It 'selects only idle, windowless, unknown background processes' {
            $cfg = Get-TestConfig
            $procs = @(
                (New-FakeProcess -ProcessId 1 -Name 'backgroundsvc' -WorkingSetMiB 300 -HasMainWindow $false -CpuPercentSinceLast 0.1),
                (New-FakeProcess -ProcessId 2 -Name 'MsMpEng' -WorkingSetMiB 500 -HasMainWindow $false),
                (New-FakeProcess -ProcessId 3 -Name 'withwindow' -WorkingSetMiB 500 -HasMainWindow $true),
                (New-FakeProcess -ProcessId 4 -Name 'busybg' -WorkingSetMiB 500 -HasMainWindow $false -CpuPercentSinceLast 40)
            )
            $classified = Get-ProcessClassification -Processes $procs -Config $cfg -ForegroundPid 0
            $targets = Get-PriorityLoweringTargets -ClassifiedProcesses $classified
            $targets.Count | Should Be 1
            $targets[0].Pid | Should Be 1
        }

        It 'never selects a protected or foreground process' {
            $cfg = Get-TestConfig
            $procs = @(
                (New-FakeProcess -ProcessId 10 -Name 'MsMpEng' -WorkingSetMiB 400 -HasMainWindow $false),
                (New-FakeProcess -ProcessId 11 -Name 'myapp' -WorkingSetMiB 400 -HasMainWindow $false)
            )
            $classified = Get-ProcessClassification -Processes $procs -Config $cfg -ForegroundPid 11
            $targets = Get-PriorityLoweringTargets -ClassifiedProcesses $classified
            $targets.Count | Should Be 0
        }

        It 'ignores processes smaller than the minimum working set' {
            $cfg = Get-TestConfig
            $procs = @((New-FakeProcess -ProcessId 20 -Name 'tiny' -WorkingSetMiB 5 -HasMainWindow $false))
            $classified = Get-ProcessClassification -Processes $procs -Config $cfg -ForegroundPid 0
            (Get-PriorityLoweringTargets -ClassifiedProcesses $classified).Count | Should Be 0
        }

        It 'ignores a windowless child of a windowed app (general IDE protection)' {
            $cfg = Get-TestConfig
            $procs = @(
                (New-FakeProcess -ProcessId 50 -Name 'SomeNewIde' -WorkingSetMiB 500 -HasMainWindow $true),
                (New-FakeProcess -ProcessId 51 -Name 'SomeNewIdeHelper' -ParentPid 50 -WorkingSetMiB 400 -HasMainWindow $false)
            )
            $classified = Get-ProcessClassification -Processes $procs -Config $cfg -ForegroundPid 0
            $targets = Get-PriorityLoweringTargets -ClassifiedProcesses $classified -Processes $procs
            $targets.Count | Should Be 0
        }

        It 'still selects a genuinely windowless background process' {
            $cfg = Get-TestConfig
            $procs = @(
                (New-FakeProcess -ProcessId 60 -Name 'SomeNewIde' -WorkingSetMiB 500 -HasMainWindow $true),
                (New-FakeProcess -ProcessId 61 -Name 'daemon' -ParentPid 1 -WorkingSetMiB 200 -HasMainWindow $false)
            )
            $classified = Get-ProcessClassification -Processes $procs -Config $cfg -ForegroundPid 0
            $targets = Get-PriorityLoweringTargets -ClassifiedProcesses $classified -Processes $procs
            $targets.Count | Should Be 1
            $targets[0].Pid | Should Be 61
        }
    }

    Context 'Live, reversible native call' {
        It 'reads, lowers and restores memory priority on our own child process' {
            $child = Start-Process -FilePath $env:ComSpec `
                -ArgumentList '/c', 'ping -n 10 127.0.0.1 >nul' -PassThru -WindowStyle Hidden
            try {
                Start-Sleep -Milliseconds 400
                (Get-SRGProcessMemoryPriority -ProcessId $child.Id).Priority | Should Be 5
                (Set-SRGProcessMemoryPriority -ProcessId $child.Id -Priority 2).Success | Should Be $true
                (Get-SRGProcessMemoryPriority -ProcessId $child.Id).Priority | Should Be 2
                (Set-SRGProcessMemoryPriority -ProcessId $child.Id -Priority 5).Success | Should Be $true
                (Get-SRGProcessMemoryPriority -ProcessId $child.Id).Priority | Should Be 5
            }
            finally {
                Wait-Process -Id $child.Id -Timeout 15 -ErrorAction SilentlyContinue
            }
        }

        It 'reports failure instead of throwing when access is denied' {
            # PID 4 is the System process; OpenProcess is denied by design.
            $r = Set-SRGProcessMemoryPriority -ProcessId 4 -Priority 2
            $r.Success | Should Be $false
            $r.Error | Should Match 'OpenProcess'
        }
    }

    Context 'WhatIf mode' {
        It 'reports WouldLower without touching the process' {
            $t = @([pscustomobject]@{ Pid = 12345; Name = 'ghost'; WorkingSetMiB = 200; CpuPercent = 0; Reason = 'test' })
            $r = Invoke-PriorityLowering -Targets $t -WhatIfOnly
            $r[0].Outcome | Should Be 'WouldLower'
        }
    }
}

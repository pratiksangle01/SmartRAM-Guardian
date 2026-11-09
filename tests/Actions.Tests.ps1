# Safety-gate and graceful-close tests. These cover the rules that must hold
# no matter what the configuration says.
. "$PSScriptRoot\TestHelpers.ps1"

Describe 'Safety gate' {

    Context 'Observe mode' {
        It 'is always closed in observe mode' {
            $cfg = Get-TestConfig -Mode 'observe'
            $sample = New-FakeSample -AvailableMiB 300 -CommitPercent 95 -PagesInputPerSec 4000
            $a = Get-PressureAssessment -Sample $sample -Config $cfg
            $gate = Test-SafetyGate -Config $cfg -Processes $sample.Processes -Assessment $a -RootPath $TestDrive
            $gate.Open | Should Be $false
            ($gate.Reasons -join ';') | Should Match 'observe'
        }
    }

    Context 'Installer-running protection' {
        It 'closes the gate for every mode while an installer is running' {
            $cfg = Get-TestConfig -Mode 'allowlisted-close' -AllowlistClose @('Spotify')
            $procs = @((New-FakeProcess -Name 'msiexec' -CommandLine 'msiexec /i setup.msi'))
            $sample = New-FakeSample -AvailableMiB 300 -CommitPercent 95 -PagesInputPerSec 4000 -Processes $procs
            $a = Get-PressureAssessment -Sample $sample -Config $cfg
            $gate = Test-SafetyGate -Config $cfg -Processes $procs -Assessment $a -RootPath $TestDrive
            $gate.Open | Should Be $false
            ($gate.Reasons -join ';') | Should Match 'installer'
        }

        It 'closes the gate during an active build' {
            $cfg = Get-TestConfig -Mode 'safe-auto'
            $procs = @((New-FakeProcess -Name 'dotnet' -CommandLine 'dotnet build -c Release'))
            $sample = New-FakeSample -AvailableMiB 300 -CommitPercent 95 -PagesInputPerSec 4000 -Processes $procs
            $a = Get-PressureAssessment -Sample $sample -Config $cfg
            $gate = Test-SafetyGate -Config $cfg -Processes $procs -Assessment $a -RootPath $TestDrive
            $gate.Open | Should Be $false
            ($gate.Reasons -join ';') | Should Match 'build'
        }
    }

    Context 'Paused state' {
        It 'closes the gate while the pause marker exists, even in safe-auto' {
            $cfg = Get-TestConfig -Mode 'safe-auto'
            $marker = Join-Path $TestDrive $cfg.PauseFile
            Set-Content -LiteralPath $marker -Value 'paused' -Encoding ASCII
            $sample = New-FakeSample -AvailableMiB 300 -CommitPercent 95 -PagesInputPerSec 4000
            $a = Get-PressureAssessment -Sample $sample -Config $cfg
            $gate = Test-SafetyGate -Config $cfg -Processes $sample.Processes -Assessment $a -RootPath $TestDrive
            $gate.Open | Should Be $false
            ($gate.Reasons -join ';') | Should Match 'paused'
            Remove-Item -LiteralPath $marker -Force
        }

        It 'reopens after the pause marker is removed' {
            $cfg = Get-TestConfig -Mode 'safe-auto'
            $sample = New-FakeSample -AvailableMiB 300 -CommitPercent 95 -PagesInputPerSec 4000
            $a = Get-PressureAssessment -Sample $sample -Config $cfg
            $gate = Test-SafetyGate -Config $cfg -Processes $sample.Processes -Assessment $a -RootPath $TestDrive
            $gate.Open | Should Be $true
        }
    }

    Context 'Cooldown' {
        It 'closes the gate while cooldown is active' {
            $cfg = Get-TestConfig -Mode 'safe-auto'
            $sample = New-FakeSample -AvailableMiB 300 -CommitPercent 95 -PagesInputPerSec 4000
            $a = Get-PressureAssessment -Sample $sample -Config $cfg -LastActionTime (Get-Date).AddSeconds(-5)
            $gate = Test-SafetyGate -Config $cfg -Processes $sample.Processes -Assessment $a -RootPath $TestDrive
            $gate.Open | Should Be $false
            ($gate.Reasons -join ';') | Should Match 'cooldown'
        }
    }
}

Describe 'Graceful close eligibility' {

    Context 'Allowlisted app' {
        It 'is eligible when it is allowlisted, has a window and no unsaved marker' {
            $cfg = Get-TestConfig -Mode 'allowlisted-close' -AllowlistClose @('Spotify')
            $p = New-FakeProcess -Name 'Spotify' -HasMainWindow $true -MainWindowTitle 'Spotify Premium'
            $e = Test-CloseEligibility -Process $p -Config $cfg -Class 'Disposable'
            $e.Eligible | Should Be $true
        }

        It 'is skipped when the window title suggests unsaved work' {
            $cfg = Get-TestConfig -Mode 'allowlisted-close' -AllowlistClose @('Editor')
            $p = New-FakeProcess -Name 'Editor' -HasMainWindow $true -MainWindowTitle '*untitled-1 - Editor'
            $e = Test-CloseEligibility -Process $p -Config $cfg -Class 'Disposable'
            $e.Eligible | Should Be $false
            $e.Reason | Should Match 'unsaved'
        }

        It 'is skipped when the process has no main window' {
            $cfg = Get-TestConfig -Mode 'allowlisted-close' -AllowlistClose @('Spotify')
            $p = New-FakeProcess -Name 'Spotify' -HasMainWindow $false -MainWindowTitle ''
            $e = Test-CloseEligibility -Process $p -Config $cfg -Class 'Disposable'
            $e.Eligible | Should Be $false
            $e.Reason | Should Match 'no main window'
        }
    }

    Context 'Non-allowlisted classes are never eligible' {
        It 'refuses Protected, ActiveWork, SuggestionOnly and Unknown' {
            $cfg = Get-TestConfig -Mode 'allowlisted-close'
            $p = New-FakeProcess -Name 'Anything' -HasMainWindow $true
            foreach ($cls in 'Protected', 'ActiveWork', 'SuggestionOnly', 'Unknown') {
                $e = Test-CloseEligibility -Process $p -Config $cfg -Class $cls
                $e.Eligible | Should Be $false
            }
        }
    }

    Context 'Close mechanics never force-kill' {
        It 'reports NoMainWindow instead of killing a windowless process' {
            $cfg = Get-TestConfig -Mode 'allowlisted-close' -AllowlistClose @('Spotify')
            # svchost is a real, windowless, protected system process. It must
            # survive any code path here untouched.
            $svc = Get-Process -Name 'svchost' -ErrorAction SilentlyContinue | Select-Object -First 1
            $svc | Should Not Be $null
            $svcPid = $svc.Id
            $res = Invoke-GracefulClose -ProcessId $svcPid -Config $cfg -WhatIfOnly
            $res.Outcome | Should Be 'NoMainWindow'
            (Get-Process -Id $svcPid -ErrorAction SilentlyContinue) | Should Not Be $null
        }

        It 'reports AlreadyGone for a pid that does not exist' {
            $cfg = Get-TestConfig -Mode 'allowlisted-close'
            $res = Invoke-GracefulClose -ProcessId 999999 -Config $cfg
            $res.Outcome | Should Be 'AlreadyGone'
        }

        It 'has no -Force / Stop-Process / taskkill code path anywhere' {
            $src = Get-ChildItem -Path (Join-Path $script:RepoRoot 'src') -Filter *.ps* |
                ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw }
            $joined = $src -join "`n"
            $joined | Should Not Match 'Stop-Process'
            $joined | Should Not Match 'taskkill'
            $joined | Should Not Match 'TerminateProcess'
        }
    }
}

# Pressure engine tests. The central claim under test: "low available RAM"
# alone is NOT actionable pressure (that is just standby cache).
. "$PSScriptRoot\TestHelpers.ps1"

Describe 'Pressure assessment' {

    Context 'False low-memory trigger (cache only)' {
        It 'does not act when available is low but commit and paging are calm' {
            $cfg = Get-TestConfig
            $sample = New-FakeSample -AvailableMiB 400 -CommitPercent 45 -PagesInputPerSec 10
            $a = Get-PressureAssessment -Sample $sample -Config $cfg
            $a.Level | Should Be 'Critical'
            $a.Signals | Should Be 'AvailableCritical'
            $a.Corroborated | Should Be $false
            $a.Actionable | Should Be $false
            $a.Reason | Should Match 'single signal'
        }

        It 'does not act when commit is merely elevated without memory pressure' {
            $cfg = Get-TestConfig
            $sample = New-FakeSample -AvailableMiB 3000 -CommitPercent 85 -PagesInputPerSec 0
            $a = Get-PressureAssessment -Sample $sample -Config $cfg
            # CommitHigh is a warning but not corroborated by memory or paging.
            $a.Signals | Should Be 'CommitHigh'
            $a.Actionable | Should Be $false
        }

        It 'reports no signals and no action on a healthy machine' {
            $cfg = Get-TestConfig
            $sample = New-FakeSample -AvailableMiB 4000 -CommitPercent 40 -PagesInputPerSec 0
            $a = Get-PressureAssessment -Sample $sample -Config $cfg
            $a.Level | Should Be 'Normal'
            $a.Signals.Count | Should Be 0
            $a.Actionable | Should Be $false
        }
    }

    Context 'Genuine pressure' {
        It 'acts when low memory is corroborated by hard paging' {
            $cfg = Get-TestConfig
            $sample = New-FakeSample -AvailableMiB 500 -CommitPercent 70 -PagesInputPerSec 3000
            $a = Get-PressureAssessment -Sample $sample -Config $cfg
            $a.Signals -contains 'AvailableCritical' | Should Be $true
            $a.Signals -contains 'HardPaging' | Should Be $true
            $a.Actionable | Should Be $true
        }

        It 'treats a critical commit charge as actionable on its own' {
            $cfg = Get-TestConfig
            $sample = New-FakeSample -AvailableMiB 2500 -CommitPercent 95 -PagesInputPerSec 0
            $a = Get-PressureAssessment -Sample $sample -Config $cfg
            $a.Signals | Should Be 'CommitCritical'
            $a.Corroborated | Should Be $true
            $a.Actionable | Should Be $true
        }
    }

    Context 'Hysteresis' {
        It 'stays Critical until the machine is clearly clear of the band' {
            $cfg = Get-TestConfig
            # 1000 MiB is below warn(1536)+hysteresis(256) => must not drop.
            $sample = New-FakeSample -AvailableMiB 1000 -CommitPercent 50 -PagesInputPerSec 0
            $a = Get-PressureAssessment -Sample $sample -Config $cfg -PreviousLevel 'Critical'
            $a.Level | Should Be 'Warn'
        }

        It 'returns to Normal when clearly above the hysteresis band' {
            $cfg = Get-TestConfig
            $sample = New-FakeSample -AvailableMiB 3000 -CommitPercent 40 -PagesInputPerSec 0
            $a = Get-PressureAssessment -Sample $sample -Config $cfg -PreviousLevel 'Critical'
            $a.Level | Should Be 'Normal'
        }

        It 'does not flap from Warn to Normal on a borderline reading' {
            $cfg = Get-TestConfig
            $sample = New-FakeSample -AvailableMiB 1600 -CommitPercent 40 -PagesInputPerSec 0
            $a = Get-PressureAssessment -Sample $sample -Config $cfg -PreviousLevel 'Warn'
            $a.Level | Should Be 'Warn'
        }
    }

    Context 'Cooldown' {
        It 'suppresses action while inside the cooldown window' {
            $cfg = Get-TestConfig
            $sample = New-FakeSample -AvailableMiB 400 -CommitPercent 95 -PagesInputPerSec 3000
            $a = Get-PressureAssessment -Sample $sample -Config $cfg -LastActionTime (Get-Date).AddSeconds(-30)
            $a.InCooldown | Should Be $true
            $a.Actionable | Should Be $false
            $a.CooldownRemaining -gt 0 | Should Be $true
        }

        It 'allows action again once the cooldown has elapsed' {
            $cfg = Get-TestConfig
            $sample = New-FakeSample -AvailableMiB 400 -CommitPercent 95 -PagesInputPerSec 3000
            $a = Get-PressureAssessment -Sample $sample -Config $cfg -LastActionTime (Get-Date).AddSeconds(-($cfg.CooldownSeconds + 5))
            $a.InCooldown | Should Be $false
            $a.Actionable | Should Be $true
        }
    }
}

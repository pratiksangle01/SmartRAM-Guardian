# Config validation tests: broken or unsafe configs must be rejected loudly.
. "$PSScriptRoot\TestHelpers.ps1"

Describe 'Config validation' {

    Context 'Defaults' {
        It 'accepts the default config shipped in config.json' {
            $cfg = Get-SRGConfig -Path (Join-Path $script:RepoRoot 'config.json')
            $cfg.Mode | Should Be 'observe'
            $cfg.AllowlistClose.Count | Should Be 0
            $cfg.EnableAggressiveTrim | Should Be $false
            $cfg.EnableEcoQoS | Should Be $false
        }

        It 'starts with an empty allowlist (no app can be closed by default)' {
            $cfg = Get-SRGConfig -Path (Join-Path $script:RepoRoot 'config.json')
            @($cfg.AllowlistClose).Count | Should Be 0
        }
    }

    Context 'Rejections' {
        It 'rejects an unknown mode' {
            $cfg = Get-SRGDefaultConfig
            $cfg.Mode = 'yolo'
            (Test-SRGConfig -Config $cfg).Count -gt 0 | Should Be $true
        }

        It 'rejects critical >= warn available threshold' {
            $cfg = Get-SRGDefaultConfig
            $cfg.CriticalAvailableMiB = 2048
            $cfg.WarnAvailableMiB = 1536
            $errors = Test-SRGConfig -Config $cfg
            $errors.Count -gt 0 | Should Be $true
        }

        It 'rejects aggressive trimming (unsafe, unsupported)' {
            $cfg = Get-SRGDefaultConfig
            $cfg.EnableAggressiveTrim = $true
            (Test-SRGConfig -Config $cfg) -join ';' | Should Match 'AggressiveTrim'
        }

        It 'rejects a process that is both protected and close-allowlisted' {
            $cfg = Get-SRGDefaultConfig
            $cfg.AllowlistClose = @('Spotify')
            $cfg.ExtraProtectedProcesses = @('Spotify')
            (Test-SRGConfig -Config $cfg) -join ';' | Should Match 'both protected'
        }

        It 'rejects an out-of-range sample interval' {
            $cfg = Get-SRGDefaultConfig
            $cfg.SampleIntervalSeconds = 1
            (Test-SRGConfig -Config $cfg) -join ';' | Should Match 'SampleIntervalSeconds'
        }
    }

    Context 'File handling' {
        It 'throws on invalid JSON' {
            $bad = Join-Path $TestDrive 'bad.json'
            Set-Content -LiteralPath $bad -Value '{ "Mode": "observe", ' -Encoding UTF8
            { Get-SRGConfig -Path $bad } | Should Throw
        }

        It 'throws on a missing file' {
            { Get-SRGConfig -Path (Join-Path $TestDrive 'nope.json') } | Should Throw
        }

        It 'merges unknown keys away and keeps defaults for absent keys' {
            $min = Join-Path $TestDrive 'min.json'
            Set-Content -LiteralPath $min -Value '{ "SampleIntervalSeconds": 45, "MadeUpKey": 1 }' -Encoding UTF8
            $cfg = Get-SRGConfig -Path $min
            $cfg.SampleIntervalSeconds | Should Be 45
            $cfg.Mode | Should Be 'observe'
            $cfg.PSObject.Properties.Name -contains 'MadeUpKey' | Should Be $false
        }
    }
}

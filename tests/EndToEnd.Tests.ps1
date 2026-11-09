# End-to-end dry run: execute the real guardian loop for exactly one pass in
# observe mode against a throwaway root, and prove it changed nothing.
. "$PSScriptRoot\TestHelpers.ps1"

Describe 'End-to-end dry run (observe mode)' {

    $root = Join-Path $TestDrive 'guardian-root'
    $cfgPath = Join-Path $root 'config.json'
    $statePath = Join-Path $root 'state.json'
    $logPath = Join-Path $root 'logs\guardian.log'
    $guardian = Join-Path $script:RepoRoot 'src\Guardian.ps1'

    # Prepare the throwaway install once, before any assertion runs.
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $cfg = Get-SRGConfig -Path (Join-Path $script:RepoRoot 'config.json')
    $cfg.Mode = 'observe'
    $cfg.LogDirectory = 'logs'
    $cfg | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $cfgPath -Encoding UTF8

    It 'runs one observe pass without throwing' {
        { & $guardian -Once -ConfigPath $cfgPath -RootPath $root } | Should Not Throw
    }

    It 'wrote a state file that says observe and gate closed' {
        Test-Path -LiteralPath $statePath | Should Be $true
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $state.mode | Should Be 'observe'
        $state.gateOpen | Should Be $false
        $state.pressureLevel | Should BeOfType 'System.String'
        ($state.availableMiB -gt 0) | Should Be $true
        ($state.classes.Protected -gt 0) | Should Be $true
    }

    It 'wrote a log with the lifecycle and sample lines' {
        Test-Path -LiteralPath $logPath | Should Be $true
        $log = Get-Content -LiteralPath $logPath -Raw
        $log | Should Match 'guardian starting'
        $log | Should Match 'avail='
        $log | Should Match 'mode=observe'
    }

    It 'took no action: no ACTION lines, no graceful close' {
        $log = Get-Content -LiteralPath $logPath -Raw
        $log | Should Not Match '\[ACTION'
        $log | Should Not Match 'graceful close'
    }

    It 'left the pid file clean after the single pass' {
        Test-Path -LiteralPath (Join-Path $root 'guardian.pid') | Should Be $false
    }

    It 'did not close any process that existed before the pass' {
        $before = @(Get-Process -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
        & $guardian -Once -ConfigPath $cfgPath -RootPath $root
        $after = @(Get-Process -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
        $missing = @($before | Where-Object { $after -notcontains $_ })
        $log = Get-Content -LiteralPath $logPath -Raw
        $log | Should Not Match 'graceful close'
        # Allow ordinary background churn; the guardian logs every close it makes.
        ($missing.Count -lt 10) | Should Be $true
    }

    It 'produces a diagnose snapshot with pressure and candidate sections' {
        # 6>&1 is required because diagnose.ps1 reports through Write-Host.
        $out = & (Join-Path $script:RepoRoot 'diagnose.ps1') 2>&1 6>&1 | Out-String
        $out | Should Match 'pressure'
        $out | Should Match 'Disposable candidates'
        $out | Should Match 'safety gate'
    }
}

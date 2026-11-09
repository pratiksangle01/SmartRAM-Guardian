# status.ps1 tests: the "process alive but first sample pending" case.
. "$PSScriptRoot\TestHelpers.ps1"

function New-FakeStateFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$AgeSeconds = 0
    )
    $state = [ordered]@{
        timestamp          = (Get-Date).ToString('s')
        mode               = 'observe'
        profile            = 'default'
        guardianPid        = 0
        availableMiB       = 2000
        totalMiB           = 8000
        commitPercent      = 50
        pagesInputPerSec   = 0
        pressureLevel      = 'Normal'
        signals            = @()
        corroborated       = $false
        actionable         = $false
        reason             = 'no pressure signals'
        inCooldown         = $false
        paused             = $false
        gateOpen           = $false
        gateReasons        = @("mode is 'observe' (log and suggest only)")
        foreground         = ''
        workContextMembers = @()
        topWorkingSets     = @()
        classes            = [ordered]@{
            Protected      = 1
            ActiveWork     = 0
            Disposable     = 0
            SuggestionOnly = 0
            Unknown        = 1
        }
    }
    ($state | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $Path -Encoding UTF8
    if ($AgeSeconds -gt 0) {
        (Get-Item -LiteralPath $Path).LastWriteTime = (Get-Date).AddSeconds(-$AgeSeconds)
    }
}

Describe 'status.ps1' {

    $statusScript = Join-Path $script:RepoRoot 'status.ps1'

    function New-StatusRoot {
        param([string]$Name)
        $root = Join-Path $TestDrive $Name
        New-Item -ItemType Directory -Path $root -Force | Out-Null
        Copy-Item (Join-Path $script:RepoRoot 'config.json') (Join-Path $root 'config.json')
        return $root
    }

    Context 'Process alive, no state file yet' {
        It 'reports True with a starting note instead of False' {
            $root = New-StatusRoot -Name 'alive-nostate'
            "$PID" | Set-Content -LiteralPath (Join-Path $root 'guardian.pid') -Encoding ASCII

            # 6>&1 captures Write-Host output.
            $out = & $statusScript -RootPath $root 2>&1 6>&1 | Out-String

            $out | Should Match "running\s+:\s+True \(pid $PID\) - starting, first sample pending"
            $out | Should Match 'state file   : pending'
            $out | Should Not Match 'running\s+:\s+False'
        }
    }

    Context 'Process alive with a fresh state file' {
        It 'reports normal running status with the pid' {
            $root = New-StatusRoot -Name 'alive-fresh'
            "$PID" | Set-Content -LiteralPath (Join-Path $root 'guardian.pid') -Encoding ASCII
            New-FakeStateFile -Path (Join-Path $root 'state.json') -AgeSeconds 1

            $out = & $statusScript -RootPath $root 2>&1 6>&1 | Out-String

            $out | Should Match "running\s+:\s+True \(pid $PID\)"
            $out | Should Not Match 'starting, first sample pending'
            $out | Should Match 'mode         : observe'
        }
    }

    Context 'Process alive but state file is stale' {
        It 'reports that no fresh sample has been written' {
            $root = New-StatusRoot -Name 'alive-stale'
            "$PID" | Set-Content -LiteralPath (Join-Path $root 'guardian.pid') -Encoding ASCII
            New-FakeStateFile -Path (Join-Path $root 'state.json') -AgeSeconds 600

            $out = & $statusScript -RootPath $root 2>&1 6>&1 | Out-String

            $out | Should Match "running\s+:\s+True \(pid $PID\) - alive, no fresh sample for \d+s"
        }
    }

    Context 'No pid file' {
        It 'reports running False and the no-state message' {
            $root = New-StatusRoot -Name 'not-running'

            $out = & $statusScript -RootPath $root 2>&1 6>&1 | Out-String

            $out | Should Match 'running\s+:\s+False'
            $out | Should Match 'state file   : none yet'
        }
    }
}

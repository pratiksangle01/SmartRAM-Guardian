#Requires -Version 5.1
<#
.SYNOPSIS
    SmartRAM-Guardian main loop. Default mode is observe: sample, assess,
    classify, log suggestions - never act.
.PARAMETER Once
    Run a single pass and exit (used by diagnose.ps1 and the dry-run test).
#>
[CmdletBinding()]
param(
    [string]$ConfigPath,
    [string]$RootPath,
    [switch]$Once
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$root = if ($RootPath) { $RootPath } else { Split-Path -Parent $PSScriptRoot }
if (-not (Test-Path -LiteralPath $root)) { New-Item -ItemType Directory -Path $root -Force | Out-Null }
if (-not $ConfigPath) { $ConfigPath = Join-Path $root 'config.json' }

foreach ($m in 'SRG.Logging', 'SRG.Config', 'SRG.Sampler', 'SRG.Pressure', 'SRG.Classifier', 'SRG.Context', 'SRG.Priority', 'SRG.Actions') {
    Import-Module (Join-Path $PSScriptRoot "$m.psm1") -Force -DisableNameChecking
}

$config = Get-SRGConfig -Path $ConfigPath
$logDir = if ([System.IO.Path]::IsPathRooted($config.LogDirectory)) { $config.LogDirectory } else { Join-Path $root $config.LogDirectory }
Initialize-SRGLog -Directory $logDir -MaxBytes $config.LogMaxBytes -MaxFiles $config.LogMaxFiles -LowDiskFreeMiB $config.LowDiskFreeMiB

$statePath = Join-Path $root $config.StateFile
$pidPath = Join-Path $root $config.PidFile
$pausePath = Join-Path $root $config.PauseFile
$stopPath = Join-Path $root 'guardian.stop'

function Test-StopRequested { Test-Path -LiteralPath $stopPath }

function Wait-IntervalSeconds {
    param([int]$Seconds)
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (Test-StopRequested) { return }
        Start-Sleep -Seconds 1
    }
}

function Write-State {
    param($Sample, $Assessment, $Context, $Gate, $Extra)
    $state = [ordered]@{
        timestamp        = (Get-Date).ToString('s')
        mode             = $config.Mode
        profile          = $config.Profiles.Active
        guardianPid      = $PID
        availableMiB     = $Sample.AvailableMiB
        totalMiB         = $Sample.TotalMiB
        commitPercent    = $Sample.CommitPercent
        pagesInputPerSec = $Sample.PagesInputPerSec
        pressureLevel    = $Assessment.Level
        signals          = @($Assessment.Signals)
        corroborated     = $Assessment.Corroborated
        actionable       = $Assessment.Actionable
        reason           = $Assessment.Reason
        inCooldown       = $Assessment.InCooldown
        paused           = (Test-Path -LiteralPath $pausePath)
        gateOpen         = $Gate.Open
        gateReasons      = @($Gate.Reasons)
        foreground       = if ($Context) { "$($Context.ForegroundName) (pid $($Context.ForegroundPid))" } else { '' }
        workContextMembers = if ($Context) { @($Context.MemberNames) } else { @() }
    }
    if ($Extra) { foreach ($k in $Extra.Keys) { $state[$k] = $Extra[$k] } }
    try {
        ($state | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $statePath -Encoding UTF8 -Force
    }
    catch {
        Write-SRGLog -Level WARN -Category 'state' -Message "failed to write state file: $($_.Exception.Message)"
    }
}

# --- Crash recovery / single instance --------------------------------------
if (-not $Once) {
    if (Test-Path -LiteralPath $pidPath) {
        $oldPid = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
        $old = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($old) {
            Write-SRGLog -Level WARN -Category 'lifecycle' -Message "another guardian instance (pid $oldPid) is alive; exiting"
            exit 0
        }
        Write-SRGLog -Level WARN -Category 'lifecycle' -Message "stale pid file from pid $oldPid (previous run crashed or was stopped); taking over"
    }
    "$PID" | Set-Content -LiteralPath $pidPath -Encoding ASCII -Force
}

Write-SRGLog -Level INFO -Category 'lifecycle' -Message "guardian starting (pid $PID, mode=$($config.Mode), interval=$($config.SampleIntervalSeconds)s)"

$pressureState = New-PressureState
$prevSample = $null
$prevCpuMap = $null
# PIDs we already lowered priority on, so we never re-issue the same call every
# pass (avoids a retry storm against a process that refuses the call).
$loweredPids = @{}

try {
    do {
        if (Test-StopRequested) { break }
        $paused = Test-Path -LiteralPath $pausePath

        $sampleResult = Get-SRGSample -Config $config -PreviousSample $prevSample -PreviousCpuMap $prevCpuMap
        $sample = $sampleResult[0]
        $cpuMap = $sampleResult[1]

        $assessment = Get-PressureAssessment -Sample $sample -Config $config `
            -PreviousLevel $pressureState.Level -LastActionTime $pressureState.LastActionTime
        $pressureState.Level = $assessment.Level
        $pressureState.Samples++

        $context = Get-WorkContext -Processes $sample.Processes -Config $config -ForegroundPid $sample.ForegroundPid
        $classified = Get-ProcessClassification -Processes $sample.Processes -Config $config -ForegroundPid $sample.ForegroundPid
        $gate = Test-SafetyGate -Config $config -Processes $sample.Processes -Assessment $assessment -RootPath $root

        $logLine = "avail={0}MiB commit={1}% pagesIn={2}/s level={3} signals=[{4}] fg={5}" -f `
            $sample.AvailableMiB, $sample.CommitPercent, [int]$sample.PagesInputPerSec, `
            $assessment.Level, ($assessment.Signals -join ','), $sample.ForegroundName
        Write-SRGLog -Level INFO -Category 'sample' -Message $logLine

        if ($assessment.Actionable -and -not $paused) {
            if ($config.Mode -eq 'observe') {
                Write-SRGLog -Level INFO -Category 'suggest' -Message "corroborated pressure but mode=observe; no action. $($assessment.Reason)"
                $wouldLower = Get-PriorityLoweringTargets -ClassifiedProcesses $classified -Processes $sample.Processes
                if ($wouldLower.Count -gt 0) {
                    Write-SRGLog -Level INFO -Category 'suggest' -Message ("would lower memory priority on: " +
                        (($wouldLower | ForEach-Object { "$($_.Name)(pid $($_.Pid), $($_.WorkingSetMiB)MiB)" }) -join ', '))
                }
            }
            elseif (-not $gate.Open) {
                Write-SRGLog -Level INFO -Category 'action' -Message "gate closed: $($gate.Reasons -join '; ')"
            }
            else {
                # Levers run in order of preference: (a) lower priority of idle
                # background processes, then (c) graceful close of allowlisted
                # apps. Nothing else is ever done.
                if ($config.EnableEcoQoS) {
                    # Drop bookkeeping for processes that are gone.
                    foreach ($k in @($loweredPids.Keys)) {
                        if (-not (Get-Process -Id $k -ErrorAction SilentlyContinue)) { $loweredPids.Remove($k) }
                    }
                    $targets = Get-PriorityLoweringTargets -ClassifiedProcesses $classified -Processes $sample.Processes
                    if ($targets.Count -gt 0) {
                        $applied = Invoke-PriorityLowering -Targets $targets -SeenPids $loweredPids -ApplyEcoQoS
                        foreach ($x in $applied) {
                            Write-SRGLog -Level ACTION -Category 'action' -Message "lower priority $($x.Name) (pid $($x.Pid)): $($x.Outcome) - $($x.Detail)"
                        }
                    }
                }
                if ($config.Mode -eq 'allowlisted-close') {
                    $targets = @($classified | Where-Object { $_.Class -eq 'Disposable' } |
                        Sort-Object WorkingSetMiB -Descending)
                    foreach ($t in $targets) {
                        if (-not $t.MainWindowTitle) {
                            Add-Member -InputObject $t -NotePropertyName MainWindowTitle `
                                -NotePropertyValue (Get-ProcessWindowTitle -ProcessId $t.Pid) -Force
                        }
                        $elig = Test-CloseEligibility -Process $t -Config $config -Class $t.Class
                        if (-not $elig.Eligible) {
                            Write-SRGLog -Level INFO -Category 'action' -Message "skip $($t.Name) (pid $($t.Pid)): $($elig.Reason)"
                            continue
                        }
                        $res = Invoke-GracefulClose -ProcessId $t.Pid -Config $config
                        Write-SRGLog -Level ACTION -Category 'action' -Message "graceful close $($t.Name) (pid $($t.Pid)): $($res.Outcome) after $($res.WaitedMs)ms"
                        if ($res.Outcome -eq 'Closed') {
                            $pressureState.LastActionTime = Get-Date
                            break
                        }
                    }
                }
                else {
                    Write-SRGLog -Level INFO -Category 'action' -Message "pressure actionable; mode=$($config.Mode) - priority lever evaluated"
                }
            }
        }
        elseif ($assessment.Signals.Count -gt 0 -and -not $assessment.Corroborated) {
            Write-SRGLog -Level DEBUG -Category 'sample' -Message "not acting: $($assessment.Reason)"
        }

        Write-State -Sample $sample -Assessment $assessment -Context $context -Gate $gate -Extra @{
            topWorkingSets = @($sample.TopByWorkingSet | ForEach-Object { "{0}={1}MiB" -f $_.Name, $_.WorkingSetMiB })
            classes        = @{
                Protected      = @($classified | Where-Object Class -eq 'Protected').Count
                ActiveWork     = @($classified | Where-Object Class -eq 'ActiveWork').Count
                Disposable     = @($classified | Where-Object Class -eq 'Disposable').Count
                SuggestionOnly = @($classified | Where-Object Class -eq 'SuggestionOnly').Count
                Unknown        = @($classified | Where-Object Class -eq 'Unknown').Count
            }
        }

        $prevSample = $sample
        $prevCpuMap = $cpuMap

        if (-not $Once) {
            # Sample faster only when pressure is corroborated (i.e. we might act),
            # not merely because a single cosmetic signal is present.
            $interval = $config.SampleIntervalSeconds
            if ($config.AdaptiveSampling -and $assessment.Actionable) {
                $interval = $config.BusyIntervalSeconds
            }
            Wait-IntervalSeconds -Seconds $interval
        }
    } while (-not $Once)
}
catch {
    Write-SRGLog -Level ERROR -Category 'lifecycle' -Message "unhandled error: $($_.Exception.Message)"
    throw
}
finally {
    if (-not $Once -and (Test-Path -LiteralPath $pidPath)) {
        $content = (Get-Content -LiteralPath $pidPath -Raw).Trim()
        if ($content -eq "$PID") { Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue }
    }
    if (Test-Path -LiteralPath $stopPath) { Remove-Item -LiteralPath $stopPath -Force -ErrorAction SilentlyContinue }
    Write-SRGLog -Level INFO -Category 'lifecycle' -Message "guardian exiting (pid $PID)"
}

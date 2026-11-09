# SRG.Actions - the levers, each behind the safety gate.
# Modes:
#   observe           -> everything here only logs what WOULD happen
#   safe-auto         -> may lower memory priority of idle background processes
#                        (only when EnableEcoQoS is true; default off)
#   allowlisted-close -> additionally may gracefully close allowlisted apps
# Invariants enforced here regardless of mode:
#   - never force-kill, never process trees, CloseMainWindow + wait only
#   - never act while an installer/build/large copy is running
#   - never act on Protected / ActiveWork / Unknown / SuggestionOnly classes
#   - never act during cooldown or while paused

Set-StrictMode -Version 2.0

function Test-SafetyGate {
    <#
    .SYNOPSIS
        Global gate evaluated before ANY action. Returns Open/Blocked + reasons.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Config,
        [Parameter(Mandatory = $true)]$Processes,
        [Parameter(Mandatory = $true)]$Assessment,
        [string]$RootPath
    )
    $blocked = New-Object System.Collections.Generic.List[string]

    if ($Config.Mode -eq 'observe') {
        $blocked.Add("mode is 'observe' (log and suggest only)")
    }
    if ($Assessment.InCooldown) {
        $blocked.Add("cooldown active ($($Assessment.CooldownRemaining)s remaining)")
    }
    if ($RootPath) {
        $pauseFile = Join-Path $RootPath $Config.PauseFile
        if (Test-Path -LiteralPath $pauseFile) { $blocked.Add('guardian is paused (pause file present)') }
    }
    $sensitive = Get-ActiveSensitiveOperations -Processes $Processes
    foreach ($s in $sensitive) { $blocked.Add($s) }

    return [pscustomobject]@{
        Open    = ($blocked.Count -eq 0)
        Reasons = $blocked.ToArray()
    }
}

function Test-CloseEligibility {
    <#
    .SYNOPSIS
        Pure per-process decision for graceful close. Returns Eligible + Reason.
        Kept side-effect free so it is fully unit-testable.
    #>
    param(
        [Parameter(Mandatory = $true)]$Process,
        [Parameter(Mandatory = $true)]$Config,
        [Parameter(Mandatory = $true)][string]$Class
    )
    if ($Class -ne 'Disposable') {
        return [pscustomobject]@{ Eligible = $false; Reason = "class is '$Class', only allowlisted (Disposable) apps may close" }
    }
    if ($Process.HasMainWindow -ne $true) {
        return [pscustomobject]@{ Eligible = $false; Reason = 'no main window - cannot request a graceful close, skipping' }
    }
    if ($Process.MainWindowTitle -match '\*' -or $Process.MainWindowTitle -match 'unsaved') {
        return [pscustomobject]@{ Eligible = $false; Reason = "window title suggests unsaved work ('$($Process.MainWindowTitle)')" }
    }
    return [pscustomobject]@{ Eligible = $true; Reason = 'allowlisted, has main window, no unsaved-work marker' }
}

function Invoke-GracefulClose {
    <#
    .SYNOPSIS
        Sends CloseMainWindow and waits. Never kills. If the app shows a save
        dialog and stays up, we simply leave it alone and report the outcome.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)]$Config,
        [switch]$WhatIfOnly
    )
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $proc) {
        return [pscustomobject]@{ Outcome = 'AlreadyGone'; ProcessId = $ProcessId; WaitedMs = 0 }
    }
    if (-not $proc.MainWindowHandle -or $proc.MainWindowHandle -eq 0) {
        return [pscustomobject]@{ Outcome = 'NoMainWindow'; ProcessId = $ProcessId; WaitedMs = 0 }
    }
    if ($WhatIfOnly) {
        return [pscustomobject]@{ Outcome = 'WouldClose'; ProcessId = $ProcessId; WaitedMs = 0 }
    }

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $sent = $proc.CloseMainWindow()
    if (-not $sent) {
        return [pscustomobject]@{ Outcome = 'CloseRequestRejected'; ProcessId = $ProcessId; WaitedMs = 0 }
    }
    $timeoutMs = [int]$Config.GracefulCloseTimeoutSeconds * 1000
    while (-not $proc.HasExited -and $sw.ElapsedMilliseconds -lt $timeoutMs) {
        Start-Sleep -Milliseconds 250
        $proc.Refresh()
    }
    $sw.Stop()
    $outcome = if ($proc.HasExited) { 'Closed' } else { 'StayedOpen' }
    return [pscustomobject]@{ Outcome = $outcome; ProcessId = $ProcessId; WaitedMs = [int]$sw.ElapsedMilliseconds }
}

function Get-DisposableCandidates {
    <#
    .SYNOPSIS
        Learning loop, stage 1: propose candidates with evidence. Nothing is
        auto-approved - the user promotes a candidate into AllowlistClose with
        the approve command (see diagnose.ps1 / README).
    .NOTES
        A candidate is: Unknown class, has a window (so it can close
        gracefully), idle CPU across the sample, and a meaningful working set.
    #>
    param(
        [Parameter(Mandatory = $true)]$ClassifiedProcesses,
        [double]$MinWorkingSetMiB = 150,
        [double]$MaxCpuPercent = 1.0
    )
    $candidates = foreach ($p in $ClassifiedProcesses) {
        if ($p.Class -eq 'Unknown' -and $p.HasMainWindow -and
            $p.WorkingSetMiB -ge $MinWorkingSetMiB -and
            ($p.PSObject.Properties['CpuPercentSinceLast'] -eq $null -or $p.CpuPercentSinceLast -le $MaxCpuPercent)) {
            $io = 'n/a'
            if ($p.PSObject.Properties['IoMiBPerSecSinceLast']) { $io = "$($p.IoMiBPerSecSinceLast) MiB/s IO" }
            [pscustomobject]@{
                Name          = $p.Name
                Pid           = $p.Pid
                WorkingSetMiB = $p.WorkingSetMiB
                Evidence      = "idle (CPU $($p.CpuPercentSinceLast)%, $io), $($p.WorkingSetMiB) MiB resident, has main window, not protected, not in foreground work context"
            }
        }
    }
    return , @($candidates | Sort-Object WorkingSetMiB -Descending)
}

function Get-StartupSuggestions {
    <#
    .SYNOPSIS
        Read-only startup inventory for the suggestion lever. We report; we do
        not change anything here. User acts, or approves a future lever.
    #>
    $items = New-Object System.Collections.Generic.List[object]
    $runKeys = @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run'
    )
    foreach ($key in $runKeys) {
        if (-not (Test-Path $key)) { continue }
        $props = (Get-ItemProperty -Path $key).PSObject.Properties |
            Where-Object { $_.Name -notmatch '^PS' }
        foreach ($prop in $props) {
            $items.Add([pscustomobject]@{
                Source  = ($key -replace 'HK..:\\', '')
                Name    = $prop.Name
                Command = ("$($prop.Value)").Trim()
            })
        }
    }
    # Chrome background mode is a classic quiet win: it keeps running after the
    # window closes. We surface the state; changing it stays a manual/approved act.
    try {
        $chromeBg = Get-ItemProperty -Path 'HKCU:\Software\Google\Chrome\PreferenceMACs' -ErrorAction SilentlyContinue
        $chromeReg = Get-ItemProperty -Path 'HKCU:\Software\Policies\Google\Chrome' -Name BackgroundModeEnabled -ErrorAction SilentlyContinue
        if ($chromeReg) {
            $items.Add([pscustomobject]@{
                Source  = 'Chrome policy'
                Name    = 'BackgroundModeEnabled'
                Command = "currently $($chromeReg.BackgroundModeEnabled) - consider disabling background mode"
            })
        }
    }
    catch { }
    return , $items.ToArray()
}

Export-ModuleMember -Function Test-SafetyGate, Test-CloseEligibility, Invoke-GracefulClose,
    Get-DisposableCandidates, Get-StartupSuggestions

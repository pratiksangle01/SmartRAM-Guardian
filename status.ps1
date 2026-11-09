#Requires -Version 5.1
<#
.SYNOPSIS
    Show guardian status: running?, mode, last sample, pressure, gate state.
#>
[CmdletBinding()]
param(
    [string]$RootPath = $PSScriptRoot
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$root = $RootPath
$pidPath = Join-Path $root 'guardian.pid'
$statePath = Join-Path $root 'state.json'
$pausePath = Join-Path $root 'guardian.pause'

$running = $false; $gpid = 0
if (Test-Path -LiteralPath $pidPath) {
    $gpid = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
    $running = [bool](Get-Process -Id $gpid -ErrorAction SilentlyContinue)
}

# A state file older than three normal sample intervals means the loop has not
# completed a pass yet (or stopped writing). We still know the process is alive.
$freshWindowSeconds = 90
$cfgPath = Join-Path $root 'config.json'
if (Test-Path -LiteralPath $cfgPath) {
    try {
        $cfg = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json
        if ($cfg.SampleIntervalSeconds -ge 10) { $freshWindowSeconds = [int]$cfg.SampleIntervalSeconds * 3 }
    }
    catch { }
}

$stateAgeSeconds = $null
if (Test-Path -LiteralPath $statePath) {
    $age = ((Get-Date) - (Get-Item -LiteralPath $statePath).LastWriteTime).TotalSeconds
    $stateAgeSeconds = [int][math]::Max(0, $age)
}

if (-not $running) {
    $runningLine = 'False'
}
elseif ($null -eq $stateAgeSeconds) {
    $runningLine = "True (pid $gpid) - starting, first sample pending"
}
elseif ($stateAgeSeconds -gt $freshWindowSeconds) {
    $runningLine = "True (pid $gpid) - alive, no fresh sample for ${stateAgeSeconds}s"
}
else {
    $runningLine = "True (pid $gpid)"
}

Write-Host ''
Write-Host '=== SmartRAM-Guardian status ===' -ForegroundColor Cyan
Write-Host ("running      : {0}" -f $runningLine)
Write-Host ("paused       : {0}" -f (Test-Path -LiteralPath $pausePath))

$task = Get-ScheduledTask -TaskName 'SmartRAM-Guardian' -ErrorAction SilentlyContinue
Write-Host ("autostart    : {0}" -f [bool]$task)

if ($null -ne $stateAgeSeconds) {
    $s = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    Write-Host ("mode         : {0} (profile {1})" -f $s.mode, $s.profile)
    Write-Host ("last sample  : {0}" -f $s.timestamp)
    Write-Host ("memory       : {0}/{1} MiB available, commit {2}%, pages-in {3}/s" -f $s.availableMiB, $s.totalMiB, $s.commitPercent, [int]$s.pagesInputPerSec)
    Write-Host ("pressure     : {0}  [{1}]" -f $s.pressureLevel, ($s.signals -join ', '))
    Write-Host ("assessment   : corroborated={0} actionable={1} cooldown={2}" -f $s.corroborated, $s.actionable, $s.inCooldown)
    Write-Host ("reason       : {0}" -f $s.reason)
    Write-Host ("foreground   : {0}" -f $s.foreground)
    if ($s.workContextMembers) {
        Write-Host ("work context : {0}" -f ($s.workContextMembers -join ', '))
    }
    if ($s.classes) {
        Write-Host ("classes      : Protected={0} ActiveWork={1} Disposable={2} Suggest={3} Unknown={4}" -f `
            $s.classes.Protected, $s.classes.ActiveWork, $s.classes.Disposable, $s.classes.SuggestionOnly, $s.classes.Unknown)
    }
    if ($s.topWorkingSets) {
        Write-Host ("top WS       : {0}" -f ($s.topWorkingSets -join '  '))
    }
    if (-not $s.gateOpen -and $s.gateReasons) {
        Write-Host ("gate closed  : {0}" -f ($s.gateReasons -join '; '))
    }
}
else {
    if ($running) {
        Write-Host 'state file   : pending (guardian is alive; first sample not written yet)'
    }
    else {
        Write-Host 'state file   : none yet (guardian has not completed a pass)'
    }
}
Write-Host ''

#Requires -Version 5.1
<#
.SYNOPSIS
    One-shot diagnostic: take a sample, classify everything, assess pressure,
    list disposable candidates and startup suggestions. Read-only - it never
    changes the system regardless of mode.
#>
[CmdletBinding()]
param(
    [switch]$Save
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
foreach ($m in 'SRG.Config', 'SRG.Sampler', 'SRG.Pressure', 'SRG.Classifier', 'SRG.Context', 'SRG.Priority', 'SRG.Actions') {
    Import-Module (Join-Path $root "src\$m.psm1") -Force -DisableNameChecking
}
$config = Get-SRGConfig -Path (Join-Path $root 'config.json')

Write-Host ''
Write-Host '=== SmartRAM-Guardian diagnose ===' -ForegroundColor Cyan
Write-Host "mode: $($config.Mode)   (observe = default, nothing here changes the system)"
Write-Host ''

# Two samples ~1 s apart so paging rates and CPU deltas are real numbers,
# not the zero you get from a single cumulative-counter read.
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$first = Get-SRGSample -Config $config
$sw.Stop()
$firstMs = $sw.ElapsedMilliseconds
Start-Sleep -Milliseconds 1000
$sw.Restart()
$second = Get-SRGSample -Config $config -PreviousSample $first[0] -PreviousCpuMap $first[1]
$sw.Stop()
$sampleMs = $sw.ElapsedMilliseconds
$sample = $second[0]

$assessment = Get-PressureAssessment -Sample $sample -Config $config
$context = Get-WorkContext -Processes $sample.Processes -Config $config -ForegroundPid $sample.ForegroundPid
$classified = Get-ProcessClassification -Processes $sample.Processes -Config $config -ForegroundPid $sample.ForegroundPid
$gate = Test-SafetyGate -Config $config -Processes $sample.Processes -Assessment $assessment -RootPath $root
$candidates = Get-DisposableCandidates -ClassifiedProcesses $classified
$sensitive = Get-ActiveSensitiveOperations -Processes $sample.Processes

Write-Host ("sample cost  : {0} ms cold / {1} ms steady-state (guardian overhead per pass)" -f $firstMs, $sampleMs)
Write-Host ("memory       : {0}/{1} MiB available, commit {2}%, pages-in {3}/s" -f $sample.AvailableMiB, $sample.TotalMiB, $sample.CommitPercent, [int]$sample.PagesInputPerSec)
Write-Host ("pressure     : {0}  signals=[{1}]" -f $assessment.Level, ($assessment.Signals -join ', '))
Write-Host ("verdict      : {0}" -f $assessment.Reason)
Write-Host ("foreground   : {0} (pid {1})" -f $sample.ForegroundName, $sample.ForegroundPid)
Write-Host ("work context : {0} member(s) protected -> {1}" -f $context.MemberCount, ($context.MemberNames -join ', '))
Write-Host ("safety gate  : {0}{1}" -f $(if ($gate.Open) { 'open' } else { 'closed' }), $(if ($gate.Reasons) { ' - ' + ($gate.Reasons -join '; ') } else { '' }))
if ($sensitive.Count -gt 0) {
    Write-Host ("sensitive ops: {0}" -f ($sensitive -join '; '))
}

Write-Host ''
Write-Host 'Top working sets:' -ForegroundColor Cyan
$sample.TopByWorkingSet | ForEach-Object {
    $cls = ($classified | Where-Object Pid -eq $_.Pid).Class
    Write-Host ('  {0,8:N1} MiB  {1,-30} {2}' -f $_.WorkingSetMiB, $_.Name, $cls)
}

Write-Host ''
Write-Host 'Disposable candidates (NOT approved - nothing will happen to them):' -ForegroundColor Cyan
if ($candidates.Count -eq 0) {
    Write-Host '  none right now'
}
else {
    foreach ($c in $candidates) {
        Write-Host ("  {0,-25} {1,8:N1} MiB   {2}" -f $c.Name, $c.WorkingSetMiB, $c.Evidence)
    }
    Write-Host ''
    Write-Host "  To approve one: .\approve.ps1 -Name '<Name>'  (adds it to AllowlistClose in config.json)"
    Write-Host "  Closing still only happens in 'allowlisted-close' mode, gracefully, when pressure is real."
}

$lowerTargets = Get-PriorityLoweringTargets -ClassifiedProcesses $classified -Processes $sample.Processes
Write-Host ''
Write-Host ('Memory-priority lever (config: EnableEcoQoS = {0}):' -f $config.EnableEcoQoS) -ForegroundColor Cyan
if ($lowerTargets.Count -eq 0) {
    Write-Host '  no eligible idle background processes right now'
}
else {
    foreach ($t in $lowerTargets) {
        Write-Host ("  would lower  {0,-25} {1,8:N1} MiB  ({2})" -f $t.Name, $t.WorkingSetMiB, $t.Reason)
    }
    Write-Host '  This never runs in observe mode. It is a hint only: no working set is trimmed.'
}

if ($Save) {
    $outDir = Join-Path $root 'logs'
    if (-not (Test-Path $outDir)) { New-Item -ItemType Directory $outDir -Force | Out-Null }
    $out = Join-Path $outDir ('diagnose-{0}.json' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    [pscustomobject]@{
        timestamp  = (Get-Date).ToString('s')
        sampleMs   = $sampleMs
        firstMs    = $firstMs
        assessment = $assessment
        top        = $sample.TopByWorkingSet
        candidates = $candidates
        gate       = $gate
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $out -Encoding UTF8
    Write-Host ''
    Write-Host "saved: $out"
}
Write-Host ''

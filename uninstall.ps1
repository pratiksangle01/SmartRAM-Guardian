#Requires -Version 5.1
<#
.SYNOPSIS
    Full rollback: stop guardian, remove scheduled task, remove state files.
    Logs and config are kept unless -RemoveLogs / -RemoveConfig are given.
#>
[CmdletBinding()]
param(
    [switch]$RemoveLogs,
    [switch]$RemoveConfig
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$taskName = 'SmartRAM-Guardian'

# 1. Stop running guardian (graceful: it is our own process and cleans up state).
& (Join-Path $root 'stop.ps1') -ErrorAction SilentlyContinue

# 2. Remove scheduled task.
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "[ok] removed scheduled task '$taskName'"
}
else {
    Write-Host "[ok] no scheduled task '$taskName' present (nothing to remove)"
}

# 3. Remove runtime state (state.json, pid, pause marker).
foreach ($f in 'state.json', 'guardian.pid', 'guardian.pause') {
    $p = Join-Path $root $f
    if (Test-Path -LiteralPath $p) {
        Remove-Item -LiteralPath $p -Force
        Write-Host "[ok] removed $f"
    }
}

if ($RemoveLogs) {
    $logDir = Join-Path $root 'logs'
    if (Test-Path -LiteralPath $logDir) {
        Remove-Item -LiteralPath $logDir -Recurse -Force
        Write-Host '[ok] removed logs/'
    }
}
if ($RemoveConfig) {
    $cfg = Join-Path $root 'config.json'
    if (Test-Path -LiteralPath $cfg) {
        Remove-Item -LiteralPath $cfg -Force
        Write-Host '[ok] removed config.json'
    }
}

Write-Host '[done] uninstall complete. No other system changes were ever made.'

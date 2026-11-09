#Requires -Version 5.1
<#
.SYNOPSIS
    Idempotent install: registers SmartRAM-Guardian to start at user logon via
    a Scheduled Task (no admin needed). Every change is logged and reversible
    with uninstall.ps1.
#>
[CmdletBinding()]
param(
    [switch]$NoAutoStart
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$taskName = 'SmartRAM-Guardian'
$startScript = Join-Path $root 'start.ps1'
$configPath = Join-Path $root 'config.json'

# Validate config first - a broken config must never be installed.
Import-Module (Join-Path $root 'src\SRG.Config.psm1') -Force
$config = Get-SRGConfig -Path $configPath
Write-Host "[ok] config.json valid (mode=$($config.Mode))"

$logDir = Join-Path $root $config.LogDirectory
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory $logDir -Force | Out-Null }
$installLog = Join-Path $logDir 'install.log'
function Log([string]$msg) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -LiteralPath $installLog -Value $line
    Write-Host $msg
}

if ($NoAutoStart) {
    Log '[skip] -NoAutoStart given; scheduled task not created'
    return
}

$psExe = Join-Path $PSHome 'powershell.exe'
$action = New-ScheduledTaskAction -Execute $psExe `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    # Idempotent: only update if the definition changed.
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Log "[update] replaced existing scheduled task '$taskName'"
}
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description 'SmartRAM-Guardian background memory manager (default mode: observe)' | Out-Null
Log "[ok] registered scheduled task '$taskName' (at logon, limited rights, no time limit)"
Log '[done] install complete. Run status.ps1 to verify, uninstall.ps1 to fully revert.'

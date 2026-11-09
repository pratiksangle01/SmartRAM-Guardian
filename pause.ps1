#Requires -Version 5.1
<#
.SYNOPSIS
    Pause all guardian actions. Sampling/logging continues so state stays
    current; every action is refused while the pause marker exists.
#>
[CmdletBinding()]
param([string]$Reason = '')
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$marker = Join-Path $root 'guardian.pause'
$text = if ($Reason) { "paused $(Get-Date -Format s) - $Reason" } else { "paused $(Get-Date -Format s)" }
$text | Set-Content -LiteralPath $marker -Encoding UTF8 -Force

$logDir = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory $logDir -Force | Out-Null }
Add-Content -LiteralPath (Join-Path $logDir 'install.log') -Value ('{0} [pause] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text)
Write-Host "[ok] paused. Guardian keeps sampling but will take no action. Resume with .\resume.ps1"

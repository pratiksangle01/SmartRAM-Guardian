#Requires -Version 5.1
<#
.SYNOPSIS
    Resume guardian actions after pause.ps1.
#>
[CmdletBinding()]
param()
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$marker = Join-Path $root 'guardian.pause'
if (-not (Test-Path -LiteralPath $marker)) {
    Write-Host '[ok] not paused; nothing to do'
    return
}
Remove-Item -LiteralPath $marker -Force

$logDir = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory $logDir -Force | Out-Null }
Add-Content -LiteralPath (Join-Path $logDir 'install.log') -Value ('{0} [resume] resumed' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Host '[ok] resumed.'

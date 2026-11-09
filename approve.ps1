#Requires -Version 5.1
<#
.SYNOPSIS
    Learning loop approval: promote a process name into AllowlistClose.
    This only WRITES CONFIG. Closing still requires mode 'allowlisted-close'
    and real, corroborated pressure, and is always graceful.
.PARAMETER Name
    Process name as shown by diagnose.ps1 (without .exe).
.PARAMETER Revoke
    Remove the name from the allowlist again.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [switch]$Revoke
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$configPath = Join-Path $root 'config.json'
Import-Module (Join-Path $root 'src\SRG.Config.psm1') -Force
Import-Module (Join-Path $root 'src\SRG.Classifier.psm1') -Force -DisableNameChecking
$x = $null

$json = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$current = @($json.AllowlistClose)

if ($Revoke) {
    $new = @($current | Where-Object { $_ -ine $Name })
    if ($new.Count -eq $current.Count) {
        Write-Host "[ok] '$Name' was not in AllowlistClose; nothing changed"
        return
    }
    $json.AllowlistClose = $new
    Write-Host "[ok] removed '$Name' from AllowlistClose"
}
else {
    # Refuse to approve anything on the protected list - hard safety rule.
    $protected = Get-ProtectedNameSet -Config (Get-SRGConfig -Path $configPath)
    if ($protected.ContainsKey($Name.ToLowerInvariant())) {
        Write-Host "[refused] '$Name' is on the protected list and can never be approved for closing." -ForegroundColor Yellow
        return
    }
    if ($current -icontains $Name) {
        Write-Host "[ok] '$Name' is already approved; nothing changed"
        return
    }
    $json.AllowlistClose = @($current + $Name)
    Write-Host "[ok] approved '$Name' -> AllowlistClose (graceful close only, only under real pressure, only in allowlisted-close mode)"
}

# Validate the merged result before persisting; refuse to write a broken config.
$merged = Get-SRGDefaultConfig
foreach ($prop in $json.PSObject.Properties) {
    if ($merged.PSObject.Properties.Name -contains $prop.Name) { $merged.($prop.Name) = $prop.Value }
}
$errors = Test-SRGConfig -Config $merged
if ($errors.Count -gt 0) {
    Write-Host ('[refused] config would be invalid: ' + ($errors -join '; ')) -ForegroundColor Yellow
    return
}
$json | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $configPath -Encoding UTF8

$logDir = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory $logDir -Force | Out-Null }
Add-Content -LiteralPath (Join-Path $logDir 'install.log') -Value ('{0} [allowlist] {1} {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $(if ($Revoke) { 'revoked' } else { 'approved' }), $Name)

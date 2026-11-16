#Requires -Version 5.1
<#
.SYNOPSIS
    Run tests on Windows PowerShell 5.1 with Pester 3.4.x or 4.x.
    See docs/SETUP.md for optional development-tool installation.
#>
[CmdletBinding()]
param(
    [string]$Path = $PSScriptRoot,
    [switch]$NoAnalyzer
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSCommandPath
$repoRoot = Split-Path -Parent $repoRoot

$pester = Get-Module -ListAvailable Pester |
    Where-Object { $_.Version -ge [version]'3.4.0' -and $_.Version.Major -lt 5 } |
    Sort-Object Version -Descending | Select-Object -First 1
if (-not $pester) {
    throw 'Pester 3.4.x or 4.x is required. See docs/SETUP.md for installation commands (Pester 5 is not supported).'
}
Import-Module $pester.Path -Force
Write-Host ("Using Pester {0}" -f $pester.Version)

$result = Invoke-Pester -Path $Path -PassThru
if ($result.TotalCount -eq 0) { throw "No tests found at '$Path'." }
$issues = @()
Write-Host ''
Write-Host ("Total={0} Passed={1} Failed={2} Skipped={3}" -f `
    $result.TotalCount, $result.PassedCount, $result.FailedCount, $result.SkippedCount)
if ($result.FailedCount -gt 0) {
    $result.TestResult | Where-Object { -not $_.Passed } | ForEach-Object {
        Write-Host ("FAIL: {0} / {1}: {2}" -f $_.Describe, $_.Name, $_.FailureMessage) -ForegroundColor Red
    }
}

if (-not $NoAnalyzer) {
    $analyzer = Get-Module -ListAvailable PSScriptAnalyzer | Sort-Object Version -Descending | Select-Object -First 1
    if ($analyzer) {
        Import-Module $analyzer.Path -Force
        Write-Host ''
        Write-Host 'PSScriptAnalyzer:'
        $issues = @(Invoke-ScriptAnalyzer -Path $repoRoot -Recurse `
            -Severity Error, Warning -ExcludeRule PSAvoidUsingWriteHost, PSAvoidUsingPositionalParameters)
        if ($issues.Count -eq 0) { Write-Host '  no issues' }
        else { $issues | Format-Table RuleName, Severity, ScriptName, Line, Message -AutoSize | Out-String -Width 200 | Write-Host }
    }
    else {
        Write-Host 'PSScriptAnalyzer not installed; skipped (CI installs it).'
    }
}

if ($result.FailedCount -gt 0 -or $issues.Count -gt 0) { exit 1 }

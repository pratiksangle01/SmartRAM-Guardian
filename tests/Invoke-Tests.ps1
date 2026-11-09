#Requires -Version 5.1
<#
.SYNOPSIS
    Run the Pester test suite. Works on Windows PowerShell 5.1 with the
    built-in Pester 3.4 (no downloads required).
#>
[CmdletBinding()]
param(
    [string]$Path = (Join-Path $PSScriptRoot '.'),
    [switch]$NoAnalyzer
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$pester = Get-Module -ListAvailable Pester | Sort-Object Version -Descending | Select-Object -First 1
if (-not $pester) {
    throw 'Pester was not found. On Windows 10/11 it ships with Windows PowerShell 5.1 (Pester 3.4).'
}
Write-Host ("Using Pester {0}" -f $pester.Version)

$result = Invoke-Pester -Path $Path -PassThru
Write-Host ''
Write-Host ("Total={0} Passed={1} Failed={2} Skipped={3}" -f `
    $result.TotalCount, $result.PassedCount, $result.FailedCount, $result.SkippedCount)
if ($result.FailedCount -gt 0) {
    $result.TestResult | Where-Object { -not $_.Passed } | ForEach-Object {
        Write-Host ("FAIL: {0} / {1}: {2}" -f $_.Describe, $_.Name, $_.FailureMessage) -ForegroundColor Red
    }
}

if (-not $NoAnalyzer) {
    $analyzer = Get-Module -ListAvailable PSScriptAnalyzer | Select-Object -First 1
    if ($analyzer) {
        Write-Host ''
        Write-Host 'PSScriptAnalyzer:'
        $issues = Invoke-ScriptAnalyzer -Path (Join-Path (Split-Path -Parent $PSScriptRoot) '.') -Recurse `
            -Severity Error, Warning -ExcludeRule PSAvoidUsingWriteHost, PSAvoidUsingPositionalParameters
        if ($issues.Count -eq 0) { Write-Host '  no issues' }
        else { $issues | Format-Table RuleName, Severity, ScriptName, Line, Message -AutoSize | Out-String -Width 200 | Write-Host }
    }
    else {
        Write-Host 'PSScriptAnalyzer not installed; skipped (CI installs it).'
    }
}

if ($result.FailedCount -gt 0) { exit 1 }

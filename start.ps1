#Requires -Version 5.1
<#
.SYNOPSIS
    Start the guardian loop in a hidden background process. Idempotent:
    refuses to start a second instance.
#>
[CmdletBinding()]
param()
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$guardian = Join-Path $root 'src\Guardian.ps1'
$pidPath = Join-Path $root 'guardian.pid'

if (Test-Path -LiteralPath $pidPath) {
    $oldPid = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
    if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
        Write-Host "[ok] guardian already running (pid $oldPid)"
        return
    }
    Remove-Item -LiteralPath $pidPath -Force
}

$psExe = Join-Path $PSHome 'powershell.exe'
$proc = Start-Process -FilePath $psExe -WindowStyle Hidden -PassThru `
    -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$guardian`""

# The guardian writes its own pid file once modules are loaded. Waiting for it
# here means 'start.ps1 ; status.ps1' cannot show "running : False" for a
# process that is already alive.
$deadline = (Get-Date).AddSeconds(15)
while (-not (Test-Path -LiteralPath $pidPath) -and (Get-Date) -lt $deadline) {
    if ($proc.HasExited) { break }
    Start-Sleep -Milliseconds 200
}

if (Test-Path -LiteralPath $pidPath) {
    $startedPid = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
    Write-Host "[ok] guardian started (pid $startedPid, mode see config.json). Use status.ps1 to check."
}
elseif ($proc.HasExited) {
    Write-Host "[warn] guardian exited during startup (exit code $($proc.ExitCode)); check logs\guardian.log"
}
else {
    Write-Host "[warn] guardian is running (pid $($proc.Id)) but its pid file is not written yet; check status.ps1 in a moment"
}

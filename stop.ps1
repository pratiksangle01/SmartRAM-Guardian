#Requires -Version 5.1
<#
.SYNOPSIS
    Stop the guardian gracefully: set a stop marker, wait for the loop to exit
    on its own (it cleans up its pid/state), and only hard-stop as a last
    resort after a timeout.
#>
[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 45
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$pidPath = Join-Path $root 'guardian.pid'
$stopPath = Join-Path $root 'guardian.stop'

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host '[ok] guardian is not running (no pid file)'
    if (Test-Path -LiteralPath $stopPath) { Remove-Item -LiteralPath $stopPath -Force }
    return
}
$gpid = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
$proc = Get-Process -Id $gpid -ErrorAction SilentlyContinue
if (-not $proc) {
    Write-Host "[ok] guardian pid $gpid not alive; removing stale pid file"
    Remove-Item -LiteralPath $pidPath -Force
    return
}

# Ask politely first: the loop checks for this marker every second.
'stop requested' | Set-Content -LiteralPath $stopPath -Encoding ASCII -Force
$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
    if (-not (Get-Process -Id $gpid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
$sw.Stop()

if (Get-Process -Id $gpid -ErrorAction SilentlyContinue) {
    # Last resort: terminate our own leftover process. This only ever targets
    # the guardian itself, never a user application.
    Stop-Process -Id $gpid -ErrorAction SilentlyContinue
    Write-Host "[warn] guardian did not exit within $TimeoutSeconds s; terminated pid $gpid"
}
else {
    Write-Host ("[ok] guardian stopped gracefully after {0:N1} s (was pid {1})" -f $sw.Elapsed.TotalSeconds, $gpid)
}
if (Test-Path -LiteralPath $stopPath) { Remove-Item -LiteralPath $stopPath -Force -ErrorAction SilentlyContinue }
if (Test-Path -LiteralPath $pidPath) { Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue }

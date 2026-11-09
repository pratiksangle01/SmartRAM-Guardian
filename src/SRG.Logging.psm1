# SRG.Logging - rotating, low-write-volume logger for SmartRAM-Guardian.
# Design notes:
#  - One log file per day would write more metadata than value; we use a single
#    rolling file with size-based rotation.
#  - Low-disk guard: when free space drops below the configured floor we stop
#    writing non-error lines and emit one warning per session.

Set-StrictMode -Version 2.0

$script:LogPath = $null
$script:LogMaxBytes = 1MB
$script:LogMaxFiles = 5
$script:LowDiskFreeMiB = 500
$script:LowDiskWarned = $false
$script:Initialized = $false

function Initialize-SRGLog {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [long]$MaxBytes = 1MB,
        [int]$MaxFiles = 5,
        [int]$LowDiskFreeMiB = 500
    )
    if (-not (Test-Path -LiteralPath $Directory)) {
        New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    }
    $script:LogPath = Join-Path $Directory 'guardian.log'
    $script:LogMaxBytes = [Math]::Max(16KB, $MaxBytes)
    $script:LogMaxFiles = [Math]::Max(1, $MaxFiles)
    $script:LowDiskFreeMiB = $LowDiskFreeMiB
    $script:LowDiskWarned = $false
    $script:Initialized = $true
}

function Test-SRGLowDisk {
    if (-not $script:LogPath) { return $false }
    try {
        $root = [System.IO.Path]::GetPathRoot($script:LogPath)
        $drive = Get-PSDrive -Name ($root.TrimEnd('\').TrimEnd(':')) -ErrorAction Stop
        $freeMiB = [math]::Floor($drive.Free / 1MB)
        return ($freeMiB -lt $script:LowDiskFreeMiB)
    }
    catch {
        return $false
    }
}

function Invoke-SRGLogRotation {
    if (-not (Test-Path -LiteralPath $script:LogPath)) { return }
    $size = (Get-Item -LiteralPath $script:LogPath).Length
    if ($size -lt $script:LogMaxBytes) { return }
    # Roll: guardian.log.4 -> delete, .3 -> .4, ... current -> .1
    $oldest = "$($script:LogPath).$($script:LogMaxFiles)"
    if (Test-Path -LiteralPath $oldest) { Remove-Item -LiteralPath $oldest -Force -ErrorAction SilentlyContinue }
    for ($i = $script:LogMaxFiles - 1; $i -ge 1; $i--) {
        $src = "$($script:LogPath).$i"
        $dst = "$($script:LogPath).$($i + 1)"
        if (Test-Path -LiteralPath $src) {
            Move-Item -LiteralPath $src -Destination $dst -Force -ErrorAction SilentlyContinue
        }
    }
    Move-Item -LiteralPath $script:LogPath -Destination "$($script:LogPath).1" -Force -ErrorAction SilentlyContinue
}

function Write-SRGLog {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][ValidateSet('DEBUG', 'INFO', 'WARN', 'ERROR', 'ACTION')][string]$Level,
        [Parameter(Mandatory = $true)][string]$Message,
        [string]$Category = 'general'
    )
    if (-not $script:Initialized) { return }

    if (Test-SRGLowDisk) {
        if ($Level -ne 'ERROR') {
            if (-not $script:LowDiskWarned) {
                $script:LowDiskWarned = $true
                $line = '{0} [WARN ] [disk] Low disk space - logging reduced to errors only' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
                [System.IO.File]::AppendAllText($script:LogPath, $line + [Environment]::NewLine)
            }
            return
        }
    }

    Invoke-SRGLogRotation
    $tag = $Level.PadRight(6)
    $line = '{0} [{1}] [{2}] {3}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $tag, $Category, $Message
    try {
        [System.IO.File]::AppendAllText($script:LogPath, $line + [Environment]::NewLine)
    }
    catch {
        # Never let logging crash the guardian loop.
    }
}

function Get-SRGLogPath { return $script:LogPath }

Export-ModuleMember -Function Initialize-SRGLog, Write-SRGLog, Get-SRGLogPath, Test-SRGLowDisk

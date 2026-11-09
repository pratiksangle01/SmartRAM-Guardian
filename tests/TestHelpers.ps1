# Shared helpers for the Pester 3.4 suites. Dot-sourced by each test file.
# Builds complete fake process objects so no real process is ever touched.

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
foreach ($m in 'SRG.Config', 'SRG.Logging', 'SRG.Sampler', 'SRG.Pressure', 'SRG.Classifier', 'SRG.Context', 'SRG.Actions') {
    Import-Module (Join-Path $script:RepoRoot "src\$m.psm1") -Force -DisableNameChecking
}

function New-FakeProcess {
    param(
        [int]$ProcessId = 1000,
        [string]$Name = 'someapp',
        [int]$ParentPid = 1,
        [double]$WorkingSetMiB = 200,
        [double]$CpuSeconds = 0,
        [string]$CommandLine = $null,
        [bool]$HasMainWindow = $true,
        [string]$MainWindowTitle = 'Some Window',
        [double]$CpuPercentSinceLast = 0,
        [datetime]$StartTime = ([datetime]'2026-01-01T00:00:00')
    )
    [pscustomobject]@{
        Pid                 = $ProcessId
        Name                = $Name
        ParentPid           = $ParentPid
        WorkingSetMiB       = $WorkingSetMiB
        CpuSeconds          = $CpuSeconds
        CommandLine         = $CommandLine
        StartTime           = $StartTime
        HasMainWindow       = $HasMainWindow
        MainWindowTitle     = $MainWindowTitle
        CpuPercentSinceLast = $CpuPercentSinceLast
    }
}

function New-FakeSample {
    param(
        [double]$AvailableMiB = 2000,
        [double]$TotalMiB = 8000,
        [double]$CommitPercent = 50,
        [double]$PagesInputPerSec = 0,
        [int]$ForegroundPid = 0,
        $Processes = @()
    )
    [pscustomobject]@{
        Timestamp        = (Get-Date)
        AvailableMiB     = $AvailableMiB
        TotalMiB         = $TotalMiB
        CommitPercent    = $CommitPercent
        PagesInputPerSec = $PagesInputPerSec
        RawPagesInput    = 0
        ForegroundPid    = $ForegroundPid
        ForegroundName   = ''
        Processes        = @($Processes)
        TopByWorkingSet  = @($Processes)
    }
}

function Get-TestConfig {
    param(
        [string]$Mode = 'observe',
        [string[]]$AllowlistClose = @(),
        [string[]]$ExtraProtected = @()
    )
    $cfg = Get-SRGDefaultConfig
    $cfg.Mode = $Mode
    $cfg.AllowlistClose = $AllowlistClose
    $cfg.ExtraProtectedProcesses = $ExtraProtected
    return $cfg
}

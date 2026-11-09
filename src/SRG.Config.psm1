# SRG.Config - load, merge-with-defaults and validate config.json.
# Invalid config must never crash the loop silently: Get-SRGConfig throws with
# a full list of problems so install/diagnose can surface them.

Set-StrictMode -Version 2.0

function Get-SRGDefaultConfig {
    return [pscustomobject]@{
        Mode                       = 'observe'
        SampleIntervalSeconds      = 30
        AdaptiveSampling           = $true
        BusyIntervalSeconds        = 15
        WarnAvailableMiB           = 1536
        CriticalAvailableMiB       = 819
        WarnCommitPercent          = 80
        CriticalCommitPercent      = 92
        PagingWarnPagesInputPerSec = 500
        HysteresisMiB              = 256
        HysteresisCommitPercent    = 5
        CooldownSeconds            = 300
        LogDirectory               = 'logs'
        LogMaxBytes                = 1048576
        LogMaxFiles                = 5
        LowDiskFreeMiB             = 500
        StateFile                  = 'state.json'
        PidFile                    = 'guardian.pid'
        PauseFile                  = 'guardian.pause'
        TopProcessCount            = 10
        IncludeCommandLine         = $true
        IncludeIoCounters          = $true
        EnableAggressiveTrim       = $false
        EnableEcoQoS               = $false
        GracefulCloseTimeoutSeconds = 15
        AllowlistClose             = @()
        SuggestionOnlyProcesses    = @('OneDrive', 'SearchIndexer', 'Widgets', 'WidgetService', 'PhoneExperienceHost', 'YourPhone', 'Teams', 'SkypeApp')
        ExtraProtectedProcesses    = @()
        Profiles                   = [pscustomobject]@{ Active = 'default'; Available = @('default', 'dev', 'gaming', 'installation') }
    }
}

function Test-SRGConfig {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Config)

    $errors = New-Object System.Collections.Generic.List[string]

    if ($Config.Mode -notin @('observe', 'safe-auto', 'allowlisted-close')) {
        $errors.Add("Mode must be observe|safe-auto|allowlisted-close, got '$($Config.Mode)'")
    }
    if ($Config.SampleIntervalSeconds -lt 10 -or $Config.SampleIntervalSeconds -gt 600) {
        $errors.Add("SampleIntervalSeconds must be 10..600, got $($Config.SampleIntervalSeconds)")
    }
    if ($Config.BusyIntervalSeconds -lt 5 -or $Config.BusyIntervalSeconds -gt $Config.SampleIntervalSeconds) {
        $errors.Add("BusyIntervalSeconds must be 5..SampleIntervalSeconds, got $($Config.BusyIntervalSeconds)")
    }
    if ($Config.CriticalAvailableMiB -ge $Config.WarnAvailableMiB) {
        $errors.Add("CriticalAvailableMiB ($($Config.CriticalAvailableMiB)) must be below WarnAvailableMiB ($($Config.WarnAvailableMiB))")
    }
    if ($Config.WarnAvailableMiB -lt 128) { $errors.Add('WarnAvailableMiB must be >= 128') }
    if ($Config.WarnCommitPercent -ge $Config.CriticalCommitPercent) {
        $errors.Add('WarnCommitPercent must be below CriticalCommitPercent')
    }
    if ($Config.CriticalCommitPercent -gt 100) { $errors.Add('CriticalCommitPercent must be <= 100') }
    if ($Config.HysteresisMiB -lt 0) { $errors.Add('HysteresisMiB must be >= 0') }
    if ($Config.CooldownSeconds -lt 0 -or $Config.CooldownSeconds -gt 86400) {
        $errors.Add("CooldownSeconds must be 0..86400, got $($Config.CooldownSeconds)")
    }
    foreach ($list in 'AllowlistClose', 'SuggestionOnlyProcesses', 'ExtraProtectedProcesses') {
        if ($null -eq $Config.$list) { $errors.Add("$list must be an array, got null") }
        elseif ($Config.$list -isnot [System.Collections.IEnumerable] -or $Config.$list -is [string]) {
            $errors.Add("$list must be a JSON array")
        }
    }
    $overlap = @($Config.AllowlistClose | Where-Object { $Config.ExtraProtectedProcesses -contains $_ })
    if ($overlap.Count -gt 0) {
        $errors.Add("Processes cannot be both protected and close-allowlisted: $($overlap -join ', ')")
    }
    if ($Config.EnableAggressiveTrim -eq $true) {
        $errors.Add('EnableAggressiveTrim is not supported and must stay false (see docs/HOW_IT_WORKS.md)')
    }
    if ($Config.GracefulCloseTimeoutSeconds -lt 5 -or $Config.GracefulCloseTimeoutSeconds -gt 120) {
        $errors.Add('GracefulCloseTimeoutSeconds must be 5..120')
    }
    return , $errors.ToArray()
}

function Get-SRGConfig {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Config file not found: $Path"
    }
    $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
    try {
        $user = $raw | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "config.json is not valid JSON: $($_.Exception.Message)"
    }

    $cfg = Get-SRGDefaultConfig
    foreach ($prop in $user.PSObject.Properties) {
        if ($cfg.PSObject.Properties.Name -contains $prop.Name) {
            $cfg.($prop.Name) = $prop.Value
        }
        # Unknown keys are ignored on purpose (forward compatibility).
    }

    $errors = Test-SRGConfig -Config $cfg
    if ($errors.Count -gt 0) {
        throw ("Config validation failed:`n - " + ($errors -join "`n - "))
    }
    return $cfg
}

Export-ModuleMember -Function Get-SRGConfig, Test-SRGConfig, Get-SRGDefaultConfig

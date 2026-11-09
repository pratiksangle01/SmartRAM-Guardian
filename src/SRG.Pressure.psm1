# SRG.Pressure - pressure assessment with multi-signal corroboration,
# hysteresis and cooldown.
#
# Core idea: "low available RAM" alone is NOT memory pressure. Windows fills
# free RAM with standby cache by design. Real pressure = low available PLUS a
# corroborating signal (commit charge near the limit, or active hard paging).
# This is what keeps us from acting on the cosmetic "free RAM" number.

Set-StrictMode -Version 2.0

function Get-PressureSignals {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Sample,
        [Parameter(Mandatory = $true)]$Config
    )
    $signals = New-Object System.Collections.Generic.List[string]
    if ($Sample.AvailableMiB -lt $Config.CriticalAvailableMiB) { $signals.Add('AvailableCritical') }
    elseif ($Sample.AvailableMiB -lt $Config.WarnAvailableMiB) { $signals.Add('AvailableLow') }
    if ($Sample.CommitPercent -ge $Config.CriticalCommitPercent) { $signals.Add('CommitCritical') }
    elseif ($Sample.CommitPercent -ge $Config.WarnCommitPercent) { $signals.Add('CommitHigh') }
    if ($Sample.PagesInputPerSec -ge $Config.PagingWarnPagesInputPerSec) { $signals.Add('HardPaging') }
    return , $signals.ToArray()
}

function Get-PressureAssessment {
    <#
    .SYNOPSIS
        Returns an assessment object: Level (Normal/Warn/Critical), raw Signals,
        Actionable (corroborated pressure worth acting on), cooldown state.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Sample,
        [Parameter(Mandatory = $true)]$Config,
        $PreviousLevel = 'Normal',
        $LastActionTime = $null
    )

    $signals = Get-PressureSignals -Sample $Sample -Config $Config

    # --- Level from signals, then apply hysteresis for de-escalation ---------
    $rawLevel = 'Normal'
    if ($signals -contains 'AvailableCritical' -or $signals -contains 'CommitCritical') { $rawLevel = 'Critical' }
    elseif ($signals.Count -gt 0) { $rawLevel = 'Warn' }

    $rank = @{ Normal = 0; Warn = 1; Critical = 2 }
    $level = $rawLevel
    if ($rank[$rawLevel] -lt $rank[$PreviousLevel]) {
        # De-escalating: require the reading to be clearly out of the band we
        # are leaving, not just a hair above the threshold, to avoid flapping.
        $clearOfCritical = ($Sample.AvailableMiB -ge ($Config.CriticalAvailableMiB + $Config.HysteresisMiB)) -or
                           ($Sample.CommitPercent -le ($Config.CriticalCommitPercent - $Config.HysteresisCommitPercent))
        $clearOfWarn = ($Sample.AvailableMiB -ge ($Config.WarnAvailableMiB + $Config.HysteresisMiB)) -and
                       ($Sample.CommitPercent -le ($Config.WarnCommitPercent - $Config.HysteresisCommitPercent))

        if ($PreviousLevel -eq 'Critical') {
            if ($rawLevel -eq 'Normal' -and $clearOfWarn) { $level = 'Normal' }
            elseif ($clearOfCritical) { $level = 'Warn' }
            else { $level = 'Critical' }
        }
        elseif ($PreviousLevel -eq 'Warn') {
            $level = if ($clearOfWarn) { 'Normal' } else { 'Warn' }
        }
    }

    # --- Corroboration: at least two independent signals, or commit near limit
    # High commit is itself damning (pagefile is the backstop, not cache), so a
    # critical commit alone is actionable. "Cache ate the free RAM" is not.
    $corroborated = ($signals.Count -ge 2) -or ($signals -contains 'CommitCritical')
    $actionable = $corroborated -and ($rank[$level] -ge $rank['Warn'])

    # --- Cooldown ------------------------------------------------------------
    $inCooldown = $false
    $cooldownRemaining = 0
    if ($LastActionTime) {
        $elapsed = ((Get-Date) - [datetime]$LastActionTime).TotalSeconds
        if ($elapsed -lt $Config.CooldownSeconds) {
            $inCooldown = $true
            $cooldownRemaining = [int]($Config.CooldownSeconds - $elapsed)
        }
    }

    $reason = if ($signals.Count -eq 0) {
        'no pressure signals'
    }
    elseif (-not $corroborated) {
        "single signal only ($($signals -join ', ')) - likely cache, not real pressure"
    }
    else {
        "corroborated pressure: $($signals -join ', ')"
    }

    return [pscustomobject]@{
        Level              = $level
        RawLevel           = $rawLevel
        Signals            = @($signals)
        Corroborated       = $corroborated
        Actionable         = ($actionable -and -not $inCooldown)
        InCooldown         = $inCooldown
        CooldownRemaining  = $cooldownRemaining
        Reason             = $reason
        AvailableMiB       = $Sample.AvailableMiB
        CommitPercent      = $Sample.CommitPercent
        PagesInputPerSec   = $Sample.PagesInputPerSec
    }
}

function New-PressureState {
    return [pscustomobject]@{
        Level          = 'Normal'
        LastActionTime = $null
        Samples        = 0
    }
}

Export-ModuleMember -Function Get-PressureSignals, Get-PressureAssessment, New-PressureState

# SRG.Context - work-context detection.
# Stage 1 infers the active "heavy project" conservatively from the foreground
# process and its process tree (ancestors + descendants). Handle-based recent
# file activity is deliberately left out: enumerating open handles is expensive
# and needs elevation. The tree heuristic already covers IDE -> language
# server / build helper / dev server chains.

Set-StrictMode -Version 2.0

function Get-WorkContext {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Processes,
        [Parameter(Mandatory = $true)]$Config,
        [int]$ForegroundPid = 0
    )
    $byPid = @{}
    foreach ($p in $Processes) { $byPid[[int]$p.Pid] = $p }

    $fg = if ($byPid.ContainsKey($ForegroundPid)) { $byPid[$ForegroundPid] } else { $null }
    $fgTitle = ''
    if ($fg) { $fgTitle = "$(Get-ProcessWindowTitle -ProcessId $ForegroundPid)" }
    $tree = @{}
    if ($fg) {
        $tree = Get-ForegroundTree -Processes $Processes -ForegroundPid $ForegroundPid
    }

    $members = @()
    $protectedMiB = 0.0
    if ($tree.Count -gt 0) {
        foreach ($p in $Processes) {
            if ($tree.ContainsKey([int]$p.Pid)) {
                $members += $p
                $protectedMiB += $p.WorkingSetMiB
            }
        }
    }

    return [pscustomobject]@{
        ForegroundPid      = $ForegroundPid
        ForegroundName     = if ($fg) { $fg.Name } else { '' }
        ForegroundTitle    = $fgTitle
        ProtectedPidSet    = $tree
        MemberCount        = $members.Count
        MemberNames        = @($members | ForEach-Object { $_.Name } | Sort-Object -Unique)
        ProtectedWorkingSetMiB = [math]::Round($protectedMiB, 1)
    }
}

Export-ModuleMember -Function Get-WorkContext

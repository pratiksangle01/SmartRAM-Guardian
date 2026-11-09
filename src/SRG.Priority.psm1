# SRG.Priority - lever (a): lower memory priority / EcoQoS for idle background
# processes. This does NOT trim working sets and does NOT touch the process's
# execution state beyond a power hint. It only marks its pages as first-choice
# for the OS to reclaim when memory is genuinely short.
#
# Default is OFF (EnableEcoQoS=false). Even when enabled it is only applied in
# safe-auto / allowlisted-close modes, through the safety gate, and never to
# Protected / ActiveWork / SuggestionOnly / Disposable classes.
#
# Reversal: priority is a per-process hint that disappears when the process
# exits. To restore a single process now:
#   Set-SRGProcessMemoryPriority -ProcessId <pid> -Priority 5

Set-StrictMode -Version 2.0

if (-not ('SRG.Native.Priority' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace SRG.Native {
    [StructLayout(LayoutKind.Sequential)]
    public struct MEMORY_PRIORITY_INFORMATION { public uint MemoryPriority; }

    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_POWER_THROTTLING_STATE {
        public uint Version;
        public uint ControlMask;
        public uint StateMask;
    }

    public static class Priority {
        const uint PROCESS_SET_INFORMATION = 0x0200;
        const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
        const int ProcessMemoryPriority = 0;
        const int ProcessPowerThrottling = 4;
        const uint THROTTLING_CURRENT_VERSION = 1;
        const uint THROTTLING_EXECUTION_SPEED = 0x1;

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool CloseHandle(IntPtr h);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool SetProcessInformation(IntPtr h, int cls, ref MEMORY_PRIORITY_INFORMATION info, int size);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool SetProcessInformation(IntPtr h, int cls, ref PROCESS_POWER_THROTTLING_STATE info, int size);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GetProcessInformation(IntPtr h, int cls, out MEMORY_PRIORITY_INFORMATION info, int size);

        public static bool SetMemoryPriority(int pid, uint priority, out string error) {
            error = null;
            IntPtr h = OpenProcess(PROCESS_SET_INFORMATION, false, pid);
            if (h == IntPtr.Zero) { error = "OpenProcess failed: " + Marshal.GetLastWin32Error(); return false; }
            try {
                MEMORY_PRIORITY_INFORMATION info;
                info.MemoryPriority = priority;
                if (!SetProcessInformation(h, ProcessMemoryPriority, ref info, Marshal.SizeOf(typeof(MEMORY_PRIORITY_INFORMATION)))) {
                    error = "SetProcessInformation(memory) failed: " + Marshal.GetLastWin32Error();
                    return false;
                }
                return true;
            } finally { CloseHandle(h); }
        }

        public static uint GetMemoryPriority(int pid, out string error) {
            error = null;
            IntPtr h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid);
            if (h == IntPtr.Zero) { error = "OpenProcess failed: " + Marshal.GetLastWin32Error(); return 0; }
            try {
                MEMORY_PRIORITY_INFORMATION info;
                if (!GetProcessInformation(h, ProcessMemoryPriority, out info, Marshal.SizeOf(typeof(MEMORY_PRIORITY_INFORMATION)))) {
                    error = "GetProcessInformation(memory) failed: " + Marshal.GetLastWin32Error();
                    return 0;
                }
                return info.MemoryPriority;
            } finally { CloseHandle(h); }
        }

        public static bool SetEcoQos(int pid, bool on, out string error) {
            error = null;
            IntPtr h = OpenProcess(PROCESS_SET_INFORMATION, false, pid);
            if (h == IntPtr.Zero) { error = "OpenProcess failed: " + Marshal.GetLastWin32Error(); return false; }
            try {
                PROCESS_POWER_THROTTLING_STATE state;
                state.Version = THROTTLING_CURRENT_VERSION;
                state.ControlMask = THROTTLING_EXECUTION_SPEED;
                state.StateMask = on ? THROTTLING_EXECUTION_SPEED : 0;
                if (!SetProcessInformation(h, ProcessPowerThrottling, ref state, Marshal.SizeOf(typeof(PROCESS_POWER_THROTTLING_STATE)))) {
                    error = "SetProcessInformation(power) failed: " + Marshal.GetLastWin32Error();
                    return false;
                }
                return true;
            } finally { CloseHandle(h); }
        }
    }
}
'@
}

function Set-SRGProcessMemoryPriority {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [ValidateSet(1, 2, 3, 4, 5)][int]$Priority = 2
    )
    $err = $null
    $ok = [SRG.Native.Priority]::SetMemoryPriority($ProcessId, [uint32]$Priority, [ref]$err)
    return [pscustomobject]@{ Success = $ok; Error = $err; Priority = $Priority }
}

function Get-SRGProcessMemoryPriority {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    $err = $null
    $v = [SRG.Native.Priority]::GetMemoryPriority($ProcessId, [ref]$err)
    return [pscustomobject]@{ Priority = $v; Error = $err }
}

function Set-SRGProcessEcoQoSMode {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [bool]$Enabled = $true
    )
    $err = $null
    $ok = [SRG.Native.Priority]::SetEcoQos($ProcessId, $Enabled, [ref]$err)
    return [pscustomobject]@{ Success = $ok; Error = $err }
}

function Test-HasWindowedAncestor {
    <#
    .SYNOPSIS
        True when any ancestor has a visible main window. A windowless child of
        a windowed app is part of that app (an Electron renderer, an IDE's
        language server), not a background daemon, and must not be touched by
        name-independent levers.
    #>
    param(
        [Parameter(Mandatory = $true)]$Process,
        [Parameter(Mandatory = $true)][hashtable]$ByPid
    )
    $cur = [int]$Process.ParentPid
    $guard = 0
    while ($cur -gt 0 -and $ByPid.ContainsKey($cur) -and $guard -lt 16) {
        $node = $ByPid[$cur]
        if ($node.HasMainWindow) { return $true }
        $cur = [int]$node.ParentPid
        $guard++
    }
    return $false
}

function Get-PriorityLoweringTargets {
    <#
    .SYNOPSIS
        Pure selection: which processes may have their memory priority lowered.
        Only Unknown-class, windowless, low-CPU, non-trivial background memory
        users qualify. Everything else is excluded by construction.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$ClassifiedProcesses,
        [double]$MinWorkingSetMiB = 64,
        [double]$MaxCpuPercent = 2.0,
        $Processes = $null
    )
    $byPid = @{}
    if ($Processes) { foreach ($p in $Processes) { $byPid[[int]$p.Pid] = $p } }

    $targets = foreach ($p in $ClassifiedProcesses) {
        if ($p.Class -ne 'Unknown') { continue }
        if ($p.HasMainWindow) { continue }
        if ($p.WorkingSetMiB -lt $MinWorkingSetMiB) { continue }
        if ($byPid.Count -gt 0 -and (Test-HasWindowedAncestor -Process $p -ByPid $byPid)) { continue }
        $cpu = 0.0
        if ($p.PSObject.Properties['CpuPercentSinceLast']) { $cpu = [double]$p.CpuPercentSinceLast }
        if ($cpu -gt $MaxCpuPercent) { continue }
        [pscustomobject]@{
            Name          = $p.Name
            Pid           = $p.Pid
            WorkingSetMiB = $p.WorkingSetMiB
            CpuPercent    = $cpu
            Reason        = "unknown background process, windowless, no windowed parent, idle ($cpu% CPU), $($p.WorkingSetMiB) MiB resident"
        }
    }
    return , @($targets | Sort-Object WorkingSetMiB -Descending)
}

function Invoke-PriorityLowering {
    <#
    .SYNOPSIS
        Apply memory priority (and optionally EcoQoS) to the given targets.
        Logs every change; never throws for a single process failure.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Targets,
        [hashtable]$SeenPids = $null,
        [switch]$ApplyEcoQoS,
        [switch]$WhatIfOnly
    )
    $applied = New-Object System.Collections.Generic.List[object]
    foreach ($t in $Targets) {
        if ($SeenPids -and $SeenPids.ContainsKey([int]$t.Pid)) { continue }
        if ($WhatIfOnly) {
            $applied.Add([pscustomobject]@{ Pid = $t.Pid; Name = $t.Name; Outcome = 'WouldLower'; Detail = $t.Reason })
            continue
        }
        $res = Set-SRGProcessMemoryPriority -ProcessId $t.Pid -Priority 2
        $detail = if ($res.Success) { 'memory priority 5 -> 2' } else { "failed: $($res.Error)" }
        if ($res.Success -and $ApplyEcoQoS) {
            $eco = Set-SRGProcessEcoQoSMode -ProcessId $t.Pid -Enabled $true
            if (-not $eco.Success) { $detail += " ; ecoqos failed: $($eco.Error)" }
            else { $detail += ' ; ecoqos on' }
        }
        $applied.Add([pscustomobject]@{
            Pid     = $t.Pid
            Name    = $t.Name
            Outcome = if ($res.Success) { 'Lowered' } else { 'Failed' }
            Detail  = $detail
        })
        if ($SeenPids) { $SeenPids[[int]$t.Pid] = $true }
    }
    return , $applied.ToArray()
}

Export-ModuleMember -Function Get-PriorityLoweringTargets, Invoke-PriorityLowering,
    Set-SRGProcessMemoryPriority, Get-SRGProcessMemoryPriority, Set-SRGProcessEcoQoSMode,
    Test-HasWindowedAncestor

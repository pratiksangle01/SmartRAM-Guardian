# SRG.Sampler - low-overhead system snapshot.
#
# Cost strategy (measured, see README "Performance"):
#  - GlobalMemoryStatusEx P/Invoke for total/available physical memory
#    (single syscall, locale-independent, includes standby in "available")
#  - Win32_PerfRawData_PerfOS_Memory for commit + raw page-input counter, so we
#    compute rates ourselves. Win32_PerfFormattedData costs ~290 ms because it
#    formats every counter in the class; the raw query is ~30 ms.
#  - Win32_Process for parent pids / command lines
#  - Get-Process for working set, CPU, window handle
# No localized perf-counter names are used anywhere.

Set-StrictMode -Version 2.0

if (-not ('SRG.Native.Memory' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace SRG.Native {
    [StructLayout(LayoutKind.Sequential)]
    public struct MEMORYSTATUSEX {
        public uint dwLength;
        public uint dwMemoryLoad;
        public ulong ullTotalPhys;
        public ulong ullAvailPhys;
        public ulong ullTotalPageFile;
        public ulong ullAvailPageFile;
        public ulong ullTotalVirtual;
        public ulong ullAvailVirtual;
        public ulong ullAvailExtendedVirtual;
    }
    public static class Memory {
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool GlobalMemoryStatusEx(ref MEMORYSTATUSEX lpBuffer);
        public static MEMORYSTATUSEX Get() {
            MEMORYSTATUSEX st = new MEMORYSTATUSEX();
            st.dwLength = (uint)Marshal.SizeOf(typeof(MEMORYSTATUSEX));
            if (!GlobalMemoryStatusEx(ref st)) {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }
            return st;
        }
    }
    public static class Foreground {
        [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    }
}
'@
}

function Get-MemoryStatus {
    <#
    .SYNOPSIS
        Fast (sub-millisecond) total/available physical memory in MiB.
    #>
    $st = [SRG.Native.Memory]::Get()
    return [pscustomobject]@{
        TotalMiB     = [math]::Floor($st.ullTotalPhys / 1MB)
        AvailableMiB = [math]::Floor($st.ullAvailPhys / 1MB)
        MemoryLoad   = $st.dwMemoryLoad
    }
}

function Get-ForegroundProcessInfo {
    try {
        $hwnd = [SRG.Native.Foreground]::GetForegroundWindow()
        if ($hwnd -eq [IntPtr]::Zero) { return $null }
        $fgPid = [uint32]0
        [void][SRG.Native.Foreground]::GetWindowThreadProcessId($hwnd, [ref]$fgPid)
        if ($fgPid -le 0) { return $null }
        $p = Get-Process -Id $fgPid -ErrorAction SilentlyContinue
        return [pscustomobject]@{
            Pid  = [int]$fgPid
            Name = if ($p) { $p.ProcessName } else { '' }
        }
    }
    catch {
        return $null
    }
}

function Get-ProcessSnapshot {
    [CmdletBinding()]
    param(
        [bool]$IncludeCommandLine = $true,
        [bool]$IncludeIoCounters = $true
    )

    $select = @('ProcessId', 'ParentProcessId', 'Name')
    if ($IncludeCommandLine) { $select += 'CommandLine' }
    if ($IncludeIoCounters) { $select += 'ReadTransferCount', 'WriteTransferCount' }
    $cim = @(Get-CimInstance Win32_Process -Property $select -ErrorAction SilentlyContinue)

    $byPid = @{}
    foreach ($c in $cim) {
        $byPid[[int]$c.ProcessId] = [pscustomobject]@{
            ParentPid   = [int]$c.ParentProcessId
            CommandLine = if ($IncludeCommandLine) { $c.CommandLine } else { $null }
            ReadBytes    = if ($IncludeIoCounters) { [double]$c.ReadTransferCount } else { 0.0 }
            WriteBytes   = if ($IncludeIoCounters) { [double]$c.WriteTransferCount } else { 0.0 }
        }
    }

    $procs = @(Get-Process -ErrorAction SilentlyContinue)
    $result = foreach ($p in $procs) {
        $meta = $byPid[[int]$p.Id]
        # MainWindowTitle is deliberately NOT read here: querying it for every
        # process costs 150-300 ms per pass. Titles are fetched lazily for the
        # foreground process and for close candidates only. StartTime is kept
        # because parent-pid links are validated against it (pid reuse).
        [pscustomobject]@{
            Pid             = [int]$p.Id
            Name            = $p.ProcessName
            WorkingSetMiB   = [math]::Round($p.WorkingSet64 / 1MB, 1)
            CpuSeconds      = if ($null -ne $p.CPU) { [double]$p.CPU } else { 0.0 }
            ParentPid       = if ($meta) { $meta.ParentPid } else { 0 }
            CommandLine     = if ($meta) { $meta.CommandLine } else { $null }
            ReadBytes       = if ($meta) { $meta.ReadBytes } else { 0.0 }
            WriteBytes      = if ($meta) { $meta.WriteBytes } else { 0.0 }
            StartTime       = if ($null -ne $p.StartTime) { $p.StartTime } else { [datetime]::MinValue }
            HasMainWindow   = ($p.MainWindowHandle -ne 0)
            MainWindowTitle = $null
        }
    }
    return , $result
}

function Get-ProcessWindowTitle {
    <# .SYNOPSIS  Fetch a window title on demand (expensive to do per process). #>
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return $p.MainWindowTitle
    }
    catch {
        return $null
    }
}

function Get-SRGSample {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Config,
        $PreviousSample = $null,
        $PreviousCpuMap = $null
    )

    $mem = Get-MemoryStatus

    $commitPercent = 0.0
    $pagesInputPerSec = 0.0
    $rawPagesInput = $null

    $raw = Get-CimInstance Win32_PerfRawData_PerfOS_Memory -ErrorAction SilentlyContinue
    if ($raw -and $raw.CommitLimit -gt 0) {
        $commitPercent = [math]::Round(100.0 * $raw.CommittedBytes / $raw.CommitLimit, 1)
        $rawPagesInput = [double]$raw.PagesInputPersec
        if ($null -ne $rawPagesInput -and $null -eq $PreviousSample) {
            # Page input is a cumulative counter; the first sample has no rate.
            $pagesInputPerSec = 0.0
        }
    }
    else {
        # Fallback: derive commit from the memory status if perf data is absent.
        $st = [SRG.Native.Memory]::Get()
        if ($st.ullTotalPageFile -gt 0) {
            $commitPercent = [math]::Round(100.0 * ($st.ullTotalPageFile - $st.ullAvailPageFile) / $st.ullTotalPageFile, 1)
        }
    }

    $elapsed = 0.0
    if ($PreviousSample) { $elapsed = ((Get-Date) - $PreviousSample.Timestamp).TotalSeconds }
    if ($elapsed -gt 0 -and $null -ne $rawPagesInput -and $PreviousSample -and
        $PreviousSample.PSObject.Properties['RawPagesInput'] -and $null -ne $PreviousSample.RawPagesInput) {
        $delta = $rawPagesInput - [double]$PreviousSample.RawPagesInput
        if ($delta -lt 0) { $delta = 0 }  # counter reset (perf service restart)
        $pagesInputPerSec = [math]::Round($delta / $elapsed, 1)
    }

    $fg = Get-ForegroundProcessInfo
    $procs = Get-ProcessSnapshot -IncludeCommandLine ([bool]$Config.IncludeCommandLine) `
        -IncludeIoCounters ([bool]$Config.IncludeIoCounters)

    # CPU + IO trend: deltas since the previous sample, per process.
    $cpuMap = @{}
    foreach ($p in $procs) { $cpuMap[$p.Pid] = $p.CpuSeconds }
    $prevIo = @{}
    if ($PreviousSample -and $PreviousSample.PSObject.Properties['Processes']) {
        foreach ($q in $PreviousSample.Processes) { $prevIo[[int]$q.Pid] = $q }
    }
    foreach ($p in $procs) {
        $prevCpu = $null
        if ($PreviousCpuMap -and $PreviousCpuMap.ContainsKey($p.Pid)) { $prevCpu = $PreviousCpuMap[$p.Pid] }
        $cpuDelta = if ($null -ne $prevCpu) { [math]::Max(0.0, $p.CpuSeconds - $prevCpu) } else { 0.0 }
        $cpuPct = if ($elapsed -gt 0) { [math]::Round(100.0 * $cpuDelta / ($elapsed * [Environment]::ProcessorCount), 1) } else { 0.0 }
        Add-Member -InputObject $p -NotePropertyName CpuPercentSinceLast -NotePropertyValue $cpuPct -Force

        $ioMiBps = 0.0
        if ($elapsed -gt 0 -and $prevIo.ContainsKey([int]$p.Pid)) {
            $prev = $prevIo[[int]$p.Pid]
            $ioDelta = [math]::Max(0.0, ($p.ReadBytes - $prev.ReadBytes) + ($p.WriteBytes - $prev.WriteBytes))
            $ioMiBps = [math]::Round(($ioDelta / 1MB) / $elapsed, 2)
        }
        Add-Member -InputObject $p -NotePropertyName IoMiBPerSecSinceLast -NotePropertyValue $ioMiBps -Force
    }

    $top = @($procs | Sort-Object WorkingSetMiB -Descending | Select-Object -First $Config.TopProcessCount)

    $sample = [pscustomobject]@{
        Timestamp        = Get-Date
        TotalMiB         = $mem.TotalMiB
        AvailableMiB     = $mem.AvailableMiB
        CommitPercent    = $commitPercent
        PagesInputPerSec = $pagesInputPerSec
        RawPagesInput    = $rawPagesInput
        ForegroundPid    = if ($fg) { $fg.Pid } else { 0 }
        ForegroundName   = if ($fg) { $fg.Name } else { '' }
        Processes        = $procs
        TopByWorkingSet  = $top
    }
    return $sample, $cpuMap
}

Export-ModuleMember -Function Get-SRGSample, Get-ForegroundProcessInfo, Get-ProcessSnapshot, Get-MemoryStatus, Get-ProcessWindowTitle

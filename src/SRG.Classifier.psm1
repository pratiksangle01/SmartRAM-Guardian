# SRG.Classifier - assigns every process exactly one class:
#   Protected      - never touched, never suggested (system, security, tools
#                    of the trade, foreground work context)
#   ActiveWork     - part of what the user is doing right now (foreground chain)
#   Disposable     - user-approved allowlist; eligible for graceful close ONLY
#   SuggestionOnly - known "background lifestyle" apps; we may suggest changes
#                    but never act on them automatically
#   Unknown        - everything else; observed and logged, never acted on
#
# The hard safety rules live here as data (the protected set) so they are
# testable without touching a real system.

Set-StrictMode -Version 2.0

# Never touch these, in any mode, ever. Name comparison is case-insensitive,
# without the .exe suffix.
$script:BuiltInProtected = @(
    # Windows core / session
    'System', 'Idle', 'Registry', 'Secure System', 'smss', 'csrss', 'wininit',
    'winlogon', 'services', 'lsass', 'lsaiso', 'svchost', 'fontdrvhost', 'dwm',
    'sihost', 'taskhostw', 'RuntimeBroker', 'ShellExperienceHost', 'StartMenuExperienceHost',
    'explorer', 'SearchHost', 'ctfmon', 'conhost', 'dllhost', 'WmiPrvSE',
    'spoolsv', 'dasHost', 'audiodg', 'AggregatorHost',
    # Security
    'MsMpEng', 'NisSrv', 'SecurityHealthService', 'SecurityHealthSystray',
    'MsSense', 'SenseIR', 'MpCmdRun', 'usocoreworker',
    # Memory management itself
    'Memory Compression', 'SystemSettings',
    # Servicing / installers / updaters
    'msiexec', 'TiWorker', 'TrustedInstaller', 'MoUsoCoreWorker', 'setup',
    'unins000', 'uninstall', 'UpdateAssistant', 'Windows10UpgraderApp',
    'winget', 'choco', 'scoop',
    # Terminals / shells / remoting
    'powershell', 'powershell_ise', 'pwsh', 'cmd', 'WindowsTerminal', 'wt',
    'bash', 'ssh', 'ssh-agent', 'OpenConsole', 'tmux',
    # WSL / VMs / containers
    'wsl', 'wslservice', 'wslhost', 'vmwp', 'vmcompute', 'vmmem', 'vmmemWSL',
    'docker', 'com.docker.backend', 'com.docker.build', 'com.docker.dev-envs',
    'Docker Desktop', 'containerd', 'podman',
    # IDEs / editors / dev tooling
    'Code', 'Code - Insiders', 'devenv', 'rider64', 'idea64', 'pycharm64',
    'webstorm64', 'goland64', 'clion64', 'studio64', 'eclipse', 'netbeans64',
    'sublime_text', 'notepad++', 'vim', 'nvim', 'emacs', 'atom', 'fleet',
    'cursor', 'Cursor', 'Windsurf', 'zed',
    # AI assistants / coding agents (user-facing work tools, same as IDEs)
    'ChatGPT', 'OpenAI', 'codex', 'codex-cli', 'codex-code-mode-host',
    'codex-computer-use', 'codex-computer-use-swift', 'codex-windows-sandbox-service',
    'Claude', 'claude-code', 'Gemini', 'Copilot', 'GitHub Copilot', 'DeepAI Code',
    # Compilers / build systems / source control
    'git', 'git-remote-https', 'git-credential-manager', 'msbuild', 'VBCSCompiler',
    'cl', 'link', 'csc', 'dotnet', 'vstest.console', 'ninja', 'make', 'cmake',
    'cargo', 'rustc', 'node', 'tsc', 'esbuild', 'webpack', 'gradle', 'javac',
    'python', 'pip', 'npm', 'pnpm', 'yarn', 'go', 'gopls',
    # Large-copy / backup tools (interrupting these corrupts work)
    'robocopy', 'xcopy', 'teracopy', 'fastcopy', 'rclone', 'rsync',
    # The guardian itself
    'SmartRAM-Guardian'
)

# Names that indicate an active build/install via command line as well.
$script:InstallerPatterns = @(
    'msiexec', 'setup\.exe', 'unins.*\.exe', 'install.*\.exe',
    'TiWorker', 'TrustedInstaller', 'winget', 'choco\s', 'scoop\s'
)
$script:BuildPatterns = @(
    'msbuild', 'dotnet\s+(build|publish|test|pack)', 'cmake\s+--build',
    'ninja', 'cargo\s+(build|test)', 'npm\s+(run\s+build|install|ci)',
    'gradle', 'mvn\s+', 'make\s', 'tsc\s', 'webpack', 'vite\s+build'
)
$script:CopyPatterns = @('robocopy', 'xcopy', 'teracopy', 'fastcopy', 'rclone\s+(copy|move|sync)')

function Get-ProtectedNameSet {
    param($Config)
    $set = @{}
    foreach ($n in $script:BuiltInProtected) { $set[$n.ToLowerInvariant()] = $true }
    if ($Config -and $Config.ExtraProtectedProcesses) {
        foreach ($n in $Config.ExtraProtectedProcesses) { $set[("$n").ToLowerInvariant()] = $true }
    }
    return $set
}

function Get-ProcessStartTime {
    param($Process)
    if ($Process.PSObject.Properties['StartTime'] -and $Process.StartTime) { return [datetime]$Process.StartTime }
    return [datetime]::MinValue
}

function Get-ForegroundTree {
    <#
    .SYNOPSIS
        Ancestor + descendant PIDs of the foreground process. Protecting the
        whole chain means we never trim the IDE while "only" looking at its
        browser, or the terminal that owns the build.
    .NOTES
        Parent pid alone is unreliable: Windows recycles pids, so a process
        whose parent died can appear to hang off an unrelated process. Every
        link is validated against StartTime, and the walk stops at session
        roots so we never pull the whole service host into the context.
    #>
    param(
        [Parameter(Mandatory = $true)]$Processes,
        [Parameter(Mandatory = $true)][int]$ForegroundPid
    )
    $tree = @{}
    if ($ForegroundPid -le 0) { return $tree }
    $byPid = @{}
    foreach ($p in $Processes) { $byPid[[int]$p.Pid] = $p }
    $sessionRoots = @('wininit', 'services', 'winlogon', 'smss', 'System', 'Idle', 'Registry')

    # Ancestors
    $cur = $ForegroundPid
    $guard = 0
    while ($cur -gt 0 -and $byPid.ContainsKey($cur) -and $guard -lt 32) {
        $node = $byPid[$cur]
        $tree[$cur] = $true
        $parentPid = [int]$node.ParentPid
        if ($parentPid -le 0 -or -not $byPid.ContainsKey($parentPid)) { break }
        $parent = $byPid[$parentPid]
        if ($sessionRoots -contains $parent.Name) { break }
        # A parent that started after its child means the pid was recycled.
        if ((Get-ProcessStartTime -Process $parent) -gt (Get-ProcessStartTime -Process $node)) { break }
        $cur = $parentPid
        $guard++
    }

    # Descendants (repeated relaxation is fine for ~200 processes)
    $changed = $true
    $guard = 0
    while ($changed -and $guard -lt 32) {
        $changed = $false
        $guard++
        foreach ($p in $Processes) {
            $pPid = [int]$p.Pid
            $parentPid = [int]$p.ParentPid
            if ($tree.ContainsKey($pPid) -or -not $tree.ContainsKey($parentPid)) { continue }
            if ((Get-ProcessStartTime -Process $p) -lt (Get-ProcessStartTime -Process $byPid[$parentPid])) { continue }
            $tree[$pPid] = $true
            $changed = $true
        }
    }
    return $tree
}

function Test-CommandLineMatch {
    param([string]$CommandLine, [string[]]$Patterns)
    if ([string]::IsNullOrEmpty($CommandLine)) { return $false }
    foreach ($pat in $Patterns) {
        if ($CommandLine -match $pat) { return $true }
    }
    return $false
}

function Get-ActiveSensitiveOperations {
    <#
    .SYNOPSIS
        Detects installers, builds and large copies that must freeze ALL actions.
        Returns a list of reason strings (empty = safe to proceed).
    #>
    param([Parameter(Mandatory = $true)]$Processes)
    $reasons = New-Object System.Collections.Generic.List[string]
    foreach ($p in $Processes) {
        $name = ("$($p.Name)")
        $cmd = $p.CommandLine
        $isInstaller = (Test-CommandLineMatch -CommandLine $name -Patterns $script:InstallerPatterns) -or
                       (Test-CommandLineMatch -CommandLine $cmd -Patterns $script:InstallerPatterns)
        $isBuild = Test-CommandLineMatch -CommandLine $cmd -Patterns $script:BuildPatterns
        $isCopy = (Test-CommandLineMatch -CommandLine $name -Patterns $script:CopyPatterns) -or
                  (Test-CommandLineMatch -CommandLine $cmd -Patterns $script:CopyPatterns)

        if ($isInstaller) {
            $reasons.Add("installer/uninstaller active: $($p.Name) (pid $($p.Pid))")
        }
        elseif ($isBuild) {
            $reasons.Add("build in progress: $($p.Name) (pid $($p.Pid))")
        }
        elseif ($isCopy) {
            $reasons.Add("large copy in progress: $($p.Name) (pid $($p.Pid))")
        }
    }
    return , $reasons.ToArray()
}

function Get-ProcessClass {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Process,
        [Parameter(Mandatory = $true)]$Config,
        [hashtable]$ProtectedSet = $null,
        [hashtable]$ForegroundTree = $null
    )
    if (-not $ProtectedSet) { $ProtectedSet = Get-ProtectedNameSet -Config $Config }
    $name = ("$($Process.Name)")

    if ($ProtectedSet.ContainsKey($name.ToLowerInvariant())) { return 'Protected' }
    if ($ForegroundTree -and $ForegroundTree.ContainsKey([int]$Process.Pid)) { return 'ActiveWork' }

    foreach ($a in @($Config.AllowlistClose)) {
        if ($name -ieq $a) { return 'Disposable' }
    }
    foreach ($s in @($Config.SuggestionOnlyProcesses)) {
        if ($name -ieq $s) { return 'SuggestionOnly' }
    }
    return 'Unknown'
}

function Get-ProcessClassification {
    <#
    .SYNOPSIS
        Classifies a full snapshot. Returns objects with an added Class field.
    #>
    param(
        [Parameter(Mandatory = $true)]$Processes,
        [Parameter(Mandatory = $true)]$Config,
        [int]$ForegroundPid = 0
    )
    $protectedSet = Get-ProtectedNameSet -Config $Config
    $fgTree = Get-ForegroundTree -Processes $Processes -ForegroundPid $ForegroundPid
    $out = foreach ($p in $Processes) {
        $cls = Get-ProcessClass -Process $p -Config $Config -ProtectedSet $protectedSet -ForegroundTree $fgTree
        Add-Member -InputObject $p -NotePropertyName Class -NotePropertyValue $cls -Force
        $p
    }
    return , @($out)
}

Export-ModuleMember -Function Get-ProtectedNameSet, Get-ForegroundTree, Get-ProcessClass,
    Get-ProcessClassification, Get-ActiveSensitiveOperations, Test-CommandLineMatch
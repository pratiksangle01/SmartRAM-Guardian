# Classifier tests: protected names, foreground work context, allowlist,
# suggestion-only, and sensitive-operation detection.
. "$PSScriptRoot\TestHelpers.ps1"

Describe 'Process classifier' {

    Context 'Hard safety rules' {
        It 'classifies Windows and security processes as Protected' {
            $cfg = Get-TestConfig
            foreach ($n in 'MsMpEng', 'lsass', 'Memory Compression', 'svchost', 'csrss') {
                $p = New-FakeProcess -Name $n
                Get-ProcessClass -Process $p -Config $cfg | Should Be 'Protected'
            }
        }

        It 'classifies terminals, IDEs, git and WSL as Protected' {
            $cfg = Get-TestConfig
            foreach ($n in 'WindowsTerminal', 'powershell', 'pwsh', 'Code', 'devenv', 'git', 'wsl', 'docker') {
                $p = New-FakeProcess -Name $n
                Get-ProcessClass -Process $p -Config $cfg | Should Be 'Protected'
            }
        }

        It 'classifies AI assistants and coding agents as Protected' {
            $cfg = Get-TestConfig
            $names = 'ChatGPT', 'chatgpt', 'OpenAI', 'codex', 'codex-cli',
                'codex-code-mode-host', 'codex-windows-sandbox-service',
                'Claude', 'claude-code', 'Copilot', 'DeepAI Code'
            foreach ($n in $names) {
                $p = New-FakeProcess -Name $n
                Get-ProcessClass -Process $p -Config $cfg | Should Be 'Protected'
            }
        }

        It 'honours user-configured extra protected processes' {
            $cfg = Get-TestConfig -ExtraProtected @('MySpecialApp')
            $p = New-FakeProcess -Name 'MySpecialApp'
            Get-ProcessClass -Process $p -Config $cfg | Should Be 'Protected'
        }

        It 'classifies the foreground process and its tree as ActiveWork' {
            $cfg = Get-TestConfig
            $procs = @(
                (New-FakeProcess -ProcessId 100 -Name 'chrome'),
                (New-FakeProcess -ProcessId 101 -Name 'renderer' -ParentPid 100),
                (New-FakeProcess -ProcessId 200 -Name 'notepad'),
                (New-FakeProcess -ProcessId 300 -Name 'unrelated')
            )
            $classified = Get-ProcessClassification -Processes $procs -Config $cfg -ForegroundPid 100
            ($classified | Where-Object Pid -eq 100).Class | Should Be 'ActiveWork'
            ($classified | Where-Object Pid -eq 101).Class | Should Be 'ActiveWork'
            ($classified | Where-Object Pid -eq 300).Class | Should Be 'Unknown'
        }

        It 'never marks a protected name ActiveWork even when it is foreground' {
            $cfg = Get-TestConfig
            $procs = @((New-FakeProcess -ProcessId 50 -Name 'Code'))
            $classified = Get-ProcessClassification -Processes $procs -Config $cfg -ForegroundPid 50
            ($classified | Where-Object Pid -eq 50).Class | Should Be 'Protected'
        }
    }

    Context 'Allowlist and suggestion-only' {
        It 'marks allowlisted names as Disposable' {
            $cfg = Get-TestConfig -AllowlistClose @('Spotify')
            $p = New-FakeProcess -Name 'Spotify'
            Get-ProcessClass -Process $p -Config $cfg | Should Be 'Disposable'
        }

        It 'marks known background apps as SuggestionOnly, not Disposable' {
            $cfg = Get-TestConfig
            $p = New-FakeProcess -Name 'OneDrive'
            Get-ProcessClass -Process $p -Config $cfg | Should Be 'SuggestionOnly'
        }

        It 'leaves unknown apps as Unknown by default' {
            $cfg = Get-TestConfig
            $p = New-FakeProcess -Name 'SomeRandomApp'
            Get-ProcessClass -Process $p -Config $cfg | Should Be 'Unknown'
            $cfg.AllowlistClose.Count | Should Be 0
        }
    }

    Context 'Work context detection' {
        It 'protects ancestors and descendants of the foreground process' {
            $cfg = Get-TestConfig
            $procs = @(
                (New-FakeProcess -ProcessId 10 -Name 'explorer'),
                (New-FakeProcess -ProcessId 20 -Name 'Code' -ParentPid 10),
                (New-FakeProcess -ProcessId 21 -Name 'tsserver' -ParentPid 20),
                (New-FakeProcess -ProcessId 22 -Name 'node' -ParentPid 21)
            )
            $ctx = Get-WorkContext -Processes $procs -Config $cfg -ForegroundPid 21
            $ctx.ProtectedPidSet.ContainsKey(21) | Should Be $true
            $ctx.ProtectedPidSet.ContainsKey(20) | Should Be $true   # ancestor IDE
            $ctx.ProtectedPidSet.ContainsKey(22) | Should Be $true   # descendant build
        }

        It 'ignores a recycled parent pid (parent started after the child)' {
            $cfg = Get-TestConfig
            $procs = @(
                (New-FakeProcess -ProcessId 30 -Name 'orphanapp' -ParentPid 31 -StartTime ([datetime]'2026-05-01')),
                (New-FakeProcess -ProcessId 31 -Name 'unrelatednew' -ParentPid 0 -StartTime ([datetime]'2026-06-01'))
            )
            $ctx = Get-WorkContext -Processes $procs -Config $cfg -ForegroundPid 30
            $ctx.ProtectedPidSet.ContainsKey(31) | Should Be $false
        }

        It 'stops the ancestor walk at session roots' {
            $cfg = Get-TestConfig
            $procs = @(
                (New-FakeProcess -ProcessId 40 -Name 'services' -ParentPid 0),
                (New-FakeProcess -ProcessId 41 -Name 'svchost' -ParentPid 40),
                (New-FakeProcess -ProcessId 42 -Name 'someapp' -ParentPid 41)
            )
            $ctx = Get-WorkContext -Processes $procs -Config $cfg -ForegroundPid 42
            $ctx.ProtectedPidSet.ContainsKey(41) | Should Be $true
            $ctx.ProtectedPidSet.ContainsKey(40) | Should Be $false   # never pull the service host in
        }
    }

    Context 'Installer / build / copy protection' {
        It 'detects msiexec by name' {
            $procs = @((New-FakeProcess -Name 'msiexec' -CommandLine 'msiexec /i app.msi'))
            $r = Get-ActiveSensitiveOperations -Processes $procs
            $r.Count | Should Be 1
            $r[0] | Should Match 'installer'
        }

        It 'detects TiWorker / TrustedInstaller servicing' {
            $procs = @((New-FakeProcess -Name 'TiWorker'))
            (Get-ActiveSensitiveOperations -Processes $procs)[0] | Should Match 'installer'
        }

        It 'detects a setup.exe command line' {
            $procs = @((New-FakeProcess -Name 'setup' -CommandLine 'C:\Temp\setup.exe /S'))
            (Get-ActiveSensitiveOperations -Processes $procs)[0] | Should Match 'installer'
        }

        It 'detects an active build from the command line' {
            $procs = @((New-FakeProcess -Name 'dotnet' -CommandLine 'dotnet build SmartRAM.sln -c Release'))
            (Get-ActiveSensitiveOperations -Processes $procs)[0] | Should Match 'build'
        }

        It 'detects a large copy' {
            $procs = @((New-FakeProcess -Name 'robocopy' -CommandLine 'robocopy D:\src E:\dst /MIR'))
            (Get-ActiveSensitiveOperations -Processes $procs)[0] | Should Match 'copy'
        }

        It 'returns nothing when the machine is idle' {
            $procs = @((New-FakeProcess -Name 'notepad' -CommandLine 'notepad.exe'))
            (Get-ActiveSensitiveOperations -Processes $procs).Count | Should Be 0
        }
    }
}

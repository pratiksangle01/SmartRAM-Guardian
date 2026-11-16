# Classifier tests: protected names, foreground work context, allowlist,
# suggestion-only, and sensitive-operation detection.
. "$PSScriptRoot\TestHelpers.ps1"

Describe 'Process classifier' {

    Context 'Hard safety rules' {
        It 'classifies Windows and security processes as Protected' {
            $cfg = Get-TestConfig
            foreach ($n in 'MsMpEng', 'lsass', 'Memory Compression', 'svchost', 'csrss') {
                $p = Get-FakeProcess -Name $n
                Get-ProcessClass -Process $p -Config $cfg | Should Be 'Protected'
            }
        }

        It 'classifies terminals, IDEs, git and WSL as Protected' {
            $cfg = Get-TestConfig
            foreach ($n in 'WindowsTerminal', 'powershell', 'pwsh', 'Code', 'devenv', 'git', 'wsl', 'docker') {
                $p = Get-FakeProcess -Name $n
                Get-ProcessClass -Process $p -Config $cfg | Should Be 'Protected'
            }
        }

        It 'classifies AI assistants and coding agents as Protected' {
            $cfg = Get-TestConfig
            $names = 'ChatGPT', 'chatgpt', 'OpenAI', 'codex', 'codex-cli',
                'codex-code-mode-host', 'codex-windows-sandbox-service',
                'Claude', 'claude-code', 'Copilot', 'DeepAI Code'
            foreach ($n in $names) {
                $p = Get-FakeProcess -Name $n
                Get-ProcessClass -Process $p -Config $cfg | Should Be 'Protected'
            }
        }

        It 'honours user-configured extra protected processes' {
            $cfg = Get-TestConfig -ExtraProtected @('MySpecialApp')
            $p = Get-FakeProcess -Name 'MySpecialApp'
            Get-ProcessClass -Process $p -Config $cfg | Should Be 'Protected'
        }

        It 'classifies the foreground process and its tree as ActiveWork' {
            $cfg = Get-TestConfig
            $procs = @(
                (Get-FakeProcess -ProcessId 100 -Name 'chrome'),
                (Get-FakeProcess -ProcessId 101 -Name 'renderer' -ParentPid 100),
                (Get-FakeProcess -ProcessId 200 -Name 'notepad'),
                (Get-FakeProcess -ProcessId 300 -Name 'unrelated')
            )
            $classified = Get-ProcessClassification -Processes $procs -Config $cfg -ForegroundPid 100
            ($classified | Where-Object Pid -eq 100).Class | Should Be 'ActiveWork'
            ($classified | Where-Object Pid -eq 101).Class | Should Be 'ActiveWork'
            ($classified | Where-Object Pid -eq 300).Class | Should Be 'Unknown'
        }

        It 'never marks a protected name ActiveWork even when it is foreground' {
            $cfg = Get-TestConfig
            $procs = @((Get-FakeProcess -ProcessId 50 -Name 'Code'))
            $classified = Get-ProcessClassification -Processes $procs -Config $cfg -ForegroundPid 50
            ($classified | Where-Object Pid -eq 50).Class | Should Be 'Protected'
        }
    }

    Context 'Allowlist and suggestion-only' {
        It 'marks allowlisted names as Disposable' {
            $cfg = Get-TestConfig -AllowlistClose @('Spotify')
            $p = Get-FakeProcess -Name 'Spotify'
            Get-ProcessClass -Process $p -Config $cfg | Should Be 'Disposable'
        }

        It 'marks known background apps as SuggestionOnly, not Disposable' {
            $cfg = Get-TestConfig
            $p = Get-FakeProcess -Name 'OneDrive'
            Get-ProcessClass -Process $p -Config $cfg | Should Be 'SuggestionOnly'
        }

        It 'leaves unknown apps as Unknown by default' {
            $cfg = Get-TestConfig
            $p = Get-FakeProcess -Name 'SomeRandomApp'
            Get-ProcessClass -Process $p -Config $cfg | Should Be 'Unknown'
            $cfg.AllowlistClose.Count | Should Be 0
        }
    }

    Context 'Work context detection' {
        It 'protects ancestors and descendants of the foreground process' {
            $procs = @(
                (Get-FakeProcess -ProcessId 10 -Name 'explorer'),
                (Get-FakeProcess -ProcessId 20 -Name 'Code' -ParentPid 10),
                (Get-FakeProcess -ProcessId 21 -Name 'tsserver' -ParentPid 20),
                (Get-FakeProcess -ProcessId 22 -Name 'node' -ParentPid 21)
            )
            $ctx = Get-WorkContext -Processes $procs -ForegroundPid 21
            $ctx.ProtectedPidSet.ContainsKey(21) | Should Be $true
            $ctx.ProtectedPidSet.ContainsKey(20) | Should Be $true   # ancestor IDE
            $ctx.ProtectedPidSet.ContainsKey(22) | Should Be $true   # descendant build
        }

        It 'ignores a recycled parent pid (parent started after the child)' {
            $procs = @(
                (Get-FakeProcess -ProcessId 30 -Name 'orphanapp' -ParentPid 31 -StartTime ([datetime]'2026-05-01')),
                (Get-FakeProcess -ProcessId 31 -Name 'unrelatednew' -ParentPid 0 -StartTime ([datetime]'2026-06-01'))
            )
            $ctx = Get-WorkContext -Processes $procs -ForegroundPid 30
            $ctx.ProtectedPidSet.ContainsKey(31) | Should Be $false
        }

        It 'stops the ancestor walk at session roots' {
            $procs = @(
                (Get-FakeProcess -ProcessId 40 -Name 'services' -ParentPid 0),
                (Get-FakeProcess -ProcessId 41 -Name 'svchost' -ParentPid 40),
                (Get-FakeProcess -ProcessId 42 -Name 'someapp' -ParentPid 41)
            )
            $ctx = Get-WorkContext -Processes $procs -ForegroundPid 42
            $ctx.ProtectedPidSet.ContainsKey(41) | Should Be $true
            $ctx.ProtectedPidSet.ContainsKey(40) | Should Be $false   # never pull the service host in
        }
    }

    Context 'Installer / build / copy protection' {
        It 'detects msiexec by name' {
            $procs = @((Get-FakeProcess -Name 'msiexec' -CommandLine 'msiexec /i app.msi'))
            $r = Get-ActiveSensitiveOperation -Processes $procs
            $r.Count | Should Be 1
            $r[0] | Should Match 'installer'
        }

        It 'detects TiWorker / TrustedInstaller servicing' {
            $procs = @((Get-FakeProcess -Name 'TiWorker'))
            (Get-ActiveSensitiveOperation -Processes $procs)[0] | Should Match 'installer'
        }

        It 'detects a setup.exe command line' {
            $procs = @((Get-FakeProcess -Name 'setup' -CommandLine 'C:\Temp\setup.exe /S'))
            (Get-ActiveSensitiveOperation -Processes $procs)[0] | Should Match 'installer'
        }

        It 'detects an active build from the command line' {
            $procs = @((Get-FakeProcess -Name 'dotnet' -CommandLine 'dotnet build SmartRAM.sln -c Release'))
            (Get-ActiveSensitiveOperation -Processes $procs)[0] | Should Match 'build'
        }

        It 'detects a large copy' {
            $procs = @((Get-FakeProcess -Name 'robocopy' -CommandLine 'robocopy D:\src E:\dst /MIR'))
            (Get-ActiveSensitiveOperation -Processes $procs)[0] | Should Match 'copy'
        }

        It 'returns nothing when the machine is idle' {
            $procs = @((Get-FakeProcess -Name 'notepad' -CommandLine 'notepad.exe'))
            (Get-ActiveSensitiveOperation -Processes $procs).Count | Should Be 0
        }
    }
}

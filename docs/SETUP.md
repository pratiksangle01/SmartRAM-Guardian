# Setup and troubleshooting

## Supported machine and source checkout

Use Windows 10/11 with **Windows PowerShell 5.1 Desktop**, launched as a normal
user from the Start menu or with `powershell.exe -NoProfile`. PowerShell 7,
Linux, macOS, and WSL are not supported runtime environments: the sampler uses
Windows DLLs and CIM classes, and startup uses `powershell.exe`.

No compiler, package restore, administrator session, or external PowerShell
module is required to run the application. Use a writable user-owned directory.
Installing Git or downloading source/development modules requires internet access;
the guardian itself makes no network calls.

If Git is installed (`git --version`), run:

```powershell
git clone https://github.com/pratiksangle01/SmartRAM-Guardian.git
Set-Location .\SmartRAM-Guardian
$PSVersionTable | Select-Object PSVersion, PSEdition
```

Expect version `5.1.*` and edition `Desktop`. If Git is unavailable, download
**Code > Download ZIP** from the repository's GitHub page, review and unblock the
trusted ZIP in File Explorer's Properties before extracting it, then change into
the extracted directory (usually `SmartRAM-Guardian-main`). Do not run inside the
ZIP viewer.

## Script execution policy

Windows may initially refuse `.ps1` files with “running scripts is disabled.”
Inspect policy and permit local scripts for this terminal session only:

```powershell
Get-ExecutionPolicy -List
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

This does not change machine-wide policy and expires when the window closes.
Review downloaded code before unblocking it. If an already extracted trusted ZIP
still causes “not digitally signed,” unblock only the scripts in this checkout:

```powershell
Get-ChildItem -Path . -Recurse -File -Include *.ps1,*.psm1 | Unblock-File
```

The existing background launcher and scheduled task pass `-ExecutionPolicy Bypass`
to their child `powershell.exe` process so startup does not depend on this terminal's
session setting. This is process-local, not a saved machine policy change; review
the code before starting it. Organization Group Policy can override process policy. If `MachinePolicy` or
`UserPolicy` prevents execution, use your organization's approved process; do not
change machine-wide policy or elevate to work around it.

## First run (no scheduled task)

From the repository root, with the supplied `config.json` unchanged:

```powershell
.\diagnose.ps1
.\start.ps1
Start-Sleep -Seconds 35
.\status.ps1
.\stop.ps1
```

Diagnosis should show memory pressure, candidate processes, and the safety gate.
Start should report a running guardian; status should show `observe`, a closed
safety gate, and a recent sample. The first sample may be pending briefly; wait
another sampling interval and check again. Logs are in `logs\guardian.log` and
state in `state.json` by default. Stop requests shutdown and normally removes
`guardian.pid`. If startup exits early, inspect the log and run the foreground,
single-pass diagnostic below to expose errors without a persistent background
process:

```powershell
.\src\Guardian.ps1 -Once
```

Configuration is validated before use; restore the supplied config if a local
edit causes validation errors. Keep `Mode` set to `observe` for setup verification.
Do not approve apps or enable automated actions just to test setup.

## Optional logon startup

```powershell
.\install.ps1
.\start.ps1
```

Before installing, check for an existing task:

```powershell
Get-ScheduledTask -TaskName SmartRAM-Guardian -ErrorAction SilentlyContinue
```

Install replaces a task with that name, and uninstall removes it; do not use
either command if that task belongs to a different application or checkout.

Installation validates config and registers the current user's
`SmartRAM-Guardian` logon task; it does not start the guardian immediately.
`install.ps1 -NoAutoStart` only validates config and creates the log directory.
Scheduled-task registration can be restricted by local policy; manual startup
and tests do not require a task. The task references this checkout, so uninstall
before moving or deleting it:

```powershell
.\uninstall.ps1
```

## Development and tests

There is no build step. The tests use Pester 3/4 syntax and include a live Windows
observe-mode pass in a temporary directory. They do not require installation or
app approvals. Pester 5 is not compatible with these suites. Do not assume a
bundled module exists on every machine; check first:

```powershell
Get-Module -ListAvailable Pester, PSScriptAnalyzer | Select-Object Name, Version
```

If Pester 3.4.x or 4.x is available, the tests-only path needs no downloads:

```powershell
.\tests\Invoke-Tests.ps1 -NoAnalyzer
```

For the same tool versions as CI, install these modules for the current user
once. These commands download from PowerShell Gallery and may prompt for
repository trust; confirm only if allowed by your organization's policy:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Install-PackageProvider NuGet -MinimumVersion 2.8.5.201 -Scope CurrentUser -Force | Out-Null
Install-Module Pester -RequiredVersion 3.4.0 -Scope CurrentUser -Repository PSGallery -Force -SkipPublisherCheck
Install-Module PSScriptAnalyzer -RequiredVersion 1.25.0 -Scope CurrentUser -Repository PSGallery -Force
.\tests\Invoke-Tests.ps1
```

`-SkipPublisherCheck` is specific to installing the older Pester release alongside
Windows' bundled signed copy; it does not disable script execution policy. The
runner explicitly imports a compatible Pester version even if Pester 5 is also
installed. PSScriptAnalyzer is optional locally and explicitly reported as skipped
if absent; CI always installs it. With it present, expect `no issues` after a
nonzero test total with `Failed=0`. Failures or analyzer findings return exit code
1; no compatible Pester or no discovered tests is an error, not a successful run.

If Gallery access fails, check TLS 1.2, network/proxy policy, and repository access
with `Get-PSRepository`. Use an approved internal module source if required;
there are no credentials or API keys to configure for this project.

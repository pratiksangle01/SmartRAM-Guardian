# Security policy

## What SmartRAM-Guardian is

A local, user-level PowerShell background process. It reads system memory
counters and process metadata, writes its own logs and a state file, and -
only if you explicitly opt in - lowers memory priority of idle background
processes or sends a normal window-close message to apps you personally
approved.

## Guarantees (enforced in code, covered by tests)

- **No network access.** No `Invoke-WebRequest`, no sockets, no telemetry, no
  update check. Nothing leaves the machine.
- **No downloaded or bundled binaries.** The only native code is small
  P/Invoke declarations of documented Win32 APIs in `src/SRG.Sampler.psm1`
  and `src/SRG.Priority.psm1`.
- **No elevation.** Everything runs as the logged-on user. The scheduled task
  is registered with `RunLevel Limited`.
- **No force-kill.** There is no `taskkill`, `Stop-Process -Force` or
  `TerminateProcess` call in `src/`. Graceful close uses
  `Process.CloseMainWindow()` and waits. (The only `Stop-Process` in the whole
  project is in `stop.ps1`, where it targets the guardian's own leftover
  process after a polite stop request times out.)
- **No system modification.** It never reboots, never logs off, never touches
  the pagefile, never changes security features, never trims working sets.
- **Reversible changes only.** The scheduled task, the allowlist and the
  config are the only persistent state; `uninstall.ps1` removes the task and
  runtime files. Memory-priority hints disappear when the target process
  exits, and can be restored for a live process in one line (see
  `docs/HOW_IT_WORKS.md`).

## What it reads

- Memory counters (`GlobalMemoryStatusEx`, `Win32_PerfRawData_PerfOS_Memory`)
- Process list, parent pids, command lines, working set, CPU time, window
  handles (`Get-Process`, `Win32_Process`, `GetForegroundWindow`)
- Startup `Run` registry keys - only when you run `diagnose.ps1`
- Chrome background-mode policy key - only when you run `diagnose.ps1`

Command lines can contain sensitive arguments (tokens, paths). They are
**never written to disk or logged**; they are used in memory for the current
pass only. Log files contain process names, pids and memory numbers.

## What it writes

- `logs/guardian.log` (rotated, 1 MiB x 5 by default)
- `logs/install.log` (install / pause / allowlist decisions)
- `state.json` (last sample summary, no command lines)
- `guardian.pid`, `guardian.pause`, `guardian.stop` (lifecycle markers)

All under the repository folder. Nothing is written to `%ProgramData%`,
`C:\Windows`, or the registry by the guardian itself.

## Privileges and scope

The guardian cannot open most system processes for modification, and it does
not try: protected names are skipped before any API is called. When Windows
denies a change (for example against `System`, pid 4), the failure is logged
once and not retried.

## Reporting a vulnerability

Please open a private security advisory on the repository (GitHub
"Report a vulnerability") or contact the maintainers directly. Include:

- PowerShell version and Windows build
- the config you used (redact paths)
- the relevant `logs/guardian.log` lines

Please do not open a public issue for anything that could let a local
attacker escalate privileges or cause app data loss.

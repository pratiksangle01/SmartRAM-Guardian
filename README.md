# SmartRAM-Guardian

A small, safety-first background memory manager for Windows 10/11 written in
Windows PowerShell 5.1. It watches for *real* memory pressure, figures out what
you are working on, protects it, and - only if you opt in - reduces the
footprint of everything else.

It does not "free RAM" to make Task Manager look nicer. On an 8 GB machine the
number that matters is responsiveness and commit pressure, not the size of the
standby cache.

```
$ .\status.ps1
=== SmartRAM-Guardian status ===
running      : True (pid 18700)
mode         : observe (profile default)
memory       : 924/7886 MiB available, commit 76.2%, pages-in 0/s
pressure     : Warn  [AvailableLow]
assessment   : corroborated=False actionable=False cooldown=False
reason       : single signal only (AvailableLow) - likely cache, not real pressure
foreground   : Code (pid 10688)
work context : bash, chrome, conhost, Code, explorer, powershell, ...
classes      : Protected=118 ActiveWork=24 Disposable=0 Suggest=1 Unknown=53
gate closed  : mode is 'observe' (log and suggest only)
```

## What it does

1. **Samples** every 30 s (15 s under real pressure): available RAM, commit %,
   hard page-ins, top working sets, per-process CPU and IO deltas, and the
   foreground process.
2. **Detects pressure with corroboration**: low available memory alone is not
   pressure. It needs a second independent signal (commit near the limit or
   active hard paging) before anything is considered actionable.
3. **Classifies** every process as Protected, ActiveWork, Disposable,
   SuggestionOnly, or Unknown.
4. **Detects your work context**: the foreground process plus its validated
   ancestor/descendant tree. Everything it depends on is temporarily protected.
5. **Acts only through a safety gate**, in this order: lower memory priority of
   idle background processes (off by default) -> suggestions -> graceful close
   of apps you approved.
6. **Learns with your consent**: it proposes disposable apps with evidence; you
   approve one at a time with a command. The default allowlist is empty.

## What it will never do

- Kill, force-close, or suspend anything. Closing is `CloseMainWindow` + wait.
- Touch system/security processes, Defender, Memory Compression, the
  foreground app, terminals, IDEs, Git, compilers, installers, active WSL or
  Docker, builds, or large file copies.
- Trim working sets, purge the standby list, change the pagefile, reboot, log
  off, or disable security features.
- Make network calls, send telemetry, or download binaries.
- Act in `observe` mode (the default) or while paused.

Every persistent change is logged and reversible. `uninstall.ps1` removes the
scheduled task and runtime files.

## Quick start (Windows PowerShell 5.1)

```powershell
# From the repository folder, one line:
Set-Location .\SmartRAM-Guardian; .\diagnose.ps1
```

That takes two samples one second apart, prints pressure analysis, the top
working sets with their class, disposable candidates and the memory-priority
targets. It changes nothing.

Then, when you want it running quietly:

```powershell
.\install.ps1              # validates config, registers a per-user logon task
.\start.ps1                # or let the next logon start it
.\status.ps1
```

Requirements: Windows 10/11, Windows PowerShell 5.1 (built in), no admin
rights, no downloads, no external modules at runtime.

## Commands

| Script | What it does |
| --- | --- |
| `diagnose.ps1` | one-shot read-only analysis, candidates, startup suggestions. `-Save` writes a JSON snapshot |
| `status.ps1` | running?, mode, last sample, pressure verdict, gate state |
| `install.ps1` | idempotent install: validate config + register logon task (`-NoAutoStart` to skip the task) |
| `uninstall.ps1` | full rollback (`-RemoveLogs`, `-RemoveConfig` optional) |
| `start.ps1` / `stop.ps1` | start in background / graceful stop |
| `pause.ps1` / `resume.ps1` | keep sampling, take no action / resume actions |
| `approve.ps1 -Name X` | add `X` to `AllowlistClose` (`-Revoke` to remove) |
| `tests\Invoke-Tests.ps1` | Pester suite + optional PSScriptAnalyzer |

## Modes

| Mode | Behaviour |
| --- | --- |
| `observe` (default) | logs and suggests only; the gate is always closed |
| `safe-auto` | may lower memory priority of idle background processes if `EnableEcoQoS` is true |
| `allowlisted-close` | additionally may gracefully close apps in `AllowlistClose`, one per pass, under real pressure |

`pause.ps1` overrides every mode. Installer/build/copy detection overrides
every mode.

## Configuration

`config.json`, validated on load. Defaults are tuned for ~8 GB machines:

- `WarnAvailableMiB` 1536, `CriticalAvailableMiB` 819
- `WarnCommitPercent` 80, `CriticalCommitPercent` 92
- `PagingWarnPagesInputPerSec` 500
- `HysteresisMiB` 256, `HysteresisCommitPercent` 5, `CooldownSeconds` 300
- `EnableEcoQoS` false, `EnableAggressiveTrim` false (and rejected if true)
- `AllowlistClose` empty

Full reference: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md#9-config-reference).

## Real measurements (not estimates)

Measured on the development machine: Windows 11, 8 GB RAM, 8 logical cores,
while Chrome, an IDE, a terminal, WSL and Defender were all running (available
memory 682-924 MiB, commit 75-78%, 286-3709 page-ins/s).

### Guardian's own cost

| Measurement | Result |
| --- | --- |
| One sample pass (in-process, steady state) | 295 ms (cold 470 ms) |
| CPU per pass inside the background loop | 0.31-0.63 s depending on system load |
| 30 s interval, adaptive off, 120 s window | 2.52 s CPU / 4 passes = **2.1 % of one core**, 0.26 % of the machine |
| 15 s interval, adaptive on, 180 s window | 2.95 s CPU / 6 passes = 1.6 % of one core, 0.21 % of the machine |
| Working set | 65-94 MiB (private 97 MiB), 580 handles, 13-14 threads |
| Log volume | 8 samples in 120 s = 1.8 KB |

`Win32_PerfFormattedData_PerfOS_Memory` costs ~290 ms per query because it
formats every counter; the implementation uses the raw counter and computes
rates itself (~30 ms). Window titles are read only for the foreground process
and close candidates; reading them for all ~250 processes cost 150-300 ms per
pass.

### Test suite

```
Using Pester 3.4.0
Total=70 Passed=70 Failed=0 Skipped=0
```

70 Pester tests, all passing on PowerShell 5.1 with the built-in Pester 3.4,
including a real end-to-end observe-mode pass and a real memory-priority
read/lower/restore round trip.

### Behaviour observed in a live 3-minute observe run

- 12 sample lines, `ACTION` lines: **0**.
- Pressure varied between `Warn` and `Critical` (this machine really was
  paging), and the guardian logged
  `corroborated pressure but mode=observe; no action` - by design.
- One `DEBUG` line explained why a `Warn` reading was ignored:
  `single signal only (AvailableLow) - likely cache, not real pressure`.

### Lever test: graceful close (real, one pass)

`charmap.exe` launched, added to `AllowlistClose` in a throwaway config,
`allowlisted-close` mode, one pass:

```
[INFO  ] [sample] avail=894MiB commit=75.6% pagesIn=0/s level=Critical signals=[AvailableCritical,CommitHigh]
[ACTION] [action] graceful close charmap (pid 7184): Closed after 265ms
```

The same pass against a Notepad window whose title started with `*` (unsaved
changes marker) produced:

```
[INFO  ] [action] skip Notepad (pid 15384): window title suggests unsaved work ('*# ... - Notepad')
```

That is the rule working: title heuristics are conservative, and "cannot rule
out unsaved work" means "do not touch".

### Lever test: memory priority (real, reversible)

```
before=5
set ok=True
after=2
ecoqos ok=True
restore ok=True now=5
```

Reading and lowering priority on a child process we started, then restoring it.
Against the `System` process (pid 4) the API correctly reports
`OpenProcess failed: 5` (access denied); the guardian logs that once and does
not retry.

### Honest before/after

Observe mode is designed to change nothing, and the measurements show it:
available RAM fluctuated between 682 and 924 MiB during the runs regardless of
the guardian. On this machine the guardian did **not** make more RAM available,
because that is not what the levers do - they change *reclaim order* and
*graceful close of approved apps*. No benchmark numbers are claimed beyond what
is printed above. If you want to evaluate it, run `diagnose.ps1` before and
after a real workload and compare page-in rates and app responsiveness, not the
free-memory number.

## Status / roadmap

| Stage | Scope | State |
| --- | --- | --- |
| 1 | sampler, classifier, pressure engine, status/diagnose, logs, tests, CI | **done** |
| 2 | safety gate, priority lever (off by default), approval loop, allowlisted graceful close | **done** |
| 3 | optional extras (startup-mode toggles, per-app profiles, richer measurements) | not started |

Deliberately not implemented: aggressive working-set trimming, standby-list
purging, process suspension. See
[docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md#never-implemented-on-purpose).

## Limitations

- The loop is PowerShell; a native service would be cheaper. 2 % of one core
  at a 30 s interval was the trade-off chosen for a no-binary, no-admin tool.
- `Available` includes standby, so on a machine with a huge cache the number
  can look low without real pressure. That is exactly why corroboration
  exists; the guardian may therefore stay quiet when a naive tool would act.
- The work context is process-tree based. It does not watch file handles (that
  needs elevation and is expensive), so exotic dependency chains can be missed.
  The failure mode is conservative: unknown processes are not closed anyway.
- Closing depends on an app honouring `CloseMainWindow`. Apps that ignore it
  are reported as `StayedOpen` and left alone.
- The unsaved-work check is a title heuristic (`*`, `unsaved`). It can skip an
  app that would have been safe, which is the intended direction of error.
- Profiles (`dev`, `gaming`, `installation`) are recorded but only `default`
  has distinct behaviour today; protection during builds/installs is already
  enforced by detection.
- Second-level memory detail (per-process private bytes, standby breakdown) is
  not collected; the focus is decisions, not dashboards.

## Uninstall

```powershell
.\uninstall.ps1            # stop, remove task, remove runtime state
.\uninstall.ps1 -RemoveLogs -RemoveConfig
```

Nothing else was ever changed on the system, so there is nothing else to undo.

## Development

```
src/SRG.Logging.psm1    rotating logger, low-disk guard
src/SRG.Config.psm1     defaults, merge, validation
src/SRG.Sampler.psm1    memory/process snapshot, foreground detection
src/SRG.Pressure.psm1   signals, corroboration, hysteresis, cooldown
src/SRG.Classifier.psm1 protected list, classes, work context, sensitive ops
src/SRG.Context.psm1    work-context summary
src/SRG.Priority.psm1   lever (a): memory priority / EcoQoS
src/SRG.Actions.psm1    safety gate, graceful close, candidates, suggestions
src/Guardian.ps1        the loop
```

Tests: `tests\Invoke-Tests.ps1`. CI (`.github/workflows/ci.yml`) runs
PSScriptAnalyzer and the Pester suite on `windows-latest` with Windows
PowerShell 5.1.

## License and references

MIT - see [LICENSE](LICENSE).

Concepts were informed by WinMemoryCleaner, Mem Reduct, WinMemClean and
Sysinternals Autoruns/RAMMap. No code was copied from them; the classification
policy, pressure model, gate and levers here are written from scratch. GPL
projects were read for ideas only.

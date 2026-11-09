# How SmartRAM-Guardian works

This document describes what the code actually does, in execution order. If
something here disagrees with the code, the code is the bug.

## 1. The loop

```
start -> load+validate config -> single-instance check -> sample -> assess
      -> classify -> detect work context -> safety gate -> (act | log)
      -> write state -> sleep (adaptive) -> repeat
```

One pass is a module pipeline; `src/Guardian.ps1` owns the loop, crash
recovery, single-instance locking and shutdown.

- **Interval:** `SampleIntervalSeconds` (default 30 s). When pressure is
  *corroborated and actionable*, the interval drops to `BusyIntervalSeconds`
  (default 15 s) so a real event is followed closely. It does **not** drop
  just because one cosmetic signal is present.
- **Adaptive rule:** `if (AdaptiveSampling -and Assessment.Actionable)`.
- **Stop:** `stop.ps1` writes a `guardian.stop` marker; the loop checks it
  once per second during the sleep loop and exits cleanly, removing its pid
  file and the marker. Only if it does not exit within 45 s does `stop.ps1`
  terminate its own leftover process.
- **Crash recovery:** a stale `guardian.pid` whose pid is not alive is
  detected and taken over, and the event is logged. A live pid blocks a second
  instance.

## 2. Sampling (`src/SRG.Sampler.psm1`)

| Value | Source | Why |
| --- | --- | --- |
| total / available physical | `GlobalMemoryStatusEx` (P/Invoke) | single syscall, locale independent; `ullAvailPhys` includes standby (what Task Manager calls "available") |
| commit % | `Win32_PerfRawData_PerfOS_Memory` `CommittedBytes / CommitLimit` | raw query is ~30 ms; the *formatted* variant costs ~290 ms because it formats every counter |
| pages input / s | delta of the raw `PagesInputPersec` counter over the real elapsed time | hard paging is the strongest "memory is actually short" signal; first sample is always 0 because a cumulative counter has no rate yet |
| per-process working set, CPU seconds, window handle, start time | `Get-Process` | cheap |
| parent pid, command line, read/write transfer counters | `Win32_Process` | paging for the tree, installer/build detection, IO trend |
| foreground pid | `GetForegroundWindow` + `GetWindowThreadProcessId` | one call |
| window titles | **lazily**, only for the foreground process and close candidates | reading titles for every process costs 150-300 ms per pass |

Per-process trends are computed against the previous sample:
`CpuPercentSinceLast = ΔCPU / (elapsed × logical cores)`,
`IoMiBPerSecSinceLast = Δ(read+write) / elapsed`.
Counter resets (perf service restart) are clamped to zero.

## 3. Pressure assessment (`src/SRG.Pressure.psm1`)

### Signals

| Signal | Condition |
| --- | --- |
| `AvailableCritical` | available < `CriticalAvailableMiB` (819) |
| `AvailableLow` | available < `WarnAvailableMiB` (1536) |
| `CommitCritical` | commit ≥ `CriticalCommitPercent` (92) |
| `CommitHigh` | commit ≥ `WarnCommitPercent` (80) |
| `HardPaging` | pages input/s ≥ `PagingWarnPagesInputPerSec` (500) |

### The rule that prevents fake "low RAM"

```
actionable = (signals.Count >= 2 OR CommitCritical) AND level >= Warn
```

Windows fills free RAM with standby cache. A machine can report 400 MiB
"available" while the user is working perfectly fine, because those pages are
reclaimable cache. Acting on that number alone burns CPU and hurts
responsiveness. So a lone low-available signal is recorded and **never** acted
on:

> `verdict: single signal only (AvailableLow) - likely cache, not real pressure`

Commit charge near the limit is different: the pagefile is the backstop, and
approaching it means real paging is imminent or happening. That is why a
critical commit is accepted on its own.

### Hysteresis

Escalation is immediate. De-escalation is one step at a time and only when the
reading is clearly outside the band being left:

- `Critical -> Warn` requires available ≥ critical + `HysteresisMiB` **or**
  commit ≤ critical − `HysteresisCommitPercent`
- `Warn -> Normal` requires available ≥ warn + `HysteresisMiB` **and**
  commit ≤ warn − `HysteresisCommitPercent`

This prevents flapping around a threshold (which would also retrigger actions).

### Cooldown

After an action, `CooldownSeconds` (default 300) must pass before another
action may run. Cooldown suppresses actions; it does not suppress sampling.

## 4. Classification (`src/SRG.Classifier.psm1`)

Every process gets exactly one class. Precedence: Protected → ActiveWork →
Disposable (allowlist) → SuggestionOnly → Unknown.

- **Protected** - a hard-coded list of names plus
  `ExtraProtectedProcesses`: Windows/session core, security (Defender,
  `MsMpEng`, `NisSrv`, `MsSense`), `Memory Compression`, servicing/installers,
  terminals/shells, WSL/VM/container hosts, IDEs and editors, AI assistants and
  coding agents (ChatGPT, Codex, Claude, Copilot, ...), compilers/build
  tools, Git, large-copy tools, and the guardian itself. These are skipped
  before any API is called.
- **ActiveWork** - the foreground process plus its validated ancestor and
  descendant tree (see below). The foreground app is never touched even if a
  user somehow allowlists its name.
- **Disposable** - only names in `AllowlistClose`. Default: empty.
- **SuggestionOnly** - known background lifestyle apps (`OneDrive`,
  `SearchIndexer`, `Widgets`, `Teams`, ...). Reported, never acted on.
- **Unknown** - everything else. Observed, logged, eligible for *suggestions*
  and (only when enabled) the priority lever. Never closed.

### Work context and pid reuse

`Get-ForegroundTree` walks up from the foreground pid and then down through
children. Two safeguards make this trustworthy:

1. Every parent link is validated with `StartTime`: a "parent" that started
   after its child means the pid was recycled after the real parent exited, so
   the link is dropped. Without this, an orphaned app can appear to hang off
   an unrelated process and drag half the machine into the protected set.
2. The ancestor walk stops at session roots (`wininit`, `services`,
   `winlogon`, `smss`, `System`), so the service host is never pulled into a
   user's work context.

Protecting the *tree* is what stops the guardian from getting clever with the
wrong process: an IDE's language server, its dev server and its build helper
are all in the tree, so "just the browser" never gets trimmed while the build
is running.

### Installer / build / copy detection

`Get-ActiveSensitiveOperations` scans names and command lines for installer
patterns (`msiexec`, `setup.exe`, `unins*.exe`, `TiWorker`,
`TrustedInstaller`, `winget`, `choco`, `scoop`), build patterns (`msbuild`,
`dotnet build|publish|test|pack`, `cmake --build`, `ninja`, `cargo build|test`,
`npm run build|install|ci`, `gradle`, `mvn`, `make`, `tsc`, `webpack`,
`vite build`) and copy patterns (`robocopy`, `xcopy`, Teracopy, FastCopy,
`rclone copy|move|sync`, `rsync`). Any match closes the global action gate, in
every mode.

## 5. The safety gate (`src/SRG.Actions.psm1`)

Before any lever runs, `Test-SafetyGate` collects blockers:

- mode is `observe`
- cooldown active
- `guardian.pause` marker present
- any sensitive operation detected (installer/build/copy)

`Open = (blockers.Count -eq 0)`. The gate is evaluated on every pass and its
verdict is written to `state.json` and `status.ps1`, so you can always see why
the guardian did or did not act.

## 6. Levers, in order of preference

### (a) Lower memory priority / EcoQoS - `src/SRG.Priority.psm1`

Off by default (`EnableEcoQoS: false`).

Targets are chosen by a pure function: `Unknown` class, no main window, no
windowed ancestor, working set ≥ 64 MiB, CPU ≤ 2%. It sets memory priority
`5 (normal) -> 2 (low)` via `SetProcessInformation(ProcessMemoryPriority)` and
optionally EcoQoS via `SetProcessInformation(ProcessPowerThrottling)`. It does
**not** trim a working set and does not suspend the process. It only tells
Windows "prefer these pages when you must reclaim".

Why the windowed-ancestor rule matters: a protected-name list cannot know every
IDE and Electron app. If a process has *any* ancestor with a visible window, it
belongs to an app the user can see, so it is treated as user context and
excluded - no name list required.

Safety details:

- Protected / ActiveWork / SuggestionOnly / Disposable are excluded by
  construction.
- Failures (e.g. access denied) are logged once and the pid is remembered, so
  there is no retry storm.
- Reversal for a live process:
  `Import-Module .\src\SRG.Priority.psm1; Set-SRGProcessMemoryPriority -ProcessId <pid> -Priority 5`
  (hints also disappear when the process exits).

### (b) Suggestions

`diagnose.ps1` lists startup `Run` entries and the Chrome background-mode
policy, plus "disposable candidates" with evidence. These are text output in
observe mode; nothing is changed.

### (c) Graceful close of allowlisted apps

Only in `allowlisted-close` mode, only for `Disposable` class, only when the
gate is open, and only when:

- the process has a main window (otherwise there is no graceful request to
  make, and we do not fall back to killing),
- the window title has no `*`/`unsaved` marker.

`Invoke-GracefulClose` calls `CloseMainWindow()`, waits up to
`GracefulCloseTimeoutSeconds`, and reports `Closed` / `StayedOpen` /
`NoMainWindow` / `AlreadyGone` / `CloseRequestRejected`. If the app shows a
save dialog and stays open, that is the correct outcome: the guardian leaves it
alone, logs `StayedOpen`, and does not escalate.

After a successful close, the cooldown starts. One close per pass, then stop.

### Never implemented on purpose

- Working-set trimming (`EmptyWorkingSet`): makes numbers look better and
  responsiveness worse - the pages are needed again immediately, so they get
  faulted back in.
- Standby-list purging, forced pagefile changes, process suspension.

`EnableAggressiveTrim` exists in the config schema only to be **rejected** if
someone sets it to `true`.

## 7. Learning loop

1. `diagnose.ps1` proposes candidates: `Unknown`, has a window, ≥ 150 MiB
   resident, idle CPU, with the evidence string.
2. You approve with `.\approve.ps1 -Name '<Name>'`. It refuses anything on the
   protected list, validates the resulting config, and appends to
   `AllowlistClose`. The decision is logged to `logs/install.log`.
3. Revoke with `.\approve.ps1 -Name '<Name>' -Revoke`.
4. Closing still requires `allowlisted-close` mode and real, corroborated
   pressure.

The default allowlist is empty: out of the box the guardian can close nothing.

## 8. State, logs and failure handling

`state.json` (written every pass):

```json
{
  "timestamp": "...", "mode": "observe", "profile": "default",
  "availableMiB": 906, "commitPercent": 75.0, "pagesInputPerSec": 286,
  "pressureLevel": "Warn", "signals": ["AvailableLow"],
  "corroborated": false, "actionable": false, "inCooldown": false,
  "reason": "single signal only (AvailableLow) - likely cache, not real pressure",
  "paused": false,
  "gateOpen": false, "gateReasons": ["mode is 'observe' ..."],
  "foreground": "Code (pid 1234)", "workContextMembers": ["..."],
  "topWorkingSets": ["Memory Compression=930.6MiB", "..."],
  "classes": { "Protected": 118, "ActiveWork": 24, "Disposable": 0,
               "SuggestionOnly": 1, "Unknown": 108 }
}
```

Logging (`src/SRG.Logging.psm1`):

- Levels `DEBUG/INFO/WARN/ERROR/ACTION`, category tag, one line per event.
- Rotation at `LogMaxBytes` (1 MiB), keeping `LogMaxFiles` (5), done before
  each write, so the file never grows unbounded.
- Low disk: when free space on the log drive drops below `LowDiskFreeMiB`
  (500), non-error logging stops and one WARN is emitted per session.
- Logging never throws into the loop; a failed write is swallowed on purpose.

Failure handling: unhandled errors are logged as `ERROR` and the loop exits
(with `$ErrorActionPreference = 'Stop'` inside the script), leaving the log
explaining why. The scheduled task retries at most twice, 5 minutes apart,
then stops - no retry storm.

## 9. Config reference

| Key | Default | Meaning |
| --- | --- | --- |
| `Mode` | `observe` | `observe`, `safe-auto`, `allowlisted-close` |
| `SampleIntervalSeconds` | 30 | normal sampling period |
| `AdaptiveSampling` | true | sample faster under actionable pressure |
| `BusyIntervalSeconds` | 15 | the faster period |
| `WarnAvailableMiB` | 1536 | low-available threshold |
| `CriticalAvailableMiB` | 819 | critical-available threshold |
| `WarnCommitPercent` | 80 | elevated commit threshold |
| `CriticalCommitPercent` | 92 | critical commit threshold (actionable alone) |
| `PagingWarnPagesInputPerSec` | 500 | hard-paging threshold |
| `HysteresisMiB` / `HysteresisCommitPercent` | 256 / 5 | de-escalation margin |
| `CooldownSeconds` | 300 | minimum time between actions |
| `LogDirectory` | `logs` | relative to the repo or absolute |
| `LogMaxBytes` / `LogMaxFiles` | 1048576 / 5 | rotation |
| `LowDiskFreeMiB` | 500 | logging floor |
| `TopProcessCount` | 10 | how many working sets to report |
| `IncludeCommandLine` | true | needed for build/install detection |
| `IncludeIoCounters` | true | per-process IO trend |
| `EnableEcoQoS` | false | enable lever (a) |
| `GracefulCloseTimeoutSeconds` | 15 | wait after `CloseMainWindow` |
| `AllowlistClose` | `[]` | apps approved for graceful close |
| `SuggestionOnlyProcesses` | list | report-only apps |
| `ExtraProtectedProcesses` | `[]` | your own never-touch list |
| `Profiles.Active` | `default` | recorded in state for future profiles |

Validation (`Test-SRGConfig`) rejects: unknown mode, out-of-range intervals,
`critical >= warn`, inverted commit thresholds, non-array lists, a name that is
both protected and close-allowlisted, `EnableAggressiveTrim: true`, and a bad
close timeout. A config that fails validation is never installed and never run:
`install.ps1` validates first, and `approve.ps1` refuses to write a config that
would fail.

Profiles (`default`, `dev`, `gaming`, `installation`) are recorded and
reserved; only `default` has behaviour wired today, and `dev`/`gaming`/
`installation` protection is effectively active already through the protected
list and sensitive-operation detection.

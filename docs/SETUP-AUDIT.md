# Newcomer setup audit

Audited 2026-11-16 against the README and `docs/HOW_IT_WORKS.md`. No project setup
command, development dependencies, credentials, or environment variables were
preconfigured. This audit distinguishes observed command failures from source
inspection and Windows-only checks; it is not a claim of testing on a physical
clean Windows installation.

## Original path, in order

| Step | Result / evidence | Correction |
| --- | --- | --- |
| Obtain source | README began with `Set-Location .\SmartRAM-Guardian` but did not explain clone/download or require Git. | Added clone URL, ZIP alternative, directory and shell checks. |
| Select shell | README named Windows PowerShell 5.1 but did not distinguish it from PowerShell 7/WSL. Audit host is Ubuntu 24.04; `powershell -NoProfile -File ./diagnose.ps1` returned `powershell: command not found`. | Made Windows/Desktop 5.1 support explicit. Linux cannot verify Windows memory APIs or scheduled tasks. |
| Execute first script | No execution-policy or downloaded-file instructions. A clean Windows machine may reject local or internet-marked scripts; this was identified by inspection, not reproduced on Linux. | Added process-scoped `RemoteSigned`, trusted-ZIP unblocking, and Group Policy guidance without elevation or permanent policy changes. |
| Diagnose / install / start / status | Windows-only live commands could not run on the audit host. Original path made task registration precede manual startup and did not wait for first state. | Documented task-free smoke test, expected output, sampling delay, logs, single-pass troubleshooting, and optional task lifecycle. |
| Launch policy / task ownership | Source inspection found child shells use process-local `Bypass`, and install/uninstall replace/remove any task with the fixed project name. | Disclosed launcher policy and added a pre-install task collision check; Group Policy still takes precedence. |
| Run tests | Development section named a script but gave no test command, module installation steps, compatibility constraint, or success criteria. “No downloads” applied only to runtime, not development. | Added runnable tests-only and CI-equivalent instructions, pinned module versions, and explicit no-build explanation. |
| Select Pester | Runner found the newest module but did not import that selection or reject incompatible Pester 5. | Explicitly select/import Pester 3.4.x/4.x and report an actionable error otherwise. CI installs 3.4.0 explicitly instead of relying on a bundled module. |
| Inspect analyzer result | Source inspection found scalar/null `.Count` use under strict mode and no failing exit code for analyzer findings; an empty test path could also appear successful. | Collect analyzer output as an array, propagate findings to exit code 1, reject zero tests, add runner regression tests. |
| Existing Windows CI | Run `42656865967` failed with `PSScriptAnalyzer reported 21 issue(s).` Its Pester step was skipped. | Fixed actual unused variables and empty catches, and documented narrow false-positive/API-compatibility suppressions rather than disabling analyzer rules globally. CI now uses the documented runner. |

## Follow-up Windows CI findings

The first updated Windows run (`43805159701`) reached all 75 tests and reported
69 passes, 6 failures, and no analyzer findings. The live priority test assumed
every child starts at memory priority 5; this runner's child started at 2. The
test now reads the initial priority, lowers relative to it, and verifies restoring
that exact value (also restoring in cleanup). This preserves the native round-trip
assertions instead of assuming a particular host's inherited process priority.
The five new child-runner regression tests failed while Windows PowerShell 5.1
converted child stderr to terminating test errors; they now capture output through
`Start-Process` and assert its exit code. Runner paths are captured before loading
test modules rather than recomputed from automatic variables afterward.

## Verification boundaries

A temporary PowerShell 7 installation on Linux was used only for syntax/static
checks and portable regression tests, not as evidence of runtime support. Trying
Pester 3.4.0 there failed during import because `Get-WmiObject` is unavailable;
Pester 4.10.1 was used for portable tests. A broader Linux subset reported
55 passed and 5 failed: one needs a Windows service process, and four need
`Get-ScheduledTask`. These are unsupported-platform failures, not waived Windows
checks. The isolated runner (5 tests) and config (10 tests) suites passed. The supported Windows PowerShell 5.1
path, pinned Pester 3.4.0 suite (including its real observe-mode pass), and static
analysis are exercised by the pull request's Windows CI run. Review that run for
the current result, not the historical benchmark/test counts in the README.

Interactive first-run policy prompts and manual logon-task registration were not
reproduced on a clean Windows desktop. They remain explicitly documented manual
checks rather than claimed results. CI needs no scheduled task or application
approvals and does not enable memory-management actions.

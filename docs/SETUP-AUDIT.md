# Setup audit and migration reconciliation

## Scope

The initial 2026-11-16 audit examined v0.1.0, a Windows PowerShell 5.1 application.
While this PR was open, base commit `3bc9b06` replaced it with the cross-platform
Python v1.0.0 application. This merge preserves that replacement and its Python
CI; it does not resurrect deleted PowerShell scripts or Pester tests. The setup
guide has been rewritten for the current source. The findings below distinguish
historical evidence from current instructions.

## Current newcomer path

| Step | Gap / correction |
| --- | --- |
| Obtain source | Preserve upstream clone/installer commands and add a ZIP alternative and an explicit repository-root check. |
| Python prerequisites | State Python >=3.8, pip/venv, platform interpreter names, and CI-tested versions. |
| Install locally | Upstream `python -m pip install .` did not isolate dependencies or explain externally managed system Python. Add per-platform venv commands without activation/policy changes. |
| Run tests | Add explicit installation of pytest and `python -m pytest -q`; package installation alone does not install the test tool. |
| First run | v1.0.0 defaults to auto, not observe. Document a non-closing scan and optional isolated observe-mode smoke test; do not carry forward the old safety/default claims. |
| Desktop pet | Explain tkinter/display requirements, headless checks, and the distinction between pytest and GUI verification. |
| Installer side effects | Explain immediate startup, login registration, optional dependency installers, source/home overrides, and uninstall's data deletion. |
| Updating and diagnostics | Give venv-specific upgrade commands, logs/state location, and common interpreter/PATH/venv/network remedies. |

## Historical v0.1.0 findings (not current setup instructions)

The original README omitted clone/ZIP instructions, execution-policy handling,
test-tool installation, compatibility versions, and success criteria. The Linux
audit host could not run the original command (`powershell: command not found`).
Pester 3.4 import on Linux failed because `Get-WmiObject` was unavailable.
Source inspection also found newest-Pester selection without compatible import,
analyzer result/exit-code problems, and a zero-test success path.

Original Windows CI run `42656865967` stopped at 21 analyzer findings before tests.
The PR fixed those issues and the native test's assumption that inherited process
memory priority is always 5. Windows run `43805713433` then passed 75/75 Pester
tests with no analyzer findings. Those scripts and fixes are now superseded by
the base branch's deliberate deletion of the PowerShell implementation.

## Current verification limits

The merge resolution is checked with the current Python suite in an isolated
Linux venv. Cross-platform tests, widget checks, and installer checks remain in
the upstream CI matrix; consult this PR's latest CI run for its current result.
The historical Windows/Pester result above is not proof of the Python migration.
Interactive desktop prompts, a clean physical Windows/macOS installation, and
login behavior are not claimed as verified in this sandbox.

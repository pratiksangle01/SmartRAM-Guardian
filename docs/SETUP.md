# Source setup and troubleshooting

These instructions describe the **v1.0.0 Python implementation**, not the retired
Windows PowerShell guardian. The application supports Linux, macOS, and Windows.
PowerShell/Pester scripts from v0.1.0 are no longer needed.

## Prerequisites and checkout

Install Git and Python **3.8 or newer**, including pip and venv. CI tests Python
3.12 on Linux, Windows, and macOS, plus Python 3.8 on Linux. Internet access is
needed to obtain the source and install packages. Use a writable user directory,
not an administrator/root shell.

```sh
git clone https://github.com/pratiksangle01/SmartRAM-Guardian.git
cd SmartRAM-Guardian
```

Without Git, download **Code > Download ZIP** from GitHub, extract it, and open a
terminal in the extracted directory containing `pyproject.toml`. Git update
commands do not apply to ZIP downloads.

## Isolated install

Linux/macOS:

```sh
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install . pytest
.venv/bin/python -m pytest -q
```

Windows PowerShell:

```powershell
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install . pytest
.\.venv\Scripts\python.exe -m pytest -q
```

If Windows has `python` rather than the `py` launcher, substitute `python` for
`py -3` after checking `python --version` is at least 3.8. A Store alias that opens
a download page is not an installed interpreter. No activation script is needed,
so these commands require no execution-policy changes. The test extra is installed
explicitly as `pytest`; it is not a runtime dependency in `pyproject.toml`.

There is no separate compile/build command. `pip install .` builds and installs
the package and its `psutil` dependency. Expect a nonzero pytest test count and
zero failures; the current suite has 20 test cases. Tests include background
start/status/stop using a temporary application home, without enabling autostart.

## Safe smoke test

The current default mode is **auto**, not the old PowerShell observe default.
Do not launch the normal daemon just to verify installation if you do not want
its default behavior. The scan command reports candidates without closing apps:

```sh
.venv/bin/python -m smartram --help
.venv/bin/python -m smartram scan
```

On Windows use `.\.venv\Scripts\python.exe` instead of `.venv/bin/python`.
Scan creates the application data directory but does not register login startup.
For an optional persistent observe-mode run, point `SMARTRAM_HOME` at a new,
absolute, writable directory and create its `config.json` containing
`{"mode": "observe", "widget": false}` before starting. For example on Linux/macOS:

```sh
export SMARTRAM_HOME="$PWD/.smartram-smoke"
mkdir -p "$SMARTRAM_HOME"
printf '%s\n' '{"mode":"observe","widget":false}' > "$SMARTRAM_HOME/config.json"
.venv/bin/python -m smartram start --no-widget
sleep 8
.venv/bin/python -m smartram status
.venv/bin/python -m smartram stop
```

On Windows set `$env:SMARTRAM_HOME` instead of `export`, use PowerShell's
`New-Item`/`Set-Content` to create the directory/config, and `Start-Sleep -Seconds 8`.
Do not overwrite an existing configuration. Expect status to show observe mode
and a recent sample. Logs and state live under `SMARTRAM_HOME` (default
`~/.smartram-guardian`): `guardian.log`, `state.json`, and `config.json`.

## Desktop pet and installers

The pet uses tkinter and a graphical desktop. Headless CI/sandboxes can run tests,
scan, or `start --no-widget` without a display. To check Tk on a desktop:

```sh
.venv/bin/python -m tkinter
.venv/bin/python -m smartram widget-test
```

If tkinter or venv is missing on Debian/Ubuntu, install the packages matching your
Python (commonly `python3-tk` and `python3-venv`) through your approved system
package manager. A separately installed Python may need its matching Tk package.
The installers try to obtain missing dependencies, which may invoke sudo on
Linux or winget on Windows; review the scripts before running them.

The README's one-line installers download code, install dependencies, enable
per-user login startup, and immediately start the guardian. They are not a
read-only check. For a reviewed local checkout use `sh install.sh` on Linux/macOS
or `.\install.ps1` in Windows PowerShell, following organizational execution policy.
`SMARTRAM_SOURCE` overrides the source installed (including a local checkout);
`SMARTRAM_HOME` overrides the installation/data location. Use the same home when
issuing subsequent commands. To undo login startup use `smartram disable`.
`smartram uninstall` stops the guardian, disables autostart, and **deletes its
application data directory**, including configuration and logs.

## Common failures

- **`python`/`pip` not found:** select the installed interpreter as above and use
  `python -m pip`, not a potentially unrelated `pip` executable.
- **Externally managed environment / permission denied:** install into the venv,
  not system Python. Do not use sudo pip or disable system-package protections.
- **Missing venv/ensurepip:** install your distribution's matching venv package,
  then recreate the environment.
- **`smartram` not found after install:** use the venv interpreter with
  `-m smartram`, or its `bin/smartram` (`Scripts\smartram.exe` on Windows).
  The Unix installer also creates `~/.local/bin/smartram`; that directory must be
  on PATH to use the short command outside the venv.
- **No display or Tk:** use `--no-widget` for daemon checks. CI uses Xvfb on Linux
  for the separate widget check; a successful headless test is not GUI proof.
- **Dependency download fails:** check approved network/proxy/package-index
  configuration. No project API keys or credentials are needed.

After source updates, rerun the same venv's `python -m pip install --upgrade .`
and `python -m pytest -q`. Use `git pull` only from a clean Git checkout.

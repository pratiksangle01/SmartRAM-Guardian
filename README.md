<h1 align="center">🐬 SmartRAM Guardian</h1>

<p align="center">
  <b>A tiny desktop dolphin that frees RAM for you.</b><br>
  It notices which app you are working in, quietly closes idle background junk,<br>
  and shows exactly what it closed and how much memory it saved.
</p>

<p align="center">
  <a href="https://github.com/pratiksangle01/SmartRAM-Guardian/actions/workflows/ci.yml"><img src="https://github.com/pratiksangle01/SmartRAM-Guardian/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/pratiksangle01/SmartRAM-Guardian/releases"><img src="https://img.shields.io/github/v/release/pratiksangle01/SmartRAM-Guardian?color=38bdf8" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/Windows%20%7C%20macOS%20%7C%20Linux-supported-38bdf8" alt="Windows, macOS and Linux">
  <img src="https://img.shields.io/badge/python-3.8%2B-38bdf8" alt="Python 3.8+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-38bdf8" alt="MIT license"></a>
</p>

<p align="center">
  <img src="docs/demo.gif" width="420" alt="The dolphin sits in the corner of the screen; one click opens a panel with RAM use, RAM saved, what was closed and what was kept">
  <br><sub>Click the dolphin to open the panel, click again to tuck it away. Picture uses sample data.</sub>
</p>

## Install

**Windows** (PowerShell)

```powershell
irm https://raw.githubusercontent.com/pratiksangle01/SmartRAM-Guardian/main/install.ps1 | iex
```

**macOS**

```sh
curl -fsSL https://raw.githubusercontent.com/pratiksangle01/SmartRAM-Guardian/main/install.sh | sh
```

**Linux**

```sh
curl -fsSL https://raw.githubusercontent.com/pratiksangle01/SmartRAM-Guardian/main/install.sh | sh
```

The installer needs no admin rights. It sets up a private Python environment in
`~/.smartram-guardian`, starts the guardian, makes it start at login and puts the
`smartram` command on your PATH (set `SMARTRAM_NO_MODIFY_PATH=1` first to skip that).
Python 3.8 or newer is required; on Windows the installer gets it with `winget` if it is missing.

**With Git**

```sh
git clone https://github.com/pratiksangle01/SmartRAM-Guardian.git
cd SmartRAM-Guardian
python -m pip install .
python -m smartram start
```

Update a Git install with `git pull` and `python -m pip install --upgrade .`.
Update an installer install by running the install command again.

## What it does

- **Always on, always visible.** A small dolphin stays in the corner of your screen and swims gently, so you know the guardian is alive. Drag it anywhere.
- **One click shows everything.** RAM in use, RAM saved, what was closed, what was kept and why.
- **Knows what you are working in.** The app in front of you and everything it started are never touched.
- **Closes background junk on its own.** Idle updaters, crash handlers and similar helpers are closed when memory gets tight.
- **Tells you what else could go.** Big apps that have sat idle in the background for a while are listed with an **Allow** button. One click, one confirmation, and the guardian closes that app now and whenever it idles again.
- **Clean now.** One button (or `smartram clean`) closes idle junk immediately.
- **Careful by design.** System, security, terminal, editor, build and container processes are protected. Nothing is closed while an installer runs.
- **Light on your machine.** It rests when there is nothing to do, saves power on battery and slows itself down if it ever uses too much CPU.
- **Private.** The guardian never connects to the network and collects nothing.

## Commands

```sh
smartram status          # RAM saved, what was closed, what was kept
smartram clean           # close idle background junk now
smartram allow <app>     # also close this app when it idles in the background
smartram protect <app>   # never close this app
smartram forget <app>    # remove an app from both lists
smartram pause           # stop closing things
smartram resume
smartram start           # run in the background
smartram stop
smartram enable          # start at login
smartram disable
smartram scan            # show what the guardian sees, close nothing
smartram doctor          # check what works on this machine
smartram uninstall
```

Open a new terminal after installing and the `smartram` command is there.
With a Git install, use `python -m smartram <command>`.

## How it decides

An app is closed only when **all** of these are true:

1. It is on the built-in list of background junk, or you allowed it (the **Allow** button or `smartram allow`).
2. It is not protected, and it is not the app in front of you or anything that app started.
3. It has been quiet in the background for several checks in a row and has been running for at least a minute.
4. RAM use is above the threshold (60% by default), or you asked for a clean.
5. No installer is running, and the guardian is not paused.

An app is judged as a whole. If any of its helper processes is busy, the app stays;
when it goes, it is closed through its main process and the helpers go with it.

Apps are asked to close the polite way: a window-close message on Windows, a
terminate signal on macOS and Linux. On Windows an app that shows no window is
ended directly. An app that keeps coming back is left alone after three tries.

The panel shows the reason for everything that stays: `in use`, `protected`,
`busy`, `not on close list`, `too small`, `just started`, `focus unknown`,
`keeps coming back` or `no permission`.

## Light on your machine

A memory tool should not be the thing using your memory.

- **Rests when nothing can be done.** A full look at every process happens every 30 seconds; in between it only reads one number, the memory in use. It looks closely every 5 seconds only when RAM is nearly full, something is being closed, or the panel is open.
- **Battery saver.** On battery it scans half as often and the dolphin swims at half speed.
- **Self-limit.** It measures its own CPU use and checks less often if it ever goes over its budget (2% of one core by default).
- **Low priority.** It runs behind every other app, and in efficiency mode on Windows.
- **Draws only what changed.** The pet and the panel are repainted only when something is different.

`smartram status` and the panel show what the guardian itself is using.

| System | CPU (of one core) | Memory |
| --- | --- | --- |
| Windows | 0.35% | 34 MB |
| macOS | 0.58% | 93 MB |
| Linux | 0.13% | 31 MB |

Measured at v1.1.0 with the dolphin on screen, on GitHub's hosted test machines
(140 to 535 running processes). Every change is measured again, and the build
fails above 5% of a core or 250 MB. Measure your own machine with `python tools/footprint.py`.

## Settings

Settings live in `~/.smartram-guardian/config.json` and are picked up without a restart.

| Setting | Default | Meaning |
| --- | --- | --- |
| `mode` | `auto` | `observe` only reports what it would close |
| `act_above_percent` | `60` | close things only when RAM use is above this |
| `interval_seconds` | `5` | how often memory is checked |
| `calm_interval_seconds` | `30` | how often every process is scanned when nothing is urgent |
| `idle_cpu_percent` | `1.0` | below this a process counts as quiet |
| `idle_samples` | `3` | quiet scans in a row before a process may be closed |
| `min_process_mb` | `10` | ignore processes smaller than this |
| `close_timeout_seconds` | `10` | how long a process gets to exit |
| `retry_minutes` | `10` | wait before trying a returning process again |
| `max_retries` | `3` | give up on a process that keeps coming back |
| `self_cpu_limit_percent` | `2.0` | the guardian's own CPU budget |
| `allow_without_focus` | `false` | close your `allow` apps even when the app in use cannot be detected |
| `close_builtin_junk` | `true` | `false` closes only the apps you allowed yourself |
| `widget` | `true` | show the dolphin |
| `close_list` | `[]` | apps you allowed |
| `protect_list` | `[]` | apps added with `smartram protect` |

## Questions

**Will it close my browser, editor or terminal?**
No. Only built-in background junk and apps you allow yourself are ever closed, and never the app you are using.

**Can I lose unsaved work?**
Built-in junk has no documents. An app you allow is asked to close: on Windows it can still show its "save changes?" question, on macOS and Linux it usually just quits. Allow only apps that are safe to close when idle.

**It did not close something. Why?**
Open the panel or run `smartram status`; every kept app shows its reason.

**Does it work on Wayland?**
The app in use is detected on X11, Sway, Hyprland and KDE (with `kdotool`). GNOME on Wayland does not reveal it, so there only built-in junk is closed and your `allow` apps wait, unless you set `allow_without_focus`. `smartram doctor` tells you what your session supports.

**There is no dolphin.**
The pet needs a desktop session and Python's `tkinter`. Without them the guardian still runs; use `smartram status`. `smartram doctor` shows which one is missing.

**How do I remove it?**
`smartram uninstall` stops it, removes it from startup and from your PATH, and deletes its own files.

## Contributing

Bug reports, ideas and new background-junk names are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
Changes are listed in the [changelog](CHANGELOG.md).

If SmartRAM Guardian saved you some memory, a ⭐ helps other people find it.

## License

[MIT](LICENSE)

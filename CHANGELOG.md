# Changelog

## 1.2.0

- Fixed: on Ubuntu nothing was ever closed, because a service that is always running there counted as an installer in progress. The same happened on Windows while Windows servicing was active.
- Fixed: an allowed app with several processes could have its idle helpers closed while the app itself was busy. An app is now judged as a whole and closed through its main process; the memory of its helpers is counted too. A different program that app started (a game from a launcher) is never asked to close and is not counted as saved; on Windows a launcher with no window of its own stays while such a program has a window open.
- Fixed: the `smartram` command now works in a new terminal after installing on Windows and macOS. Uninstall undoes this.
- Fixed: on macOS only the status dot was visible while the panel was closed; the dolphin is back. On Windows it no longer has a dark fringe on light backgrounds.
- Fixed: on Windows a click on the taskbar or the desktop made every app count as "in use" for that moment, which kept resetting how long apps had been idle.
- Lighter on Windows: a scan no longer asks for the state of every process, which is slow there.
- Suggestions have an **Allow** button with a confirmation, list only whole apps (never system components or another app's helpers), and show the app's full size.
- The panel shows one row per app instead of one per process.
- Safer focus detection: Store apps on Windows are recognised as the app in use, and a window that belongs to another account no longer counts.
- Uninstall deletes only the guardian's own files, and finishes by itself on Windows.
- Update notifiers and the backup scheduler are no longer treated as junk on Linux.
- New setting `close_builtin_junk`.

## 1.1.0

- The guardian is lighter on the machine: it scans every process only every 30 seconds and reads just the memory figure in between, scans closely only when RAM is nearly full, something is closing or the panel is open, halves its activity on battery, lowers its own priority, and slows down by itself if it goes over its CPU budget.
- The pet and the panel are redrawn only when something changed.
- New **Clean now** button and `smartram clean` command.
- The panel and `smartram status` list big apps that have been idle in the background, with the command to let the guardian close them, and show what the guardian itself is using.
- New `smartram doctor` command reports what works on this machine.
- Safer closing: an app you just switched away from is no longer closed the moment it loses focus, and when the app in use cannot be detected only built-in junk is closed.
- The app in use is now detected on Sway, Hyprland and KDE Wayland (with `kdotool`).

## 1.0.0

- Rewritten as one Python app for Windows, macOS and Linux.
- Desktop pet that expands into a status panel; the pet is a dolphin.
- Automatic closing of idle background junk, with protection for the app in use.
- One-line installers with start at login.

## 0.1.0

- Windows-only PowerShell guardian.

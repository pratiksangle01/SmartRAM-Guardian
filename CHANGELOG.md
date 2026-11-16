# Changelog

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

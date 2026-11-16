#!/bin/sh
# SmartRAM Guardian installer for Linux and macOS.
set -eu

SOURCE="${SMARTRAM_SOURCE:-https://github.com/pratiksangle01/SmartRAM-Guardian/archive/refs/heads/main.zip}"
HOME_DIR="${SMARTRAM_HOME:-$HOME/.smartram-guardian}"
VENV="$HOME_DIR/venv"

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 &&
        "$c" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
        PY="$c"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "Python 3.8 or newer is required. Install it, then run this again." >&2
    exit 1
fi

# Best effort: system packages for the venv, the desktop pet and focus detection.
pkg_install() {
    SUDO=""
    [ "$(id -u)" -eq 0 ] || SUDO="sudo"
    if command -v apt-get >/dev/null 2>&1; then
        $SUDO apt-get install -y python3-venv python3-tk xdotool
    elif command -v dnf >/dev/null 2>&1; then
        $SUDO dnf install -y python3-tkinter xdotool
    elif command -v pacman >/dev/null 2>&1; then
        $SUDO pacman -S --needed --noconfirm tk xdotool
    elif command -v zypper >/dev/null 2>&1; then
        $SUDO zypper install -y python3-tk xdotool
    fi
}

mkdir -p "$HOME_DIR"
if [ "$(uname -s)" = "Linux" ]; then
    if ! "$PY" -c 'import tkinter, ensurepip' 2>/dev/null || ! command -v xdotool >/dev/null 2>&1; then
        pkg_install || echo "Could not install optional system packages; continuing." >&2
    fi
fi

"$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet --upgrade "$SOURCE"

"$VENV/bin/smartram" stop >/dev/null 2>&1 || true
"$VENV/bin/smartram" enable
"$VENV/bin/smartram" start

if ! "$VENV/bin/python" -c 'import tkinter' 2>/dev/null; then
    echo "Note: Python has no tkinter here, so the guardian runs without the desktop pet."
fi
"$VENV/bin/smartram" link

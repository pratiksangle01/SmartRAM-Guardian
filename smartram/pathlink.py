"""Make the `smartram` command work in new terminals, and undo that on uninstall."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

MARK = b"# added by SmartRAM Guardian"


def _program():
    """The installed `smartram` program that sits next to this Python, or None."""
    name = "smartram.exe" if sys.platform == "win32" else "smartram"
    path = Path(sys.executable).with_name(name)
    return path if path.exists() else None


# ---- Windows: a small folder on the user's PATH ---------------------------------

def _windows_path(directory, add):
    """Add or remove a folder in the current user's PATH (no admin rights needed)."""
    import winreg

    def same(entry):
        return os.path.normcase(os.path.normpath(os.path.expandvars(entry))) == \
            os.path.normcase(os.path.normpath(directory))

    access = winreg.KEY_READ | winreg.KEY_SET_VALUE
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, access) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except OSError:
            value, kind = "", winreg.REG_EXPAND_SZ
        parts = [p for p in value.split(";") if p]
        wanted = [p for p in parts if not same(p)] + ([directory] if add else [])
        if wanted == parts:
            return
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(wanted))
    _announce_environment_change()


def _announce_environment_change():
    """Tell running programs (Explorer, so new terminals) that PATH changed."""
    import ctypes
    from ctypes import wintypes
    send = ctypes.windll.user32.SendMessageTimeoutW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
                     wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    # HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG
    send(0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000, ctypes.byref(ctypes.c_size_t()))


def _link_windows(home, program):
    bin_dir = Path(home) / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    command = bin_dir / program.name
    try:
        # The launcher pip writes finds Python by its full path, so a copy runs from anywhere.
        shutil.copy2(str(program), str(command))
    except OSError:
        if not command.exists():
            raise
    if os.environ.get("SMARTRAM_NO_MODIFY_PATH"):
        return "Command: %s" % command
    _windows_path(str(bin_dir), add=True)
    return "Command: smartram (open a new terminal first)"


# ---- macOS and Linux: a link in ~/.local/bin, on PATH through the shell profile ----

def _bin_dir():
    return Path.home() / ".local" / "bin"


def _fish_file():
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "fish" / "conf.d" / "smartram.fish"


def _profile_for(shell, platform=None):
    """The file the user's shell reads at start, chosen so nothing else stops being read."""
    home, name = Path.home(), os.path.basename(shell or "")
    if name == "zsh":
        return Path(os.environ.get("ZDOTDIR") or str(home)) / ".zshrc"
    if name == "bash":
        if (platform or sys.platform) != "darwin":
            return home / ".bashrc"
        # A new ~/.bash_profile would hide an existing ~/.profile from bash.
        for candidate in (".bash_profile", ".bash_login"):
            if (home / candidate).exists():
                return home / candidate
    return home / ".profile"


def _all_profiles():
    home = Path.home()
    files = [home / n for n in (".zshrc", ".bashrc", ".bash_profile", ".bash_login", ".profile")]
    if os.environ.get("ZDOTDIR"):
        files.append(Path(os.environ["ZDOTDIR"]) / ".zshrc")
    return files


def _link_posix(program):
    bin_dir = _bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "smartram"
    if link.is_symlink():
        link.unlink()
    if link.exists():
        return "Command: %s (%s is another program, left alone)" % (program, link)
    link.symlink_to(program)
    if str(bin_dir) in os.environ.get("PATH", "").split(os.pathsep):
        return "Command: smartram"
    if os.environ.get("SMARTRAM_NO_MODIFY_PATH"):
        return "Command: %s" % link
    shell = os.environ.get("SHELL", "")
    if os.path.basename(shell) == "fish":
        profile, line = _fish_file(), b'set -gx PATH "$HOME/.local/bin" $PATH  ' + MARK + b"\n"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_bytes(line)
    else:
        profile, line = _profile_for(shell), b'export PATH="$HOME/.local/bin:$PATH"  ' + MARK + b"\n"
        old = profile.read_bytes() if profile.exists() else b""
        if MARK not in old:
            with open(str(profile), "ab") as f:
                f.write((b"" if not old or old.endswith(b"\n") else b"\n") + line)
    return "Command: smartram (open a new terminal first; %s now puts ~/.local/bin on PATH)" % profile


def _unlink_posix():
    program, link = _program(), _bin_dir() / "smartram"
    # Only undo what this installation set up.
    if program is None or not link.is_symlink() or \
            os.path.realpath(str(link)) != os.path.realpath(str(program)):
        return
    link.unlink()
    try:
        _fish_file().unlink()
    except OSError:
        pass
    for profile in _all_profiles():
        try:
            lines = profile.read_bytes().splitlines(True)
            kept = [line for line in lines if MARK not in line]
            if kept != lines:
                profile.write_bytes(b"".join(kept))
        except OSError:
            pass


def link(home):
    """Make `smartram` a command; returns a sentence telling the user how to run it."""
    program = _program()
    if program is None:
        return "Command: %s -m smartram" % sys.executable
    if sys.platform == "win32":
        return _link_windows(home, program)
    return _link_posix(program)


def unlink(home):
    try:
        if sys.platform == "win32":
            _windows_path(str(Path(home) / "bin"), add=False)
        else:
            _unlink_posix()
    except OSError:
        pass

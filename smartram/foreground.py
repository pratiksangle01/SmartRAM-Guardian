"""Find the PID of the app the user is working in. Returns None when unknown."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys


def _run(cmd):
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=2)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _windows():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def _macos():
    asn = _run(["lsappinfo", "front"])
    if not asn:
        return None
    m = re.search(r"(\d+)\s*$", _run(["lsappinfo", "info", "-only", "pid", asn]))
    return int(m.group(1)) if m else None


def _linux():
    if not os.environ.get("DISPLAY"):
        return None
    if shutil.which("xdotool"):
        out = _run(["xdotool", "getactivewindow", "getwindowpid"])
        if out.isdigit():
            return int(out)
    if shutil.which("xprop"):
        m = re.search(r"(0x[0-9a-fA-F]+)", _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"]))
        if m and int(m.group(1), 16):
            m = re.search(r"=\s*(\d+)", _run(["xprop", "-id", m.group(1), "_NET_WM_PID"]))
            if m:
                return int(m.group(1))
    return None


def foreground_pid():
    try:
        if sys.platform == "win32":
            return _windows()
        if sys.platform == "darwin":
            return _macos()
        return _linux()
    except Exception:
        return None

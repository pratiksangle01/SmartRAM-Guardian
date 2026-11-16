"""Find the PID of the app the user is working in. Returns None when unknown."""
from __future__ import annotations

import json
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


def _user32():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetDesktopWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, enum_proc, wintypes.LPARAM]
    return user32, enum_proc


def window_pid(hwnd):
    import ctypes
    from ctypes import wintypes
    pid = wintypes.DWORD()
    _user32()[0].GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def window_class(hwnd):
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    _user32()[0].GetClassNameW(hwnd, buf, 256)
    return buf.value


def child_window_pid(hwnd, class_name):
    """PID that owns the first window inside hwnd with this class, or None."""
    user32, enum_proc = _user32()
    found = []

    def cb(child, _):
        if window_class(child) == class_name:
            found.append(window_pid(child))
            return False
        return True

    user32.EnumChildWindows(hwnd, enum_proc(cb), 0)
    return found[0] if found else None


def _windows():
    hwnd = _user32()[0].GetForegroundWindow()
    if not hwnd:
        return None
    # Store apps draw inside a shared frame window; the app itself owns a child of it.
    if window_class(hwnd) == "ApplicationFrameWindow":
        pid = child_window_pid(hwnd, "Windows.UI.Core.CoreWindow")
        if pid:
            return pid
    return window_pid(hwnd)


def _macos():
    asn = _run(["lsappinfo", "front"])
    if not asn:
        return None
    m = re.search(r"(\d+)\s*$", _run(["lsappinfo", "info", "-only", "pid", asn]))
    return int(m.group(1)) if m else None


def _pid_tool(tool):
    out = _run([tool, "getactivewindow", "getwindowpid"])
    return int(out) if out.isdigit() else None


def _xprop():
    m = re.search(r"(0x[0-9a-fA-F]+)", _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"]))
    if m and int(m.group(1), 16):
        m = re.search(r"=\s*(\d+)", _run(["xprop", "-id", m.group(1), "_NET_WM_PID"]))
        if m:
            return int(m.group(1))
    return None


def parse_hyprland(text):
    """PID from `hyprctl activewindow -j`."""
    try:
        pid = json.loads(text).get("pid")
    except (ValueError, AttributeError):
        return None
    return pid if isinstance(pid, int) and pid > 0 else None


def parse_sway(text):
    """PID of the focused window in `swaymsg -t get_tree`."""
    try:
        stack = [json.loads(text)]
    except ValueError:
        return None
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("focused") and isinstance(node.get("pid"), int):
            return node["pid"]
        stack.extend(node.get("nodes") or [])
        stack.extend(node.get("floating_nodes") or [])
    return None


def strategy(env=None, platform=None, which=shutil.which):
    """(name, lookup function) for this session, or (None, None) when there is none."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    if platform == "win32":
        return "Windows API", _windows
    if platform == "darwin":
        return "lsappinfo", _macos
    if env.get("WAYLAND_DISPLAY") or env.get("XDG_SESSION_TYPE") == "wayland":
        # X11 tools only see XWayland windows there, so they would report a stale app.
        if env.get("HYPRLAND_INSTANCE_SIGNATURE") and which("hyprctl"):
            return "hyprctl", lambda: parse_hyprland(_run(["hyprctl", "activewindow", "-j"]))
        if env.get("SWAYSOCK") and which("swaymsg"):
            return "swaymsg", lambda: parse_sway(_run(["swaymsg", "-t", "get_tree"]))
        if "KDE" in env.get("XDG_CURRENT_DESKTOP", "") and which("kdotool"):
            return "kdotool", lambda: _pid_tool("kdotool")
        return None, None
    if env.get("DISPLAY"):
        if which("xdotool"):
            return "xdotool", lambda: _pid_tool("xdotool")
        if which("xprop"):
            return "xprop", _xprop
    return None, None


def foreground_pid():
    try:
        lookup = strategy()[1]
        return lookup() if lookup else None
    except Exception:
        return None

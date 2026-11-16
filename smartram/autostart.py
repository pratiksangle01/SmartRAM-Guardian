"""Start the guardian at login (per user, no admin rights needed)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

NAME = "SmartRAMGuardian"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def command():
    exe = Path(sys.executable)
    if sys.platform == "win32":
        # pythonw has no console window.
        windowless = exe.with_name("pythonw.exe")
        if windowless.exists():
            exe = windowless
    return [str(exe), "-m", "smartram", "run"]


def _mac_plist():
    return Path.home() / "Library" / "LaunchAgents" / "com.smartram.guardian.plist"


def _linux_desktop():
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "smartram-guardian.desktop"


def enable():
    """Register autostart; returns a description of where."""
    cmd = command()
    if sys.platform == "win32":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
        return "HKCU\\%s\\%s" % (_RUN_KEY, NAME)
    if sys.platform == "darwin":
        import plistlib
        path = _mac_plist()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "wb") as f:
            plistlib.dump({"Label": "com.smartram.guardian", "ProgramArguments": cmd,
                           "RunAtLoad": True, "LimitLoadToSessionType": "Aqua",
                           "ProcessType": "Background"}, f)
        return str(path)
    path = _linux_desktop()
    path.parent.mkdir(parents=True, exist_ok=True)
    exec_line = " ".join('"%s"' % c if " " in c else c for c in cmd)
    path.write_text("[Desktop Entry]\nType=Application\nName=SmartRAM Guardian\n"
                    "Exec=%s\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n" % exec_line,
                    encoding="utf-8")
    return str(path)


def disable():
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, NAME)
        except OSError:
            pass
        return
    path = _mac_plist() if sys.platform == "darwin" else _linux_desktop()
    try:
        path.unlink()
    except OSError:
        pass


def is_enabled():
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, NAME)
            return True
        except OSError:
            return False
    return (_mac_plist() if sys.platform == "darwin" else _linux_desktop()).exists()

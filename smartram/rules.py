"""Process name rules: what is never touched, what is background junk."""
from __future__ import annotations

import os
import sys

_SUFFIXES = (".exe", ".app", ".bin")


def normalize(name):
    n = (name or "").strip().lower()
    for s in _SUFFIXES:
        if n.endswith(s):
            n = n[: -len(s)]
    return n


# Never closed, even if the user adds them to the close list.
PROTECTED = frozenset(normalize(n) for n in (
    # Windows core / session
    "System", "Idle", "System Idle Process", "Registry", "Secure System", "smss",
    "csrss", "wininit", "winlogon", "services", "lsass", "lsaiso", "svchost",
    "fontdrvhost", "dwm", "sihost", "taskhostw", "RuntimeBroker",
    "ShellExperienceHost", "StartMenuExperienceHost", "explorer", "SearchHost",
    "ctfmon", "conhost", "dllhost", "WmiPrvSE", "spoolsv", "dasHost", "audiodg",
    "AggregatorHost", "Memory Compression", "MemCompression", "SystemSettings",
    # Windows security / servicing
    "MsMpEng", "NisSrv", "SecurityHealthService", "SecurityHealthSystray",
    "MsSense", "SenseIR", "MpCmdRun", "usocoreworker", "msiexec", "TiWorker",
    "TrustedInstaller", "MoUsoCoreWorker",
    # macOS core
    "kernel_task", "launchd", "WindowServer", "loginwindow", "Finder", "Dock",
    "SystemUIServer", "ControlCenter", "NotificationCenter", "coreaudiod",
    "cfprefsd", "mds", "mds_stores", "mdworker", "distnoted", "logd", "opendirectoryd",
    "securityd", "trustd", "syspolicyd", "tccd", "UserEventAgent", "Spotlight",
    "coreservicesd", "launchservicesd", "powerd", "configd", "bluetoothd",
    "airportd", "Terminal", "iTerm2",
    # Linux core / session
    "systemd", "init", "kthreadd", "systemd-journald", "systemd-logind",
    "systemd-udevd", "systemd-resolved", "dbus-daemon", "dbus-broker", "Xorg",
    "Xwayland", "gnome-shell", "gnome-session-binary", "plasmashell", "kwin_x11",
    "kwin_wayland", "ksmserver", "xfce4-session", "xfwm4", "cinnamon", "mate-session",
    "sway", "Hyprland", "i3", "gdm", "gdm3", "sddm", "lightdm", "login", "polkitd",
    "pipewire", "pipewire-pulse", "wireplumber", "pulseaudio", "NetworkManager",
    "wpa_supplicant", "sshd", "cron", "udisksd", "upowerd", "gnome-keyring-daemon",
    "gnome-terminal-server", "konsole", "xterm", "alacritty", "kitty", "wezterm-gui",
    # Shells / terminals / remoting
    "powershell", "powershell_ise", "pwsh", "cmd", "WindowsTerminal", "wt", "bash",
    "zsh", "fish", "sh", "dash", "ssh", "ssh-agent", "OpenConsole", "tmux", "screen",
    "sudo", "su",
    # VMs / containers
    "wsl", "wslservice", "wslhost", "vmwp", "vmcompute", "vmmem", "vmmemWSL",
    "docker", "dockerd", "com.docker.backend", "Docker Desktop", "containerd",
    "podman", "qemu-system-x86_64", "VBoxHeadless", "VirtualBoxVM", "vmware-vmx",
    # Editors / IDEs / AI coding tools
    "Code", "Code - Insiders", "devenv", "rider64", "idea64", "pycharm64",
    "webstorm64", "goland64", "clion64", "studio64", "eclipse", "sublime_text",
    "notepad++", "vim", "nvim", "emacs", "cursor", "Windsurf", "zed", "Xcode",
    "ChatGPT", "codex", "Claude", "claude-code", "Copilot",
    # Build tools / source control / copy tools
    "git", "git-remote-https", "msbuild", "cl", "link", "dotnet", "ninja", "make",
    "cmake", "cargo", "rustc", "node", "gradle", "java", "javac", "go", "gcc",
    "g++", "clang", "ld", "pip", "pip3", "npm", "pnpm", "yarn", "robocopy", "xcopy",
    "teracopy", "rclone", "rsync", "cp", "mv", "dd", "tar",
    # Windows shell parts that host other apps' windows and input
    "ApplicationFrameHost", "TextInputHost", "LockApp", "SearchApp", "SearchUI",
    "Taskmgr", "smartscreen", "backgroundTaskHost", "UserOOBEBroker", "msedgewebview2",
    # Linux desktop parts and input methods
    "xfce4-panel", "xfdesktop", "xfsettingsd", "mate-panel", "caja", "marco",
    "nemo-desktop", "lxqt-panel", "budgie-panel", "kded5", "kded6", "kdeinit5",
    "kwalletd5", "kwalletd6", "ibus-daemon", "fcitx", "fcitx5", "gpg-agent", "gjs",
    "mutter", "openbox", "waybar", "polybar",
    # Databases and servers
    "postgres", "mysqld", "mariadbd", "mongod", "redis-server", "nginx", "httpd",
    "apache2", "sqlservr", "ollama",
    # The guardian itself
    "smartram", "SmartRAM-Guardian",
))

# Known background helpers that hold RAM without doing user-visible work.
JUNK = frozenset(normalize(n) for n in (
    # Windows
    "GoogleUpdate", "GoogleUpdater", "GoogleCrashHandler", "GoogleCrashHandler64",
    "MicrosoftEdgeUpdate", "BraveUpdate", "BraveCrashHandler", "BraveCrashHandler64",
    "opera_autoupdate", "AdobeARM", "armsvc", "acrotray", "AdobeUpdateService",
    "AdobeGCClient", "Adobe Crash Processor", "jusched", "jucheck",
    "software_reporter_tool", "OneDriveStandaloneUpdater", "steamerrorreporter",
    "steamerrorreporter64", "CompatTelRunner", "GameBarPresenceWriter",
    "DropboxUpdate", "ZoomAutoUpdater", "SkypeBackgroundHost", "CCleanerBrowserUpdate",
    # macOS
    "GoogleSoftwareUpdateAgent", "ksfetch", "Keystone", "Microsoft AutoUpdate",
    "Microsoft Update Assistant", "AdobeCRDaemon", "Adobe Crash Reporter",
    "AdobeResourceSynchronizer", "Dropbox Update",
    # Linux (not update notifiers or backup schedulers: closing those has a cost)
    "gnome-software", "snap-store", "apport-gtk", "kerneloops-applet",
))

_JUNK_SUFFIXES = ("crashpad_handler", "crashhandler", "crashhandler64")

# While one of these runs, nothing is closed (an install/upgrade is in progress).
INSTALLERS = frozenset(normalize(n) for n in (
    "msiexec", "setup", "install", "installer",
    "winget", "choco", "scoop", "apt", "apt-get", "aptitude", "dpkg", "dnf", "yum",
    "rpm", "zypper", "pacman", "yay", "paru", "flatpak", "brew", "softwareupdate",
    "do-release-upgr",
))
# Deliberately absent: services that are always or often running without an install
# in progress (Ubuntu's unattended-upgr, Windows' TiWorker and TrustedInstaller).
# Listing them would stop the guardian from ever closing anything.


def is_protected(norm, extra=()):
    return norm in PROTECTED or norm in extra or norm.startswith("python")


def is_junk(norm):
    return norm in JUNK or norm.endswith(_JUNK_SUFFIXES)


def looks_like_app(path, platform=None, windows_dir=None):
    """True when an executable path is an ordinary app rather than a system component."""
    if not path:
        return False
    platform = platform or sys.platform
    if platform == "win32":
        root = (windows_dir or os.environ.get("SystemRoot") or r"C:\Windows").rstrip("\\/")
        return not path.replace("/", "\\").lower().startswith(root.lower() + "\\")
    if platform == "darwin":
        # The main program of an app bundle; helpers and services are nested deeper.
        return (".app/Contents/MacOS/" in path and path.count(".app/") == 1
                and not path.startswith("/System/Library/"))
    return "/libexec/" not in path and not path.startswith(("/usr/sbin/", "/sbin/"))

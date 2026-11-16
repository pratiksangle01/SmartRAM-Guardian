"""Command line entry point: python -m smartram <command>."""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

import psutil

from . import __version__, autostart
from . import config as cfgmod
from .engine import Engine
from .rules import is_protected, normalize
from .widget import fmt_bytes, panel_text


def running_pid(paths):
    """PID of the live guardian, or None."""
    try:
        info = json.loads(paths.pid.read_text(encoding="utf-8"))
        proc = psutil.Process(info["pid"])
        if abs(proc.create_time() - info["create"]) < 2 and proc.pid != os.getpid():
            return proc.pid
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        pass
    return None


def cmd_run(paths, args):
    if running_pid(paths):
        print("SmartRAM Guardian is already running.")
        return 0
    handler = logging.handlers.RotatingFileHandler(
        str(paths.log), maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("smartram").addHandler(handler)
    logging.getLogger("smartram").setLevel(logging.INFO)

    if not paths.config.exists():
        cfgmod.save(paths.config, cfgmod.DEFAULTS)
    try:
        paths.stop.unlink()
    except OSError:
        pass
    cfgmod.write_json(paths.pid, {"pid": os.getpid(),
                                  "create": psutil.Process().create_time()})
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except (ValueError, OSError):
            pass
    engine = Engine(paths)
    try:
        if args.no_widget or not cfgmod.load(paths.config)[0]["widget"]:
            engine.run(stop)
        else:
            from . import widget
            worker = threading.Thread(target=engine.run, args=(stop,), daemon=True)
            worker.start()
            if widget.run(engine, stop):
                stop.set()
            # No display: keep guarding without the pet.
            while worker.is_alive():
                worker.join(0.5)
    finally:
        for f in (paths.pid, paths.stop):
            try:
                f.unlink()
            except OSError:
                pass
    return 0


def cmd_start(paths, args):
    if running_pid(paths):
        print("Already running.")
        return 0
    cmd = autostart.command() + (["--no-widget"] if args.no_widget else [])
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # detached, new process group
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)
    for _ in range(40):
        if running_pid(paths):
            print("SmartRAM Guardian started.")
            return 0
        time.sleep(0.25)
    print("Failed to start; see %s" % paths.log)
    return 1


def cmd_stop(paths, _args):
    pid = running_pid(paths)
    if not pid:
        print("Not running.")
        return 0
    paths.stop.write_text("stop", encoding="utf-8")
    try:
        proc = psutil.Process(pid)
        try:
            proc.wait(8)
        except psutil.TimeoutExpired:
            proc.terminate()
            proc.wait(5)
    except psutil.Error:
        pass
    for f in (paths.stop, paths.pid):
        try:
            f.unlink()
        except OSError:
            pass
    print("Stopped.")
    return 0


def cmd_status(paths, _args):
    pid = running_pid(paths)
    print("SmartRAM Guardian %s: %s" % (__version__, "running (pid %s)" % pid if pid
                                        else "not running"))
    print("Autostart: %s" % ("on" if autostart.is_enabled() else "off"))
    try:
        snap = json.loads(paths.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0 if pid else 3
    t = panel_text(snap)
    print("Status: %s\n%s\n%s\n%s" % (t["status"], t["ram"], t["saved"], t["focus"]))
    print("%s\n%s\nKept running\n%s" % (t["closed_title"], t["closed"], t["kept"]))
    return 0 if pid else 3


def cmd_pause(paths, _args):
    paths.pause.write_text("paused", encoding="utf-8")
    print("Paused. Nothing will be closed until 'smartram resume'.")
    return 0


def cmd_resume(paths, _args):
    try:
        paths.pause.unlink()
    except OSError:
        pass
    print("Resumed.")
    return 0


def _edit_list(paths, name, add_to, remove_from):
    name = normalize(name)
    if not name:
        print("Give a process name, for example: spotify")
        return 2
    if add_to == "close_list" and is_protected(name):
        print("%s is a protected system/work process and is never closed." % name)
        return 2
    cfg, _ = cfgmod.load(paths.config)
    if add_to:
        cfg[add_to] = sorted(set(cfg[add_to]) | {name})
    for key in remove_from:
        cfg[key] = [n for n in cfg[key] if n != name]
    cfgmod.save(paths.config, cfg)
    print("close list: %s\nprotect list: %s" % (", ".join(cfg["close_list"]) or "-",
                                                ", ".join(cfg["protect_list"]) or "-"))
    return 0


def cmd_allow(paths, args):
    return _edit_list(paths, args.name, "close_list", ["protect_list"])


def cmd_protect(paths, args):
    return _edit_list(paths, args.name, "protect_list", ["close_list"])


def cmd_forget(paths, args):
    return _edit_list(paths, args.name, None, ["close_list", "protect_list"])


class _ScanOnly:
    def request(self, _proc):
        return False, 0, "scan only"


def cmd_scan(paths, _args):
    """One look at the system; never closes anything."""
    engine = Engine(paths, closer=_ScanOnly())
    engine.tick()
    time.sleep(1)
    print(json.dumps(engine.tick(), indent=2))
    return 0


def cmd_enable(_paths, _args):
    print("Autostart enabled: %s" % autostart.enable())
    return 0


def cmd_disable(_paths, _args):
    autostart.disable()
    print("Autostart disabled.")
    return 0


def cmd_uninstall(paths, args):
    cmd_stop(paths, args)
    autostart.disable()
    link = os.path.join(os.path.expanduser("~"), ".local", "bin", "smartram")
    if os.path.islink(link):
        os.unlink(link)
    # The running interpreter may live inside this folder (Windows keeps it locked).
    shutil.rmtree(str(paths.home), ignore_errors=True)
    if paths.home.exists():
        print("Stopped and removed from startup. Delete this folder to finish: %s" % paths.home)
    else:
        print("Uninstalled.")
    return 0


def cmd_widget_test(paths, args):
    """Open the pet, expand it, then exit. Used by CI to prove the UI starts."""
    from . import widget
    engine = Engine(paths, closer=_ScanOnly())
    engine.tick()
    if not widget.run(engine, threading.Event(), selftest=args.seconds):
        print("No display available.")
        return 1
    print("Widget OK.")
    return 0


COMMANDS = {
    "run": (cmd_run, "run in this terminal (default)"),
    "start": (cmd_start, "start in the background"),
    "stop": (cmd_stop, "stop the background guardian"),
    "status": (cmd_status, "show RAM saved, what was closed and what was kept"),
    "pause": (cmd_pause, "stop closing things until resume"),
    "resume": (cmd_resume, "continue after pause"),
    "allow": (cmd_allow, "also close this app when it idles in the background"),
    "protect": (cmd_protect, "never close this app"),
    "forget": (cmd_forget, "remove an app from the allow/protect lists"),
    "scan": (cmd_scan, "print what the guardian sees, without closing anything"),
    "enable": (cmd_enable, "start automatically at login"),
    "disable": (cmd_disable, "do not start at login"),
    "uninstall": (cmd_uninstall, "stop, remove autostart and delete data"),
    "widget-test": (cmd_widget_test, argparse.SUPPRESS),
}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="smartram", description="SmartRAM Guardian")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", metavar="command")
    for name, (_, text) in COMMANDS.items():
        p = sub.add_parser(name, help=text)
        if name in ("run", "start"):
            p.add_argument("--no-widget", action="store_true", help="no desktop pet")
        if name in ("allow", "protect", "forget"):
            p.add_argument("name", help="process name, e.g. spotify")
        if name == "widget-test":
            p.add_argument("--seconds", type=float, default=1.5)
    args = parser.parse_args(argv)
    if not args.command:
        args = parser.parse_args(["run"] + list(argv or sys.argv[1:]))
    paths = cfgmod.Paths().ensure()
    return COMMANDS[args.command][0](paths, args)


if __name__ == "__main__":
    sys.exit(main())

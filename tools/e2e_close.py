"""Check on a real machine that the guardian closes a real idle process and keeps a busy one.

usage: python tools/e2e_close.py

Starts throwaway processes with unique names and a private guardian (its own data
folder, built-in junk switched off) that may close only those. Takes about 80 seconds.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from smartram import engine, foreground  # noqa: E402

WINDOWS = sys.platform == "win32"
IDLE, BUSY = "srg-idle", "srg-busy"  # names nothing else on the machine has
FAILED = []


def check(ok, text):
    print("%s: %s" % ("PASS" if ok else "FAIL", text))
    if not ok:
        FAILED.append(text)


def start_victims(work):
    """One process that waits and one that burns CPU, under unique names.

    They are copies of a shell: unlike `sleep` or `yes`, which on some systems are
    one program that looks at its own name, a shell runs under any name.
    """
    if WINDOWS:
        shell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        wait, spin, suffix = ["/k"], ["/c", "for /l %i in (1,0,2) do @rem"], ".exe"
        hidden = dict(creationflags=0x08000000)
    else:
        shell = shutil.which("dash") or shutil.which("bash") or "/bin/sh"
        wait, spin, suffix, hidden = ["-c", "read line"], ["-c", "while :; do :; done"], "", {}
    idle_exe = shutil.copy(shell, os.path.join(work, IDLE + suffix))
    busy_exe = shutil.copy(shell, os.path.join(work, BUSY + suffix))
    quiet = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **hidden)
    idle = subprocess.Popen([idle_exe] + wait, stdin=subprocess.PIPE, **quiet)
    busy = subprocess.Popen([busy_exe] + spin, stdin=subprocess.DEVNULL, **quiet)
    time.sleep(2)
    if idle.poll() is not None or busy.poll() is not None:
        for proc in (idle, busy):
            if proc.poll() is None:
                proc.kill()
        raise SystemExit("could not start the throwaway processes (copies of %s)" % shell)
    return idle, busy


def check_windows_calls():
    """Run the Windows-only window code against real windows."""
    user32 = foreground._user32()[0]
    desktop = user32.GetDesktopWindow()
    print("desktop window class: %r" % foreground.window_class(desktop))
    check(foreground.child_window_pid(desktop, "no-such-window-class") is None,
          "looking inside a window for a Store app finds nothing when there is none")
    print("foreground PID right now: %s" % foreground.foreground_pid())

    notepad = shutil.which("notepad")
    if not notepad:
        print("SKIP: no notepad on this machine, window-close message not tried")
        return
    app = subprocess.Popen([notepad])
    time.sleep(5)
    asked = engine.post_wm_close([app.pid])
    print("windows asked to close: %d" % asked)
    if asked:
        try:
            app.wait(15)
        except subprocess.TimeoutExpired:
            pass
        check(app.poll() is not None, "an app with a window exits when asked politely")
    else:
        print("SKIP: notepad showed no window owned by its own process here")
    if app.poll() is None:
        app.kill()


def main():
    work = tempfile.mkdtemp(prefix="smartram-e2e-")
    home = os.path.join(work, "home")
    os.makedirs(home)
    with open(os.path.join(home, "config.json"), "w") as f:
        json.dump({"act_above_percent": 0, "interval_seconds": 1, "calm_interval_seconds": 1,
                   "min_process_mb": 0, "close_builtin_junk": False, "widget": False,
                   "allow_without_focus": True, "close_list": [IDLE, BUSY]}, f)
    env = dict(os.environ, SMARTRAM_HOME=home, PYTHONPATH=REPO)
    smartram = [sys.executable, "-m", "smartram"]
    state_file = os.path.join(home, "state.json")

    def state():
        try:
            with open(state_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    idle, busy = start_victims(work)
    guard = subprocess.Popen(smartram + ["run", "--no-widget"], env=env, cwd=REPO)
    try:
        if WINDOWS:
            check_windows_calls()
        deadline = time.time() + 150
        while time.time() < deadline and idle.poll() is None:
            time.sleep(1)
        closed_after = 150 - (deadline - time.time())
        while time.time() < deadline + 15 and not state().get("closed_total"):
            time.sleep(1)
        snap = state()
        note = snap.get("note", "")
        if idle.poll() is None and note.startswith("waiting:"):
            print("SKIP: the guardian is %s, so it rightly closed nothing" % note)
            return 0
        check(idle.poll() is not None, "idle process was closed (after %.0f s)" % closed_after)
        check(busy.poll() is None, "busy process with an allowed name was kept")
        closed = snap.get("closed") or [{}]
        check(closed[0].get("name", "").startswith(IDLE) and closed[0].get("bytes", 0) > 0,
              "the panel lists it as closed with the memory it freed: %s" % closed[0])
        kept = {k["name"]: k["reason"] for k in snap.get("kept", [])}
        busy_reason = [why for name, why in kept.items() if name.startswith(BUSY)]
        check(busy_reason == ["busy"], "the panel says why the other one stayed: %s" % busy_reason)
        check(guard.poll() is None, "the guardian is still running")
        subprocess.run(smartram + ["status"], env=env, cwd=REPO)
        subprocess.run(smartram + ["stop"], env=env, cwd=REPO)
        try:
            guard.wait(15)
        except subprocess.TimeoutExpired:
            pass
        check(guard.poll() is not None, "the guardian stops when asked")
    finally:
        for proc in (idle, busy, guard):
            if proc.poll() is None:
                proc.kill()
        log = os.path.join(home, "guardian.log")
        if os.path.exists(log):
            with open(log, encoding="utf-8") as f:
                print(f.read()[-2000:])
        shutil.rmtree(work, ignore_errors=True)
    print("%d check(s) failed" % len(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

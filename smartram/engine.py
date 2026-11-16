"""Sampling, decisions and safe closing of idle background junk."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import namedtuple

import psutil

from . import config as cfgmod
from . import rules
from .foreground import foreground_pid

log = logging.getLogger("smartram")

# cpu is percent of one core since the previous sample (None = unknown).
Proc = namedtuple("Proc", "pid ppid name norm rss cpu create")

MIN_AGE_SECONDS = 60
HISTORY = 30


def make_proc(pid, name, rss=0, cpu=0.0, ppid=1, create=0.0):
    return Proc(pid, ppid, name, rules.normalize(name), rss, cpu, create)


class Sampler:
    """Reads memory and the process table through psutil."""

    def __init__(self):
        self._prev = {}
        self._t = None

    def __call__(self):
        now = time.monotonic()
        dt = (now - self._t) if self._t else None
        self._t = now
        vm = psutil.virtual_memory()
        mem = {"total": vm.total, "available": vm.available, "percent": vm.percent}
        procs, seen = [], {}
        attrs = ["pid", "ppid", "name", "memory_info", "cpu_times", "create_time", "status"]
        for p in psutil.process_iter(attrs):
            i = p.info
            if i.get("status") == psutil.STATUS_ZOMBIE or not i.get("name"):
                continue
            key = (i["pid"], i.get("create_time") or 0.0)
            ct, cpu = i.get("cpu_times"), None
            if ct is not None:
                total = ct.user + ct.system
                prev = self._prev.get(key)
                if prev is not None and dt:
                    cpu = max(0.0, total - prev) / dt * 100.0
                seen[key] = total
            mi = i.get("memory_info")
            procs.append(make_proc(i["pid"], i["name"], mi.rss if mi else 0, cpu,
                                   i.get("ppid") or 0, key[1]))
        self._prev = seen
        return mem, procs


class Closer:
    """Asks a process to exit. Never force-kills an app that has windows."""

    def __init__(self):
        try:
            self._me = psutil.Process().username()
        except Exception:
            self._me = None

    def request(self, proc):
        """Return (requested, bytes that will be freed, reason if not requested)."""
        try:
            p = psutil.Process(proc.pid)
            if abs(p.create_time() - proc.create) > 1:
                return False, 0, "gone"
            if self._me is None or p.username() != self._me:
                return False, 0, "no permission"
            try:
                freed = p.memory_full_info().uss
            except Exception:
                freed = proc.rss
            if sys.platform == "win32" and _post_wm_close(proc.pid):
                return True, freed, ""
            # SIGTERM on POSIX; on Windows only reached for windowless helpers.
            p.terminate()
            return True, freed, ""
        except psutil.NoSuchProcess:
            return False, 0, "gone"
        except (psutil.AccessDenied, OSError):
            return False, 0, "no permission"


def _post_wm_close(pid):
    """Send WM_CLOSE to the visible windows of pid. Returns how many were asked."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                    wintypes.LPARAM]
    found = []

    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(enum_proc(cb), 0)
    for hwnd in found:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
    return len(found)


def active_tree(by_pid, root):
    """PIDs of root, its ancestors and its descendants."""
    if root not in by_pid:
        return set()
    out, pid = {root}, root
    while True:
        parent = by_pid[pid].ppid
        if parent in out or parent not in by_pid:
            break
        out.add(parent)
        pid = parent
    children = {}
    for p in by_pid.values():
        children.setdefault(p.ppid, []).append(p.pid)
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in out:
                out.add(child)
                stack.append(child)
    return out


class Engine:
    def __init__(self, paths, sampler=None, closer=None, foreground=foreground_pid,
                 clock=time.time):
        self.paths = paths
        self.sampler = sampler or Sampler()
        self.closer = closer or Closer()
        self.foreground = foreground
        self.clock = clock
        self.cfg, self._cfg_mtime = dict(cfgmod.DEFAULTS), None
        self._idle = {}       # (pid, create) -> consecutive idle samples
        self._pending = {}    # pid -> close request awaiting exit
        self._attempts = {}   # name -> (ticks with a close request, last time)
        self._denied = set()  # (pid, create) we are not allowed to close
        self._last_fg = None
        self._self = {os.getpid(), os.getppid()}
        self.saved_session = 0
        self.saved_total = 0
        self.closed_total = 0
        self.history = []
        self.snapshot = {}
        self._load_state()

    def _load_state(self):
        try:
            old = json.loads(self.paths.state.read_text(encoding="utf-8"))
            self.saved_total = int(old.get("saved_total", 0))
            self.closed_total = int(old.get("closed_total", 0))
            self.history = [e for e in old.get("closed", []) if isinstance(e, dict)][:HISTORY]
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _reload_config(self):
        try:
            mtime = self.paths.config.stat().st_mtime
        except OSError:
            mtime = 0
        if mtime != self._cfg_mtime:
            self._cfg_mtime = mtime
            self.cfg, warnings = cfgmod.load(self.paths.config)
            for w in warnings:
                log.warning("config: %s", w)

    def _resolve_pending(self, now, by_pid):
        for pid, req in list(self._pending.items()):
            cur = by_pid.get(pid)
            if cur is None or abs(cur.create - req["create"]) > 1:
                del self._pending[pid]
                self.saved_session += req["bytes"]
                self.saved_total += req["bytes"]
                self.closed_total += 1
                self.history.insert(0, {"name": req["name"], "bytes": req["bytes"], "time": now})
                del self.history[HISTORY:]
                log.info("closed %s (pid %s), freed %.0f MB", req["name"], pid,
                         req["bytes"] / 2**20)
            elif now >= req["deadline"]:
                del self._pending[pid]
                log.info("%s (pid %s) did not exit; left running", req["name"], pid)

    def _reason_kept(self, p, active, now, protect, targets, this_tick=()):
        """Why p is not closed right now; '' means it is a close candidate."""
        cfg = self.cfg
        if rules.is_protected(p.norm, protect):
            return "protected"
        if p.pid in active:
            return "in use"
        if p.norm not in targets and not rules.is_junk(p.norm):
            return "not on close list"
        if p.rss < cfg["min_process_mb"] * 2**20:
            return "too small"
        if now - p.create < MIN_AGE_SECONDS:
            return "just started"
        if self._idle.get((p.pid, p.create), 0) < cfg["idle_samples"]:
            return "busy"
        if (p.pid, p.create) in self._denied:
            return "no permission"
        # Several instances may close together; a name that returns is retried
        # a few times, then left alone.
        count, last = self._attempts.get(p.norm, (0, 0))
        if p.norm not in this_tick and count:
            if count >= cfg["max_retries"]:
                return "keeps coming back"
            if now - last < cfg["retry_minutes"] * 60:
                return "retry later"
        return ""

    def tick(self):
        now = self.clock()
        self._reload_config()
        cfg = self.cfg
        mem, procs = self.sampler()
        by_pid = {p.pid: p for p in procs}

        fg = self.foreground()
        # Clicking the pet must not make the guardian itself "the app in use".
        if fg in self._self or fg not in by_pid:
            fg = self._last_fg if self._last_fg in by_pid else None
        self._last_fg = fg
        active = active_tree(by_pid, fg)
        self._resolve_pending(now, by_pid)

        idle = {}
        for p in procs:
            key = (p.pid, p.create)
            idle[key] = self._idle.get(key, 0) + 1 if (
                p.cpu is not None and p.cpu < cfg["idle_cpu_percent"]) else 0
        self._idle = idle

        installing = sorted({p.norm for p in procs} & rules.INSTALLERS)
        paused = self.paths.pause.exists()
        if paused:
            note = "paused"
        elif cfg["mode"] == "observe":
            note = "observe only"
        elif installing:
            note = "waiting: %s is running" % installing[0]
        elif mem["percent"] < cfg["act_above_percent"]:
            note = "RAM is fine"
        else:
            note = ""

        protect, targets = set(cfg["protect_list"]), set(cfg["close_list"])
        kept, would_close, this_tick = [], [], set()
        self._denied &= {(p.pid, p.create) for p in procs}
        for p in procs:
            if p.pid in self._self or p.pid in self._pending:
                continue
            reason = self._reason_kept(p, active, now, protect, targets, this_tick)
            if not reason and note:
                would_close.append(p.name)
                reason = note
            elif not reason:
                ok, freed, why = self.closer.request(p)
                if ok:
                    if p.norm not in this_tick:
                        this_tick.add(p.norm)
                        self._attempts[p.norm] = (self._attempts.get(p.norm, (0, 0))[0] + 1, now)
                    self._pending[p.pid] = {"name": p.name, "bytes": freed, "create": p.create,
                                            "deadline": now + cfg["close_timeout_seconds"]}
                    log.info("asked %s (pid %s) to close", p.name, p.pid)
                    continue
                if why == "gone":
                    continue
                self._denied.add((p.pid, p.create))
                reason = why
            is_target = p.norm in targets or rules.is_junk(p.norm)
            kept.append((is_target, p.rss, p.name, reason))

        # Close-list entries first, then the biggest memory users.
        kept.sort(key=lambda k: (not k[0], -k[1]))
        self.snapshot = {
            "updated": now, "pid": os.getpid(), "mode": cfg["mode"], "paused": paused,
            "note": note or "active", "mem": mem,
            "focus": by_pid[fg].name if fg in by_pid else None,
            "saved_session": self.saved_session, "saved_total": self.saved_total,
            "closed_total": self.closed_total, "closed": list(self.history),
            "closing": [r["name"] for r in self._pending.values()],
            "would_close": sorted(set(would_close)),
            "kept": [{"name": n, "bytes": r, "reason": why} for _, r, n, why in kept[:8]],
        }
        return self.snapshot

    def run(self, stop_event):
        log.info("guardian started (pid %s)", os.getpid())
        while not stop_event.is_set():
            try:
                cfgmod.write_json(self.paths.state, self.tick())
            except Exception:
                log.exception("tick failed")
            deadline = time.monotonic() + self.cfg["interval_seconds"]
            while time.monotonic() < deadline and not stop_event.is_set():
                if self.paths.stop.exists():
                    stop_event.set()
                time.sleep(0.5)
        log.info("guardian stopped")

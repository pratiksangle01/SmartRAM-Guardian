"""Sampling, decisions and safe closing of idle background apps."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
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
SUGGEST_MIN_BYTES = 200 * 2**20
SUGGEST_IDLE_SECONDS = 15 * 60
MAX_SLOWDOWN = 8
RECOVER_SCANS = 5  # light scans in a row before the guardian speeds up again
HIGH_PERCENT = 90  # RAM this full is watched at the fast interval
RAM_FINE = "RAM is fine"
OTHER_OPEN = "has another app open"
WINDOWS = sys.platform == "win32"

# When instances of one app are kept for different reasons, the panel shows the first of these.
_RANK = ("in use", "protected", "busy", "just started", "focus unknown", OTHER_OPEN,
         "no permission", "keeps coming back", "retry later", "too small")


def _rank(reason):
    return _RANK.index(reason) if reason in _RANK else len(_RANK)


def make_proc(pid, name, rss=0, cpu=0.0, ppid=1, create=0.0):
    return Proc(pid, ppid, name, rules.normalize(name), rss, cpu, create)


def read_memory():
    vm = psutil.virtual_memory()
    return {"total": vm.total, "available": vm.available, "percent": vm.percent}


def on_battery():
    try:
        battery = psutil.sensors_battery()
        return battery is not None and battery.power_plugged is False
    except Exception:
        return False


class Sampler:
    """Reads memory and the process table through psutil."""

    ATTRS = ["pid", "ppid", "name", "memory_info", "cpu_times", "create_time"]
    if sys.platform != "win32":
        ATTRS.append("status")  # to skip zombies; Windows has none and the lookup is slow there

    def __init__(self):
        self._prev = {}
        self._t = None

    def __call__(self):
        now = time.monotonic()
        dt = (now - self._t) if self._t else None
        self._t = now
        procs, seen = [], {}
        for p in psutil.process_iter(self.ATTRS):
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
            # Linux reports the part shared with other processes; leaving it out keeps
            # an app with many processes from being counted several times over.
            rss = mi.rss - getattr(mi, "shared", 0) if mi else 0
            procs.append(make_proc(i["pid"], i["name"], rss, cpu,
                                   i.get("ppid") or 0, key[1]))
        self._prev = seen
        return read_memory(), procs


class SelfMeter:
    """CPU and memory used by the guardian itself, including its helper commands."""

    def __init__(self):
        self._proc = psutil.Process()
        self._cpu = self._t = None

    def __call__(self):
        t = self._proc.cpu_times()
        total = (t.user + t.system + getattr(t, "children_user", 0.0)
                 + getattr(t, "children_system", 0.0))
        now, cpu = time.monotonic(), None
        if self._t is not None and now > self._t:
            cpu = (total - self._cpu) / (now - self._t) * 100.0
        self._cpu, self._t = total, now
        return {"cpu": cpu, "rss": self._proc.memory_info().rss}


class Inspector:
    """Slow facts about a process, looked up only when needed and remembered."""

    def __init__(self):
        try:
            self._me = psutil.Process().username()
        except Exception:
            self._me = None
        self._cache = {}

    def _get(self, proc, kind, read):
        key = (proc.pid, proc.create, kind)
        if key not in self._cache:
            if len(self._cache) > 1024:
                self._cache.clear()
            try:
                self._cache[key] = read(psutil.Process(proc.pid))
            except (psutil.Error, OSError):
                self._cache[key] = None
        return self._cache[key]

    def owned(self, proc):
        """True when the process belongs to the current user."""
        return self._me is not None and self._get(proc, "user", lambda p: p.username()) == self._me

    def app_like(self, proc):
        """True when the program is an ordinary app rather than a system component."""
        return rules.looks_like_app(self._get(proc, "exe", lambda p: p.exe()))


def unique_bytes(proc):
    """Memory that only this process holds; its resident size when that is not readable."""
    try:
        return psutil.Process(proc.pid).memory_full_info().uss
    except Exception:
        return proc.rss


def own_pids(proc, family):
    """The app's own processes: its main process and the helpers named after it.

    Something else it started (a game from a launcher, a meeting app opened from a
    browser link) is not part of it.
    """
    return [proc.pid] + [m.pid for m in family if m.norm.startswith(proc.norm)]


class Closer:
    """Asks an app to exit through its main process. Never force-kills an app that has windows."""

    def __init__(self):
        try:
            self._me = psutil.Process().username()
        except Exception:
            self._me = None

    def request(self, proc, family=()):
        """Ask proc to exit; family is the rest of its process tree.

        Returns (requested, reason if not requested, members): members is
        (pid, create, bytes held) for the whole tree, measured just before asking.
        """
        try:
            p = psutil.Process(proc.pid)
            if abs(p.create_time() - proc.create) > 1:
                return False, "gone", []
            if self._me is None or p.username() != self._me:
                return False, "no permission", []
            windows = []
            if WINDOWS:
                own = own_pids(proc, family)
                windows = visible_windows(own)
                # Ending a launcher can take a program it started down with it, so one
                # with no window of its own stays while such a program shows a window.
                if not windows and visible_windows([m.pid for m in family if m.pid not in own]):
                    return False, OTHER_OPEN, []
            members = [(m.pid, m.create, unique_bytes(m)) for m in [proc] + list(family)]
            if windows:
                close_windows(windows)
            else:
                # SIGTERM on POSIX; on Windows only reached when no window is showing.
                p.terminate()
            return True, "", members
        except psutil.NoSuchProcess:
            return False, "gone", []
        except (psutil.AccessDenied, OSError):
            return False, "no permission", []


def visible_windows(pids):
    """Windows: the visible top-level windows that belong to these PIDs."""
    import ctypes
    from ctypes import wintypes
    pids = set(pids)
    if not pids:
        return []
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    found = []

    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(enum_proc(cb), 0)
    return found


def close_windows(windows):
    """Windows: send WM_CLOSE to these windows, as a click on their close button would."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                    wintypes.LPARAM]
    for hwnd in windows:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)


def parent_of(by_pid, proc):
    """The live parent of proc, or None. A 'parent' that started later is a reused PID."""
    parent = by_pid.get(proc.ppid)
    if parent is None or parent.pid == proc.pid or parent.create > proc.create + 1:
        return None
    return parent


def children_map(by_pid):
    out = {}
    for p in by_pid.values():
        if parent_of(by_pid, p) is not None:
            out.setdefault(p.ppid, []).append(p)
    return out


def descendants(children, pid):
    """Every process below pid."""
    out, stack, seen = [], [pid], {pid}
    while stack:
        for child in children.get(stack.pop(), ()):
            if child.pid not in seen:
                seen.add(child.pid)
                out.append(child)
                stack.append(child.pid)
    return out


def main_process(by_pid, proc):
    """The top-most ancestor with the same name: the process that stands for the app."""
    for _ in range(64):
        parent = parent_of(by_pid, proc)
        if parent is None or parent.norm != proc.norm:
            break
        proc = parent
    return proc


def active_tree(by_pid, root, children=None):
    """PIDs in use because of root: itself, its ancestors and what it started."""
    if root not in by_pid:
        return set()
    out, proc = {root}, by_pid[root]
    for _ in range(64):
        proc = parent_of(by_pid, proc)
        if proc is None or proc.pid in out:
            break
        out.add(proc.pid)
    # The desktop starts every app; a click on it does not put them all in use.
    if by_pid[root].norm not in rules.SESSION_SHELLS:
        if children is None:
            children = children_map(by_pid)
        out.update(p.pid for p in descendants(children, root))
    return out


class Engine:
    def __init__(self, paths, sampler=None, closer=None, foreground=foreground_pid,
                 clock=time.time, memory=read_memory, battery=on_battery,
                 self_usage=None, owned=None, app_like=None, dry_run=False):
        self.paths = paths
        self.dry_run = dry_run  # look only: close nothing, consume no requests
        self.sampler = sampler or Sampler()
        self.closer = closer or Closer()
        self.foreground = foreground
        self.clock = clock
        self.memory = memory
        self.battery = battery
        self.self_usage = self_usage or SelfMeter()
        inspector = Inspector()
        self.owned = owned or inspector.owned
        self.app_like = app_like or inspector.app_like
        self.cfg, self._cfg_mtime = dict(cfgmod.DEFAULTS), None
        self._idle = {}        # (pid, create) -> consecutive idle samples
        self._idle_since = {}  # (pid, create) -> when it became idle
        self._pending = {}     # main pid -> close request awaiting exit
        self._attempts = {}    # name -> (ticks with a close request, last time)
        self._denied = set()   # (pid, create) we are not allowed to close
        self._last_fg = None
        self._focus_known = False
        self._self = {os.getpid(), os.getppid()}
        self._urgent = False   # RAM is nearly full and closing is allowed
        self._scan_percent = 0.0
        self._last_scan = None
        self._was_paused = False
        self._on_battery = False
        self._wake = threading.Event()
        self.watching = False  # the panel is open: keep its details fresh
        self.slowdown = 1      # raised when the guardian itself uses too much CPU
        self._light_scans = 0
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

    def _config_mtime(self):
        try:
            return self.paths.config.stat().st_mtime
        except OSError:
            return 0

    def _reload_config(self):
        mtime = self._config_mtime()
        if mtime != self._cfg_mtime:
            self._cfg_mtime = mtime
            self.cfg, warnings = cfgmod.load(self.paths.config)
            for w in warnings:
                log.warning("config: %s", w)

    def _resolve_pending(self, now, by_pid):
        def gone(pid, create):
            cur = by_pid.get(pid)
            return cur is None or abs(cur.create - create) > 1

        for pid, req in list(self._pending.items()):
            if gone(pid, req["create"]):
                del self._pending[pid]
                # Count only what really exited: something the app started may live on.
                freed = sum(size for mpid, create, size in req["members"] if gone(mpid, create))
                self.saved_session += freed
                self.saved_total += freed
                self.closed_total += 1
                self.history.insert(0, {"name": req["name"], "bytes": freed, "time": now})
                del self.history[HISTORY:]
                log.info("closed %s (pid %s), freed %.0f MB", req["name"], pid, freed / 2**20)
            elif now >= req["deadline"]:
                del self._pending[pid]
                log.info("%s (pid %s) did not exit; left running", req["name"], pid)

    def _is_junk(self, norm):
        return self.cfg["close_builtin_junk"] and rules.is_junk(norm)

    def _closing_pids(self):
        """PIDs of the apps that were asked to close, helpers included."""
        return {pid for req in self._pending.values() for pid, _, _ in req["members"]}

    def _not_wanted(self, p, active, protect, targets):
        """Why the app whose main process is p is not up for closing at all; '' if it is."""
        if rules.is_protected(p.norm, protect):
            return "protected"
        if p.pid in active:
            return "in use"
        junk = self._is_junk(p.norm)
        if p.norm not in targets and not junk:
            return "not on close list"
        # Without knowing which app is in use, a user's own app could be the one
        # in front, so only built-in background junk is closed.
        if not junk and not self._focus_known and not self.cfg["allow_without_focus"]:
            return "focus unknown"
        return ""

    def _not_ready(self, p, family, now, this_tick=()):
        """Why a wanted app (main process p plus its family) cannot be closed yet; '' if it can."""
        cfg = self.cfg
        if p.rss + sum(m.rss for m in family) < cfg["min_process_mb"] * 2**20:
            return "too small"
        if now - p.create < MIN_AGE_SECONDS:
            return "just started"
        # One busy helper keeps the whole app.
        if any(self._idle.get((m.pid, m.create), 0) < cfg["idle_samples"] for m in [p] + family):
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

    def _suggestions(self, procs, by_pid, children, now, protect, targets):
        """Big apps that have sat idle in the background for a long time."""
        # "In the background" cannot be judged without knowing which app is in front.
        if not self._focus_known and not self.cfg["allow_without_focus"]:
            return []
        groups = {}
        for p in procs:
            if p.pid in self._self or p.norm in targets:
                continue
            if rules.is_protected(p.norm, protect) or rules.is_junk(p.norm):
                continue
            # Apps the session started, not helpers: those go when their own app goes.
            parent = parent_of(by_pid, p)
            if parent is not None and parent.pid != 1 and not rules.is_protected(parent.norm):
                continue
            g = groups.setdefault(p.norm, {"name": p.name, "bytes": 0, "since": 0.0, "mains": []})
            g["mains"].append(p)
            for m in [p] + descendants(children, p.pid):
                g["bytes"] += m.rss
                start = self._idle_since.get((m.pid, m.create))
                # One busy or in-use process means the whole app is not idle.
                g["since"] = None if start is None or g["since"] is None else max(g["since"], start)
        out = []
        for g in groups.values():
            if g["since"] is None or now - g["since"] < SUGGEST_IDLE_SECONDS:
                continue
            if g["bytes"] < SUGGEST_MIN_BYTES:
                continue
            if not all(self.owned(p) and self.app_like(p) for p in g["mains"]):
                continue
            out.append({"name": g["name"], "bytes": g["bytes"],
                        "idle_minutes": int((now - g["since"]) // 60)})
        out.sort(key=lambda s: -s["bytes"])
        return out[:3]

    def _take_clean_request(self):
        try:
            self.paths.clean.unlink()
            return True
        except OSError:
            return False

    def _govern(self, usage):
        """Back off when the guardian itself is the one using the CPU."""
        cpu, limit = usage.get("cpu"), self.cfg["self_cpu_limit_percent"]
        if cpu is None:
            return
        before = self.slowdown
        if cpu > limit:
            self.slowdown, self._light_scans = min(MAX_SLOWDOWN, self.slowdown * 2), 0
        elif cpu < limit / 2.0 and self.slowdown > 1:
            # Speed up again only after a run of light scans, or it would flip back and forth.
            self._light_scans += 1
            if self._light_scans >= RECOVER_SCANS:
                self.slowdown, self._light_scans = self.slowdown // 2, 0
        else:
            self._light_scans = 0
        if self.slowdown != before:
            log.info("own CPU use %.1f%% (limit %.1f%%): scan interval x%d",
                     cpu, limit, self.slowdown)

    def tick(self):
        """Full scan: sample every process, close what is safe to close."""
        now = self.clock()
        self._reload_config()
        cfg = self.cfg
        mem, procs = self.sampler()
        by_pid = {p.pid: p for p in procs}
        children = children_map(by_pid)

        fg = self.foreground()
        # Clicking the pet must not make the guardian itself "the app in use", and a
        # window of another account (or a PID from inside a sandbox) proves nothing.
        if fg in self._self or fg not in by_pid or not self.owned(by_pid[fg]):
            fg = self._last_fg if self._last_fg in by_pid else None
        self._last_fg = fg
        self._focus_known = fg is not None
        active = active_tree(by_pid, fg, children)
        self._resolve_pending(now, by_pid)

        idle, since = {}, {}
        for p in procs:
            key = (p.pid, p.create)
            # Idle means quiet and in the background, so an app the user just
            # switched away from is not closed the moment it loses focus.
            if p.cpu is not None and p.cpu < cfg["idle_cpu_percent"] and p.pid not in active:
                idle[key] = self._idle.get(key, 0) + 1
                since[key] = self._idle_since.get(key, now)
            else:
                idle[key] = 0
        self._idle, self._idle_since = idle, since

        installing = sorted({p.norm for p in procs} & rules.INSTALLERS)
        paused = self.paths.pause.exists()
        clean = not self.dry_run and self._take_clean_request()
        if paused:
            note = "paused"
        elif cfg["mode"] == "observe":
            note = "observe only"
        elif installing:
            note = "waiting: %s is running" % installing[0]
        elif mem["percent"] < cfg["act_above_percent"] and not clean:
            note = RAM_FINE
        elif self.dry_run:
            note = "scan only"
        else:
            note = ""

        protect, targets = set(cfg["protect_list"]), set(cfg["close_list"])
        self._denied &= {(p.pid, p.create) for p in procs}
        # An app is decided as a whole, through its main process: helpers are never
        # closed on their own, and one busy helper keeps the whole app.
        main = {p.pid: main_process(by_pid, p) for p in procs}
        going = self._closing_pids()
        outcome, would_close, this_tick, requested = {}, [], set(), 0
        for p in procs:
            if main[p.pid] is not p or p.pid in self._self or p.pid in going:
                continue
            reason = self._not_wanted(p, active, protect, targets)
            family = []
            if not reason:
                family = [m for m in descendants(children, p.pid) if m.pid not in self._self]
                reason = self._not_ready(p, family, now, this_tick)
            if not reason and note:
                would_close.append(p.name)
                reason = note
            elif not reason:
                ok, why, members = self.closer.request(p, family)
                if ok:
                    if p.norm not in this_tick:
                        this_tick.add(p.norm)
                        self._attempts[p.norm] = (self._attempts.get(p.norm, (0, 0))[0] + 1, now)
                    self._pending[p.pid] = {"name": p.name, "create": p.create,
                                            "members": members,
                                            "deadline": now + cfg["close_timeout_seconds"]}
                    requested += 1
                    log.info("asked %s (pid %s) to close", p.name, p.pid)
                    continue
                if why == "gone":
                    continue
                if why == "no permission":
                    self._denied.add((p.pid, p.create))
                reason = why
            outcome[p.pid] = reason

        # One row per app name: its processes added up, with the most telling reason.
        kept, going = {}, self._closing_pids()
        for p in procs:
            reason = outcome.get(main[p.pid].pid)
            if reason is None or p.pid in self._self or p.pid in going:
                continue
            row = kept.get(p.norm)
            if row is None:
                kept[p.norm] = [p.norm in targets or self._is_junk(p.norm), p.rss,
                                main[p.pid].name, reason]
            else:
                row[1] += p.rss
                if _rank(reason) < _rank(row[3]):
                    row[3] = reason

        usage = self.self_usage()
        self._govern(usage)
        self._on_battery = bool(self.battery())
        self._last_scan, self._was_paused, self._scan_percent = now, paused, mem["percent"]
        self._urgent = not note and mem["percent"] >= HIGH_PERCENT

        shown = note or "active"
        if clean and not note:
            shown = "cleaning" if requested else "nothing idle to clean"
        # Close-list entries first, then the biggest memory users.
        rows = sorted(kept.values(), key=lambda r: (not r[0], -r[1]))[:8]
        self.snapshot = {
            "updated": now, "pid": os.getpid(), "mode": cfg["mode"], "paused": paused,
            "note": shown, "mem": mem,
            "focus": by_pid[fg].name if fg in by_pid else None,
            "saved_session": self.saved_session, "saved_total": self.saved_total,
            "closed_total": self.closed_total, "closed": list(self.history),
            "closing": [r["name"] for r in self._pending.values()],
            "would_close": sorted(set(would_close)),
            "kept": [{"name": name, "bytes": size, "reason": why} for _, size, name, why in rows],
            "suggest": self._suggestions(procs, by_pid, children, now, protect, targets),
            "self": dict(usage, slowdown=self.slowdown),
            "battery": self._on_battery,
        }
        return self.snapshot

    def _calm_interval(self):
        return self.cfg["calm_interval_seconds"] * (2 if self._on_battery else 1)

    def _level(self, percent):
        """0 below the action threshold, 1 above it, 2 nearly full."""
        if percent >= HIGH_PERCENT:
            return 2
        return 1 if percent >= self.cfg["act_above_percent"] else 0

    def _can_skip_scan(self, now):
        """True while a full scan is unlikely to find anything new to do."""
        if self._pending or self._urgent or self.watching or self._last_scan is None:
            return False
        if now - self._last_scan >= self._calm_interval():
            return False
        if self.paths.clean.exists() or self.paths.pause.exists() != self._was_paused:
            return False
        return self._config_mtime() == self._cfg_mtime

    def step(self):
        """One cycle. Returns True after a full scan, False after a cheap memory check."""
        now = self.clock()
        if self._can_skip_scan(now):
            mem = self.memory()
            # Scan at once when memory crosses a threshold; 2 points absorbs jitter.
            crossed = (abs(mem["percent"] - self._scan_percent) >= 2
                       and self._level(mem["percent"]) != self._level(self._scan_percent))
            if not crossed:
                self.snapshot = dict(self.snapshot, mem=mem, updated=now)
                return False
        self.tick()
        return True

    def wake(self):
        """Run the next cycle now instead of waiting for the interval."""
        self._wake.set()

    def run(self, stop_event):
        log.info("guardian started (pid %s)", os.getpid())
        while not stop_event.is_set():
            try:
                if self.step():
                    cfgmod.write_json(self.paths.state, self.snapshot)
            except Exception:
                log.exception("tick failed")
            deadline = time.monotonic() + self.cfg["interval_seconds"] * self.slowdown
            while not stop_event.is_set():
                left = deadline - time.monotonic()
                if left <= 0 or self._wake.wait(min(1.0, left)):
                    break
                if self.paths.stop.exists():
                    stop_event.set()
                elif self.paths.clean.exists():
                    break
            self._wake.clear()
        log.info("guardian stopped")

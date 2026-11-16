import json
import os
import subprocess
import sys

import psutil
import pytest

from smartram import autostart, config, foreground, rules
from smartram.__main__ import main
from smartram.engine import Engine, active_tree, make_proc
from smartram.widget import fmt_bytes, mood, panel_text

MB = 2**20


class FakeCloser:
    def __init__(self, result=(True, 50 * MB, "")):
        self.result, self.asked = result, []

    def request(self, proc):
        self.asked.append(proc.name)
        return self.result


class World:
    """A fake machine the engine can sample."""

    def __init__(self, tmp_path, percent=80, closer=None):
        self.now = 10000.0
        self.percent = percent
        self.fg = 100
        self.procs = [
            make_proc(1, "systemd", 20 * MB),
            make_proc(100, "firefox", 900 * MB, cpu=5.0),
            make_proc(101, "crashpad_handler", 30 * MB, ppid=100),
            make_proc(200, "GoogleUpdate.exe", 60 * MB),
            make_proc(300, "spotify", 400 * MB),
        ]
        self.closer = closer or FakeCloser()
        self.paths = config.Paths(tmp_path).ensure()
        self.scans, self.on_battery, self.own_cpu = 0, False, 0.1
        self.engine = Engine(self.paths, sampler=self.sample, closer=self.closer,
                             foreground=lambda: self.fg, clock=lambda: self.now,
                             memory=self.memory, battery=lambda: self.on_battery,
                             self_usage=lambda: {"cpu": self.own_cpu, "rss": 30 * MB},
                             owned=lambda proc: True)

    def memory(self):
        return {"total": 8 * 2**30, "available": 2 * 2**30, "percent": self.percent}

    def sample(self):
        self.scans += 1
        return self.memory(), list(self.procs)

    def tick(self, times=1):
        for _ in range(times):
            self.now += 5
            snap = self.engine.tick()
        return snap

    def step(self):
        """One cycle of the real loop: True for a full scan, False for a cheap check."""
        self.now += 5
        return self.engine.step()

    def remove(self, pid):
        self.procs = [p for p in self.procs if p.pid != pid]

    def reason(self, snap, name):
        return {k["name"]: k["reason"] for k in snap["kept"]}.get(name)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_normalize_and_rules():
    assert rules.normalize(" GoogleUpdate.EXE ") == "googleupdate"
    assert rules.is_junk("googleupdate") and rules.is_junk("chrome_crashpad_handler")
    assert not rules.is_junk("firefox")
    assert rules.is_protected("explorer") and rules.is_protected("python3.12")
    assert rules.is_protected("spotify", {"spotify"}) and not rules.is_protected("spotify")
    assert not rules.JUNK & rules.PROTECTED


def test_config_defaults_and_bad_values(tmp_path):
    cfg, warnings = config.load(tmp_path / "missing.json")
    assert cfg == config.DEFAULTS and not warnings
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"interval_seconds": -1, "mode": "kill", "act_above_percent": 75,
                                "close_list": ["Spotify.exe"], "protect_list": "x",
                                "allow_without_focus": "yes", "widget": False,
                                "unknown": 1}))
    cfg, warnings = config.load(path)
    assert cfg["interval_seconds"] == 5 and cfg["mode"] == "auto"
    assert cfg["act_above_percent"] == 75 and cfg["close_list"] == ["spotify"]
    assert cfg["allow_without_focus"] is False and cfg["widget"] is False
    assert len(warnings) == 4 and "unknown" not in cfg
    path.write_text("{broken")
    cfg, warnings = config.load(path)
    assert cfg == config.DEFAULTS and warnings


def test_active_tree_covers_ancestors_and_descendants():
    procs = [make_proc(1, "init", ppid=0), make_proc(2, "shell", ppid=1),
             make_proc(3, "app", ppid=2), make_proc(4, "child", ppid=3),
             make_proc(5, "other", ppid=1)]
    by_pid = {p.pid: p for p in procs}
    assert active_tree(by_pid, 3) == {1, 2, 3, 4}
    assert active_tree(by_pid, 99) == set()


def test_closes_idle_junk_and_counts_saved_ram(world):
    snap = world.tick(2)
    assert world.closer.asked == [] and world.reason(snap, "GoogleUpdate.exe") == "busy"
    snap = world.tick()
    assert world.closer.asked == ["GoogleUpdate.exe"]
    assert snap["closing"] == ["GoogleUpdate.exe"] and snap["saved_total"] == 0
    world.remove(200)
    snap = world.tick()
    assert snap["saved_session"] == 50 * MB and snap["closed_total"] == 1
    assert snap["closed"][0]["name"] == "GoogleUpdate.exe" and snap["closing"] == []
    # Totals survive a restart.
    world.paths.state.write_text(json.dumps(snap))
    assert Engine(world.paths, sampler=world.sample).saved_total == 50 * MB


def test_never_touches_foreground_tree_protected_or_unlisted(world):
    snap = world.tick(5)
    assert world.closer.asked == ["GoogleUpdate.exe"]
    assert world.reason(snap, "crashpad_handler") == "in use"
    assert world.reason(snap, "firefox") == "in use"
    assert world.reason(snap, "spotify") == "not on close list"
    assert world.reason(snap, "systemd") == "protected"
    assert snap["focus"] == "firefox"


def test_close_list_and_protect_list(world):
    config.save(world.paths.config, dict(config.DEFAULTS, close_list=["spotify", "systemd"],
                                         protect_list=["googleupdate"]))
    snap = world.tick(4)
    assert world.closer.asked == ["spotify"]
    assert world.reason(snap, "GoogleUpdate.exe") == "protected"
    assert world.reason(snap, "systemd") == "protected"


def test_busy_small_and_new_processes_are_kept(world):
    world.procs += [make_proc(400, "jusched", 80 * MB, cpu=30.0),
                    make_proc(401, "jucheck", 2 * MB),
                    make_proc(402, "AdobeARM", 80 * MB, create=world.now),
                    make_proc(403, "acrotray", 80 * MB, cpu=None)]
    world.remove(200)
    snap = world.tick(4)
    assert world.closer.asked == []
    assert world.reason(snap, "jusched") == "busy"
    assert world.reason(snap, "jucheck") == "too small"
    assert world.reason(snap, "AdobeARM") == "just started"
    assert world.reason(snap, "acrotray") == "busy"


@pytest.mark.parametrize("setup,note", [
    (lambda w: w.paths.pause.write_text("x"), "paused"),
    (lambda w: config.save(w.paths.config, dict(config.DEFAULTS, mode="observe")), "observe only"),
    (lambda w: w.procs.append(make_proc(500, "apt-get", MB)), "waiting: apt-get is running"),
    (lambda w: setattr(w, "percent", 30), "RAM is fine"),
])
def test_gates_block_closing(world, setup, note):
    setup(world)
    snap = world.tick(5)
    assert world.closer.asked == []
    assert snap["note"] == note and snap["would_close"] == ["GoogleUpdate.exe"]


def test_process_that_ignores_close_is_left_running_then_retried(world):
    snap = world.tick(3)
    assert snap["closing"] == ["GoogleUpdate.exe"]
    world.now += 60
    snap = world.tick()
    assert snap["closing"] == [] and snap["saved_total"] == 0
    assert world.reason(snap, "GoogleUpdate.exe") == "retry later"
    world.now += 11 * 60
    world.tick()
    assert world.closer.asked == ["GoogleUpdate.exe"] * 2


def test_respawning_junk_is_given_up_on(world):
    for pid in (200, 201, 202, 203):
        world.procs = [p for p in world.procs if p.norm != "googleupdate"]
        world.procs.append(make_proc(pid, "GoogleUpdate.exe", 60 * MB))
        world.now += 11 * 60
        snap = world.tick(4)
    assert len(world.closer.asked) == 3
    assert world.reason(snap, "GoogleUpdate.exe") == "keeps coming back"


def test_same_named_instances_close_together(world):
    world.procs.append(make_proc(201, "GoogleUpdate.exe", 60 * MB))
    world.tick(3)
    assert world.closer.asked == ["GoogleUpdate.exe"] * 2


def test_no_permission_is_reported_and_not_retried(tmp_path):
    world = World(tmp_path, closer=FakeCloser((False, 0, "no permission")))
    snap = world.tick(6)
    assert world.closer.asked == ["GoogleUpdate.exe"]
    assert world.reason(snap, "GoogleUpdate.exe") == "no permission"


def test_calm_mode_scans_only_when_something_can_happen(world):
    world.percent = 30
    assert world.step() is True and world.scans == 1           # the first look is full
    assert [world.step() for _ in range(5)] == [False] * 5 and world.scans == 1
    assert world.engine.snapshot["updated"] == world.now       # memory is still refreshed
    assert world.step() is True and world.scans == 2           # 30 s calm interval passed
    world.percent = 80                                         # RAM got tight: act at once
    assert world.step() is True and world.step() is True and world.scans == 4


def test_stays_calm_above_the_threshold_until_ram_is_nearly_full(world):
    world.remove(200)                                          # 80% used, nothing closable
    assert world.step() is True
    assert [world.step() for _ in range(5)] == [False] * 5
    world.percent = 81                                         # jitter is not a reason to scan
    assert world.step() is True and world.step() is False      # (30 s scan, then calm again)
    world.percent = 95                                         # nearly full: watch closely
    assert world.step() is True and world.step() is True


def test_calm_mode_wakes_for_panel_pause_and_config(world):
    world.percent = 30
    world.step()
    world.engine.watching = True                               # panel opened
    assert world.step() is True
    world.engine.watching = False
    assert world.step() is False
    world.paths.pause.write_text("x")
    assert world.step() is True and world.engine.snapshot["note"] == "paused"
    assert world.step() is False
    world.paths.pause.unlink()
    assert world.step() is True
    config.save(world.paths.config, dict(config.DEFAULTS, mode="observe"))
    assert world.step() is True and world.engine.snapshot["note"] == "observe only"


def test_on_battery_scans_half_as_often(world):
    world.percent, world.on_battery = 30, True
    world.step()
    assert [world.step() for _ in range(11)] == [False] * 11   # 55 s without a scan
    assert world.step() is True and world.engine.snapshot["battery"] is True


def test_backs_off_when_the_guardian_itself_is_heavy(world):
    world.own_cpu = 5.0                                        # over the 2% limit
    assert world.tick()["self"]["slowdown"] == 2
    assert world.tick(5)["self"]["slowdown"] == 8              # capped
    world.own_cpu = 1.5                                        # under the limit: hold
    assert world.tick()["self"]["slowdown"] == 8
    world.own_cpu = 0.2
    assert world.tick(3)["self"]["slowdown"] == 1


def test_clean_now_ignores_the_ram_threshold(world):
    world.percent = 30
    world.tick(4)
    assert world.closer.asked == []
    world.paths.clean.write_text("x")
    assert world.step() is True and not world.paths.clean.exists()
    assert world.closer.asked == ["GoogleUpdate.exe"]
    assert world.engine.snapshot["note"] == "cleaning"
    world.remove(200)
    world.paths.clean.write_text("x")
    assert world.tick()["note"] == "nothing idle to clean"


def test_app_just_switched_away_from_is_not_closed_at_once(world):
    config.save(world.paths.config, dict(config.DEFAULTS, close_list=["spotify"]))
    world.fg = 300                                             # working in spotify, 0% CPU
    world.tick(6)
    world.fg = 100                                             # switch to firefox
    snap = world.tick()
    assert "spotify" not in world.closer.asked and world.reason(snap, "spotify") == "busy"
    world.tick(2)
    assert "spotify" in world.closer.asked


def test_unknown_focus_only_closes_builtin_junk(world):
    config.save(world.paths.config, dict(config.DEFAULTS, close_list=["spotify"]))
    world.fg = None
    snap = world.tick(4)
    assert set(world.closer.asked) == {"GoogleUpdate.exe", "crashpad_handler"}
    assert world.reason(snap, "spotify") == "focus unknown"
    config.save(world.paths.config, dict(config.DEFAULTS, close_list=["spotify"],
                                         allow_without_focus=True))
    world.engine._cfg_mtime = None
    world.tick()
    assert "spotify" in world.closer.asked


def test_suggests_big_apps_idle_in_the_background_for_long(world):
    assert world.tick()["suggest"] == []
    world.now += 16 * 60
    snap = world.tick()
    assert snap["suggest"] == [{"name": "spotify", "bytes": 400 * MB, "idle_minutes": 16}]
    tips = panel_text(snap)["tips"]
    assert "spotify" in tips and "idle 16 min" in tips and "smartram allow" in tips
    world.engine.owned = lambda proc: False                    # someone else's process
    assert world.tick()["suggest"] == []
    world.engine.owned = lambda proc: True
    world.procs.append(make_proc(301, "spotify", 50 * MB, cpu=40.0))  # one busy instance
    assert world.tick()["suggest"] == []


def test_focus_strategy_and_wayland_parsers():
    def pick(env, platform="linux"):
        return foreground.strategy(env, platform, lambda tool: "/usr/bin/" + tool)[0]

    assert pick({}, "win32") == "Windows API" and pick({}, "darwin") == "lsappinfo"
    assert pick({"DISPLAY": ":0"}) == "xdotool" and pick({}) is None
    # GNOME on Wayland: X11 tools would report a stale window, so report nothing.
    assert pick({"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}) is None
    assert pick({"WAYLAND_DISPLAY": "w", "SWAYSOCK": "/run/sway"}) == "swaymsg"
    assert pick({"WAYLAND_DISPLAY": "w", "HYPRLAND_INSTANCE_SIGNATURE": "x"}) == "hyprctl"
    assert pick({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}) == "kdotool"
    assert foreground.strategy({"DISPLAY": ":0"}, "linux", lambda tool: None) == (None, None)
    assert foreground.parse_hyprland('{"pid": 4242, "class": "kitty"}') == 4242
    assert foreground.parse_hyprland("{}") is None and foreground.parse_hyprland("") is None
    tree = {"nodes": [{"nodes": [{"pid": 7, "focused": False}],
                       "floating_nodes": [{"pid": 9, "focused": True}]}]}
    assert foreground.parse_sway(json.dumps(tree)) == 9
    assert foreground.parse_sway("not json") is None and foreground.parse_sway("{}") is None


def test_own_widget_in_foreground_keeps_previous_focus(world):
    world.tick()
    world.fg = os.getpid()
    snap = world.tick()
    assert snap["focus"] == "firefox"
    world.fg = None
    assert world.tick()["focus"] == "firefox"


def test_panel_text_and_mood(world):
    world.tick(3)
    world.remove(200)
    text = panel_text(world.tick())
    assert text["ram"] == "RAM 80%   6.0 GB of 8.0 GB used"
    assert text["saved"].startswith("Saved 50 MB") and text["closed_title"] == "Closed (1)"
    assert "GoogleUpdate.exe" in text["closed"] and "firefox" in text["kept"]
    assert text["focus"] == "Working in: firefox"
    assert text["self"] == "Guardian itself: 30 MB, 0.1% CPU" and text["tips"] == ""
    assert panel_text({})["closed"] == "nothing yet"
    assert fmt_bytes(1536 * MB) == "1.5 GB"
    assert mood({"mem": {"percent": 95}}, 60) == "high"
    assert mood({"mem": {"percent": 70}}, 60) == "busy"
    assert mood({"mem": {"percent": 10}}, 60) == "ok"
    assert mood({"paused": True}, 60) == "paused"


def test_pet_art_is_png():
    import base64
    from smartram import pet_art
    for name in ("OPEN", "BLINK", "PAUSED"):
        assert base64.b64decode(getattr(pet_art, name)).startswith(b"\x89PNG")


def test_cli_lists_scan_and_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SMARTRAM_HOME", str(tmp_path))
    assert main(["allow", "Spotify.exe"]) == 0
    assert main(["protect", "slack"]) == 0
    assert main(["allow", "explorer.exe"]) == 2
    cfg, _ = config.load(tmp_path / "config.json")
    assert cfg["close_list"] == ["spotify"] and cfg["protect_list"] == ["slack"]
    assert main(["protect", "spotify"]) == 0 and main(["forget", "slack"]) == 0
    cfg, _ = config.load(tmp_path / "config.json")
    assert cfg["close_list"] == [] and cfg["protect_list"] == ["spotify"]
    assert main(["pause"]) == 0 and (tmp_path / "guardian.pause").exists()
    assert main(["resume"]) == 0 and not (tmp_path / "guardian.pause").exists()
    capsys.readouterr()
    assert main(["scan"]) == 0
    snap = json.loads(capsys.readouterr().out)
    assert snap["mem"]["total"] > 0 and snap["kept"] and snap["closed_total"] == 0
    assert main(["status"]) == 3
    assert "not running" in capsys.readouterr().out
    assert main(["clean"]) == 1 and not (tmp_path / "guardian.clean").exists()
    capsys.readouterr()
    assert main(["doctor"]) == 0
    report = capsys.readouterr().out
    assert "Focus detection:" in report and "Desktop pet:" in report


def test_background_start_status_stop(tmp_path):
    # Observe mode: this starts a real guardian, which must not close anything here.
    config.save(tmp_path / "config.json", dict(config.DEFAULTS, mode="observe"))
    env = dict(os.environ, SMARTRAM_HOME=str(tmp_path))
    run = lambda *a: subprocess.run([sys.executable, "-m", "smartram"] + list(a), env=env,
                                    stdout=subprocess.PIPE, universal_newlines=True, timeout=60)
    try:
        assert run("start", "--no-widget").returncode == 0
        assert run("start", "--no-widget").stdout.strip() == "Already running."
        status = run("status")
        assert status.returncode == 0 and "running (pid" in status.stdout
        # The guardian puts itself behind every other app.
        pid = json.loads((tmp_path / "guardian.pid").read_text())["pid"]
        nice = psutil.Process(pid).nice()
        assert nice == psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else nice >= 10
        assert run("clean").returncode == 0
    finally:
        assert run("stop").returncode == 0
    assert run("status").returncode == 3
    assert not (tmp_path / "guardian.pid").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="registry is covered by the CI smoke test")
def test_autostart_files(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    assert not autostart.is_enabled()
    path = autostart.enable()
    assert autostart.is_enabled() and "smartram" in open(path, "rb").read().decode()
    autostart.disable()
    assert not autostart.is_enabled()

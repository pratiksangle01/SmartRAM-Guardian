import json
import os
import subprocess
import sys
import threading

import psutil
import pytest

from smartram import autostart, config, foreground, pathlink, rules
from smartram.__main__ import main
from smartram.engine import (Engine, active_tree, children_map, main_process, make_proc,
                             parent_of)
from smartram.widget import SIZE, Widget, fmt_bytes, mood, panel_text

MB = 2**20


class FakeCloser:
    def __init__(self, result=(True, 50 * MB, "")):
        self.result, self.asked, self.families = result, [], []

    def request(self, proc, family=()):
        self.asked.append(proc.name)
        self.families.append(sorted(m.pid for m in family))
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
                             owned=lambda proc: True, app_like=lambda proc: True)

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


def app(pid, name, rss=100 * MB, cpu=0.0, ppid=1):
    return make_proc(pid, name, rss, cpu=cpu, ppid=ppid)


def test_app_closes_through_its_main_process_and_takes_helpers_along(world):
    config.save(world.paths.config, dict(config.DEFAULTS, close_list=["discord"]))
    world.remove(200)
    world.procs += [app(400, "Discord"), app(401, "Discord", ppid=400),
                    app(402, "Discord", ppid=401), app(403, "discord_gpu_helper", ppid=400)]
    snap = world.tick(4)
    assert world.closer.asked == ["Discord"] and world.closer.families == [[401, 402, 403]]
    assert snap["closing"] == ["Discord"] and world.reason(snap, "Discord") is None
    assert world.reason(snap, "discord_gpu_helper") is None     # goes with its app
    world.procs = [p for p in world.procs if p.pid < 400]
    assert world.tick()["closed"][0]["name"] == "Discord"


def test_one_busy_helper_keeps_the_whole_app(world):
    config.save(world.paths.config, dict(config.DEFAULTS, close_list=["discord"]))
    world.remove(200)
    world.procs += [app(400, "Discord"), app(401, "Discord", ppid=400),
                    app(402, "voice_engine", cpu=20.0, ppid=401)]
    snap = world.tick(5)
    assert world.closer.asked == []
    assert world.reason(snap, "Discord") == "busy"
    rows = {k["name"]: k["bytes"] for k in snap["kept"]}
    assert rows["Discord"] == 200 * MB                          # one row for the app


def test_helpers_of_an_unlisted_app_are_never_closed_alone(world):
    # The helper is on the close list, its main process is not.
    config.save(world.paths.config, dict(config.DEFAULTS, close_list=["slack"]))
    world.remove(200)
    world.procs += [app(400, "launcher"), app(401, "slack", ppid=400),
                    app(402, "slack", ppid=401)]
    world.tick(4)
    assert world.closer.asked == ["slack"] and world.closer.families == [[402]]


def test_reused_parent_pid_is_not_a_parent():
    old = make_proc(10, "app", create=100.0, ppid=50)
    newer = make_proc(50, "other", create=900.0)                # PID 50 was reused later
    real = make_proc(60, "app", create=50.0)
    child = make_proc(61, "app", create=100.0, ppid=60)
    by_pid = {p.pid: p for p in (old, newer, real, child)}
    assert parent_of(by_pid, old) is None and parent_of(by_pid, child) is real
    assert main_process(by_pid, child) is real and main_process(by_pid, old) is old
    assert children_map(by_pid) == {60: [child]}
    assert active_tree(by_pid, 10) == {10}


def test_desktop_in_front_does_not_put_every_app_in_use(world):
    config.save(world.paths.config, dict(config.DEFAULTS, close_list=["spotify"]))
    world.procs = [make_proc(10, "explorer.exe", 80 * MB, ppid=0),
                   make_proc(11, "chrome.exe", 500 * MB, ppid=10),
                   make_proc(12, "chrome.exe", 300 * MB, ppid=11),
                   make_proc(13, "spotify", 400 * MB, ppid=10)]
    by_pid = {p.pid: p for p in world.procs}
    assert active_tree(by_pid, 10) == {10}                      # a click on the taskbar
    assert active_tree(by_pid, 11) == {10, 11, 12}              # working in the browser
    world.fg = 10
    world.tick(4)
    assert world.closer.asked == ["spotify"]                    # still counted as idle


def test_no_suggestions_while_the_app_in_front_is_unknown(world):
    world.fg = None
    world.tick()
    world.now += 16 * 60
    assert world.tick()["suggest"] == []
    config.save(world.paths.config, dict(config.DEFAULTS, allow_without_focus=True))
    assert [s["name"] for s in world.tick()["suggest"]] == ["spotify"]


def test_window_of_another_account_does_not_count_as_focus(world):
    config.save(world.paths.config, dict(config.DEFAULTS, close_list=["spotify"]))
    world.engine.owned = lambda proc: proc.pid != 100           # firefox is someone else's
    snap = world.tick(4)
    assert snap["focus"] is None and world.reason(snap, "spotify") == "focus unknown"


def test_builtin_junk_can_be_turned_off(world):
    config.save(world.paths.config, dict(config.DEFAULTS, close_builtin_junk=False))
    snap = world.tick(5)
    assert world.closer.asked == []
    assert world.reason(snap, "GoogleUpdate.exe") == "not on close list"


def test_services_that_always_run_do_not_block_closing(world):
    world.procs += [make_proc(500, "unattended-upgr", MB), make_proc(501, "TiWorker.exe", MB),
                    make_proc(502, "TrustedInstaller.exe", MB)]
    assert world.tick(4)["note"] == "active" and world.closer.asked == ["GoogleUpdate.exe"]
    assert not {"deja-dup-monitor", "update-notifier"} & rules.JUNK


def test_suggestions_are_whole_top_level_apps_only(world):
    world.procs += [app(400, "Slack", rss=150 * MB), app(401, "Slack Helper", ppid=400),
                    app(402, "Slack Helper (Renderer)", rss=300 * MB, ppid=400),
                    app(500, "Xorg", rss=600 * MB), app(600, "tracker-miner", rss=400 * MB)]
    world.engine.app_like = lambda proc: proc.name != "tracker-miner"
    world.tick()
    world.now += 16 * 60
    names = {s["name"]: s["bytes"] for s in world.tick()["suggest"]}
    assert names == {"Slack": 550 * MB, "spotify": 400 * MB}    # helpers counted, not listed


@pytest.mark.parametrize("path,platform,expected", [
    (r"C:\Windows\System32\TextInputHost.exe", "win32", False),
    (r"c:\windows\explorer.exe", "win32", False),
    (r"C:\Users\me\AppData\Local\Discord\app-1.0\Discord.exe", "win32", True),
    (r"C:\WindowsApps\Spotify.exe", "win32", True),
    ("/Applications/Slack.app/Contents/MacOS/Slack", "darwin", True),
    ("/System/Applications/Mail.app/Contents/MacOS/Mail", "darwin", True),
    ("/Applications/Slack.app/Contents/Frameworks/Slack Helper.app/Contents/MacOS/Slack Helper",
     "darwin", False),
    ("/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder", "darwin", False),
    ("/opt/homebrew/bin/postgres", "darwin", False),
    ("/usr/lib/firefox/firefox", "linux", True),
    ("/usr/libexec/evolution-data-server", "linux", False),
    ("/usr/lib/x86_64-linux-gnu/libexec/kf5/kioslave5", "linux", False),
    ("", "linux", False), (None, "win32", False),
])
def test_looks_like_app(path, platform, expected):
    assert rules.looks_like_app(path, platform, r"C:\Windows") is expected


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
    assert world.tick(8)["self"]["slowdown"] == 8
    world.own_cpu = 0.2                                        # light for a while: recover
    assert world.tick(4)["self"]["slowdown"] == 8              # ...but not at once
    assert world.tick()["self"]["slowdown"] == 4
    assert world.tick(10)["self"]["slowdown"] == 1


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
    for name in ("OPEN", "BLINK", "PAUSED", "CRISP_OPEN", "CRISP_BLINK", "CRISP_PAUSED"):
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


@pytest.mark.skipif(sys.platform == "win32", reason="the Windows PATH is checked by the CI installer step")
def test_command_is_linked_and_unlinked(tmp_path, monkeypatch):
    program = tmp_path / "venv" / "bin" / "smartram"
    program.parent.mkdir(parents=True)
    program.write_text("#!/bin/sh\n")
    monkeypatch.setattr(pathlink, "_program", lambda: program)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    for name in ("ZDOTDIR", "SMARTRAM_NO_MODIFY_PATH"):
        monkeypatch.delenv(name, raising=False)
    zshrc, link = tmp_path / ".zshrc", tmp_path / ".local" / "bin" / "smartram"
    zshrc.write_text("alias ll='ls -l'")

    assert "new terminal" in pathlink.link(tmp_path)
    assert os.path.realpath(str(link)) == os.path.realpath(str(program))
    pathlink.link(tmp_path)                                    # running the installer again
    text = zshrc.read_text()
    assert text.startswith("alias ll='ls -l'\n") and text.count(".local/bin") == 1
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    pathlink.link(tmp_path)
    fish = tmp_path / ".config" / "fish" / "conf.d" / "smartram.fish"
    assert fish.exists()

    pathlink.unlink(tmp_path)
    assert not os.path.lexists(str(link)) and not fish.exists()
    assert zshrc.read_text() == "alias ll='ls -l'\n"           # the user's own lines stay

    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("PATH", "%s:/usr/bin" % link.parent)    # already on PATH: edit nothing
    assert pathlink.link(tmp_path) == "Command: smartram"
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("SMARTRAM_NO_MODIFY_PATH", "1")
    assert pathlink.link(tmp_path) == "Command: %s" % link
    assert "SmartRAM" not in zshrc.read_text()

    # Another installation's link is not ours to remove.
    monkeypatch.setattr(pathlink, "_program", lambda: tmp_path / "other" / "smartram")
    pathlink.unlink(tmp_path)
    assert os.path.lexists(str(link))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell profiles")
def test_shell_profile_choice_never_hides_another_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    pick = pathlink._profile_for
    assert pick("/bin/zsh").name == ".zshrc" and pick("/bin/bash", "linux").name == ".bashrc"
    assert pick("/bin/dash").name == ".profile" and pick("").name == ".profile"
    assert pick("/bin/bash", "darwin").name == ".profile"      # no new ~/.bash_profile
    (tmp_path / ".bash_profile").write_text("")
    assert pick("/bin/bash", "darwin").name == ".bash_profile"


def test_uninstall_deletes_only_its_own_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SMARTRAM_HOME", str(tmp_path))
    monkeypatch.setattr(autostart, "disable", lambda: None)
    monkeypatch.setattr(pathlink, "unlink", lambda home: None)
    for name in ("config.json", "state.json", "guardian.log", "guardian.log.1", "thesis.docx"):
        (tmp_path / name).write_text("x")
    (tmp_path / "venv").mkdir()
    (tmp_path / "venv" / "pyvenv.cfg").write_text("x")
    assert main(["uninstall"]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["thesis.docx"]
    assert "Other files were left" in capsys.readouterr().out
    (tmp_path / "thesis.docx").unlink()
    (tmp_path / "config.json").write_text("{}")
    assert main(["uninstall"]) == 0 and not tmp_path.exists()


@pytest.fixture
def pet(world):
    pytest.importorskip("tkinter")
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        pytest.skip("no display")
    world.procs.append(app(700, "Slack", rss=300 * MB))
    world.tick()
    world.now += 16 * 60
    world.tick()                                               # spotify and Slack are suggested
    widget = Widget(world.engine, threading.Event())
    yield widget
    try:
        widget.root.destroy()
    except Exception:
        pass


def test_pet_opens_closes_and_can_be_dragged(pet, world):
    from types import SimpleNamespace as Event
    size = lambda: (pet.root.winfo_reqwidth(), pet.root.winfo_reqheight())
    pet.root.update()
    assert not pet.expanded and pet._size == (SIZE, SIZE)
    pet._press(Event(x_root=500, y_root=500))
    pet._release(Event())                                      # a click opens the panel
    pet.root.update()
    assert pet.expanded and world.engine.watching and size()[0] > 200 and size()[1] > 200
    assert pet.labels["ram"].cget("text").startswith("RAM 80%")
    assert "firefox" in pet.labels["kept"].cget("text")
    x = pet.px
    pet._press(Event(x_root=500, y_root=500))
    pet._motion(Event(x_root=440, y_root=470))
    pet._release(Event())                                      # a drag moves it, no toggle
    assert pet.expanded and pet.px == x - 60
    pet.toggle()
    pet.root.update()
    assert not pet.expanded and not world.engine.watching and pet._size == (SIZE, SIZE)


def test_pet_buttons_pause_clean_and_quit(pet, world):
    pet.toggle()
    pet.pause_btn.command()
    assert world.paths.pause.exists() and pet.pause_btn.cget("text") == "Resume"
    assert pet._pet_state[0] == "PAUSED"
    pet.pause_btn.command()
    assert not world.paths.pause.exists() and pet.pause_btn.cget("text") == "Pause"
    pet.clean_btn.command()
    assert world.paths.clean.exists()
    pet._quit()
    assert pet.stop.is_set()


def test_pet_allow_asks_first_then_lets_the_guardian_close_the_app(pet, world):
    def rows():
        pet.root.update_idletasks()
        return [[c.cget("text") for c in row.winfo_children()]
                for row in pet.tip_rows.winfo_children()]

    def click(row, label):
        button = [c for c in pet.tip_rows.winfo_children()[row].winfo_children()
                  if c.cget("text") == label][0]
        button.command()

    pet.toggle()
    assert [r[1:] for r in rows()] == [["Allow"], ["Allow"]] and "spotify" in rows()[0][0]
    click(0, "Allow")
    assert rows()[0][0] == "Close spotify when idle?" and sorted(rows()[0][1:]) == ["No", "Yes"]
    click(0, "No")
    assert rows()[0][1:] == ["Allow"] and not world.paths.config.exists()
    click(0, "Allow")
    click(0, "Yes")
    assert config.load(world.paths.config)[0]["close_list"] == ["spotify"]
    assert world.paths.clean.exists() and len(rows()) == 1 and "Slack" in rows()[0][0]
    world.tick(2)                                              # the guardian acts on it
    assert world.closer.asked == ["GoogleUpdate.exe", "spotify"]
    pet._refresh_panel()
    assert len(rows()) == 1 and "spotify" in pet.labels["closed"].cget("text")


@pytest.mark.skipif(sys.platform == "win32", reason="registry is covered by the CI smoke test")
def test_autostart_files(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    assert not autostart.is_enabled()
    path = autostart.enable()
    assert autostart.is_enabled() and "smartram" in open(path, "rb").read().decode()
    autostart.disable()
    assert not autostart.is_enabled()
